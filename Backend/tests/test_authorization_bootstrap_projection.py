"""回归真实路由与 AG-UI 权限初始化步骤的一致性，不连接 SaaS。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.graph.workflow import route_prepare_build_tasks
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.protocols.workflow.projection import _workflow_next_nodes
from app.services.authorization_workspace_projector import RUNTIME
from app.services.authorization_table_projection import compile_authorization_tables
from app.services.builtin_authorization_seed import compile_builtin_authorization_seed
from tests.authorization_seed_fixture import KEY, SUBJECT, save_plan, seed_plan, write_schema
from app.utils.atomic_json import atomic_write_json


class SparseBuildGraph:
    """模拟上游携带完整上下文、DAG 节点只返回增量的页面开发流。"""

    def __init__(self, state: dict, include_plan: bool) -> None:
        """保存路由输入，最终状态在执行后供运行时读取。"""
        self.state = state
        self.values = {}
        self.visited = []
        self.include_plan = include_plan

    async def astream(self, initial_state, *, config, stream_mode):
        """按真实选择器决定下一节点，同时保持 DAG 完成 update 不携带规划和工作区。"""
        del initial_state, config, stream_mode
        yield "updates", {"inspect_workspace": self.state}
        update = {"status": "completed", "phase": "prepare_build_tasks"}
        if self.include_plan:
            update["technical_plan"] = self.state["technical_plan"]
        yield "updates", {"prepare_build_tasks": update}
        next_node = route_prepare_build_tasks(self.state)
        self.visited.append(next_node)
        if next_node != "build":
            yield "updates", {next_node: {"status": "completed", "phase": next_node}}
        self.values = {**self.state, "phase": "build", "status": "requires_user_input"}
        yield "updates", {"build": {"phase": "build", "status": "requires_user_input"}}

    async def aget_state(self, config):
        """返回测试图当前状态，不伪造持久化 checkpoint。"""
        del config
        return SimpleNamespace(values=self.values)


class AuthorizationBootstrapProjectionTests(unittest.TestCase):
    """覆盖第二页面、首次 Builtin、旧模式和确认门的展示分流。"""

    def setUp(self) -> None:
        """构造有效成功 marker，配置读取隔离于真实应用和任何密钥。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.application = {
            "datasource": {"type": "database", "db": {"useBuiltin": True}},
            "authorization": {"enabled": True, "initialAdministratorSubjects": [SUBJECT]},
            "managedDatabase": {"status": "REGISTERED", "datasourceKey": "12ab34cd56ef", "datasourceId": 2},
        }
        self.state = {"workspace": str(self.root), "status": "completed", "technical_plan": seed_plan()}
        save_plan(self.root, self.state["technical_plan"])
        write_schema(self.root)
        config = patch("app.graph.workflow.read_application_config", return_value=self.application)
        config.start()
        self.addCleanup(config.stop)

    def mark_ready(self, *, seed_ready: bool = True) -> None:
        """写入与当前绑定一致的已核验成功事实，不按页面或 Manifest 缓存。"""
        compiled = compile_builtin_authorization_seed(self.application, self.state["technical_plan"], datasource_key=KEY)
        ddl = compile_authorization_tables(self.root, KEY)
        atomic_write_json(self.root / RUNTIME / "state.json", {
            "schemaVersion": 1, "datasourceMode": "builtin", "status": "schema_ready",
            "datasourceKey": "12ab34cd56ef", "datasourceId": 2, "schemaReady": True,
            "ddlFingerprint": ddl["ddlFingerprint"], "verifiedAt": "2026-11-03T12:45:05+00:00",
            **({"seed": {"status": "seed_ready", "operationStatus": "SUCCEEDED", "operationId": "456",
                         "ready": True, "verifiedAt": "2026-11-03T12:46:05+00:00",
                         "seedFingerprint": compiled["seedFingerprint"], "idempotencyKey": compiled["idempotencyKey"]}}
               if seed_ready else {}),
        })

    def assert_projection(self, expected: str, **updates) -> None:
        """对照真实图路由，并证明展示选择器不触发远端操作。"""
        state = {**self.state, **updates}
        with patch("app.services.managed_table_client.ManagedTableClient.request") as remote:
            self.assertEqual(route_prepare_build_tasks(state), expected)
            self.assertEqual(_workflow_next_nodes("prepare_build_tasks", state), [] if expected == "await_user_input" else [expected])
            remote.assert_not_called()

    def test_ready_second_page_skips_both_bootstrap_nodes(self) -> None:
        """权限已就绪时第二页直接 Build，不因当前 Manifest 仍有资源而显示旧节点。"""
        self.mark_ready()
        self.assert_projection("build")

    def test_first_builtin_and_changed_binding_use_builtin_node(self) -> None:
        """首次或绑定变化时展示独立 Builtin 节点，不能显示旧脚本节点。"""
        self.assert_projection("builtin_authorization_bootstrap")
        self.mark_ready(seed_ready=False)
        self.assert_projection("builtin_authorization_bootstrap")
        self.mark_ready()
        self.application["managedDatabase"]["datasourceId"] = 3
        self.assert_projection("builtin_authorization_bootstrap")

    def test_other_modes_disabled_and_confirmation_gates_match_graph(self) -> None:
        """保留原模式分流，关闭权限直接 Build，待确认不预测开始步骤。"""
        for datasource in ({"type": "database", "db": {"useBuiltin": False}}, {"type": "static"}, {"type": "external_api"}):
            self.application["datasource"] = datasource
            self.assert_projection("authorization_bootstrap")
        self.application["authorization"]["enabled"] = False
        self.assert_projection("build")
        self.assert_projection("await_user_input", status="requires_user_input")
        self.assert_projection("await_user_input", clarification={"mode": "development_target_selection"})
        self.assert_projection("handle_failure", status="failed")

    def test_sparse_ag_ui_updates_match_first_and_second_page_routes(self) -> None:
        """收集真实 AG-UI，稀疏增量不能误报第二页初始化，也不能漏报首轮节点。"""
        for ready in (False, True):
            if ready:
                self.mark_ready()
            for include_plan in (False, True):
                with self.subTest(ready=ready, include_plan=include_plan):
                    graph = SparseBuildGraph(self.state, include_plan)

                    async def collect() -> list[str]:
                        """运行真实事件编码器，模拟页面开发而不访问用户工作区。"""
                        return [frame async for frame in build_workflow_ag_ui_stream(
                            graph=graph, payload={"threadId": "projection-thread", "runId": "projection-run",
                                "messages": [{"role": "user", "content": "开发页面"}],
                                "forwardedProps": {"resumeFrom": "inspect_workspace"}},
                        )]

                    async def observe_start(*, operation, callback, **kwargs):
                        """仅执行开始节点观察，隔离其余依赖真实持久化工作区的回调。"""
                        if operation == "node.started":
                            return await callback()

                    with patch("app.protocols.workflow.runtime.observe_node_started", new_callable=AsyncMock) as observed, patch(
                        "app.protocols.workflow.runtime.best_effort_recovery_observation", side_effect=observe_start,
                    ):
                        frames = asyncio.run(collect())
                    expected = "build" if ready else "builtin_authorization_bootstrap"
                    self.assertEqual(graph.visited, [expected])
                    events = [json.loads(line[5:].strip()) for frame in frames for line in frame.splitlines() if line.startswith("data:")]
                    self.assertTrue(any(event.get("type") == "RUN_FINISHED" for event in events))
                    self.assertFalse(any(event.get("type") == "RUN_ERROR" for event in events))
                    process_nodes = [event.get("value", {}).get("nodeName") for event in events if event.get("name") == "agent-process"]
                    self.assertNotIn("authorization_bootstrap", process_nodes)
                    self.assertIn("build", process_nodes)
                    started = [call.kwargs.get("node_name") for call in observed.await_args_list]
                    self.assertIn(expected, started)
                    self.assertNotIn("authorization_bootstrap", started)
                    if ready:
                        self.assertNotIn("builtin_authorization_bootstrap", process_nodes)
                        self.assertNotIn("builtin_authorization_bootstrap", started)
