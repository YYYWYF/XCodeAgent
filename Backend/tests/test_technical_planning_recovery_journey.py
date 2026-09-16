"""Technical Planning 连续失败与恢复动作的真实生产链路回归测试。"""

from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.config import Settings
from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation,
    ApplicationPlanningRecoveryBoundary,
    application_planning_boundary_payload,
)
from app.domain.execution_recovery import (
    DurableExecutionStatus,
    RecoveryActionKind,
)
from app.graph.application_planning_workflow import (
    application_planning_graph_for_request,
)
from app.graph.nodes.planning import _attach_technical_plan_contracts
from app.persistence.checkpoints import (
    close_workflow_checkpointer_for_workspace,
)
from app.persistence.execution_recovery import (
    get_execution,
    get_node_entry_boundary,
    list_executions_for_thread,
    list_recovery_attempts_for_thread,
)
from app.protocols.execution_recovery import build_execution_recovery_ag_ui_stream
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.services.application_lifecycle import (
    create_application_lifecycle,
    persist_application_lifecycle_transition,
    write_application_lifecycle,
    load_application_lifecycle,
)
from app.services.application_planning_recovery_coordinator import (
    resolve_application_planning_recovery,
)
from app.services.product_plan import create_product_plan
from app.services.project_plan import create_technical_plan
from app.services.requirement_spec import create_requirement_spec
from app.services.execution_recovery_lineage import (
    RecoveryLineageState,
    resolve_recovery_lineage_head,
)
from app.workspace.plan_documents import technical_plan_json_path
from app.workspace.product_plan_documents import (
    write_confirmed_product_plan_documents,
)
from app.workspace.spec_documents import (
    write_confirmed_requirement_spec_document,
    write_ui_designs_json,
)


class FakeModelConnectionError(Exception):
    """提供可被真实 failure classifier 识别的模型 404 异常。"""

    __module__ = "openai"

    def __init__(self, *, model: str) -> None:
        """保存测试模型名称和生产 classifier 所需的 HTTP 状态。"""

        super().__init__("model not found")
        self.status_code = 404
        self.model = model


def _settings_for(model_name: str) -> Settings:
    """构造只用于测试的当前模型配置，不访问外部模型服务。"""

    return Settings(
        model_base_url="https://model.test.invalid/v1",
        model_api_key="test-key",
        model_name=model_name,
    )


