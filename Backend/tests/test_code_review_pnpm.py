from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.tools import ToolException

from app.tools.code_review_pnpm import (
    create_code_review_pnpm_install_tool,
    read_pnpm_install_evidence,
)


class CodeReviewPnpmToolTests(unittest.TestCase):
    """验证代码审查专用 pnpm 工具的固定命令和工作区边界。"""

    def test_tool_runs_fixed_command_without_shell_and_writes_evidence(self) -> None:
        """工具只能在 frontend 执行固定 pnpm install 并持久化日志证据。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text("{}", encoding="utf-8")
            tool = create_code_review_pnpm_install_tool(workspace)

            def successful_install(*_args, **_kwargs) -> subprocess.CompletedProcess[str]:
                """模拟 pnpm 成功生成 frontend/pnpm-lock.yaml。"""

                (frontend / "pnpm-lock.yaml").write_text(
                    "lockfileVersion: '9.0'\n",
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(
                    args=["/usr/local/bin/pnpm", "install"],
                    returncode=0,
                    stdout="installed",
                    stderr="",
                )

            with patch(
                "app.tools.code_review_pnpm.shutil.which",
                return_value="/usr/local/bin/pnpm",
            ), patch(
                "app.tools.code_review_pnpm.subprocess.run",
                side_effect=successful_install,
            ) as run:
                result = json.loads(tool.invoke({}))

            self.assertEqual(tool.args, {})
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["command"], ["pnpm", "install"])
            run.assert_called_once()
            self.assertEqual(
                run.call_args.args[0],
                ["/usr/local/bin/pnpm", "install"],
            )
            self.assertEqual(run.call_args.kwargs["cwd"], frontend.resolve())
            self.assertFalse(run.call_args.kwargs["shell"])
            evidence = read_pnpm_install_evidence(workspace)
            self.assertEqual(evidence["execution_id"], result["execution_id"])
            self.assertTrue((Path(workspace) / result["stdout_log"]).is_file())

    def test_tool_rejects_symlinked_frontend(self) -> None:
        """frontend 符号链接不能把固定命令工作目录带出用户工作区。"""

        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            outside_frontend = Path(outside)
            (outside_frontend / "package.json").write_text("{}", encoding="utf-8")
            (Path(workspace) / "frontend").symlink_to(outside_frontend, target_is_directory=True)
            tool = create_code_review_pnpm_install_tool(workspace)

            with self.assertRaises(ToolException):
                tool.invoke({})

    def test_tool_rejects_symlinked_runtime_log_directory(self) -> None:
        """运行日志目录符号链接不能把安装证据写到工作区之外。"""

        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            root = Path(workspace)
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text("{}", encoding="utf-8")
            (root / ".xcodeagent").symlink_to(Path(outside), target_is_directory=True)
            tool = create_code_review_pnpm_install_tool(workspace)

            with patch(
                "app.tools.code_review_pnpm.shutil.which",
                return_value="/usr/local/bin/pnpm",
            ), patch("app.tools.code_review_pnpm.subprocess.run") as run:
                with self.assertRaises(ToolException):
                    tool.invoke({})

            run.assert_not_called()

    def test_tool_rejects_symlinked_lockfile_before_execution(self) -> None:
        """已有 lockfile 符号链接不能让 pnpm 把写入带到 frontend 之外。"""

        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            frontend = Path(workspace) / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text("{}", encoding="utf-8")
            outside_lockfile = Path(outside) / "pnpm-lock.yaml"
            outside_lockfile.write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
            (frontend / "pnpm-lock.yaml").symlink_to(outside_lockfile)
            tool = create_code_review_pnpm_install_tool(workspace)

            with patch("app.tools.code_review_pnpm.subprocess.run") as run:
                with self.assertRaises(ToolException):
                    tool.invoke({})

            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
