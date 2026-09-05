from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.services.template_state import TEMPLATE_STATE_RELATIVE_PATH
from app.services.workspace_bootstrap.materializer import WorkspaceMaterializer


class _FailingGitManager:
    """模拟 Git 初始化后 baseline commit 失败的受控依赖。"""

    def initialize_baseline(self, workspace: str | Path) -> str:
        """留下 `.git` 后失败，用于验证 Journal 的受管回滚。"""

        Path(workspace, ".git").mkdir()
        raise OSError("git commit failed")


def _template_state() -> dict[str, object]:
    """返回满足当前 Engine 冻结四字段契约的最小 State。"""

    return {
        "templateRevision": "template-r1",
        "managedFiles": {},
        "requested": {},
        "effective": {},
    }


def _write_package(path: Path) -> None:
    """写入已经通过上游 Package 校验的最小 frontend/backend ZIP。"""

    with zipfile.ZipFile(path, "w") as package:
        package.writestr("frontend/package.json", "{}\n")
        package.writestr("backend/pom.xml", "<project />\n")
        package.writestr(str(TEMPLATE_STATE_RELATIVE_PATH), json.dumps(_template_state()))


class WorkspaceMaterializerTests(unittest.TestCase):
    """验证首次 Workspace 物化仅留下完整可用产物或零受管产物。"""

    def test_materialize_creates_independent_git_baseline_and_excludes_state(self) -> None:
        """成功提交后必须存在两个根、唯一 State 与不包含 State 的 Git baseline。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            archive = workspace / "template.zip"
            _write_package(archive)

            WorkspaceMaterializer().materialize(
                workspace=workspace,
                archive_path=archive,
                template_state=_template_state(),
            )

            self.assertTrue((workspace / "frontend/package.json").is_file())
            self.assertTrue((workspace / "backend/pom.xml").is_file())
            self.assertTrue((workspace / ".git").is_dir())
            self.assertTrue((workspace / TEMPLATE_STATE_RELATIVE_PATH).is_file())
            self.assertFalse((workspace / ".xcodeagent/bootstrap-staging").exists())
            exclude = (workspace / ".git/info/exclude").read_text(encoding="utf-8")
            self.assertIn(".xcodeagent/", exclude)

    def test_second_root_move_failure_rolls_back_all_managed_outputs(self) -> None:
        """第二个 root 的移动失败时不得留下第一个 root、Git 或 TemplateState。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            archive = workspace / "template.zip"
            _write_package(archive)
            original_replace = __import__("os").replace
            calls = 0

            def fail_second_root(source: str | bytes | Path, target: str | bytes | Path) -> None:
                """仅在 backend root move 注入失败，其余原子写入保持原行为。"""

                nonlocal calls
                if Path(target).name in {"frontend", "backend"}:
                    calls += 1
                    if calls == 2:
                        raise OSError("backend move failed")
                original_replace(source, target)

            with patch("app.services.workspace_bootstrap.materializer.os.replace", side_effect=fail_second_root):
                with self.assertRaisesRegex(OSError, "backend move failed"):
                    WorkspaceMaterializer().materialize(
                        workspace=workspace,
                        archive_path=archive,
                        template_state=_template_state(),
                    )

            for relative in ("frontend", "backend", ".git", TEMPLATE_STATE_RELATIVE_PATH):
                self.assertFalse((workspace / relative).exists(), relative)

    def test_template_state_write_failure_rolls_back_git_and_roots(self) -> None:
        """State 原子写入失败时已有 Git baseline 与两个 roots 必须一并回滚。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            archive = workspace / "template.zip"
            _write_package(archive)
            with patch(
                "app.services.workspace_bootstrap.materializer._write_template_state",
                side_effect=OSError("state write failed"),
            ):
                with self.assertRaisesRegex(OSError, "state write failed"):
                    WorkspaceMaterializer().materialize(
                        workspace=workspace,
                        archive_path=archive,
                        template_state=_template_state(),
                    )

            for relative in ("frontend", "backend", ".git", TEMPLATE_STATE_RELATIVE_PATH):
                self.assertFalse((workspace / relative).exists(), relative)

    def test_git_commit_or_readiness_failure_rolls_back_all_managed_outputs(self) -> None:
        """Git baseline 或 readiness 失败均不得遗留半成品 Workspace。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            archive = workspace / "template.zip"
            _write_package(archive)
            with self.assertRaisesRegex(OSError, "git commit failed"):
                WorkspaceMaterializer(git_manager=_FailingGitManager()).materialize(
                    workspace=workspace,
                    archive_path=archive,
                    template_state=_template_state(),
                )
            for relative in ("frontend", "backend", ".git", TEMPLATE_STATE_RELATIVE_PATH):
                self.assertFalse((workspace / relative).exists(), relative)

            def fail_readiness(_workspace: Path) -> None:
                """注入最终 readiness 失败，模拟 Commit Section 最后一步失败。"""

                raise RuntimeError("readiness failed")

            with self.assertRaisesRegex(RuntimeError, "readiness failed"):
                WorkspaceMaterializer().materialize(
                    workspace=workspace,
                    archive_path=archive,
                    template_state=_template_state(),
                    readiness=fail_readiness,
                )
            for relative in ("frontend", "backend", ".git", TEMPLATE_STATE_RELATIVE_PATH):
                self.assertFalse((workspace / relative).exists(), relative)

    def test_preflight_rejects_existing_managed_root(self) -> None:
        """已有 frontend 等受管目标时禁止覆盖或开始写入。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "frontend").mkdir()
            archive = workspace / "template.zip"
            _write_package(archive)

            with self.assertRaisesRegex(Exception, "受管产物"):
                WorkspaceMaterializer().materialize(
                    workspace=workspace,
                    archive_path=archive,
                    template_state=_template_state(),
                )