class TechnicalPlanningRecoveryJourneyHarness:
    """保存一条使用真实 Graph、Runtime、Durable Store 的 TechnicalPlan journey。"""

    def __init__(self) -> None:
        """创建隔离工作区并准备正式上游产物、生命周期和 Graph 初始边界。"""

        self.temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_workspace.name)
        self.thread_id = "technical-planning-recovery-journey"
        self.project_id = "weather-app-recovery-journey"
        self.request = "创建一个天气预报应用"
        self.source_run_id = "technical-planning-run-a"
        self.model_name = "DeepSeek"
        self.called_models: list[str] = []
        self.graph: Any | None = None
        self._seed_formal_artifacts()
        self._seed_lifecycle()

    def close(self) -> None:
        """关闭工作区专属 checkpoint 连接并释放临时工作区。"""

        self.temporary_workspace.cleanup()

    def _seed_formal_artifacts(self) -> None:
        """用现有生产 helper 写入 Technical Planning journey 所需的正式上游 JSON。"""

        state = {"workspace": str(self.workspace)}
        requirement_spec = create_requirement_spec(self.request)
        requirement_spec["confirmation_status"] = "confirmed"
        write_confirmed_requirement_spec_document(state, requirement_spec)

        product_plan = create_product_plan(requirement_spec)
        product_plan["confirmation_status"] = "confirmed"
        write_confirmed_product_plan_documents(state, product_plan)

        ui_designs = {
            "schema_version": "ui-manifest.v3",
            "confirmation_status": "skipped",
            "pages": [],
        }
        write_ui_designs_json(state, ui_designs)

        base_candidate = create_technical_plan(
            {
                **requirement_spec,
                "confirmed_product_plan": product_plan,
            },
            agent_plan={"entities": deepcopy(requirement_spec.get("entities", []))},
        )
        self.candidate = _attach_technical_plan_contracts(
            {
                **state,
                "workflow_scope": "application_planning",
                "requirement_spec": requirement_spec,
                "product_plan": product_plan,
                "ui_designs": ui_designs,
            },
            base_candidate,
        )
        self.candidate["confirmation_status"] = "pending_user_confirmation"
        self.requirement_spec = requirement_spec
        self.product_plan = product_plan
        self.ui_designs = ui_designs

    def _seed_lifecycle(self) -> None:
        """通过真实生命周期状态机把 source A 放入 TechnicalPlan 生成阶段。"""

        lifecycle = create_application_lifecycle(
            application_id=self.project_id,
            application_name="天气预报应用",
            initialization_thread_id=self.thread_id,
            active_run_id=self.source_run_id,
        )
        write_application_lifecycle(self.workspace, lifecycle, expected_revision=0)
        for stage, status in (
            (
                ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                ApplicationLifecycleStatus.RUNNING,
            ),
            (
                ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
                ApplicationLifecycleStatus.RUNNING,
            ),
            (
                ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
                ApplicationLifecycleStatus.RUNNING,
            ),
            (
                ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
                ApplicationLifecycleStatus.AWAITING_USER,
            ),
            (
                ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
                ApplicationLifecycleStatus.RUNNING,
            ),
        ):
            lifecycle = persist_application_lifecycle_transition(
                self.workspace,
                stage=stage,
                status=status,
                active_run_id=self.source_run_id,
            )

    async def initialize_graph(self) -> None:
        """使用生产 Graph 工厂写入正式 INPUT_COMMITTED checkpoint 初始边界。"""

        self.graph = await application_planning_graph_for_request(
            workspace=str(self.workspace),
            project_id=self.project_id,
        )
        await self.graph.aupdate_state(
            {"configurable": {"thread_id": self.thread_id}},
            {
                "workspace": str(self.workspace),
                "project_id": self.project_id,
                "workflow_scope": "application_planning",
                "request": self.request,
                "application_name": "天气预报应用",
                "active_thread_id": self.thread_id,
                "active_run_id": self.source_run_id,
                "requirement_spec": self.requirement_spec,
                "product_plan": self.product_plan,
                "ui_designs": self.ui_designs,
                "technical_plan": {},
                "application_planning_recovery_boundary": application_planning_boundary_payload(
                    operation_id=f"technical-plan-{self.source_run_id}",
                    operation=ApplicationPlanningOperation.INITIAL,
                    boundary=ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED,
                    request=self.request,
                ),
                "application_planning_interaction": {},
                "technical_plan_candidate": {},
                "technical_plan_candidate_sha256": "",
                "resume_from": "technical_planning_begin",
                "selected_skill_names": [],
                "timeline": [],
                "phase": "technical_planning_begin",
                "status": "running",
            },
        )

    def current_settings(self) -> Settings:
        """返回模型 fake 当前应读取的 Settings 快照。"""

        return _settings_for(self.model_name)

    def fake_technical_plan_generation(
        self,
        _state: dict[str, Any],
        _requirement_spec: dict[str, Any],
        _existing_plan: dict[str, Any] | None,
        *,
        initial_errors: list[str] | None = None,
    ) -> tuple[dict[str, Any] | None, list[str], dict[str, Any] | None]:
        """只替代模型调用，按当前 Settings 产生真实 classifier 可识别的 404 或合法计划。"""

        del initial_errors
        model_name = Settings.from_env().model_name
        self.called_models.append(model_name)
        if model_name != "working-model":
            raise FakeModelConnectionError(model=model_name)
        return deepcopy(self.candidate), [], None

    async def run_initial(self) -> None:
        """经由生产 Workflow Runtime 执行 source A，并保留完整 AG-UI 流。"""

        if self.graph is None:
            raise AssertionError("journey graph has not been initialized")
        payload = {
            "threadId": self.thread_id,
            "runId": self.source_run_id,
            "projectId": self.project_id,
            "request": self.request,
            "resume_from": "technical_planning_begin",
            "forwardedProps": {
                "workspaceRoot": str(self.workspace),
                "workflowScope": "application_planning",
            },
        }
        _ = [
            frame
            async for frame in build_workflow_ag_ui_stream(
                graph=self.graph,
                payload=payload,
            )
        ]

    async def current_head(self) -> Any:
        """解析当前线程唯一 canonical lineage head，禁止测试猜测 child runId。"""

        resolution = await resolve_recovery_lineage_head(
            self.workspace,
            thread_id=self.thread_id,
            execution_kind="application_planning",
        )
        if resolution.head is None:
            raise AssertionError(f"missing canonical recovery head: {resolution.state}")
        return resolution.head

    async def resolve_action(self) -> tuple[Any, Any, dict[str, Any]]:
        """通过真实 Recovery Projection/Coordinator 读取当前 Backend action 身份。"""

        if self.graph is None:
            raise AssertionError("journey graph has not been initialized")
        source = await self.current_head()
        snapshot = await self.graph.aget_state(
            {"configurable": {"thread_id": self.thread_id}}
        )
        projection = await resolve_application_planning_recovery(
            workspace=str(self.workspace),
            thread_id=self.thread_id,
            graph=self.graph,
            snapshot=snapshot,
            lifecycle=load_application_lifecycle(self.workspace),
            source=source,
        )
        action_plan = projection.recovery_action_plan
        if not isinstance(action_plan, dict):
            raise AssertionError("recovery projection did not issue an action plan")
        primary_action = action_plan.get("primaryAction")
        if not isinstance(primary_action, dict):
            raise AssertionError("recovery projection did not issue a primary action")
        return source, projection, action_plan

    async def execute_action(self, action_plan: dict[str, Any]) -> Any:
        """经由公开 execution-recovery execute 语义执行 Backend 签发的 action。"""

        primary_action = action_plan.get("primaryAction")
        if not isinstance(primary_action, dict):
            raise AssertionError("action plan has no primary action")
        _ = [
            frame
            async for frame in build_execution_recovery_ag_ui_stream(
                payload={
                    "forwardedProps": {
                        "workspaceRoot": str(self.workspace),
                        "executionRecovery": {
                            "action": "execute",
                            "incidentId": action_plan["incidentId"],
                            "actionId": primary_action["actionId"],
                        },
                    }
                }
            )
        ]
        return await self.current_head()


