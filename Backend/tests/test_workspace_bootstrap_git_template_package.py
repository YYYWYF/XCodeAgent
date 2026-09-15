from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.workspace_bootstrap.git_template_package import (
    GitTemplatePackageBuilder,
)
from app.services.workspace_bootstrap.models import ArchiveLimits
from app.services.workspace_bootstrap.template_package import validate_template_package


class GitTemplatePackageBuilderTests(unittest.TestCase):
    """验证公开 Git 模式复用统一三端 Package 契约。"""

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


if __name__ == "__main__":
    unittest.main()
