"""复用真实 DAG 增量编译，验证批量分析不生成任何正式或待确认任务。"""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus, utc_now
from app.domain.application_revision import ActiveFormalRevision
from app.domain.development_artifacts import DevelopmentArtifactProgress, DevelopmentArtifactTarget
from app.graph.workflow import build_graph
from app.services.application_lifecycle import (
    create_application_lifecycle, load_application_lifecycle, write_application_lifecycle,
)
from app.services.completed_development_analysis import analyze_completed_development, analyze_completed_target
from app.services.development_artifacts import complete_initial_development, test_entry_gate
from tests.dag_planning_baseline_fixtures import (
    build_context, confirmed_baseline, execution_scope, project_plan,
    write_confirmed_endpoint_designs, write_json,
)


class CompletedDevelopmentAnalysisTests(unittest.IsolatedAsyncioTestCase):
    """真实合同、复用和缺项规则共享正式规划路径，仅隔离上游页面合同物化。"""

    def setUp(self):
        """准备已完成订单页/API以及未完成客户页/API。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = project_plan()
        self.baseline = confirmed_baseline(self.plan, execution_scope())
        for task in self.baseline["task_registry"].values():
            task["status"] = "completed"
        # 基础夹具仅声明裸 Unit；这里补齐真实新规划要求的精确 capability 覆盖。
        self.baseline["task_registry"]["orders:page"]["provides_capabilities"] = ["frontend.page:orders"]
        self.baseline["task_registry"]["backend:bootstrap::config"]["provides_capabilities"] = ["backend.bootstrap:database"]
        self.baseline["task_registry"]["orders:controller"]["provides_capabilities"] = [
            f"{kind}:orders-api:orders.list" for kind in (
                "backend.endpoint.objects", "backend.repository", "backend.application_service", "backend.endpoint_controller",
            )
        ]
        self.write_inputs()
        state = create_application_lifecycle(application_id="analysis", application_name="分析")
        state.initialization.stage = ApplicationLifecycleStage.READY_FOR_WORKBENCH
        state.initialization.status = ApplicationLifecycleStatus.COMPLETED
        state.active_formal_revision = ActiveFormalRevision(
            changeId="revision", formalBranch="design_stage_revision", sourceThreadId="source",
            sourceRunId="source", request="修改订单", target={"type": "page", "pageId": "orders"},
            impactInteractionId="impact", planningThreadId="planning", status="building",
        )
        done = DevelopmentArtifactProgress(initialDevelopmentStatus="completed", completedAt=utc_now(),
                                           completedRunId="old", completedThreadId="old", completedBranchName="v1")
        state.development_artifacts.pages["orders"] = done
        state.development_artifacts.endpoints["orders-api"] = {"orders.list": done.model_copy(deep=True)}
        write_application_lifecycle(self.root, state)

    def write_inputs(self):
        """落盘正式目录和确认任务，不使用待确认计划。"""
        write_json(self.root, ".devagentstudio/plans/product-plan.json", {
            "confirmation_status": "confirmed", "pages": self.plan["pages"],
        })
        write_json(self.root, ".devagentstudio/specs/requirement-spec.json", {"confirmation_status": "confirmed"})
        write_json(self.root, ".devagentstudio/specs/ui-designs.json", {"confirmation_status": "skipped"})
        write_json(self.root, ".devagentstudio/plans/technical-plan.json", self.plan)
        write_json(self.root, ".devagentstudio/plans/build-task-plan.json", self.baseline)
        write_confirmed_endpoint_designs(self.root, self.plan)
        write_json(self.root, ".devagentstudio/application.json", {"schemaVersion": 6, "configRevision": 1,
                   "appName": "分析", "authorization": {"enabled": False}, "auth": {"enable": False}})

    def analyze(self):
        """仅隔离上游正式产物物化，后续目标解析、合同覆盖和状态写入全部真实执行。"""
        with patch("app.services.completed_development_analysis._load_current_confirmation_plan", return_value=self.plan):
            return analyze_completed_development(str(self.root), change_id="revision")

    def test_unchanged_and_changed_contracts(self):
        """无变化保持完成，接口参数变化标记相关已完成页和 API，未完成目标始终跳过。"""
        self.assertEqual(self.analyze(), {"checked": 2, "changed": 0, "unavailable": 0})
        before = load_application_lifecycle(self.root).development_artifacts.pages["orders"]
        self.plan["api_contracts"][0]["endpoints"][0]["parameters"] = [
            {"name": "size", "in": "query", "schema": {"type": "integer", "default": 13}},
        ]
        self.write_inputs()
        baseline_bytes = (self.root / ".devagentstudio/plans/build-task-plan.json").read_bytes()
        self.assertEqual(self.analyze(), {"checked": 2, "changed": 2, "unavailable": 0})
        state = load_application_lifecycle(self.root)
        page = state.development_artifacts.pages["orders"]
        self.assertEqual(page.change_status, "changed")
        self.assertEqual(page.completed_run_id, before.completed_run_id)
        self.assertEqual(page.completed_branch_name, "v1")
        self.assertEqual(state.development_artifacts.pages["customers"].change_status, "unchanged")
        self.assertEqual(state.development_artifacts.endpoints["customers-api"]["customers.list"].initial_development_status, "pending")
        self.assertFalse(test_entry_gate(state).allowed)
        self.assertEqual((self.root / ".devagentstudio/plans/build-task-plan.json").read_bytes(), baseline_bytes)
        self.assertFalse((self.root / ".devagentstudio/drafts/plans/build-task-plan.pending.json").exists())
        revision = state.revision
        self.analyze()
        self.assertEqual(load_application_lifecycle(self.root).revision, revision)

    def test_unrelated_changes_and_pending_targets_are_not_analyzed(self):
        """客户合同变化不能使已完成订单变更，客户目标不进入上下文加载或缺项计算。"""
        self.plan["api_contracts"][1]["endpoints"][0]["parameters"] = [{"name": "q", "in": "query", "schema": {"type": "string"}}]
        self.write_inputs()
        from app.services.completed_development_analysis import _target_analysis
        with patch("app.services.completed_development_analysis._target_analysis", wraps=_target_analysis) as analyze:
            self.assertEqual(self.analyze(), {"checked": 2, "changed": 0, "unavailable": 0})
        self.assertEqual(len(analyze.call_args_list), 2)
        self.assertTrue(all(call.args[1].page_id == "orders" or call.args[1].endpoint_id == "orders.list"
                            for call in analyze.call_args_list))

    def test_invalid_baseline_is_unavailable_not_unchanged(self):
        """损坏基线不能误标无变更，保留完成证据并明确待检查。"""
        write_json(self.root, ".devagentstudio/plans/build-task-plan.json", {})
        self.assertEqual(self.analyze(), {"checked": 2, "changed": 0, "unavailable": 2})
        page = load_application_lifecycle(self.root).development_artifacts.pages["orders"]
        self.assertEqual(page.initial_development_status, "completed")
        self.assertEqual(page.change_status, "unavailable")

    def test_missing_responsibilities_and_unexecuted_tasks_are_changes(self):
        """任务已确认但未执行也不能作为当前实现覆盖，复用相同缺项计算。"""
        target = DevelopmentArtifactTarget(type="page", pageId="orders")
        context = build_context(self.plan, execution_scope())
        baseline = deepcopy(self.baseline)
        for task in baseline["task_registry"].values():
            if task["unit_id"] == "page:orders":
                task["status"] = "pending"
        status, _ = analyze_completed_target(project_plan=self.plan, baseline=baseline, context=context, target=target)
        self.assertEqual(status, "changed")

    def test_no_completed_targets_does_not_load_planning_inputs(self):
        """空完成集合不读取规划基线或任何目标上下文，直接显示选择卡所需结果。"""
        state = load_application_lifecycle(self.root)
        state.development_artifacts.pages = {}
        state.development_artifacts.endpoints = {}
        write_application_lifecycle(self.root, state)
        with patch("app.services.completed_development_analysis.load_confirmed_build_task_plan", side_effect=AssertionError("未完成不检查")):
            self.assertEqual(self.analyze(), {"checked": 0, "changed": 0, "unavailable": 0})

    def test_changed_completed_targets_block_test_even_when_everything_was_completed(self):
        """所有目标首次完成也不能绕过本轮变更或待检查状态。"""
        state = load_application_lifecycle(self.root)
        state.development_artifacts.pages = {"orders": state.development_artifacts.pages["orders"]}
        state.development_artifacts.endpoints = {"orders-api": state.development_artifacts.endpoints["orders-api"]}
        state.development_artifacts.catalog_error = None
        self.assertTrue(test_entry_gate(state).allowed)
        state.development_artifacts.pages["orders"].change_status = "changed"
        self.assertFalse(test_entry_gate(state).allowed)
        state.development_artifacts.pages["orders"].change_status = "unavailable"
        self.assertFalse(test_entry_gate(state).allowed)

    async def test_real_graph_returns_selection_without_planning_or_build(self):
        """即便来源是具体页面，修订续接也只分析全部完成项并结束 Graph。"""
        planning = AsyncMock(side_effect=AssertionError("不能生成 DAG"))
        graph = build_graph(checkpointer=InMemorySaver(), prepare_build_tasks_node=planning)
        with patch("app.services.completed_development_analysis._load_current_confirmation_plan", return_value=self.plan):
            result = await graph.ainvoke({
                "workspace": str(self.root), "project_plan": self.plan, "selectedPageId": "orders",
                "change_id": "revision", "completed_development_analysis": True,
            }, config={"configurable": {"thread_id": "analysis"}})
        self.assertEqual(result["clarification"]["mode"], "development_target_selection")
        self.assertFalse(result["completed_development_analysis"])
        self.assertNotIn("build", result.get("timeline", []))
        planning.assert_not_awaited()

    def test_successful_redevelopment_clears_change_without_rewriting_completion(self):
        """原目标成功入口只清除当前变更，保持首次完成时间与分支。"""
        from app.services.application_lifecycle import start_workbench_execution
        state = load_application_lifecycle(self.root)
        state.active_formal_revision = None
        page = state.development_artifacts.pages["orders"]
        original = page.model_copy(deep=True)
        page.change_status = "changed"
        write_application_lifecycle(self.root, state)
        start_workbench_execution(self.root, scope="page", target_id="orders", page_id="orders",
                                  thread_id="new", run_id="new", phase="build", initial_development_entry=True)
        result = complete_initial_development(self.root, run_id="new")
        page = result.development_artifacts.pages["orders"]
        self.assertEqual(page.change_status, "unchanged")
        self.assertEqual(page.completed_at, original.completed_at)
        self.assertEqual(page.completed_run_id, "old")
        self.assertEqual(page.completed_branch_name, "v1")
