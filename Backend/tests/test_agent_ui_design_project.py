"""验证设计生产者的路径、身份与当前文件缓存边界。"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.services.agent_ui_design_project import design_project_root, tree_digest, prepare_agent_ui_design_project
from app.services.agent_ui_tool_host import AgentUiToolHostInspection


class AgentUiDesignProjectTests(unittest.TestCase):
    """独立设计工程必须留在指定工作区的设计资产中。"""

    def test_native_draft_path_and_traversal(self) -> None:
        """中文空格路径可用，外部目录和符号链接不能取得写入权限。"""
        with TemporaryDirectory(prefix="Agent UI 设计 ") as directory:
            workspace = Path(directory)
            draft = workspace / ".devagentstudio/drafts/current"
            self.assertEqual(design_project_root(workspace, draft), draft / "agent-ui-project")
            with self.assertRaises(ValueError):
                design_project_root(workspace, workspace / "other")
            outside = workspace / "outside"
            outside.mkdir()
            (workspace / ".devagentstudio").mkdir()
            (workspace / ".devagentstudio/linked").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                design_project_root(workspace, workspace / ".devagentstudio/linked")

    def test_digest_tracks_content_and_refuses_symlinks(self) -> None:
        """摘要覆盖实际源码，不能跟随链接把工程外文件纳入缓存。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            source = root / "src/App.tsx"
            source.write_text("first", encoding="utf-8")
            first = tree_digest(root / "src")
            source.write_text("second", encoding="utf-8")
            self.assertNotEqual(first, tree_digest(root / "src"))
            (root / "src/linked.ts").symlink_to(source)
            with self.assertRaises(ValueError):
                tree_digest(root / "src")

    def test_existing_incomplete_project_preserves_user_source(self) -> None:
        """安装失败或人工留下的工程不自动重建，不覆盖既有应用层。"""
        with TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            root = design_project_root(workspace, workspace / ".devagentstudio/ui-design")
            source = root / "tools/agent-ui-consumer/src/App.tsx"
            source.parent.mkdir(parents=True)
            source.write_text("user customization", encoding="utf-8")
            cli = workspace / "pnpm.cjs"
            cli.write_text("", encoding="utf-8")
            inspection = AgentUiToolHostInspection(workspace, workspace,
                                                   workspace / "node", "22.16.0", "0.0.1", "^22.0.0", "identity")
            with patch("app.services.agent_ui_design_project.inspect_agent_ui_tool_host", return_value=inspection):
                with self.assertRaisesRegex(ValueError, "保留文件"):
                    prepare_agent_ui_design_project(inspection, draft_root=root.parent, pnpm_cli=cli)
            self.assertEqual(source.read_text(encoding="utf-8"), "user customization")

    def test_tampered_package_is_rejected_before_draft_creation(self) -> None:
        """成品包 bytes/hash 不符时停止，不写入设计草稿或执行安装。"""
        with TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            vendor = workspace / "vendor/agent-ui"
            vendor.mkdir(parents=True)
            (vendor / "artifact.tgz").write_bytes(b"tampered")
            (vendor / "manifest.json").write_text(json.dumps({"packages": [{
                "file": "artifact.tgz", "bytes": 8, "sha256": hashlib.sha256(b"original").hexdigest(),
            }]}), encoding="utf-8")
            cli = workspace / "pnpm.cjs"
            cli.touch()
            inspection = AgentUiToolHostInspection(workspace, workspace,
                                                   workspace / "node", "22.16.0", "0.0.1", "^22.0.0", "identity")
            draft = workspace / ".devagentstudio/ui-design"
            with patch("app.services.agent_ui_design_project.REPOSITORY", workspace), \
                    patch("app.services.agent_ui_design_project.inspect_agent_ui_tool_host", return_value=inspection):
                with self.assertRaisesRegex(ValueError, "摘要不匹配"):
                    prepare_agent_ui_design_project(inspection, draft_root=draft, pnpm_cli=cli)
            self.assertFalse(draft.exists())


if __name__ == "__main__":
    unittest.main()
