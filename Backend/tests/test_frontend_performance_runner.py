from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.frontend_performance_runner import (
    PERFORMANCE_CHECK_ID,
    _lighthouserc_config,
    frontend_performance_available,
    run_frontend_performance_check,
)


class FrontendPerformanceRunnerTests(unittest.TestCase):
    def _workspace_state(self, workspace: str) -> dict:
        """构造带 frontend 启动脚本的最小工作区状态。"""

        frontend = Path(workspace) / "frontend"
        frontend.mkdir(parents=True)
        (frontend / "package.json").write_text(
            '{"scripts":{"dev":"vite --port 5173"}}',
            encoding="utf-8",
        )
        return {
            "workspace": workspace,
            "test_results": [
                {"id": "frontend_build", "passed": True},
            ],
        }

    def test_availability_requires_frontend_dev_or_start_script(self) -> None:
        """只有 frontend 工程存在且声明 dev/start 脚本时才可执行。"""

        with tempfile.TemporaryDirectory() as workspace:
            state = self._workspace_state(workspace)
            self.assertTrue(frontend_performance_available(state))
            Path(workspace, "frontend", "package.json").write_text(
                '{"scripts":{"build":"vite build"}}',
                encoding="utf-8",
            )
            self.assertFalse(frontend_performance_available(state))

    def test_lighthouserc_config_skips_full_page_screenshot(self) -> None:
        """整页截图不参与评分和指标，跳过它规避新版 Chrome 的协议超时。"""

        config = _lighthouserc_config("http://localhost:3000")
        settings = config["ci"]["collect"]["settings"]

        self.assertEqual(config["ci"]["collect"]["url"], ["http://localhost:3000"])
        self.assertEqual(settings["skipAudits"], ["full-page-screenshot"])

    def test_missing_frontend_returns_skipped_advisory(self) -> None:
        """没有可启动前端时跳过性能测试且不阻断门禁。"""

        with tempfile.TemporaryDirectory() as workspace:
            result = run_frontend_performance_check(
                {
                    "workspace": workspace,
                    "test_results": [{"id": "frontend_build", "passed": True}],
                }
            )
        check = result["test_results"][0]
        self.assertEqual(check["id"], PERFORMANCE_CHECK_ID)
        self.assertTrue(check["passed"])
        self.assertTrue(check["skipped"])
        self.assertFalse(check["blocking"])

    def test_launch_failure_returns_advisory_failure_without_stop(self) -> None:
        """前端启动失败时返回咨询性失败，且不会尝试停止未启动的服务。"""

        with tempfile.TemporaryDirectory() as workspace:
            state = self._workspace_state(workspace)
            with patch(
                "app.services.frontend_performance_runner.launch_frontend_project",
                return_value={
                    "status": "failed",
                    "message": "启动超时",
                    "preview_url": None,
                    "server": {},
                },
            ) as launch, patch(
                "app.services.frontend_performance_runner.stop_frontend_project"
            ) as stop:
                result = run_frontend_performance_check(state)
        check = result["test_results"][0]
        self.assertFalse(check["passed"])
        self.assertTrue(check["skipped"])
        self.assertFalse(check["blocking"])
        launch.assert_called_once()
        stop.assert_not_called()

    def test_success_parses_report_and_stops_started_server(self) -> None:
        """LHCI 成功后解析得分/指标并停止本次启动的前端服务。"""

        with tempfile.TemporaryDirectory() as workspace:
            state = self._workspace_state(workspace)
            launch_result = {
                "status": "running",
                "preview_url": "http://localhost:5173",
                "server": {"reused": False},
            }

            def fake_run(argv, **kwargs):
                """在 LHCI 输出目录写入可解析的报告。"""

                cwd = Path(kwargs["cwd"])
                cwd.mkdir(parents=True, exist_ok=True)
                (cwd / "frontend-performance.json").write_text(
                    json.dumps(
                        {
                            "categories": {
                                "performance": {"score": 0.92},
                                "accessibility": {"score": 0.95},
                                "best-practices": {"score": 1.0},
                                "seo": {"score": 0.88},
                            },
                            "audits": {
                                "first-contentful-paint": {"numericValue": 900},
                                "largest-contentful-paint": {"numericValue": 1800},
                                "total-blocking-time": {"numericValue": 120},
                                "cumulative-layout-shift": {"numericValue": 0.02},
                                "speed-index": {"numericValue": 1500},
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                (cwd / "frontend-performance.html").write_text(
                    "<html>report</html>",
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with patch(
                "app.services.frontend_performance_runner.launch_frontend_project",
                return_value=launch_result,
            ), patch(
                "app.services.frontend_performance_runner.subprocess.run",
                side_effect=fake_run,
            ), patch(
                "app.services.frontend_performance_runner.stop_frontend_project"
            ) as stop:
                result = run_frontend_performance_check(state)

        check = result["test_results"][0]
        self.assertTrue(check["passed"])
        self.assertFalse(check["blocking"])
        self.assertEqual(check["performance_scores"]["performance"], 92)
        self.assertEqual(check["performance_scores"]["best_practices"], 100)
        self.assertEqual(check["performance_metrics"]["lcp"], 1800)
        self.assertTrue(check["report_path"].endswith("frontend-performance.html"))
        stop.assert_called_once()

    def test_reused_server_is_not_stopped(self) -> None:
        """复用用户已有预览服务时，性能测试结束后不得停止它。"""

        with tempfile.TemporaryDirectory() as workspace:
            state = self._workspace_state(workspace)
            launch_result = {
                "status": "running",
                "preview_url": "http://localhost:5173",
                "server": {"reused": True},
            }

            def fake_run(argv, **kwargs):
                """写入最小报告文件。"""

                cwd = Path(kwargs["cwd"])
                cwd.mkdir(parents=True, exist_ok=True)
                (cwd / "frontend-performance.json").write_text(
                    json.dumps(
                        {
                            "categories": {"performance": {"score": 0.8}},
                            "audits": {},
                        }
                    ),
                    encoding="utf-8",
                )
                (cwd / "frontend-performance.html").write_text(
                    "<html>report</html>",
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with patch(
                "app.services.frontend_performance_runner.launch_frontend_project",
                return_value=launch_result,
            ), patch(
                "app.services.frontend_performance_runner.subprocess.run",
                side_effect=fake_run,
            ), patch(
                "app.services.frontend_performance_runner.stop_frontend_project"
            ) as stop:
                result = run_frontend_performance_check(state)

        self.assertTrue(result["test_results"][0]["passed"])
        stop.assert_not_called()

    def test_failed_run_clears_stale_report_artifacts(self) -> None:
        """LHCI 失败时不能把上一次的旧报告当作本轮结果。"""

        with tempfile.TemporaryDirectory() as workspace:
            state = self._workspace_state(workspace)
            runtime_dir = (
                Path(workspace)
                / ".xcodeagent"
                / "runtime"
                / "tests"
                / "frontend_performance"
            )
            runtime_dir.mkdir(parents=True)
            (runtime_dir / "frontend-performance.json").write_text(
                '{"categories": {"performance": {"score": 0.9}}}',
                encoding="utf-8",
            )
            (runtime_dir / "frontend-performance.html").write_text(
                "<html>stale</html>",
                encoding="utf-8",
            )
            launch_result = {
                "status": "running",
                "preview_url": "http://localhost:3000",
                "server": {"reused": False},
            }

            with patch(
                "app.services.frontend_performance_runner.launch_frontend_project",
                return_value=launch_result,
            ), patch(
                "app.services.frontend_performance_runner.subprocess.run",
                return_value=SimpleNamespace(returncode=1, stdout="", stderr="failed"),
            ), patch(
                "app.services.frontend_performance_runner.stop_frontend_project"
            ):
                result = run_frontend_performance_check(state)

        check = result["test_results"][0]
        self.assertFalse(check["passed"])
        self.assertIsNone(check.get("report_path"))
        self.assertNotIn("performance_scores", check)
        self.assertFalse((runtime_dir / "frontend-performance.json").exists())
        self.assertFalse((runtime_dir / "frontend-performance.html").exists())


if __name__ == "__main__":
    unittest.main()
