"""独立 API 开发不消费视觉设计，必要业务合同与正式确认仍保持有效。"""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from app.graph.nodes.api_design import api_design_readiness_gate
from app.graph.nodes.task_planning_adapter import _assemble_planning_context
from app.graph.workflow import build_graph
from app.persistence.checkpoints import close_workflow_checkpointer
from app.protocols.workflow.request import workflow_run_inputs
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.automatic_artifact_regeneration import prepare_automatic_regeneration
from app.services.stage_artifacts import _ARTIFACTS, load_stage_artifacts
from app.services.stage_prerequisites import regeneration_for_retry
from tests.dag_planning_baseline_fixtures import (
    execution_scope, formal_artifacts, project_plan, write_confirmed_endpoint_designs,
    workspace_snapshot, write_json,
)
from tests.test_build_task_reuse_workspace import _ready_template
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class EndpointDevelopmentWithoutUiTests(unittest.IsolatedAsyncioTestCase):
    """从真实文件经过请求、自动路由、API 门禁和 DAG 输入验证隔离边界。"""

    def setUp(self):
        """准备没有页面开发记录、也没有 UI 清单的已就绪应用。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addAsyncCleanup(close_workflow_checkpointer)
        self.workspace = Path(directory.name)
        _write_ready_workbench_lifecycle(str(self.workspace))
        _ready_template(self.workspace)
        self.scope = execution_scope("endpoint")
        self.plan = project_plan()
        artifacts = formal_artifacts(self.plan)
        artifacts["product_plan"]["pages"] = deepcopy(self.plan["pages"])
        # 技术规划仍记录 UI 的规划来源，但 API 执行不消费视觉引用。
        artifacts["technical_plan"].update(
            ui_designs_sha256="0" * 64,
            basedOn=[{"artifactKey": "ui-design", "sha256": "0" * 64}],
        )
        for key, value in artifacts.items():
            if key != "ui_designs":
                write_json(self.workspace, f".devagentstudio/{_ARTIFACTS[key]}", value)
        self.plan = artifacts["technical_plan"]
        write_confirmed_endpoint_designs(self.workspace, self.plan)
        self.snapshot = write_json(self.workspace, ".devagentstudio/cache/workspace-snapshot.json", workspace_snapshot())

    def payload(self, *, scope_only=False):
        """构造与开发接口按钮一致的显式目标请求，也覆盖只有 scope 的入口。"""
        return {
            "runId": "api-no-ui-run", "threadId": "api-no-ui-thread", "request": "开始开发接口：GET /orders",
            "forwardedProps": {
                "workspaceRoot": str(self.workspace), "sessionId": "api-no-ui-session",
                "buildExecutionScope": self.scope,
                **({} if scope_only else {"selectedApiContractId": "orders-api", "selectedEndpointId": "orders.list"}),
            },
        }

    async def test_ui_states_do_not_redirect_endpoint_to_planning(self):
        """UI 缺失、损坏、待确认或失效均不改写 API 开发阶段，也不修改原文件。"""
        ui_path = self.workspace / ".devagentstudio/specs/ui-designs.json"
        for content in (None, "{", json.dumps({"confirmation_status": "pending_user_confirmation", "pages": []}),
                        json.dumps({"confirmation_status": "stale", "pages": []})):
            if content is not None:
                ui_path.write_text(content)
            for scope_only in (False, True):
                with self.subTest(content=content, scope_only=scope_only):
                    inputs = workflow_run_inputs(self.payload(scope_only=scope_only))
                    self.assertEqual(inputs["resume_values"]["build_execution_scope"], self.scope)
                    self.assertIsNone(await prepare_automatic_regeneration(inputs, run_id="run", thread_id="thread"))
                    self.assertFalse(inputs["workflow_scope"])
                    state = {**inputs["resume_values"], "workspace": str(self.workspace)}
                    for node in ("api_design_readiness_gate", "prepare_build_tasks", "authorization_bootstrap"):
                        self.assertIsNone(regeneration_for_retry(str(self.workspace), node, state=state))
                        loaded = load_stage_artifacts(state, node)
                        self.assertEqual(loaded["ui_designs"], {})
                        self.assertEqual(loaded["ui_designs_json_path"], "")
                    self.assertEqual(api_design_readiness_gate({**state, **loaded})["status"], "completed")
            if content is not None:
                self.assertEqual(ui_path.read_text(), content)

    def test_production_dag_context_is_not_blocked_by_ui(self):
        """真实 DAG 输入装配仍复检映射与模板，但不要求 UI 清单或页面完成记录。"""
        inputs = workflow_run_inputs(self.payload())
        state = {**inputs["resume_values"], "workspace": str(self.workspace),
                 "workspace_snapshot_path": str(self.snapshot), "active_run_id": "run", "active_thread_id": "thread"}
        context = _assemble_planning_context(state)
        self.assertNotIsInstance(context, dict)

    def test_business_drift_and_page_dependencies_are_still_checked(self):
        """API 的产品合同变更仍阻断，页面开发仍保留其 UI 依赖。"""
        workspace = str(self.workspace)
        self.assertEqual(regeneration_for_retry(workspace, "api_design_readiness_gate").key, "ui_designs")
        write_json(self.workspace, ".devagentstudio/plans/product-plan.json",
                   {"confirmation_status": "confirmed", "requirement_spec_sha256": "0" * 64})
        issue = regeneration_for_retry(workspace, "api_design_readiness_gate", state={"build_execution_scope": self.scope})
        self.assertEqual(issue.key, "product_plan")

    async def test_agui_api_entry_never_publishes_ui_confirmation(self):
        """通过真实 AG-UI 与生产 Graph 走到任务确认，接口请求不得切换到设计稿卡。"""
        visited = []

        def inspect(state):
            """隔离源码扫描，只记录生产图已经通过 API 门禁。"""
            visited.append("inspect_workspace")
            return {"phase": "inspect_workspace", "status": "completed", "workspace_revision": "test-snapshot"}

        def planner(state):
            """在原任务确认门停止，不调用模型或生成代码。"""
            visited.append("prepare_build_tasks")
            return {"phase": "prepare_build_tasks", "status": "requires_user_input",
                    "clarification": {"mode": "build_task_plan_confirmation", "status": "requires_user_input"}}

        with patch("app.graph.workflow.nodes.inspect_workspace", new=inspect):
            graph = build_graph(checkpointer=InMemorySaver(), prepare_build_tasks_node=planner)
            frames = "".join([frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload=self.payload())])
        self.assertEqual(set(visited), {"inspect_workspace", "prepare_build_tasks"})
        self.assertEqual(visited[-1], "prepare_build_tasks")
        self.assertIn("build_task_plan_confirmation", frames)
        self.assertNotIn("ui_design_confirmation", frames)
        self.assertNotIn('"phase": "ui_confirmation"', frames)
