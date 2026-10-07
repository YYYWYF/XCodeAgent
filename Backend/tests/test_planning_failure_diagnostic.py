"""验证中文规划诊断与同一执行归属，不使用模型原文猜测。"""

import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.protocols.workflow import build_workflow_ag_ui_stream
from app.services import planning_run as transitions
from app.services.dag_planning_orchestrator import DagPlanningError
from app.services.execution_failure_classifier import public_failure_diagnostic
from app.services.execution_recovery_projection import recovery_failure_diagnostic
from app.domain.execution_recovery import DurableExecutionStatus, ExecutionFailureEvidence, ExecutionFailureOrigin
from app.services.planning_failure_diagnostic import planning_failure_evidence
from app.services.planning_issues import ValidationIssue
from app.services.planning_run_contracts import PlanningRunProjection, UnitRoundHistory
from app.workspace.planning_run_documents import project_planning_run
from tests.planning_run_fixtures import AT, UNIT, exhausted, run


def failed_snapshot(code="RAW_CANDIDATE_TASK_ID_MISSING"):
    """从真实状态转换构造终态，并记录有明确错误码的历史轮次。"""

    state = exhausted(transitions.begin_generation(run(), at=AT))
    issue = ValidationIssue(
        code=code, level="unit", category="generation", unit_ids=(UNIT,),
        retry_unit_ids=(UNIT,), retryable=True, message="private-model-output",
    )
    unit = state.unit_states[UNIT].model_copy(update={
        "current_issues": (issue,), "generation_round": 2, "total_attempts": 6,
        "round_history": (UnitRoundHistory(
            generation_round=1, attempt_in_round=3, generation_status="round_exhausted",
            candidate_id=None, issues=(issue.model_copy(update={"code": "UNIT_CANDIDATE_OUTPUT_TRUNCATED"}),),
        ),),
    })
    state = state.model_copy(update={"unit_states": {UNIT: unit}, "global_repair_round": 1})
    failure = ValidationIssue(
        code="GLOBAL_REPAIR_LIMIT_EXHAUSTED", level="global", category="generation",
        unit_ids=(UNIT,), retryable=False, message="额度耗尽",
        details={"issues": [{"code": "GLOBAL_CANDIDATE_MISSING"}]},
    )
    return transitions.fail(state, failure, at=AT)


class PlanningFailureDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    """覆盖实时、轻量投影、未知原因和身份隔离。"""

    def test_structured_round_reasons_and_identity(self):
        """实时和磁盘投影得到一致中文，并且拒绝其他 Run/Thread。"""

        state = failed_snapshot()
        identity = {"source_run_id": state.workflow_run_id, "source_thread_id": state.thread_id}
        failure = planning_failure_evidence(state, **identity)
        projected = PlanningRunProjection.model_validate(project_planning_run(state))
        self.assertEqual(failure, planning_failure_evidence(projected, **identity))
        self.assertIn("自动修复后仍未通过检查", failure.user_message)
        self.assertIn("第 1 轮：模型输出达到长度限制，被截断", failure.diagnostic_message)
        self.assertIn("缺少必要的任务标识", failure.diagnostic_message)
        self.assertNotIn("private-model-output", failure.diagnostic_message)
        self.assertIsNone(planning_failure_evidence(state, **{**identity, "source_run_id": "other"}))
        self.assertIsNone(planning_failure_evidence(state, **{**identity, "source_thread_id": "other"}))

    def test_other_codes_and_unknown_reason(self):
        """格式、标识重复、归属冲突和未知错误各自给出有证据的说明。"""

        for code, reason in (
            ("RAW_CANDIDATE_JSON_MALFORMED", "数据格式不符合要求"),
            ("RAW_CANDIDATE_TASK_ID_DUPLICATE", "任务标识重复"),
            ("CANDIDATE_ENDPOINT_OWNER_CONFLICT", "归属冲突"),
            ("CANDIDATE_PATH_OUTSIDE_ALLOWED_SCOPE", "允许修改范围之外"),
            ("NEW_UNKNOWN_FAILURE", "未能确定具体原因"),
        ):
            with self.subTest(code=code):
                state = failed_snapshot(code)
                failure = planning_failure_evidence(
                    state, source_run_id=state.workflow_run_id, source_thread_id=state.thread_id,
                )
                self.assertIn(reason, failure.diagnostic_message)

    def test_refresh_adds_display_evidence_without_changing_durable_identity(self):
        """刷新仅补充同一执行的诊断，不修改失败记录，不读取其他执行的原因。"""

        state = failed_snapshot()
        original = ExecutionFailureEvidence(origin=ExecutionFailureOrigin.UNKNOWN, code="dagplanningerror")
        record = SimpleNamespace(
            execution_kind="workbench", status=DurableExecutionStatus.FAILED,
            workspace="/test", run_id=state.workflow_run_id, thread_id=state.thread_id,
            failure=original,
        )
        with patch("app.workspace.planning_run_documents.load_planning_run", return_value=project_planning_run(state)):
            diagnostic = recovery_failure_diagnostic(record)
            self.assertIn("自动修复后仍未通过检查", diagnostic["userMessage"])
            self.assertIs(record.failure, original)
            record.run_id = "another-run"
            self.assertNotIn("userMessage", recovery_failure_diagnostic(record))
        with patch("app.workspace.planning_run_documents.load_planning_run", side_effect=ValueError("bad file")):
            self.assertEqual(recovery_failure_diagnostic(record)["code"], "dagplanningerror")

    async def test_ag_ui_stream_keeps_current_diagnostic(self):
        """真实 AG-UI 失败帧和状态快照共享中文诊断，原文不泄漏。"""

        state = failed_snapshot()
        error = DagPlanningError((state.failure,), state)

        class FailingGraph:
            """在真实 Workflow 错误收口边界抛出规划失败。"""

            async def astream(self, *_args, **_kwargs):
                """模拟已终止的规划节点。"""

                raise error
                yield

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.protocols.workflow.runtime.resolve_current_node_entry_boundary",
            new=AsyncMock(return_value=SimpleNamespace(target_node="prepare_build_tasks")),
        ):
            frames = [frame async for frame in build_workflow_ag_ui_stream(
                graph=FailingGraph(), payload={
                    "threadId": state.thread_id, "runId": state.workflow_run_id,
                    "messages": [{"role": "user", "content": "生成执行计划"}],
                    "forwardedProps": {"workspaceRoot": directory, "sessionId": "session-1"},
                }, accept="text/event-stream",
            )]
        events = [json.loads(line.removeprefix("data: ")) for frame in frames
                  for line in frame.splitlines() if line.startswith("data: ")]
        custom = next(item for item in reversed(events) if item.get("name") == "workflow-run")
        diagnostic = custom["value"]["summary"]["failureDiagnostic"]
        self.assertEqual(diagnostic, public_failure_diagnostic(planning_failure_evidence(
            state, source_run_id=state.workflow_run_id, source_thread_id=state.thread_id,
        ), source_run_id=state.workflow_run_id))
        self.assertIn('"type":"RUN_ERROR"', "".join(frames))
        self.assertNotIn("private-model-output", "".join(frames))
