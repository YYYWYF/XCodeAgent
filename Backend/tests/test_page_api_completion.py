"""验证页面与本轮实际完成接口的原子登记及 AG-UI 投影。"""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage, ApplicationLifecycleStatus, WorkbenchExecutionStatus,
)
from app.graph.nodes.lifecycle import test_phase_confirmation
from app.services.application_lifecycle import (
    create_application_lifecycle, start_workbench_execution,
    update_workbench_execution, write_application_lifecycle,
)
from app.services.development_artifacts import (
    complete_initial_development, refresh_development_artifacts, test_entry_gate,
)
from app.workspace.task_documents import build_task_plan_sha256


class PageApiCompletionTests(unittest.TestCase):
    """通过真实完成节点检查切片、任务终态和单测门的共同约束。"""

    def setUp(self) -> None:
        """准备一个页面、两个依赖接口和一个范围外接口。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        plans = self.workspace / ".devagentstudio/plans"
        plans.mkdir(parents=True)
        for name, plan in {
            "product-plan": {"pages": [{"pageId": "one"}]},
            "technical-plan": {"api_contracts": [{"id": "api", "endpoints": [
                {"id": endpoint} for endpoint in ("get", "save", "other")
            ]}]},
        }.items():
            (plans / f"{name}.json").write_text(json.dumps({
                **plan, "confirmation_status": "confirmed",
            }), encoding="utf-8")
        lifecycle = create_application_lifecycle(application_id="test", application_name="测试")
        lifecycle.initialization.stage = ApplicationLifecycleStage.READY_FOR_WORKBENCH
        lifecycle.initialization.status = ApplicationLifecycleStatus.COMPLETED
        write_application_lifecycle(self.workspace, lifecycle)

    def start_page(self, run_id: str = "run") -> None:
        """登记服务端页面执行，确保完成事实归属同一 Run 和 Thread。"""

        start_workbench_execution(
            self.workspace, scope="page", target_id="one", page_id="one",
            run_id=run_id, thread_id="thread", phase="api_design_readiness_gate",
            initial_development_entry=True,
        )

    def build_state(self, run_id: str = "run") -> dict:
        """构造已绑定计划与实际任务切片，包含累计 DAG 中的范围外任务。"""

        scope = {"type": "page", "targetId": "one"}
        units = ["page:one", "backend:endpoint:api:get", "backend:endpoint:api:save",
                 "backend:endpoint:api:other"]
        tasks = [{"id": f"task-{index}", "unit_id": unit, "status": "completed"}
                 for index, unit in enumerate([*units, units[1]])]
        plan = {
            "schema_version": "build-dag.v4", "confirmation_status": "confirmed",
            "build_execution_scope": scope,
            "task_registry": {task["id"]: deepcopy(task) for task in tasks},
            "unit_graph": {"nodes": units, "edges": [
                {"from": unit, "to": "page:one", "type": "depends_on"}
                for unit in units[1:3]
            ]},
        }
        return {
            "workspace": str(self.workspace), "active_run_id": run_id,
            "build_execution_scope": scope, "build_task_plan": plan,
            "build_run_id": "build-run", "build_run_plan_sha256": build_task_plan_sha256(plan),
            "tasks": tasks,
            "build_execution_slice": {"scope": scope, "task_ids": ["task-0", "task-1", "task-2", "task-4"]},
            "build_summary": {"status": "completed"}, "unit_test_gate_passed": True,
        }

    def test_page_completes_only_successful_in_scope_apis_atomically(self) -> None:
        """本轮接口与页面共用完成证据；累计 DAG 中其他接口仍未完成。"""

        self.start_page()
        state = self.build_state()
        state["tasks"][2]["status"] = "already_satisfied"
        test_phase_confirmation(state)
        lifecycle = refresh_development_artifacts(self.workspace)
        page = lifecycle.development_artifacts.pages["one"]
        for endpoint in ("get", "save"):
            self.assertEqual(lifecycle.development_artifacts.endpoints["api"][endpoint], page)
        self.assertEqual(lifecycle.development_artifacts.endpoints["api"]["other"].initial_development_status, "pending")
        self.assertEqual(test_entry_gate(lifecycle).completed, 3)
        again = complete_initial_development(self.workspace, run_id="run", build_state=state)
        self.assertEqual(again.revision, lifecycle.revision)

    def test_incomplete_api_tasks_do_not_gain_completion(self) -> None:
        """接口内任一任务未成功或不在执行切片，都不能由页面完成推断成功。"""

        for status in ("pending", "running", "failed", "missing", "outside_slice"):
            with self.subTest(status=status):
                run_id = f"run-{status}"
                self.start_page(run_id)
                state = self.build_state(run_id)
                if status == "missing":
                    state["tasks"].pop()
                elif status == "outside_slice":
                    state["build_execution_slice"]["task_ids"].remove("task-4")
                else:
                    state["tasks"][-1]["status"] = status
                test_phase_confirmation(state)
                lifecycle = refresh_development_artifacts(self.workspace)
                self.assertEqual(lifecycle.development_artifacts.endpoints["api"]["get"].initial_development_status, "pending")

    def test_missing_or_mismatched_binding_never_completes_apis(self) -> None:
        """单独成功摘要、旧 Run、错页或漂移计划均不足以补记关联 API。"""

        self.start_page()
        original = self.build_state()
        for field, value in (
            ("build_task_plan", {}), ("build_run_id", ""),
            ("build_run_plan_sha256", "stale"), ("active_run_id", "old-run"),
            ("build_execution_slice", {}),
            ("build_execution_scope", {"type": "page", "targetId": "other"}),
        ):
            with self.subTest(field=field):
                state = {**deepcopy(original), field: value}
                lifecycle = complete_initial_development(self.workspace, run_id="run", build_state=state)
                self.assertEqual(test_entry_gate(lifecycle).completed, 1)

    def test_build_and_unit_test_gates_block_all_completion(self) -> None:
        """Build 或单测门未通过时，真实节点不能登记页面及 API。"""

        self.start_page()
        for field, value in (("build_summary", {}), ("unit_test_gate_passed", False)):
            with self.subTest(field=field):
                state = self.build_state()
                state[field] = value
                if field == "build_summary":
                    state["tasks"] = []
                test_phase_confirmation(state)
                self.assertEqual(test_entry_gate(refresh_development_artifacts(self.workspace)).completed, 0)

    def test_failed_execution_cannot_complete_apis(self) -> None:
        """已失败运行即使残留成功证据也不能提交关联接口完成。"""

        self.start_page()
        update_workbench_execution(self.workspace, run_id="run", phase="build", status=WorkbenchExecutionStatus.FAILED)
        with self.assertRaises(ValueError):
            test_phase_confirmation(self.build_state())
        self.assertEqual(test_entry_gate(refresh_development_artifacts(self.workspace)).completed, 0)

    def test_existing_api_completion_identity_survives_page_build(self) -> None:
        """共享接口的首次完成归属不被后续页面开发覆盖。"""

        start_workbench_execution(
            self.workspace, scope="endpoint", target_id="get", api_contract_id="api",
            page_id=None, run_id="api-run", thread_id="api-thread",
            phase="api_design_readiness_gate", initial_development_entry=True,
        )
        before = complete_initial_development(self.workspace, run_id="api-run").development_artifacts.endpoints["api"]["get"]
        self.start_page()
        test_phase_confirmation(self.build_state())
        after = refresh_development_artifacts(self.workspace).development_artifacts.endpoints["api"]["get"]
        self.assertEqual(before, after)

    def test_lifecycle_stream_publishes_page_and_api_completion(self) -> None:
        """真实 AG-UI 在同一 lifecycle 帧投影页面/API 完成与测试门禁。"""

        self.assert_completion_stream(all_completed=False)

    def test_last_page_and_apis_unlock_test_confirmation(self) -> None:
        """其他目标完成后，最后一个页面及接口共同完成便显示显式测试确认。"""

        start_workbench_execution(
            self.workspace, scope="endpoint", target_id="other", api_contract_id="api",
            page_id=None, run_id="other-run", thread_id="other-thread",
            phase="api_design_readiness_gate", initial_development_entry=True,
        )
        complete_initial_development(self.workspace, run_id="other-run")
        self.assert_completion_stream(all_completed=True)

    def test_page_redevelopment_clears_only_verified_api_changes(self) -> None:
        """重开发可完成新增接口并清除通过验证接口的变更，不覆盖首次完成证据。"""

        self.start_page()
        complete_initial_development(self.workspace, run_id="run")
        self.start_page("revision")
        state = self.build_state("revision")
        test_phase_confirmation(state)
        lifecycle = refresh_development_artifacts(self.workspace)
        page = lifecycle.development_artifacts.pages["one"].model_copy(deep=True)
        endpoint = lifecycle.development_artifacts.endpoints["api"]["get"]
        first = endpoint.model_copy(deep=True)
        endpoint.change_status = "changed"
        endpoint.change_reason = "接口正式合同发生变化"
        other = lifecycle.development_artifacts.endpoints["api"]["save"]
        other.change_status = "changed"
        other.change_reason = "尚未完成重开发"
        write_application_lifecycle(self.workspace, lifecycle)
        state["tasks"][2]["status"] = "pending"
        test_phase_confirmation(state)
        after = refresh_development_artifacts(self.workspace)
        self.assertEqual(after.development_artifacts.pages["one"], page)
        self.assertEqual(after.development_artifacts.endpoints["api"]["get"], first)
        self.assertEqual(after.development_artifacts.endpoints["api"]["save"].change_status, "changed")

    def assert_completion_stream(self, *, all_completed: bool) -> None:
        """消费真实 AG-UI 完成流，检查生命周期、门禁和执行交接一致。"""

        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.graph import END, START, StateGraph
        from app.graph.state import ProjectState
        from app.protocols.workflow import build_workflow_ag_ui_stream

        def successful_build(state: ProjectState) -> dict:
            """提供本轮绑定的 Build 切片与单测结果。"""

            return self.build_state(state["active_run_id"])

        builder = StateGraph(ProjectState)
        builder.add_node("api_design_readiness_gate", successful_build)
        builder.add_node("test_phase_confirmation", test_phase_confirmation)
        builder.add_edge(START, "api_design_readiness_gate")
        builder.add_edge("api_design_readiness_gate", "test_phase_confirmation")
        builder.add_edge("test_phase_confirmation", END)
        graph = builder.compile(checkpointer=InMemorySaver())

        async def collect() -> list[dict]:
            """消费真实事件流直到 RunFinished。"""

            frames = [frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload={
                "runId": "stream-run", "threadId": "thread",
                "messages": [{"role": "user", "content": "开发页面"}],
                "forwardedProps": {"workspaceRoot": str(self.workspace), "selectedPageId": "one"},
            })]
            return [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]

        with patch("app.protocols.workflow.request._project_plan_start_values", return_value={}):
            events = asyncio.run(collect())
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        lifecycle = [event["value"] for event in events if event.get("name") == "application-lifecycle"][-1]
        self.assertEqual(lifecycle["testEntryGate"]["completed"], 4 if all_completed else 3)
        self.assertEqual(lifecycle["testEntryGate"]["allowed"], all_completed)
        self.assertEqual(lifecycle["developmentArtifacts"]["endpoints"]["api"]["get"]["completedRunId"], "stream-run")
        if all_completed:
            self.assertEqual(lifecycle["activeExecutions"]["stream-run"]["status"], "awaiting_user")
        else:
            self.assertEqual(lifecycle["activeExecutions"], {})
