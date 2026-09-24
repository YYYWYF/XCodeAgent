from __future__ import annotations

import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.template_reconcile.protocol_v2 import TemplateStateV2
from app.services.workspace_bootstrap.fs import remove_managed_path
from app.services.workspace_bootstrap.git_template_package import (
    GitTemplatePackageBuilder,
    _write_archive,
)
from app.services.workspace_bootstrap.models import ArchiveLimits, GitTemplateError, TemplatePackageDownload
from app.services.workspace_bootstrap.template_package import validate_template_package


class GitTemplatePackageBuilderTests(unittest.TestCase):
    """验证公开 Git 模式复用统一三端 Package 契约。"""

    def test_clone_retries_transient_connection_failure_after_cleaning_partial_root(self) -> None:
        """GitHub 建连超时后清理残留目录，并重试当前模板。"""

        builder = GitTemplatePackageBuilder(self._settings())
        clone_attempts = 0

        def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
            """模拟首次网络失败及第二次成功，检查重试前残留已清理。"""

            nonlocal clone_attempts
            if command[1] == "rev-parse":
                return SimpleNamespace(returncode=0, stdout="a" * 40, stderr="")
            clone_attempts += 1
            target_root = Path(str(command[-1]))
            if clone_attempts == 2:
                self.assertFalse(target_root.exists())
            target_root.mkdir()
            if clone_attempts == 1:
                (target_root / "partial").write_text("incomplete", encoding="utf-8")
                return SimpleNamespace(
                    returncode=128, stdout="",
                    stderr="error: RPC failed; curl 28 Failed to connect to github.com port 443",
                )
            (target_root / "package.json").write_text("{}", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.workspace_bootstrap.git_template_package.workspace_process_registry.run",
            side_effect=fake_run,
        ), patch("app.services.workspace_bootstrap.git_template_package.time.sleep"):
            root = Path(directory)
            revision = builder._clone_repository(
                root, root, target="frontend",
                repository_url="https://example.test/frontend.git", branch="main",
            )

        self.assertEqual(clone_attempts, 2)
        self.assertEqual(revision["commitSha"], "a" * 40)

    def test_clone_falls_back_to_ssh_after_three_https_network_failures(self) -> None:
        """三次 HTTPS 网络失败后，用同仓库 SSH 地址非交互克隆。"""

        builder = GitTemplatePackageBuilder(self._settings())
        clone_urls: list[str] = []

        def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
            """模拟 HTTPS 连续失败，并验证 SSH 克隆使用独立临时目录。"""

            if command[1] == "rev-parse":
                return SimpleNamespace(returncode=0, stdout="b" * 40, stderr="")
            clone_urls.append(command[-2])
            target_root = Path(command[-1])
            self.assertFalse(target_root.exists())
            target_root.mkdir()
            if len(clone_urls) <= 3:
                (target_root / "partial").write_text("incomplete", encoding="utf-8")
                return SimpleNamespace(
                    returncode=128, stdout="",
                    stderr=(
                        f"Cloning into '{target_root}'... fatal: unable to access "
                        "'https://github.com/ruyue1/frontend-template.git/': "
                        "Recv failure: Operation timed out"
                    ),
                )
            self.assertIn("BatchMode=yes", str(kwargs["env"]["GIT_SSH_COMMAND"]))
            (target_root / "package.json").write_text("{}", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.workspace_bootstrap.git_template_package.workspace_process_registry.run",
            side_effect=fake_run,
        ), patch("app.services.workspace_bootstrap.git_template_package.time.sleep"):
            root = Path(directory)
            revision = builder._clone_repository(
                root, root, target="frontend",
                repository_url="https://github.com/ruyue1/frontend-template.git",
                branch="main",
            )

        self.assertEqual(clone_urls, [
            "https://github.com/ruyue1/frontend-template.git",
            "https://github.com/ruyue1/frontend-template.git",
            "https://github.com/ruyue1/frontend-template.git",
            "git@github.com:ruyue1/frontend-template.git",
        ])
        self.assertEqual(revision["commitSha"], "b" * 40)

    def test_clone_tries_ssh_after_three_https_branch_failures(self) -> None:
        """HTTPS 错误内容不改变三次尝试加一次 SSH 的固定顺序。"""

        builder = GitTemplatePackageBuilder(self._settings())
        with tempfile.TemporaryDirectory() as directory, patch(
            "app.services.workspace_bootstrap.git_template_package.workspace_process_registry.run",
            return_value=SimpleNamespace(
                returncode=128, stdout="", stderr="fatal: Remote branch main not found",
            ),
        ) as run, patch("app.services.workspace_bootstrap.git_template_package.time.sleep"):
            root = Path(directory)
            with self.assertRaisesRegex(GitTemplateError, "Remote branch main not found"):
                builder._clone_repository(
                    root, root, target="frontend",
                    repository_url="https://github.com/ruyue1/frontend-template.git",
                    branch="main",
                )
        self.assertEqual(
            [call.args[0][-2] for call in run.call_args_list],
            [
                "https://github.com/ruyue1/frontend-template.git",
                "https://github.com/ruyue1/frontend-template.git",
                "https://github.com/ruyue1/frontend-template.git",
                "git@github.com:ruyue1/frontend-template.git",
            ],
        )

    def test_direct_repositories_use_runtime_branch_and_login_frontend(self) -> None:
        """Direct Auth 组合只拉前端认证分支和 Runtime 专用分支。"""

        settings = self._settings()
        settings.template_git_agent_runtime_direct_branch = "agent-runtime-direct"
        repositories = GitTemplatePackageBuilder(settings)._repositories(
            {
                "capabilities": {
                    "login": {"enabled": True, "config": {}},
                    "agent_runtime_public_edge": {"enabled": True, "config": {}},
                }
            },
            ("frontend", "agent-runtime"),
        )

        self.assertEqual(
            tuple((target, branch) for target, _url, branch in repositories),
            (("frontend", "auth"), ("agent-runtime", "agent-runtime-direct")),
        )

    def test_generate_conditionally_packages_agent_runtime(self) -> None:
        """有 Agent root 时必须拉取 master 并写入同一安全 ZIP。"""

        settings = SimpleNamespace(
            template_git_frontend_repository_url="https://example.test/frontend.git",
            template_git_backend_repository_url="https://example.test/backend.git",
            template_git_agent_runtime_repository_url=(
                "https://github.com/Bettetman/agent-runtime-template.git"
            ),
            template_git_agent_runtime_branch="master",
            template_git_clone_timeout_seconds=30,
        )
        builder = GitTemplatePackageBuilder(settings)

        def fake_clone(
            _workspace: Path,
            source_root: Path,
            *,
            target: str,
            repository_url: str,
            branch: str,
        ) -> dict[str, str]:
            """创建不访问网络的最小模板 root，并返回固定来源事实。"""

            target_root = source_root / target
            target_root.mkdir()
            marker = {
                "frontend": "package.json",
                "backend": "pom.xml",
                "agent-runtime": "pyproject.toml",
            }[target]
            (target_root / marker).write_text("template\n", encoding="utf-8")
            return {
                "repositoryUrl": repository_url,
                "branch": branch,
                "commitSha": f"{target}-revision",
            }

        roots = ("frontend", "backend", "agent-runtime")
        with tempfile.TemporaryDirectory() as directory, patch.object(
            builder,
            "_clone_repository",
            side_effect=fake_clone,
        ) as clone:
            download = builder.generate(
                Path(directory),
                {"capabilities": {}},
                roots,
            )
            try:
                package = validate_template_package(
                    download.temporary_path,
                    ArchiveLimits(
                        max_package_bytes=1024 * 1024,
                        max_files=20,
                        max_extracted_bytes=1024 * 1024,
                    ),
                    roots,
                )
                with zipfile.ZipFile(download.temporary_path) as archive:
                    self.assertIn("agent-runtime/pyproject.toml", archive.namelist())
                self.assertTrue(package.template_state.templateRevision.startswith("git-"))
                self.assertEqual(clone.call_count, 3)
                self.assertEqual(clone.call_args_list[-1].kwargs["branch"], "master")
            finally:
                download.temporary_path.unlink(missing_ok=True)

    def test_supplement_engine_package_adds_missing_agent_runtime(self) -> None:
        """Engine V1 ZIP 缺第三根时，必须从 Git 补齐并保留原 TemplateState。"""

        settings = self._settings()
        builder = GitTemplatePackageBuilder(settings)
        roots = ("frontend", "backend", "agent-runtime")
        with tempfile.TemporaryDirectory() as directory:
            download = self._engine_download(Path(directory), include_agent_runtime=False)
            with patch.object(
                builder, "_clone_repository", side_effect=self._fake_clone
            ) as clone:
                try:
                    supplemented = builder.supplement_engine_package(
                        Path(directory), download, roots
                    )
                    package = validate_template_package(
                        supplemented.temporary_path, self._limits(), roots
                    )
                    with zipfile.ZipFile(supplemented.temporary_path) as archive:
                        names = archive.namelist()
                    self.assertIn("frontend/package.json", names)
                    self.assertIn("backend/pom.xml", names)
                    self.assertIn("agent-runtime/pyproject.toml", names)
                    self.assertEqual(package.template_state.templateRevision, "engine-r1")
                    self.assertEqual(clone.call_count, 1)
                    self.assertEqual(clone.call_args.kwargs["target"], "agent-runtime")
                    self.assertEqual(clone.call_args.kwargs["branch"], "master")
                    self.assertFalse(download.temporary_path.exists())
                finally:
                    download.temporary_path.unlink(missing_ok=True)
                    if "supplemented" in locals():
                        supplemented.temporary_path.unlink(missing_ok=True)

    def test_supplement_engine_package_skips_when_third_root_already_present(self) -> None:
        """Engine ZIP 已含 agent-runtime 时不得再次克隆或改写 Package。"""

        settings = self._settings()
        builder = GitTemplatePackageBuilder(settings)
        with tempfile.TemporaryDirectory() as directory:
            download = self._engine_download(Path(directory), include_agent_runtime=True)
            with patch.object(builder, "_clone_repository") as clone:
                try:
                    result = builder.supplement_engine_package(
                        Path(directory),
                        download,
                        ("frontend", "backend", "agent-runtime"),
                    )
                    self.assertIs(result, download)
                    clone.assert_not_called()
                finally:
                    download.temporary_path.unlink(missing_ok=True)

    def test_supplement_engine_package_skips_without_agent_contracts(self) -> None:
        """普通应用不得向 Engine ZIP 注入 agent-runtime。"""

        settings = self._settings()
        builder = GitTemplatePackageBuilder(settings)
        with tempfile.TemporaryDirectory() as directory:
            download = self._engine_download(Path(directory), include_agent_runtime=False)
            with patch.object(builder, "_clone_repository") as clone:
                try:
                    result = builder.supplement_engine_package(
                        Path(directory), download, ("frontend", "backend")
                    )
                    self.assertIs(result, download)
                    clone.assert_not_called()
                finally:
                    download.temporary_path.unlink(missing_ok=True)

    def test_materialize_missing_agent_runtime_root_copies_cloned_project(self) -> None:
        """已有工作区缺少 pyproject.toml 时必须从 Git 模板写入 agent-runtime 根目录。"""

        settings = self._settings()
        builder = GitTemplatePackageBuilder(settings)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            with patch.object(
                builder, "_clone_repository", side_effect=self._fake_clone
            ) as clone:
                created = builder.materialize_missing_agent_runtime_root(workspace)
                skipped = builder.materialize_missing_agent_runtime_root(workspace)

            self.assertTrue(created)
            self.assertFalse(skipped)
            self.assertTrue((workspace / "agent-runtime/pyproject.toml").is_file())
            clone.assert_called_once()
            self.assertEqual(clone.call_args.kwargs["target"], "agent-runtime")

    def _settings(self) -> SimpleNamespace:
        """构造补齐第三根所需的最小 Git 配置。"""

        return SimpleNamespace(
            template_git_frontend_repository_url="https://example.test/frontend.git",
            template_git_backend_repository_url="https://example.test/backend.git",
            template_git_agent_runtime_repository_url=(
                "https://github.com/Bettetman/agent-runtime-template.git"
            ),
            template_git_agent_runtime_branch="master",
            template_git_clone_timeout_seconds=30,
        )

    def _limits(self) -> ArchiveLimits:
        """返回适合小型 fixture 的 ZIP 配额。"""

        return ArchiveLimits(
            max_package_bytes=1024 * 1024,
            max_files=20,
            max_extracted_bytes=1024 * 1024,
        )

    def _engine_download(
        self, directory: Path, *, include_agent_runtime: bool
    ) -> TemplatePackageDownload:
        """写入最小 Engine V1 ZIP，可选地预置第三根以覆盖幂等路径。"""

        path = directory / "engine.zip"
        state = {
            "schemaVersion": 2,
            "templateRevision": "engine-r1",
            "requested": {},
            "effective": {},
            "appliedAdditions": {},
        }
        with zipfile.ZipFile(path, "w") as package:
            package.writestr("frontend/package.json", "{}\n")
            package.writestr("backend/pom.xml", "<project />\n")
            if include_agent_runtime:
                package.writestr("agent-runtime/pyproject.toml", "[project]\n")
            package.writestr(".xcodeagent/template-state.json", json.dumps(state))
        return TemplatePackageDownload(
            temporary_path=path,
            sha256="ignored",
            size=path.stat().st_size,
            content_type="application/zip",
        )

    def _fake_clone(
        self,
        _workspace: Path,
        source_root: Path,
        *,
        target: str,
        repository_url: str,
        branch: str,
    ) -> dict[str, str]:
        """创建不访问网络的最小模板 root，并返回固定来源事实。"""

        del branch
        target_root = source_root / target
        target_root.mkdir()
        marker = {
            "frontend": "package.json",
            "backend": "pom.xml",
            "agent-runtime": "pyproject.toml",
        }[target]
        (target_root / marker).write_text("template\n", encoding="utf-8")
        return {
            "repositoryUrl": repository_url,
            "branch": "master",
            "commitSha": f"{target}-revision",
        }


