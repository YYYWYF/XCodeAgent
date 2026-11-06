"""使用真实 Node/HTTP 验证隔离预览的权限和生命周期。"""

from __future__ import annotations

import asyncio
import http.client
import json
import os
import shutil
import socket
import unittest
from pathlib import Path
from dataclasses import replace
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from unittest.mock import AsyncMock, patch

from app.services.agent_ui_design_preview import AgentUiDesignPreviews
from app.services.agent_ui_design_mock import AgentUiDesignMock
from app.services.agent_ui_design_project import AgentUiDesignProject, source_digest, tree_digest
from app.services.workspace_process_registry import workspace_process_registry


@unittest.skipUnless(shutil.which("node"), "需要实际 Node 执行静态服务器")
class AgentUiDesignPreviewTests(unittest.TestCase):
    """只服务当前构建，不允许 URL 变成工作区文件读取器。"""

    def setUp(self) -> None:
        """创建包含中文/空格的最小构建和独立预览 owner。"""
        self.temp = TemporaryDirectory(prefix="Agent UI 预览 ")
        self.workspace = Path(self.temp.name).resolve()
        root = self.workspace / ".devagentstudio/drafts/current/agent-ui-project"
        project = root / "tools/agent-ui-consumer"
        (project / "src").mkdir(parents=True)
        (project / "src/App.tsx").write_text("export {}", encoding="utf-8")
        for name in ("package.json", "pnpm-lock.yaml", ".pnpmfile.cjs", "tsconfig.json",
                     "eslint.config.mjs", "vite.config.ts", "index.html"):
            (project / name).write_text("{}", encoding="utf-8")
        (project / "dist/assets").mkdir(parents=True)
        (project / "dist/index.html").write_text('<script src="./assets/app.js"></script>', encoding="utf-8")
        (project / "dist/assets/app.js").write_text("console.log('preview')", encoding="utf-8")
        self.project = AgentUiDesignProject(self.workspace, root, project, "packages",
                                            source_digest(project), tree_digest(project / "dist"))
        self.previews = AgentUiDesignPreviews()
        self.node = Path(shutil.which("node") or "")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.previews.close_all)

    def request(self, url: str, path: str | None = None, **headers: str) -> tuple[int, dict[str, str], bytes]:
        """直接请求原生路径，使路径穿越测试不被 HTTP 客户端预先归一化。"""
        parsed = urlsplit(url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        try:
            connection.request("GET", path or parsed.path, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_http_scope_headers_and_immutable_snapshot(self) -> None:
        """URL/Host/来源必须匹配，静态服务不跟随源码、链接或启动后的磁盘变化。"""
        launch = self.previews.start(self.project, node_executable=self.node)
        status, headers, body = self.request(launch.url)
        self.assertEqual(status, 200)
        self.assertIn(b"./assets/app.js", body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        policy = headers["Content-Security-Policy"]
        sandbox = next(part.strip().split()[1:] for part in policy.split(";")
                       if part.strip().startswith("sandbox "))
        self.assertEqual(set(sandbox), {"allow-scripts", "allow-same-origin", "allow-forms"})
        self.assertIn("form-action 'none'", policy)
        self.assertIn("connect-src 'self'", policy)
        for path in ("/", "/package.json", urlsplit(launch.url).path + "../../package.json",
                     urlsplit(launch.url).path + "%2e%2e/package.json", urlsplit(launch.url).path + ".env"):
            self.assertEqual(self.request(launch.url, path)[0], 404)
        self.assertEqual(self.request(launch.url, Host="malicious.example")[0], 403)
        self.assertEqual(self.request(launch.url, Origin="https://malicious.example")[0], 403)
        asset = urlsplit(launch.url).path + "assets/app.js"
        original = self.request(launch.url, asset)[2]
        (self.project.project_dir / "dist/assets/app.js").write_text("changed", encoding="utf-8")
        self.assertEqual(self.request(launch.url, asset)[2], original)

    def test_restart_stale_build_cancel_and_close(self) -> None:
        """相同构建复用 launch，源码变化阻止启动，停止与关闭都回收实际进程。"""
        first = self.previews.start(self.project, node_executable=self.node)
        second = self.previews.start(self.project, node_executable=self.node)
        self.assertEqual(first.launch_id, second.launch_id)
        self.assertEqual(workspace_process_registry.active_process_ids(self.workspace), [second.process_id])
        self.previews.stop(self.workspace)
        self.assertEqual(workspace_process_registry.active_process_ids(self.workspace), [])
        (self.project.project_dir / "src/App.tsx").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.previews.start(self.project, node_executable=self.node)

    def test_failed_replacement_preserves_previous_preview(self) -> None:
        """新构建服务启动失败时，旧预览及其真实进程继续可用。"""
        first = self.previews.start(self.project, node_executable=self.node)
        (self.project.project_dir / "dist/private.map").write_text("invalid asset", encoding="utf-8")
        changed = replace(self.project, build_digest=tree_digest(self.project.project_dir / "dist"))
        with self.assertRaises(ValueError):
            self.previews.start(changed, node_executable=self.node)
        self.assertEqual(self.previews.status(self.workspace), first)
        self.assertEqual(self.request(first.url)[0], 200)
        self.assertEqual(workspace_process_registry.active_process_ids(self.workspace), [first.process_id])

    def test_cancel_at_preview_handoff_preserves_previous_launch(self) -> None:
        """设计调整在新服务就绪后取消时，不能先关闭旧服务或留下孤立新进程。"""
        first = self.previews.start(self.project, node_executable=self.node)
        (self.project.project_dir / "dist/assets/app.js").write_text("updated", encoding="utf-8")
        changed = replace(self.project, build_digest=tree_digest(self.project.project_dir / "dist"))
        checks = 0

        def cancel_at_handoff() -> None:
            """第一次启动检查放行，交接前模拟用户取消。"""
            nonlocal checks
            checks += 1
            if checks == 2:
                raise ValueError("cancelled")

        with self.assertRaises(ValueError):
            self.previews.start(changed, node_executable=self.node, check_cancel=cancel_at_handoff)
        self.assertEqual(checks, 2)
        self.assertEqual(self.previews.status(self.workspace), first)
        self.assertEqual(self.request(first.url)[0], 200)
        self.assertEqual(workspace_process_registry.active_process_ids(self.workspace), [first.process_id])

    def test_replacement_waits_for_new_service_and_fences_old_stop(self) -> None:
        """新服务先就绪再清理旧进程，旧 launch 的迟到取消不能关闭新预览。"""
        first = self.previews.start(self.project, node_executable=self.node)
        (self.project.project_dir / "dist/assets/app.js").write_text("updated build", encoding="utf-8")
        changed = replace(self.project, build_digest=tree_digest(self.project.project_dir / "dist"))
        previous = self.previews._previews[self.workspace]
        original = previous.context

        class CheckedCleanup:
            """回收旧进程时，验证新进程已登记且旧 HTTP 快照尚能读取。"""

            def __exit__(inner_self, *arguments: object) -> None:
                """保持原上下文清理，额外观察两个进程的交接时序。"""
                self.assertEqual(len(workspace_process_registry.active_process_ids(self.workspace)), 2)
                self.assertEqual(self.request(first.url)[0], 200)
                original.__exit__(*arguments)

        previous.context = CheckedCleanup()
        second = self.previews.start(changed, node_executable=self.node)
        self.assertNotEqual(first.launch_id, second.launch_id)
        self.previews.stop(self.workspace, launch_id=first.launch_id)
        self.assertEqual(self.previews.status(self.workspace), second)
        self.assertEqual(workspace_process_registry.active_process_ids(self.workspace), [second.process_id])
        self.previews.close_all()
        with self.assertRaises(ValueError):
            self.previews.start(self.project, node_executable=self.node)

    def test_occupied_port_and_workspace_deletion(self) -> None:
        """端口占用不能附着到其他服务，工作区删除栅栏终止并阻止迟到启动。"""
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with self.assertRaises(ValueError):
                self.previews.start(self.project, node_executable=self.node, port=occupied.getsockname()[1])
        self.assertEqual(workspace_process_registry.active_process_ids(self.workspace), [])
        self.previews.start(self.project, node_executable=self.node)
        try:
            result = workspace_process_registry.cancel_workspace(self.workspace)
            self.assertEqual(result["remainingProcessIds"], [])
            self.previews.stop(self.workspace)
            with self.assertRaises(RuntimeError):
                self.previews.start(self.project, node_executable=self.node)
        finally:
            workspace_process_registry.end_workspace_deletion(self.workspace)

    def test_run_cancellation_reaps_current_launch(self) -> None:
        """取消只结束所属 run 的预览，查询不会返回已死进程的旧 URL。"""
        run_id = "agent-ui-design-cancellation-fixture"
        workspace_process_registry.allow_run(run_id)
        self.previews.start(self.project, node_executable=self.node, run_id=run_id)
        workspace_process_registry.cancel_run(run_id)
        self.assertEqual(workspace_process_registry.wait_run_processes(run_id), [])
        self.assertIsNone(self.previews.status(self.workspace))
        self.assertEqual(workspace_process_registry.active_process_ids(self.workspace), [])

    def test_workspace_preview_stop_uses_current_owner(self) -> None:
        """原工作区预览停机入口必须回收新设计进程，而不是只停旧 Vite。"""
        from app.services.project_launcher import stop_project_preview

        self.previews.start(self.project, node_executable=self.node)
        stopped = {"status": "stopped"}
        with patch("app.services.agent_ui_design_preview.agent_ui_design_previews", self.previews), \
                patch("app.services.project_launcher.stop_frontend_project", return_value=stopped), \
                patch("app.services.project_launcher.stop_workspace_backend_project", return_value=stopped), \
                patch("app.services.project_launcher.stop_runtime", return_value=stopped), \
                patch("app.services.preview_runtime_state.finish_attempt"):
            result = stop_project_preview(self.workspace)
        self.assertEqual(result["agent_ui_design"]["status"], "stopped")
        self.assertIsNone(self.previews.status(self.workspace))
        self.assertEqual(workspace_process_registry.active_process_ids(self.workspace), [])

    def test_invalid_static_files_fail_without_leaking_process(self) -> None:
        """源码映射不属于允许服务的构建类型，启动失败也必须回收句柄。"""
        (self.project.project_dir / "dist/private.map").write_text("source content", encoding="utf-8")
        project = AgentUiDesignProject(self.project.workspace, self.project.root, self.project.project_dir,
                                       self.project.identity, self.project.source_digest,
                                       tree_digest(self.project.project_dir / "dist"))
        with self.assertRaises(ValueError):
            self.previews.start(project, node_executable=self.node)
        self.assertEqual(workspace_process_registry.active_process_ids(self.workspace), [])

    def mock_options(self, **changes: str) -> AgentUiDesignMock:
        """用明确提供的合规 Node 和真实公开包创建开发侧 Mock 选项。"""
        from app.services.agent_ui_tool_host import inspect_agent_ui_tool_host

        self.node = Path(os.environ["AGENT_UI_TEST_NODE"])
        inspection = inspect_agent_ui_tool_host(workspace=self.workspace,
            installation_root=Path(__file__).resolve().parents[2] / "tools/agent-ui",
            node_executable=self.node, creator_version="0.0.1")
        record = {"status": "built", "revision": "a" * 64,
                  "sourceDigest": self.project.source_digest, "buildDigest": self.project.build_digest,
                  "pages": [{"pageId": "chat"}], "confirmation_status": "pending_user_confirmation"}
        (self.project.root / "generation.json").write_text(json.dumps(record), encoding="utf-8")
        return AgentUiDesignMock(inspection=inspection, **{"page_id": "chat", "revision": "a" * 64, **changes})

    @unittest.skipUnless(os.environ.get("AGENT_UI_TEST_NODE"), "需显式提供合规 AGENT_UI_TEST_NODE")
    def test_official_mock_is_launch_scoped_and_static_mode_has_no_agent(self) -> None:
        """同源 Mock 绑定本轮页面/修订；普通静态预览不能访问测试服务。"""
        mock = self.mock_options()
        launch = self.previews.start(self.project, node_executable=self.node, mock=mock)
        self.assertEqual(launch.mock_evidence["binding"]["revision"], mock.revision)
        self.assertEqual(launch.mock_evidence["source"], "official-mock")
        body = self.request(launch.url)[2]
        self.assertIn(b'agent-ui-design-preview', body)
        self.assertIn(launch.launch_id.encode(), body)
        parsed = urlsplit(launch.url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            data = json.dumps({"threadId": "thread", "runId": "run", "state": {},
                               "messages": [], "tools": [], "context": [], "forwardedProps": {}})
            connection.request("POST", parsed.path + "agent", body=data,
                               headers={"Content-Type": "application/json", "Origin": f"http://{parsed.netloc}"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(b'RUN_STARTED', response.read())
        finally:
            connection.close()
        for path in (parsed.path + "agent?scenario=tool-error", "/agent", parsed.path + "mock-scenarios"):
            self.assertEqual(self.request(launch.url, path)[0], 404)
        self.assertEqual(self.request(launch.url, parsed.path + "agent", Origin="https://invalid.example")[0], 403)
        second = self.previews.start(self.project, node_executable=self.node)
        self.assertIsNone(second.mock_evidence)
        self.assertNotIn(b'agent-ui-design-preview', self.request(second.url)[2])
        self.assertEqual(self.request(second.url, urlsplit(second.url).path + "agent")[0], 404)
        self.assertEqual(json.loads((self.project.root / "generation.json").read_text())["confirmation_status"],
                         "pending_user_confirmation")

    @unittest.skipUnless(os.environ.get("AGENT_UI_TEST_NODE"), "需显式提供合规 AGENT_UI_TEST_NODE")
    def test_mock_rejects_wrong_page_revision_and_unknown_scenario(self) -> None:
        """无效启动保留当前有效预览；新工具失败必须回收进程，不回退真实服务。"""
        for changes in ({"page_id": "unknown"}, {"revision": "b" * 64}, {"scenario_id": "invented"}):
            mock = self.mock_options(**changes)
            with self.assertRaises(ValueError):
                self.previews.start(self.project, node_executable=self.node, mock=mock)
            self.assertEqual(workspace_process_registry.active_process_ids(self.workspace), [])


class AgentUiDesignShutdownTests(unittest.TestCase):
    """真实后端生命周期必须保证其他 owner 的 finally 清理。"""

    def test_design_shutdown_failure_preserves_other_cleanup(self) -> None:
        """新设计 owner 关闭失败也不能跳过工具、Graph 和检查点收尾。"""
        from app.main import app, lifespan

        async def exercise() -> None:
            """运行实际 lifespan，不启动业务网络或模型。"""
            async with lifespan(app):
                pass

        with patch("app.main.ensure_database_platform_key"), patch("app.main.ensure_agents_document"), \
                patch("app.main.initialize_backend_instance"), \
                patch("app.main.shutdown_agent_ui_design_previews", new_callable=AsyncMock,
                      side_effect=RuntimeError("fixture cleanup")), \
                patch("app.main.shutdown_agent_ui_tool_hosts", new_callable=AsyncMock) as tools, \
                patch("app.main.clear_workflow_graph_cache"), \
                patch("app.main.clear_application_planning_graph_cache"), \
                patch("app.main.clear_direct_modification_graph_cache"), \
                patch("app.main.close_workflow_checkpointer", new_callable=AsyncMock) as checkpoints:
            with self.assertRaises(RuntimeError):
                asyncio.run(exercise())
        tools.assert_awaited_once()
        checkpoints.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
