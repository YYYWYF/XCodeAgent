"""Regenerate adapter 只从已领取的 Node Entry 读取原操作身份。"""

from __future__ import annotations

import unittest

from app.domain.execution_recovery import WorkflowReentryReason
from app.graph.nodes.task_planning_adapter import _run_regenerate_branch
from app.services.node_recovery_context import (
    NodeRecoveryContext, bind_node_recovery, bind_recovery_runtime,
)
from app.services.unit_generation_contracts import UnitGenerationPolicy
from tests.test_unit_generation_contracts import _policy_payload


class TaskPlanningNativeRegenerateContextTests(unittest.IsolatedAsyncioTestCase):
    """覆盖无业务 failed 快照和中断继续时的 adapter 操作交接。"""

    async def test_verified_entry_reaches_operation_service_without_progress(self) -> None:
        """两种 reason 都带原操作来源，Candidate 仅在失败重试时可用。"""

        action = {"action": "regenerate", "planning_run_id": "planning-A", "draft_digest": "a" * 64}
        scope = {"type": "application", "targetId": "application"}
        state = {
            "active_run_id": "child", "active_thread_id": "thread",
            "build_execution_scope": scope, "build_task_plan_confirmation": action,
        }
        policy = UnitGenerationPolicy(**_policy_payload())
        for reason, expect_candidate in (
            (WorkflowReentryReason.FAILURE_RETRY, True),
            (WorkflowReentryReason.INTERRUPTED_CONTINUE, False),
        ):
            with self.subTest(reason=reason):
                captured: list[dict] = []

                async def operation_service(_state, **kwargs):
                    """捕获 adapter 真实交给操作恢复服务的参数。"""

                    captured.append(kwargs)
                    raise RuntimeError("operation-service-called")

                recovery = NodeRecoveryContext(
                    source_run_id="source", execution_run_id="child",
                    thread_id="thread", target_node="prepare_build_tasks",
                    checkpoint_id="entry", reentry_reason=reason,
                    entry_state={**state, "active_run_id": "source"},
                    source_lineage_run_ids=("source", "original"),
                )
                with bind_recovery_runtime(recovery), bind_node_recovery(state, "prepare_build_tasks"):
                    with self.assertRaisesRegex(RuntimeError, "operation-service-called"):
                        await _run_regenerate_branch(
                            state, action, policy=policy, settings=None,
                            generate_once=None, regenerate_service=operation_service,
                        )
                self.assertEqual(len(captured), 1)
                self.assertEqual(captured[0]["recovery_source_workflow_run_id"], "source")
                self.assertEqual(captured[0]["recovery_lineage_run_ids"], ("source", "original"))
                self.assertEqual(captured[0]["reuse_recovery_candidate"], expect_candidate)
