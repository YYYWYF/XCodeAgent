"""验证调试续跑以服务端 Build 目标登记初次完成，计数与确认卡保持一致。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus, WorkbenchExecutionStatus
from app.domain.application_revision import ActiveFormalRevision
from app.graph.state import ProjectState
from app.graph.nodes.lifecycle import test_phase_confirmation
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.services.application_lifecycle import create_application_lifecycle, start_workbench_execution, update_workbench_execution, write_application_lifecycle


class DevelopmentCompletionResumeTests(unittest.TestCase):
    """通过真实 checkpoint、AG-UI、生命周期持久化验证完成状态。"""

    def run_completion(self, scope: dict[str, str], previous_scope: str = "application", passed: bool = True, formal: bool = False) -> None:
        """模拟调试面板发送应用范围，但同一 thread 的已执行 Build 明确绑定产物。"""
        with tempfile.TemporaryDirectory() as workspace:
            plans = Path(workspace) / ".xcodeagent/plans"
            plans.mkdir(parents=True)
            (plans / "product-plan.json").write_text(json.dumps({"confirmation_status": "confirmed", "pages": [{"pageId": "one"}]}))
            (plans / "technical-plan.json").write_text(json.dumps({"confirmation_status": "confirmed", "api_contracts": [{"id": "api", "endpoints": [{"id": "get"}]}]}))
            lifecycle = create_application_lifecycle(application_id="test", application_name="测试")
            lifecycle.initialization.stage = ApplicationLifecycleStage.READY_FOR_WORKBENCH
            lifecycle.initialization.status = ApplicationLifecycleStatus.COMPLETED
            if formal:
                lifecycle.active_formal_revision = ActiveFormalRevision(
                    changeId="change", formalBranch="workbench_plan_revision", sourceThreadId="thread",
                    sourceRunId="source", request="正式修订", target={"type": "page", "pageId": "one"},
                    impactInteractionId="impact", planningThreadId="planning", status="building",
                )
            write_application_lifecycle(workspace, lifecycle)
            start_workbench_execution(workspace, scope=previous_scope,
                target_id="application" if previous_scope == "application" else scope["targetId"],
                page_id=None, thread_id="thread", run_id="previous", phase="unit_test",
                api_contract_id=scope.get("apiContractId"))
            update_workbench_execution(workspace, run_id="previous", phase="unit_test", status=WorkbenchExecutionStatus.FAILED)

            def unit_check(state: ProjectState) -> dict:
                """确认运行前已恢复真实目标，再提供确定性的单测门禁结果。"""
                self.assertEqual(state["build_execution_scope"], {"type": "application", "targetId": "application"} if formal else scope)
                return {"phase": "unit_test", "status": "completed", "unit_test_gate_passed": passed}

            builder = StateGraph(ProjectState)
            builder.add_node("unit_test", unit_check)
            builder.add_node("test_phase_confirmation", test_phase_confirmation)
            builder.add_edge(START, "unit_test")
            builder.add_edge("unit_test", "test_phase_confirmation")
            builder.add_edge("test_phase_confirmation", END)
            graph = builder.compile(checkpointer=InMemorySaver())
            graph.update_state({"configurable": {"thread_id": "thread"}}, {
                "workspace": workspace, "build_execution_scope": {"type": "application", "targetId": "application"},
                "build_execution_slice": {"scope": scope}, "build_summary": {"status": "completed"},
            }, as_node="unit_test")

            async def collect() -> list[dict]:
                """消费整个续跑流，不使用客户端完成计数作为事实。"""
                frames = [frame async for frame in build_workflow_ag_ui_stream(graph=graph, payload={
                    "threadId": "thread", "runId": "resumed",
                    "messages": [{"role": "user", "content": "从单测继续"}],
                    "forwardedProps": {"workspaceRoot": workspace,
                        "workflowDebug": {"enabled": True, "resumeFrom": "unit_test"},
                        "resumeExecutionRunId": "previous",
                        "buildExecutionScope": {"type": "application", "targetId": "application"}},
                })]
                return [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]

            with patch("app.protocols.workflow.request._project_plan_start_values", return_value={}):
                events = asyncio.run(collect())
            self.assertEqual(events[-1]["type"], "RUN_FINISHED")
            payloads = [event["value"] for event in events if event.get("name") == "application-lifecycle"]
            self.assertTrue(payloads)
            self.assertEqual(payloads[0]["activeExecutions"]["resumed"]["developmentPurpose"], None if formal else "initial")
            final = payloads[-1]
            self.assertEqual(final["testEntryGate"]["completed"], 1 if passed and not formal else 0)
            self.assertEqual(final["testEntryGate"]["total"], 2)
            if passed and not formal:
                self.assertEqual(len(final["testEntryGate"]["blockers"]), 1)
                other_type = "endpoint" if scope["type"] == "page" else "page"
                self.assertEqual(final["testEntryGate"]["blockers"][0]["type"], other_type)
                self.assertEqual(final["activeExecutions"]["resumed"]["status"], "awaiting_user")

    def test_application_debug_resume_completes_page_only(self) -> None:
        """页面完成后为 1/2，接口保持未完成。"""
        self.run_completion({"type": "page", "targetId": "one"})

    def test_application_debug_resume_completes_endpoint_only(self) -> None:
        """接口续跑保留完整身份，不把同应用页面一并完成。"""
        self.run_completion({"type": "endpoint", "targetId": "get", "apiContractId": "api"})

    def test_debug_revision_label_does_not_hide_initial_completion(self) -> None:
        """未完成产物的普通调试执行即使曾标为 revision，也按实际初次完成登记。"""
        self.run_completion({"type": "page", "targetId": "one"}, previous_scope="page")

    def test_unpassed_unit_gate_never_marks_completed(self) -> None:
        """恢复目标不等于完成，单测门未通过仍保持 0/2。"""
        self.run_completion({"type": "page", "targetId": "one"}, passed=False)

    def test_formal_revision_does_not_backfill_initial_completion(self) -> None:
        """正式修订继续使用已登记范围，不因旧 Build 切片补记初次完成。"""
        self.run_completion({"type": "page", "targetId": "one"}, formal=True)
