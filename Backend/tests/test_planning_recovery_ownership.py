"""规划确认更换 Run 后，最新 checkpoint 与旧生命周期 owner 的恢复一致性。"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.domain.execution_recovery import DurableExecutionRecord, DurableExecutionStatus, RecoveryExecutionError
from app.graph.state import ProjectState
from app.persistence.execution_recovery import insert_execution
from app.protocols.application_page_planning import _build_application_planning_recovery_projection
from app.protocols.execution_recovery import _resolve_action_source_run
from app.services.application_lifecycle import (
    create_application_lifecycle, load_application_lifecycle, write_application_lifecycle,
)
from app.services.business_node_retry import prepare_business_node_retry, retry_action_plan
from app.services.planning_recovery_ownership import planning_source_is_current
from app.services.retry_preparation import abandon_prepared_retry


class PlanningRecoveryOwnershipTests(unittest.IsolatedAsyncioTestCase):
    """用真实 checkpoint、执行记录和生命周期验证恢复入口及原生确认门。"""

    async def asyncSetUp(self) -> None:
        """构造生成 Run 仍为 owner，而确认 Run 已在同线程最新 checkpoint 失败的现场。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = str(Path(temporary.name).resolve())
        self.thread_id = "planning-thread"
        lifecycle = create_application_lifecycle(
            application_id="application", application_name="恢复测试",
            initialization_thread_id=self.thread_id, active_run_id="generation-run",
        )
        write_application_lifecycle(self.workspace, lifecycle, expected_revision=0)
        now = datetime.now(timezone.utc)
        self.owner = DurableExecutionRecord(
            run_id="generation-run", thread_id=self.thread_id, workspace=self.workspace,
            execution_kind="application_planning", workflow_scope="application_planning",
            first_node="technical_planning_generate", current_node="technical_planning_review",
            status="awaiting_user", started_at=now, updated_at=now,
        )
        self.source = self.owner.model_copy(update={
            "run_id": "confirmation-run", "status": DurableExecutionStatus.FAILED,
            "first_node": "technical_planning_confirm", "current_node": "technical_planning_confirm",
        })
        await insert_execution(self.owner)
        await insert_execution(self.source)

        def fail_confirmation(_state: ProjectState) -> dict[str, Any]:
            """保留真实失败任务，不调用模型或修改业务产物。"""
            raise ValueError("应用配置已变化")

        def review_again(_state: ProjectState) -> dict[str, Any]:
            """重试必须再次中断等待确认，不能重放旧的确认动作。"""
            interrupt({"artifact": "technical_plan", "message": "请重新审阅"})
            return {}

        builder = StateGraph(ProjectState)
        builder.add_node("technical_planning_confirm", fail_confirmation)
        builder.add_node("technical_planning_review", review_again)
        builder.add_conditional_edges(START, lambda state: state.get("resume_from") or "technical_planning_confirm")
        builder.add_edge("technical_planning_confirm", END)
        builder.add_edge("technical_planning_review", END)
        self.graph = builder.compile(checkpointer=InMemorySaver())
        self.config = {"configurable": {"thread_id": self.thread_id, "checkpoint_ns": ""}}
        with self.assertRaisesRegex(ValueError, "应用配置已变化"):
            await self.graph.ainvoke({
                "workspace": self.workspace, "active_run_id": self.source.run_id,
                "workflow_scope": "application_planning", "request": "创建聊天应用",
                "application_planning_interaction": {"action": "confirm"},
            }, self.config)

    async def test_ownership_timeout_preserves_source_and_allows_retry(self) -> None:
        """归属读取超时取消底层任务，保留原 owner，并允许随后重新读取同一失败现场。"""

        lifecycle = load_application_lifecycle(self.workspace)
        cancelled = asyncio.Event()

        async def blocked_read(_config: dict[str, Any]) -> None:
            """模拟阻塞的 checkpoint 读取，并记录平台超时后的取消交付。"""

            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        graph = SimpleNamespace(aget_state=blocked_read)
        with patch("app.services.retry_preparation.CHECKPOINT_PREPARATION_SECONDS", 0.01):
            with self.assertRaises(RecoveryExecutionError) as denied:
                await planning_source_is_current(self.source, graph, lifecycle)
        self.assertEqual(denied.exception.code, "RETRY_CHECKPOINT_UNAVAILABLE")
        self.assertIsInstance(denied.exception.__cause__, TimeoutError)
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        self.assertEqual(load_application_lifecycle(self.workspace), lifecycle)
        self.assertTrue(await planning_source_is_current(self.source, self.graph, lifecycle))

    async def test_projection_action_is_executable_with_a_stale_same_thread_owner(self) -> None:
        """投影签发的动作不能仅因旧生成 Run 尚在 lifecycle 中而立即过期。"""

        snapshot = await self.graph.aget_state(self.config)
        projection = await _build_application_planning_recovery_projection(
            workspace=self.workspace, thread_id=self.thread_id, graph=self.graph,
            snapshot=snapshot, source=self.source,
        )
        self.assertEqual(projection.classification, "ready_to_continue")
        plan = await retry_action_plan(self.source, self.graph)
        with patch("app.protocols.execution_recovery.application_planning_graph_for_request",
                   new=AsyncMock(return_value=self.graph)):
            resolved = await _resolve_action_source_run(
                workspace=self.workspace, incident_id=plan.incident_id,
                action_id=plan.primary_action.action_id,
            )
        self.assertEqual(resolved, self.source.run_id)

    async def test_retry_handoffs_stale_owner_and_returns_to_user_confirmation(self) -> None:
        """显式重试通过原 CAS 交接 owner，并清掉旧确认后停回原生审阅门。"""

        context = await prepare_business_node_retry(self.source, self.graph)
        try:
            self.assertEqual(load_application_lifecycle(self.workspace).active_run_id, context.new_run_id)
            self.assertEqual(context.fork_snapshot.values["application_planning_interaction"], {})
            self.assertEqual(context.fork_snapshot.values["request"], "创建聊天应用")
            result = await self.graph.ainvoke(context.fork_snapshot.values, context.fork_config)
            self.assertTrue(result.get("__interrupt__"))
        finally:
            await abandon_prepared_retry(
                workspace=self.workspace, source=self.source,
                before=context.retry_lifecycle_before_handoff, new_run_id=context.new_run_id,
                lease=context.workspace_lease,
            )

    async def test_newer_checkpoint_makes_the_old_action_stale(self) -> None:
        """最新线程现场已有其他 Run 时，旧动作不能借用仍未刷新的生命周期。"""

        plan = await retry_action_plan(self.source, self.graph)
        await self.graph.aupdate_state(self.config, {"active_run_id": "newer-run"})
        with patch("app.protocols.execution_recovery.application_planning_graph_for_request",
                   new=AsyncMock(return_value=self.graph)):
            with self.assertRaises(RecoveryExecutionError) as denied:
                await _resolve_action_source_run(
                    workspace=self.workspace, incident_id=plan.incident_id,
                    action_id=plan.primary_action.action_id,
                )
        self.assertEqual(denied.exception.code, "STALE_RECOVERY_ACTION")
        self.assertEqual(load_application_lifecycle(self.workspace).active_run_id, self.owner.run_id)

    async def test_live_or_unrelated_owner_cannot_be_replaced(self) -> None:
        """真实存活的 owner 与不同线程的 owner 均不得被最新失败 Run 抢占。"""

        for owner in (self.owner, self.owner.model_copy(update={"thread_id": "other-thread"})):
            with self.subTest(owner_thread=owner.thread_id), patch(
                "app.persistence.execution_recovery.get_execution", new=AsyncMock(return_value=owner),
            ), patch(
                "app.services.checkpoint_execution.execution_is_live", return_value=owner == self.owner,
            ):
                with self.assertRaises(RecoveryExecutionError) as denied:
                    await prepare_business_node_retry(self.source, self.graph)
                self.assertEqual(denied.exception.code, "RETRY_SOURCE_SUPERSEDED")
        self.assertEqual(load_application_lifecycle(self.workspace).active_run_id, self.owner.run_id)

    async def test_matching_lifecycle_owner_cannot_authorize_an_old_checkpoint(self) -> None:
        """生命周期恰好仍指向旧失败 Run，也不能恢复已经被新现场替代的动作。"""

        lifecycle = load_application_lifecycle(self.workspace)
        write_application_lifecycle(self.workspace, lifecycle.model_copy(update={"active_run_id": self.source.run_id}))
        await self.graph.aupdate_state(self.config, {"active_run_id": "newer-run"})
        plan = await retry_action_plan(self.source, self.graph)
        self.assertIsNone(plan.primary_action)
        self.assertEqual(plan.reason_code, "RETRY_SOURCE_SUPERSEDED")
