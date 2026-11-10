"""覆盖失效路由、旧执行隔离、原目标续接和新任务计划确认门。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver

from app.services.automatic_artifact_regeneration import prepare_automatic_regeneration
from app.services.stage_prerequisites import regeneration_for_retry
from app.services.retry_node_state import retry_node_state
from app.services.application_lifecycle import load_application_lifecycle
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class AutomaticArtifactRegenerationTests(unittest.IsolatedAsyncioTestCase):
    """使用实际文件、生命周期和 Graph 条件边，模型生成仅在边界替换。"""

    def write_artifacts(self, workspace):
        """写入结构可读但技术计划上游引用过期的应用。"""
        from app.services.stage_artifacts import _ARTIFACTS
        root = Path(workspace) / ".devagentstudio"
        root.mkdir(exist_ok=True)
        (root / "application.json").write_text("{}")
        for key, relative in _ARTIFACTS.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            value = {"confirmation_status": "confirmed"}
            if key == "technical_plan":
                value.update(artifact_type="technical-plan", basedOn=[{"artifactKey": "product-plan", "sha256": "0" * 64}])
            path.write_text(json.dumps(value))
        return root

    def test_existing_json_with_stale_hash_selects_producer(self):
        """文件存在且标记 confirmed 仍必须重生成，读取不能修改原正式产物。"""
        with tempfile.TemporaryDirectory() as workspace:
            root = self.write_artifacts(workspace)
            path = root / "plans/technical-plan.json"
            before = path.read_bytes()
            for node in ("api_design_readiness_gate", "prepare_build_tasks", "build"):
                issue = regeneration_for_retry(workspace, node)
                self.assertEqual(issue.node, "technical_planning_begin")
            self.assertEqual(before, path.read_bytes())

    def test_native_semantic_hash_selects_earliest_producer(self):
        """原生产品摘要失效时先生成产品文档，不能继续使用下游已确认标记。"""
        with tempfile.TemporaryDirectory() as workspace:
            root = self.write_artifacts(workspace)
            path = root / "plans/product-plan.json"
            path.write_text(json.dumps({"confirmation_status": "confirmed", "requirement_spec_sha256": "0" * 64}))
            self.assertEqual(regeneration_for_retry(workspace, "build").node, "product_planning")

    def test_readable_old_dag_is_not_reused_after_input_change(self):
        """旧 DAG 自身仍合法时，真实直接输入变化仍把 Build 重试送回任务规划。"""
        from copy import deepcopy
        from tests.dag_planning_baseline_fixtures import project_plan, execution_scope, confirmed_baseline, build_context, formal_artifacts, write_json
        from app.services.stage_artifacts import _ARTIFACTS
        from app.workspace.task_documents import write_build_task_plan_json
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / ".devagentstudio"
            root.mkdir()
            (root / "application.json").write_text("{}")
            plan = project_plan()
            scope = execution_scope()
            baseline = confirmed_baseline(plan, scope)
            changed = deepcopy(plan)
            changed["api_contracts"][0]["endpoints"][0]["parameters"] = [
                {"name": "pageSize", "in": "query", "schema": {"type": "integer", "default": 13}}]
            for key, value in formal_artifacts(changed).items():
                # 本用例只验证任务输入变化；当前正式 UI 必须已确认，不能沿用夹具的 skipped。
                if key == "ui_designs":
                    value = {"confirmation_status": "confirmed"}
                write_json(Path(workspace), str(Path(".devagentstudio") / _ARTIFACTS[key]), value)
            state = {"workspace": workspace, "build_execution_scope": scope}
            write_build_task_plan_json(state, baseline)
            context = build_context(changed, scope)
            with patch("app.services.build_context_resolver.resolve_confirmation_context", return_value={"project_plan": changed, "build_context": context}):
                issue = regeneration_for_retry(workspace, "build", state=state)
            self.assertEqual(issue.key, "build_task_plan")
            self.assertEqual(issue.node, "prepare_build_tasks")

    async def test_normal_entry_preserves_development_and_reports_invalid_inputs(self):
        """上游失效时仍进入原开发检查，不创建修订、不改文件或消费任务确认。"""
        from copy import deepcopy
        with tempfile.TemporaryDirectory() as workspace:
            self.write_artifacts(workspace)
            _write_ready_workbench_lifecycle(workspace)
            inputs = {"workspace": workspace, "request": "开发订单页", "resume_values": {
                "build_execution_scope": {"type": "page", "targetId": "orders"},
                "tasks": [{"id": "old"}], "build_run_id": "old-build", "build_task_plan_confirmation": {"action": "confirm"},
            }}
            original = deepcopy(inputs)
            lifecycle = load_application_lifecycle(workspace)
            with patch("app.graph.application_planning_workflow.application_planning_graph_for_request", AsyncMock()) as planner:
                graph = await prepare_automatic_regeneration(inputs, run_id="new-run", thread_id="thread")
            self.assertIsNone(graph)
            planner.assert_not_awaited()
            self.assertEqual(inputs, original)
            self.assertEqual(load_application_lifecycle(workspace), lifecycle)

    async def test_each_upstream_issue_is_read_only_for_page_and_api_entry(self):
        """缺失、损坏和待确认上游都只参与检查，页面与 API 均不能自动返回规划。"""
        from copy import deepcopy
        from app.services.stage_artifacts import _ARTIFACTS
        for key in _ARTIFACTS:
            for content in (None, "{", json.dumps({"confirmation_status": "pending_user_confirmation"})):
                for target_type in ("page", "endpoint"):
                    with self.subTest(key=key, content=content, target=target_type), tempfile.TemporaryDirectory() as workspace:
                        root = self.write_artifacts(workspace)
                        _write_ready_workbench_lifecycle(workspace)
                        path = root / _ARTIFACTS[key]
                        if content is None:
                            path.unlink()
                        else:
                            path.write_text(content)
                        original = {item: item.read_bytes() for item in root.rglob("*.json")}
                        inputs = {"workspace": workspace, "request": "开发目标", "resume_values": {
                            "build_execution_scope": {"type": target_type, "targetId": "orders"}}}
                        before = deepcopy(inputs)
                        self.assertIsNone(await prepare_automatic_regeneration(inputs, run_id="run", thread_id="thread"))
                        self.assertEqual(inputs, before)
                        self.assertEqual({item: item.read_bytes() for item in root.rglob("*.json")}, original)

    def test_replanning_discards_execution_but_preserves_business_target(self):
        """重规划不能恢复旧任务、运行绑定、修复计划、快照及一次性确认。"""
        before = {"selectedPageId": "orders", "request": "开发订单", "build_execution_scope": {"type": "page", "targetId": "orders"},
            "tasks": [{"id": "old"}], "build_run_id": "old", "build_run_plan_sha256": "f" * 64,
            "retry_source_run_id": "old", "workspace_snapshot": {"old": True}, "workspace_revision": "old",
            "repair_task_plan": {"old": True}, "build_task_plan_confirmation": {"action": "confirm"}}
        state = retry_node_state(before, "prepare_build_tasks", planning=False)
        for key in ("tasks", "build_task_plan_confirmation", "workspace_snapshot", "repair_task_plan", "build_run_id", "build_run_plan_sha256", "retry_source_run_id", "workspace_revision"):
            self.assertFalse(state[key], key)
        self.assertEqual(state["selectedPageId"], "orders")
        self.assertEqual(state["build_execution_scope"], before["build_execution_scope"])
        self.assertEqual(before["build_run_id"], "old")

    async def test_source_drift_routes_back_to_new_plan_confirmation(self):
        """实际 Graph 从来源失效的 Build 自动重规划，新确认门前不能再次开发。"""
        from app.graph.workflow import build_graph
        observed = []

        def failed_build(state):
            """模拟验收报告来源失效，不能按普通失败再次执行旧合同。"""
            observed.append("build")
            return {"status": "failed", "repair_task_plan": {"terminal_failures": [{"failure_handling": "replan_build_dag"}]}}

        def gate(state):
            """验证旧执行证据在重新检测映射前已撤销。"""
            observed.append("gate")
            self.assertEqual(state["tasks"], [])
            self.assertEqual(state["build_run_id"], "")
            return {"status": "completed", "clarification": {}}

        def planner(state):
            """新计划只发布待确认状态，不自动消费上一轮确认。"""
            observed.append("planner")
            self.assertEqual(state["build_task_plan_confirmation"], {})
            self.assertEqual(state["selectedPageId"], "orders")
            return {"status": "requires_user_input", "clarification": {"mode": "build_task_plan_confirmation"}}

        def inspect(state):
            """隔离代码扫描，只验证生产 Graph 的自动回路与确认门。"""
            return {}

        with patch("app.services.stage_artifacts.load_stage_artifacts", return_value={}), \
                patch("app.graph.workflow._authorization_bootstrap_required", return_value=False), \
                patch("app.graph.workflow.nodes.build", side_effect=failed_build), \
                patch("app.graph.workflow.nodes.api_design_readiness_gate", side_effect=gate), \
                patch("app.graph.workflow.nodes.inspect_workspace", new=inspect):
            graph = build_graph(checkpointer=InMemorySaver(), prepare_build_tasks_node=planner)
            result = await graph.ainvoke({"resume_from": "build", "selectedPageId": "orders", "tasks": [{"id": "old"}],
                "build_run_id": "old", "build_task_plan_confirmation": {"action": "confirm"}}, {"configurable": {"thread_id": "test"}})
        self.assertEqual(observed, ["build", "gate", "planner"])
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "build_task_plan_confirmation")