class TechnicalPlanningRecoveryJourneyTests(unittest.IsolatedAsyncioTestCase):
    """锁定 Technical Planning 连续失败、降级和再次恢复的生产组合链路。"""

    async def asyncSetUp(self) -> None:
        """创建隔离 journey harness，并安排 checkpoint 与临时目录清理。"""

        self.harness = TechnicalPlanningRecoveryJourneyHarness()
        self.addAsyncCleanup(self._cleanup_harness)

    async def _cleanup_harness(self) -> None:
        """先关闭工作区 SQLite 连接，再释放临时目录。"""

        await close_workflow_checkpointer_for_workspace(
            workspace=str(self.harness.workspace),
            project_id=self.harness.project_id,
        )
        self.harness.close()

    async def test_failed_technical_planning_retries_exact_generation_node(self) -> None:
        """Technical Planning 生成异常只能从精确失败节点重入并使用当前模型。"""

        with (
            patch(
                "app.config.Settings.from_env",
                side_effect=self.harness.current_settings,
            ),
            patch(
                "app.graph.nodes.planning._generate_valid_technical_plan",
                side_effect=self.harness.fake_technical_plan_generation,
            ),
        ):
            await self.harness.initialize_graph()
            await self.harness.run_initial()
            run_a = await get_execution(
                self.harness.workspace,
                self.harness.source_run_id,
            )
            self.assertIsNotNone(run_a)
            assert run_a is not None
            self.assertEqual(run_a.status, DurableExecutionStatus.FAILED)
            self.assertEqual(run_a.current_node, "technical_planning_generate")
            self.assertIsNotNone(run_a.failure)
            assert run_a.failure is not None
            self.assertEqual(run_a.failure.http_status, 404)
            self.assertEqual(run_a.failure.model, "DeepSeek")

            source_a, projection_a, action_a = await self.harness.resolve_action()
            self.assertEqual(projection_a.classification, "ready_to_continue")
            self.assertEqual(action_a["status"], "recoverable")
            self.assertEqual(
                action_a["primaryAction"]["kind"],
                RecoveryActionKind.RETRY_FAILED_NODE.value,
            )
            self.assertTrue(action_a["incidentId"])
            self.assertTrue(action_a["primaryAction"]["actionId"])

            self.harness.model_name = "working-model"
            run_b = await self.harness.execute_action(action_a)
            self.assertNotEqual(run_b.run_id, source_a.run_id)
            self.assertEqual(run_b.first_node, "technical_planning_generate")
            self.assertNotEqual(run_b.status, DurableExecutionStatus.FAILED)

            lifecycle = load_application_lifecycle(self.harness.workspace)
            self.assertIsNotNone(lifecycle)
            assert lifecycle is not None
            self.assertEqual(
                lifecycle.initialization.stage,
                ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            )
            self.assertEqual(
                lifecycle.initialization.status,
                ApplicationLifecycleStatus.AWAITING_USER,
            )
            self.assertEqual(lifecycle.active_run_id, run_b.run_id)
            self.assertTrue(
                technical_plan_json_path(
                    {"workspace": str(self.harness.workspace)}
                ).is_file()
            )
            self.assertEqual(
                self.harness.called_models,
                ["DeepSeek", "working-model"],
            )

            resolution = await resolve_recovery_lineage_head(
                self.harness.workspace,
                thread_id=self.harness.thread_id,
                execution_kind="application_planning",
            )
            self.assertEqual(resolution.state, RecoveryLineageState.AWAITING_USER_HEAD)
            self.assertIsNotNone(resolution.head)
            assert resolution.head is not None
            self.assertEqual(resolution.head.run_id, run_b.run_id)
            self.assertEqual(
                resolution.head.status,
                DurableExecutionStatus.AWAITING_USER,
            )

            attempts = await list_recovery_attempts_for_thread(
                self.harness.workspace,
                thread_id=self.harness.thread_id,
            )
            self.assertEqual(len(attempts), 1)
            native_attempt = next(
                attempt
                for attempt in attempts
                if attempt.source_run_id == source_a.run_id
            )
            self.assertIsNotNone(native_attempt.source_checkpoint_id)
            self.assertNotIn("strategy", native_attempt.model_fields)
            self.assertNotIn("source_authority_kind", native_attempt.model_fields)

            executions = await list_executions_for_thread(
                self.harness.workspace,
                thread_id=self.harness.thread_id,
                execution_kind="application_planning",
            )
            self.assertEqual(
                {execution.run_id for execution in executions},
                {source_a.run_id, run_b.run_id},
            )

    async def test_g7_repeated_failure_retries_from_canonical_child_checkpoint(self) -> None:
        """G7 锁定 A→B 失败后只从 B 的同 Node checkpoint 再次恢复。"""

        with (
            patch(
                "app.config.Settings.from_env",
                side_effect=self.harness.current_settings,
            ),
            patch(
                "app.graph.nodes.planning._generate_valid_technical_plan",
                side_effect=self.harness.fake_technical_plan_generation,
            ),
        ):
            await self.harness.initialize_graph()
            await self.harness.run_initial()
            source_a, _projection_a, action_a = await self.harness.resolve_action()

            self.harness.model_name = "MiMo"
            run_b = await self.harness.execute_action(action_a)
            source_b, _projection_b, action_b = await self.harness.resolve_action()
            self.assertEqual(source_b.run_id, run_b.run_id)
            self.assertEqual(source_b.status, DurableExecutionStatus.FAILED)
            self.assertEqual(source_b.current_node, source_a.current_node)
            self.assertEqual(
                action_b["primaryAction"]["kind"],
                RecoveryActionKind.RETRY_FAILED_NODE.value,
            )

            attempts_after_b = await list_recovery_attempts_for_thread(
                self.harness.workspace,
                thread_id=self.harness.thread_id,
            )
            self.assertEqual(len(attempts_after_b), 1)
            attempt_from_a = attempts_after_b[0]
            boundary_b = await get_node_entry_boundary(
                self.harness.workspace,
                source_run_id=run_b.run_id,
                thread_id=self.harness.thread_id,
                target_node=source_b.current_node or "",
            )
            self.assertIsNotNone(boundary_b)
            assert boundary_b is not None

            self.harness.model_name = "working-model"
            run_c = await self.harness.execute_action(action_b)
            self.assertNotEqual(run_c.run_id, run_b.run_id)
            self.assertEqual(run_b.first_node, "technical_planning_generate")
            self.assertEqual(run_c.first_node, source_b.current_node)
            self.assertNotEqual(run_c.status, DurableExecutionStatus.FAILED)

            lifecycle = load_application_lifecycle(self.harness.workspace)
            self.assertIsNotNone(lifecycle)
            assert lifecycle is not None
            self.assertEqual(
                lifecycle.initialization.stage,
                ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            )
            self.assertEqual(lifecycle.active_run_id, run_c.run_id)
            self.assertEqual(
                self.harness.called_models,
                ["DeepSeek", "MiMo", "working-model"],
            )

            attempts = await list_recovery_attempts_for_thread(
                self.harness.workspace,
                thread_id=self.harness.thread_id,
            )
            self.assertEqual(len(attempts), 2)
            attempt_from_b = next(
                attempt for attempt in attempts if attempt.source_run_id == run_b.run_id
            )
            self.assertEqual(attempt_from_b.source_run_id, source_b.run_id)
            self.assertEqual(
                attempt_from_b.source_checkpoint_id,
                boundary_b.checkpoint_id,
            )
            self.assertNotEqual(
                attempt_from_b.source_checkpoint_id,
                attempt_from_a.source_checkpoint_id,
            )
            attempts_by_source = {attempt.source_run_id: attempt for attempt in attempts}
            self.assertIsNotNone(
                attempts_by_source[self.harness.source_run_id].source_checkpoint_id
            )
            self.assertIsNotNone(attempts_by_source[run_b.run_id].source_checkpoint_id)
            for attempt in attempts:
                self.assertIsNotNone(attempt.source_checkpoint_id)

            executions = await list_executions_for_thread(
                self.harness.workspace,
                thread_id=self.harness.thread_id,
                execution_kind="application_planning",
            )
            self.assertEqual(
                {execution.run_id for execution in executions},
                {
                    self.harness.source_run_id,
                    run_b.run_id,
                    run_c.run_id,
                },
            )


__all__ = ["TechnicalPlanningRecoveryJourneyTests"]
