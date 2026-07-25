from __future__ import annotations

import unittest
from unittest.mock import patch

from app.workspace.workspace import (
    TerminalExecRequest,
    _classify_command,
    _command_argv,
)


class WorkspaceCommandPlatformTests(unittest.TestCase):
    def test_windows_command_preserves_backslashes_and_quoted_paths(self) -> None:
        """验证 Windows 兼容解析不会破坏盘符、反斜杠或带空格路径。"""

        request = TerminalExecRequest(
            workspace_root="C:\\workspace",
            command='"C:\\Program Files\\Python\\python.exe" "C:\\work dir\\run.py"',
        )
        with patch("app.workspace.workspace.os.name", "nt"):
            argv = _command_argv(request)

        self.assertEqual(
            argv,
            [
                "C:\\Program Files\\Python\\python.exe",
                "C:\\work dir\\run.py",
            ],
        )

    def test_windows_command_extensions_cannot_bypass_risk_classification(self) -> None:
        """验证 Windows 可执行扩展名和大小写不会绕过命令风险等级。"""

        with patch("app.workspace.workspace.os.name", "nt"):
            package_risk = _classify_command(["PNPM.CMD", "INSTALL"])
            shell_risk = _classify_command(["PowerShell.EXE", "-Command", "Remove-Item file"])

        self.assertEqual(package_risk["level"], "medium")
        self.assertEqual(shell_risk["level"], "high")


if __name__ == "__main__":
    unittest.main()
