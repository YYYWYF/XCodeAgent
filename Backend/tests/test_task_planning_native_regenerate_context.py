"""Regenerate adapter 只从已领取的 Node Entry 读取原操作身份。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.execution_recovery import WorkflowReentryReason
from app.graph.nodes.task_planning_adapter import _run_regenerate_branch, _run_async_workflow_planning_adapter
from app.services.node_recovery_context import (
    NodeRecoveryContext, bind_node_recovery, bind_recovery_runtime,
)
from app.services.unit_generation_contracts import UnitGenerationPolicy
from tests.test_unit_generation_contracts import _policy_payload


class TaskPlanningNativeRegenerateContextTests(unittest.IsolatedAsyncioTestCase):
    """覆盖无业务 failed 快照和中断继续时的 adapter 操作交接。"""

    async def test_ordinary_interruption_selects_candidate_progress(self) -> None:
        """普通 DAG 中断重入向真实服务入口提供精确来源及独立证据类型。"""

        state = {"active_run_id": "child", "active_thread_id": "thread"}
        recovery = NodeRecoveryContext(source_run_id="source", execution_run_id="child",
            thread_id="thread", target_node="prepare_build_tasks", checkpoint_id="entry",
            reentry_reason=WorkflowReentryReason.INTERRUPTED_CONTINUE, entry_state=state)
        captured = {}

        async def service(*args, **kwargs):
            """捕获普通生成路径参数，不执行模型或写入用户工作区。"""

            captured.update(kwargs)
            raise RuntimeError("planning-service-called")

        with patch("app.graph.nodes.task_planning_adapter._assemble_planning_context",
                   return_value=SimpleNamespace(inputs=object())), \
             bind_recovery_runtime(recovery), bind_node_recovery(state, "prepare_build_tasks"):
            with self.assertRaisesRegex(RuntimeError, "planning-service-called"):
                await _run_async_workflow_planning_adapter(state,
                    policy=UnitGenerationPolicy(**_policy_payload()), settings=None,
                    generate_once=None, planning_service=service,
                    confirm_service=service, regenerate_service=service)
        self.assertEqual(captured["recovery_source_workflow_run_id"], "source")
        self.assertTrue(captured["recovery_interrupted"])

    async def test_verified_entry_reaches_operation_service_without_progress(self) -> None:
        """失败和中断都带原操作来源，并明确选择对应候选证据。"""

        action = {"action": "regenerate", "planning_run_id": "planning-A", "draft_digest": "a" * 64}
        scope = {"type": "application", "targetId": "application"}
        state = {
            "active_run_id": "child", "active_thread_id": "thread",
            "build_execution_scope": scope, "build_task_plan_confirmation": action,
        }
        policy = UnitGenerationPolicy(**_policy_payload())
        for reason, expect_candidate in (
            (WorkflowReentryReason.FAILURE_RETRY, True),
            (WorkflowReentryReason.INTERRUPTED_CONTINUE, True),
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
                self.assertEqual(captured[0]["recovery_interrupted"],
                                 reason is WorkflowReentryReason.INTERRUPTED_CONTINUE)
