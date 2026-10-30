"""页面验收自行准备依赖，不执行真实安装、Runtime、Electron 或模型请求。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from unittest import TestCase
from unittest.mock import Mock, patch

from app.services.agent_runtime_launcher import check_runtime_target
from app.services.runtime_acceptance_frontend import prepare_acceptance_dependencies


class RuntimeAcceptancePreparationTests(TestCase):
    """验证跳过单测后的依赖同步、失败短路及准备和调用顺序。"""

    def test_existing_directory_still_syncs_current_dependencies(self):
        """已有依赖目录也要同步，不能漏掉本轮代码新增的包。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "frontend"
            (source / "node_modules").mkdir(parents=True)
            (source / "package.json").write_text("{}")
            (source / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'")
            with patch("app.services.runtime_acceptance_frontend.shutil.which", return_value="/tools/pnpm"), \
                    patch("app.services.runtime_acceptance_frontend._run_install", return_value={
                        "returncode": 0, "timed_out": False}) as install:
                prepare_acceptance_dependencies(root, root / "logs", timeout_seconds=25)
            install.assert_called_once()
            self.assertEqual(install.call_args.kwargs["cwd"], source)
            self.assertEqual(install.call_args.kwargs["timeout_seconds"], 25)

    def test_install_failure_starts_no_runtime_or_desktop_request(self):
        """安装失败立即结束，保留日志位置且不消费本轮模型调用预算。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "frontend").mkdir()
            (root / "frontend/package.json").write_text("{}")
            dispatch = Mock()
            with patch("app.services.runtime_acceptance_frontend.shutil.which", return_value="/tools/pnpm"), \
                    patch("app.services.runtime_acceptance_frontend._run_install", return_value={
                        "returncode": 1, "timed_out": False}) as install, \
                    patch("app.services.agent_runtime_launcher._launch_runtime_locked") as launch, \
                    patch("app.services.runtime_acceptance_bridge.await_desktop_acceptance") as desktop:
                with self.assertRaisesRegex(ValueError, "退出码 1.*日志：logs/attempts/"):
                    check_runtime_target(root, root / "logs", case={"mode": "page", "agentId": "assistant", "modelRequestLimit": 1},
                        specification={"scope": {"targetId": "chat"}}, deadline=monotonic() + 300,
                        before_dispatch=dispatch)
            install.assert_called_once()
            launch.assert_not_called()
            desktop.assert_not_called()
            dispatch.assert_not_called()

    def test_page_prepares_before_runtime_and_reuses_dependencies(self):
        """前端安装和复制完成后才启动 Runtime，桌面只签发一次并清理临时前端。"""
        order = []

        def dependencies(*args, **kwargs):
            """记录安装准备阶段，不调用包管理器。"""
            order.append("dependencies")

        def view(root, attempt, page_id, identity):
            """核对独立尝试目录，避免手动重试覆盖旧视图。"""
            order.append("view")
            self.assertEqual(attempt.parent, root / "logs/attempts")
            return attempt / "frontend"

        def launch(*args, **kwargs):
            """执行真实 probe 编排，但不启动 Runtime 进程。"""
            order.append("runtime")
            self.assertFalse(kwargs["skip_install"])
            return {"status": "completed", "execution": {"cleanup_succeeded": True},
                    "targetEvidence": kwargs["probe_action"]("http://127.0.0.1:32000")}

        def desktop(job, remaining):
            """交回匹配本轮身份的最小证据，不发送模型请求。"""
            order.append("desktop")
            return {"passed": True, "lifecycle": True, "pageObserved": True, "textBytes": 1,
                    **{key: job[key] for key in ("agentId", "threadId", "runId")}}

        with TemporaryDirectory() as directory, \
                patch("app.services.runtime_acceptance_frontend.prepare_acceptance_dependencies", side_effect=dependencies), \
                patch("app.services.runtime_acceptance_frontend.prepare_acceptance_frontend", side_effect=view), \
                patch("app.services.agent_runtime_launcher._launch_runtime_locked", side_effect=launch), \
                patch("app.services.frontend_project_launcher.launch_frontend_project", return_value={
                    "status": "running", "preview_url": "http://127.0.0.1:32001"}) as frontend, \
                patch("app.services.frontend_project_launcher.stop_frontend_project", return_value={"status": "stopped"}) as stop, \
                patch("app.services.runtime_acceptance_bridge.await_desktop_acceptance", side_effect=desktop) as dispatch:
            root = Path(directory)
            result = check_runtime_target(root, root / "logs", case={"mode": "page", "agentId": "assistant", "modelRequestLimit": 1},
                specification={"scope": {"targetId": "chat"}}, deadline=monotonic() + 300,
                before_dispatch=lambda: order.append("consume"))
        self.assertTrue(result["passed"])
        self.assertEqual(order, ["dependencies", "view", "runtime", "consume", "desktop"])
        self.assertTrue(frontend.call_args.kwargs["skip_install"])
        self.assertTrue(frontend.call_args.kwargs["linked_dependencies"])
        dispatch.assert_called_once()
        stop.assert_called_once()
