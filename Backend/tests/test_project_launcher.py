from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.graph.nodes.lifecycle import launch_project
from app.services.project_launcher import launch_frontend_project


class ProjectLauncherTests(unittest.TestCase):
    def test_launch_frontend_project_reads_frontend_package_and_starts_dev_server(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "Frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"dev":"vite --host 127.0.0.1"}}',
                encoding="utf-8",
            )
            (frontend / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'", encoding="utf-8")

            fake_process = SimpleNamespace(pid=12345)
            with (
                patch("app.services.project_launcher.shutil.which", return_value="/usr/bin/pnpm"),
                patch(
                    "app.services.project_launcher.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout="installed", stderr=""),
                ) as run,
                patch(
                    "app.services.project_launcher.subprocess.Popen",
                    return_value=fake_process,
                ) as popen,
                patch("app.services.project_launcher._wait_until_ready", return_value=True),
            ):
                result = launch_frontend_project({"workspace": workspace})

        self.assertEqual(result["status"], "running")
        self.assertEqual(result["preview_url"], "http://127.0.0.1:5173")
        self.assertTrue(result["package_json_path"].endswith("Frontend/package.json"))
        self.assertEqual(result["package_manager"], "pnpm")
        self.assertEqual(result["script"], "dev")
        self.assertEqual(result["server"]["pid"], 12345)
        self.assertEqual(run.call_args.args[0], ["pnpm", "install"])
        self.assertEqual(popen.call_args.args[0], ["pnpm", "run", "dev"])

    def test_launch_project_returns_acceptance_request_after_successful_launch(self) -> None:
        launch_result = {
            "status": "running",
            "message": "ok",
            "preview_url": "http://127.0.0.1:5173",
            "package_json_path": "/workspace/Frontend/package.json",
            "server": {"pid": 123},
        }
        with patch(
            "app.graph.nodes.lifecycle.launch_frontend_project",
            return_value=launch_result,
        ):
            result = launch_project({"workspace": "/workspace"})

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["preview_url"], "http://127.0.0.1:5173")
        self.assertEqual(result["acceptance_request"]["preview_url"], "http://127.0.0.1:5173")
        self.assertEqual(result["launch_result"], launch_result)

    def test_launch_project_reports_startup_failure(self) -> None:
        with patch(
            "app.graph.nodes.lifecycle.launch_frontend_project",
            return_value={"status": "failed", "message": "未找到前端 package.json。"},
        ):
            result = launch_project({"workspace": "/workspace"})

        self.assertEqual(result["status"], "failed")
        self.assertIn("项目启动失败", result["acceptance_request"]["message"])


if __name__ == "__main__":
    unittest.main()
