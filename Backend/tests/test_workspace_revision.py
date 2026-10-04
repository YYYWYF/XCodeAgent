"""工作区 revision 只反映用户文件变化。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from app.services.workspace_inspector import workspace_inventory


class WorkspaceRevisionTests(unittest.TestCase):
    """覆盖已跟踪和未跟踪的平台状态对 revision 的影响。"""

    def test_platform_state_writes_do_not_change_workspace_revision(self) -> None:
        """同一 Workflow 写入平台产物后仍可验证原工作区入口。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            platform = root / ".devagentstudio"
            platform.mkdir()
            (platform / "application.json").write_text("{}", encoding="utf-8")
            source = root / "app.py"
            source.write_text("value = 1\n", encoding="utf-8")
            for command in (
                ["git", "init", "-q"],
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "add", "."],
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
            ):
                subprocess.run(command, cwd=root, check=True, capture_output=True)

            baseline = workspace_inventory(root)[1]
            (platform / "application.json").write_text('{"status":"running"}', encoding="utf-8")
            (platform / "recovery.json").write_text("{}", encoding="utf-8")
            self.assertEqual(workspace_inventory(root)[1], baseline)

            source.write_text("value = 2\n", encoding="utf-8")
            self.assertNotEqual(workspace_inventory(root)[1], baseline)