class GitTemplateCleanupTests(unittest.TestCase):
    """验证 Windows 只读 Git pack 不会阻断模板打包。"""

    def test_remove_tree_deletes_readonly_git_pack(self) -> None:
        """只读 pack 索引必须能被清掉，避免 WinError 5 中断 Bootstrap。"""

        with tempfile.TemporaryDirectory() as directory:
            git_dir = Path(directory) / ".git" / "objects" / "pack"
            git_dir.mkdir(parents=True)
            idx = git_dir / "pack.idx"
            idx.write_bytes(b"idx")
            idx.chmod(stat.S_IREAD)
            remove_managed_path(Path(directory) / ".git")
            self.assertFalse((Path(directory) / ".git").exists())

    def test_write_archive_skips_nested_git_metadata(self) -> None:
        """残留 .git 不得进入 Bootstrap ZIP。"""

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            (source / "frontend").mkdir(parents=True)
            (source / "backend").mkdir()
            (source / "frontend/package.json").write_text("{}\n", encoding="utf-8")
            (source / "backend/pom.xml").write_text("<project/>\n", encoding="utf-8")
            pack = source / "frontend/.git/objects/pack"
            pack.mkdir(parents=True)
            idx = pack / "pack.idx"
            idx.write_bytes(b"idx")
            idx.chmod(stat.S_IREAD)
            archive = Path(directory) / "template.zip"
            _write_archive(
                archive,
                source,
                ("frontend", "backend"),
                TemplateStateV2.model_validate(
                    {
                        "schemaVersion": 2,
                        "templateRevision": "r1",
                        "requested": {},
                        "effective": {},
                        "appliedAdditions": {},
                    }
                ),
            )
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
            self.assertIn("frontend/package.json", names)
            self.assertTrue(all(".git" not in name.split("/") for name in names))


if __name__ == "__main__":
    unittest.main()
