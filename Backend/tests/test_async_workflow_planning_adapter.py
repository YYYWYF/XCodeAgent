"""T11.6.3 async Workflow Planning adapter 的 Graph 级集成测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from app.config import Settings
from app.domain.execution_recovery import WorkflowReentryReason
from app.domain.application_lifecycle import (
    ApplicationLifecycleStage, ApplicationLifecycleStatus,
    PendingInteractionType, WorkbenchExecutionStatus,
)
from app.graph.nodes.task_planning_adapter import (
    create_async_workflow_planning_adapter,
    production_unit_generation_policy,
)
from app.services.build_task_planning_service import run_mainline_planning
from app.services.application_lifecycle import (
    create_application_lifecycle, start_workbench_execution,
    update_workbench_execution, write_application_lifecycle,
)
from app.services.execution_recovery import (
    observe_execution_failed, observe_execution_started, observe_node_started,
)
from app.services.execution_recovery_action_planner import plan_failed_node_reentry_action
from app.services.execution_recovery_executor import prepare_native_recovery
from app.services.execution_lease_heartbeat import stop_execution_heartbeat
from app.services.workflow_reentry import FailureTargetResolver, InterruptedTargetResolver
from app.persistence.execution_recovery import get_execution, mark_execution_interrupted
from app.protocols.workflow.lifecycle import begin_workflow_lifecycle, fail_workflow_lifecycle
from app.services.node_recovery_context import (
    NodeRecoveryContext,
    bind_node_recovery,
    bind_recovery_runtime,
    current_node_recovery_context,
)
from app.graph.workflow import build_graph
from app.services.planning_frozen import plain_json
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from app.services.unit_generation import UnitGenerationInfrastructureError
from app.workspace.planning_run_documents import load_planning_run
from app.workspace.task_documents import (
    build_task_plan_pending_json_path,
    load_build_task_plan_json,
    load_pending_build_task_plan,
    validate_pending_self_digest,
    write_build_task_plan_json,
)
from app.services.dag_planning_regeneration import regenerate_pending_build_task_plan
from tests.dag_planning_baseline_fixtures import (
    confirmed_baseline,
    execution_scope,
    formal_artifacts,
    project_plan,
    write_confirmed_endpoint_designs,
    workspace_snapshot,
    write_json,
)
from tests.dag_planning_orchestrator_fixtures import model_tasks
from tests.test_build_task_reuse_workspace import _ready_template
from tests.test_unit_generation_contracts import _policy_payload


ARTIFACT_PATHS = {
    "requirement_spec": ".devagentstudio/specs/requirement-spec.json",
    "product_plan": ".devagentstudio/plans/product-plan.json",
    "ui_designs": ".devagentstudio/specs/ui-designs.json",
    "technical_plan": ".devagentstudio/plans/technical-plan.json",
}


class AsyncWorkflowPlanningAdapterTests(unittest.IsolatedAsyncioTestCase):
    """验证真实 Graph await 链、Pending 投影和取消传播。"""

    def setUp(self) -> None:
        """创建固定 Unit policy，并为每个测试隔离临时工作区。"""

        self.policy = UnitGenerationPolicy(**_policy_payload())
        self.generated_jobs = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        self.plan = project_plan()
        for key, payload in formal_artifacts(self.plan).items():
            write_json(self.workspace, ARTIFACT_PATHS[key], payload)
        write_confirmed_endpoint_designs(self.workspace, self.plan)
        self.snapshot_path = write_json(
            self.workspace,
            ".devagentstudio/cache/workspace-snapshot.json",
            workspace_snapshot(),
        )

    def test_production_unit_generation_policy_enables_only_sdk_retry(self) -> None:
        """production policy 开启 SDK max_retries=2，同时保持 Local、timeout、token 和 reader 预算。"""

        policy = production_unit_generation_policy()

        self.assertEqual(policy.local_max_attempts, 3)
        self.assertEqual(policy.model_max_retries, 2)
        self.assertEqual(policy.model_max_tokens, 4096)
        self.assertEqual(policy.request_timeout, 120.0)
        self.assertEqual(policy.unit_session_timeout, 600.0)
        self.assertEqual(policy.model_turn_limit, 8)
        self.assertEqual(dict(policy.frozen_contract_read_limits), {
            "max_reads": 24,
            "max_total_bytes": 2_000_000,
            "max_bytes_per_read": 200_000,
        })

    def test_production_policy_binds_configured_unit_token_budget(self) -> None:
        """production policy 必须把 DAG Settings 的 token budget 绑定到 Unit Policy。"""

        settings = Settings(
            model_base_url="https://example.com/v1",
            model_api_key="test-key",
            model_name="test-model",
            dag_unit_max_tokens=8192,
        )

        policy = production_unit_generation_policy(settings=settings)

        self.assertEqual(policy.model_max_tokens, 8192)

    def test_default_adapter_passes_settings_to_production_policy(self) -> None:
        """默认 Adapter 必须用传入 Settings 创建 production policy，而不是丢弃配置。"""

        settings = Settings(
            model_base_url="https://example.com/v1",
            model_api_key="test-key",
            model_name="test-model",
            dag_unit_max_tokens=8192,
        )

        with patch(
            "app.graph.nodes.task_planning_adapter.production_unit_generation_policy",
            wraps=production_unit_generation_policy,
        ) as policy_factory:
            create_async_workflow_planning_adapter(settings=settings)

        policy_factory.assert_called_once_with(settings=settings)

    def _state(self, scope: dict[str, str]) -> dict:
        """构造与 Workflow runtime 一致的 generation 分支输入。"""

        return {
            "resume_from": "prepare_build_tasks",
            "workspace": str(self.workspace),
            "project_plan": self.plan,
            "workspace_snapshot_path": str(self.snapshot_path),
            "build_execution_scope": scope,
            "owner_session_id": "session-async-adapter",
            "active_run_id": "workflow-async-adapter",
            "active_thread_id": "thread-async-adapter",
        }

    async def test_prepare_wrapper_sync_async_and_exception_context(self) -> None:
        """真实 Graph 对四种注入返回路径均只调用一次并在退出后撤销上下文。"""

        for asynchronous in (False, True):
            for failing in (False, True):
                with self.subTest(asynchronous=asynchronous, failing=failing):
                    state = {**self._state(execution_scope()), "workspace_revision": "ready"}
                    seen: list[str] = []
                    context = NodeRecoveryContext(
                        source_run_id="source", execution_run_id=state["active_run_id"],
                        thread_id=state["active_thread_id"], target_node="prepare_build_tasks",
                        checkpoint_id="entry", reentry_reason=WorkflowReentryReason.FAILURE_RETRY,
                    )

                    def node(_: dict) -> dict:
                        """同步注入节点读取一次作用域，按测试用例返回或抛错。"""

                        seen.append(current_node_recovery_context().source_run_id)
                        if failing:
                            raise RuntimeError("injected")
                        return {"phase": "prepare_build_tasks", "status": "requires_user_input"}

                    async def async_node(value: dict) -> dict:
                        """异步注入节点跨 await 后仍读取同一个可信上下文。"""

                        await asyncio.sleep(0)
                        return node(value)

                    graph = build_graph(
                        checkpointer=InMemorySaver(),
                        prepare_build_tasks_node=async_node if asynchronous else node,
                    )
                    with bind_recovery_runtime(context):
                        if failing:
                            with self.assertRaisesRegex(RuntimeError, "injected"):
                                await graph.ainvoke(state, config={"configurable": {"thread_id": f"wrapper-{asynchronous}-{failing}"}})
                        else:
                            await graph.ainvoke(state, config={"configurable": {"thread_id": f"wrapper-{asynchronous}-{failing}"}})
                        self.assertIsNone(current_node_recovery_context())
                    self.assertEqual(seen, ["source"])

    async def _generate(self, job, **_: object) -> UnitGenerationAttemptResult:
        """让真实 Scheduler/Assembly/Global 链消费合法的逐 Unit Candidate。"""

        self.generated_jobs.append(job)
        tasks = model_tasks(job)
        return UnitGenerationAttemptResult(
            identity=job.identity,
            input_fingerprint=job.context.input_fingerprint,
            raw_response=json.dumps({"tasks": tasks}),
            tasks=tasks,
        )

    async def _native_regenerate_round(self, *, interrupted: bool) -> None:
        """从真实 Pending 经过 Graph、Durable 记录和 Native fork 恢复 Regenerate。"""

        scope = execution_scope(name="orders")
        write_build_task_plan_json(self._state(scope), confirmed_baseline(self.plan, scope))
        saver = InMemorySaver()
        model_calls = 0
        service_calls = 0
        fail_source = False

        async def generate(job, **kwargs):
            """只替换外部模型，在旧 Pending 消费后注入一次逃逸故障。"""

            nonlocal model_calls
            model_calls += 1
            if fail_source:
                if interrupted:
                    raise InterruptedError("backend interrupted after consume")
                raise UnitGenerationInfrastructureError(
                    identity=job.identity, stage="model_invoke",
                    cause=RuntimeError("regenerate model failed"),
                )
            return await self._generate(job, **kwargs)

        async def observe_service(*args, **kwargs):
            """计数但不替换 production Regenerate 服务。"""

            nonlocal service_calls
            service_calls += 1
            return await regenerate_pending_build_task_plan(*args, **kwargs)

        adapter = create_async_workflow_planning_adapter(
            policy=self.policy, generate_once=generate,
            regenerate_service=observe_service,
        )
        graph = build_graph(checkpointer=saver, prepare_build_tasks_node=adapter)
        seed_thread = "regenerate-seed"
        with patch(
            "app.graph.nodes.task_planning_adapter.load_template_state",
            return_value=_ready_template(self.workspace),
        ):
            await graph.ainvoke(self._state(scope), config={"configurable": {"thread_id": seed_thread}})
        old_pending = load_pending_build_task_plan(self._state(scope))
        old_identity = validate_pending_self_digest(old_pending)
        source_run_id = "regenerate-source-interrupted" if interrupted else "regenerate-source-failed"
        thread_id = seed_thread
        lifecycle = create_application_lifecycle(
            application_id="native-regenerate", application_name="Native Regenerate",
        )
        lifecycle = lifecycle.model_copy(update={
            "initialization": lifecycle.initialization.model_copy(update={
                "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                "status": ApplicationLifecycleStatus.COMPLETED,
            }),
        })
        write_application_lifecycle(self.workspace, lifecycle)
        start_workbench_execution(
            self.workspace, scope="page", target_id="orders", page_id="orders",
            thread_id=seed_thread, run_id="workflow-async-adapter",
            phase="prepare_build_tasks", owner_session_id="session-async-adapter",
        )
        waiting = update_workbench_execution(
            self.workspace, run_id="workflow-async-adapter", phase="prepare_build_tasks",
            status=WorkbenchExecutionStatus.AWAITING_USER,
            pending_type=PendingInteractionType.TASK_PLAN_CONFIRMATION,
            pending_payload={"mode": "build_task_plan_confirmation"},
        )
        interaction = waiting.active_executions["workflow-async-adapter"].pending_interaction
        assert interaction is not None
        begin_workflow_lifecycle(
            {
                "workspace": str(self.workspace),
                "resume_values": {
                    "owner_session_id": "session-async-adapter",
                    "build_execution_scope": scope,
                    "resume_execution_run_id": "workflow-async-adapter",
                    "lifecycle_interaction_submission": {
                        "runId": "workflow-async-adapter", "id": interaction.id,
                        "basedOnRevision": interaction.based_on_revision,
                    },
                },
            },
            thread_id=thread_id, run_id=source_run_id, phase="prepare_build_tasks",
        )
        source_state = {
            **self._state(scope), "active_run_id": source_run_id,
            "active_thread_id": thread_id,
            "build_task_plan_confirmation": {
                "action": "regenerate",
                "planning_run_id": old_identity.planning_run_id,
                "draft_digest": old_identity.draft_digest,
            },
        }
        await observe_execution_started(
            workspace=str(self.workspace), project_id=None, thread_id=thread_id,
            run_id=source_run_id, workflow_scope="application",
            first_node="prepare_build_tasks",
        )
        await observe_node_started(
            workspace=str(self.workspace), run_id=source_run_id, thread_id=thread_id,
            workflow_scope="application", node_name="prepare_build_tasks",
        )
        fail_source = True
        with patch(
            "app.graph.nodes.task_planning_adapter.load_template_state",
            return_value=_ready_template(self.workspace),
        ):
            with self.assertRaises((InterruptedError, RuntimeError)) as raised:
                await graph.ainvoke(source_state, config={"configurable": {"thread_id": thread_id}})
        self.assertIsNone(load_pending_build_task_plan(source_state))
        fact_path = (
            self.workspace / ".devagentstudio" / "runtime" / "dag-regeneration"
            / f"{old_identity.draft_digest}.json"
        )
        self.assertEqual(json.loads(fact_path.read_text())["phase"], "consumed")
        source_snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        self.assertNotEqual(source_snapshot.values.get("status"), "failed")
        if interrupted:
            source = await mark_execution_interrupted(
                workspace=self.workspace, run_id=source_run_id,
                interrupted_at=datetime.now(timezone.utc),
            )
            assert source is not None
            # 重建 Graph/adapter 实例，只沿既存 checkpoint 和磁盘操作事实继续。
            adapter = create_async_workflow_planning_adapter(
                policy=self.policy, generate_once=generate,
                regenerate_service=observe_service,
            )
            graph = build_graph(checkpointer=saver, prepare_build_tasks_node=adapter)
            resolution = await InterruptedTargetResolver().resolve(
                workspace=str(self.workspace), source=source, graph=graph,
            )
            self.assertEqual(resolution.kind, "continue")
            reentry = resolution.reentry_plan
            self.assertEqual(reentry.reason, WorkflowReentryReason.INTERRUPTED_CONTINUE)
        else:
            fail_workflow_lifecycle(str(self.workspace), run_id=source_run_id,
                                    phase="prepare_build_tasks", error=raised.exception)
            await observe_execution_failed(
                workspace=str(self.workspace), run_id=source_run_id,
                thread_id=thread_id, workflow_scope="application",
                exception=raised.exception, authoritative_node="prepare_build_tasks",
            )
            source = await get_execution(self.workspace, source_run_id)
            assert source is not None
            reentry = await FailureTargetResolver().resolve(
                workspace=str(self.workspace), source=source, graph=graph,
            )
            action = plan_failed_node_reentry_action(
                workspace=str(self.workspace), source=source, reentry_plan=reentry,
            )
            self.assertIsNotNone(action.primary_action)
        assert reentry is not None
        context = await prepare_native_recovery(
            workspace=str(self.workspace), source_run_id=source_run_id,
            graph=graph, reentry_plan=reentry,
        )
        self.assertIsNone(context.internal_progress)
        self.assertEqual(context.entry_state["build_task_plan_confirmation"],
                         source_state["build_task_plan_confirmation"])
        fail_source = False
        before_resume_calls = service_calls
        try:
            with (
                patch("app.graph.nodes.task_planning_adapter.load_template_state",
                      return_value=_ready_template(self.workspace)),
                bind_recovery_runtime(context.node_recovery_context()),
            ):
                await graph.ainvoke(None, config=context.fork_config)
        finally:
            await stop_execution_heartbeat(context.heartbeat_task)
        new_pending = load_pending_build_task_plan(source_state)
        self.assertIsNotNone(new_pending)
        self.assertNotEqual(new_pending["draft_identity"]["draft_digest"], old_identity.draft_digest)
        committed_fact = json.loads(fact_path.read_text())
        self.assertEqual(committed_fact["phase"], "committed")
        self.assertEqual(committed_fact["current_workflow_run_id"], context.new_run_id)
        self.assertEqual(service_calls, before_resume_calls + 1)
        self.assertGreater(model_calls, 0)

    async def test_native_regenerate_failure_retries_consumed_operation(self) -> None:
        """无业务 failed checkpoint 的异常可经上方恢复入口重新调用操作服务。"""

        await self._native_regenerate_round(interrupted=False)

    async def test_native_regenerate_interrupted_continue_consumed_operation(self) -> None:
        """中断继续保留原 reason 与旧 Pending 消费事实。"""

        await self._native_regenerate_round(interrupted=True)

    async def test_prepare_retry_uses_bound_native_recovery_source(self) -> None:
        """DAG Prepare 只在目标节点的本次 Native Recovery 调用读取可信 source。"""

        state = self._state(execution_scope())
        context = NodeRecoveryContext(
            source_run_id="workflow-source-r1",
            execution_run_id=str(state["active_run_id"]),
            thread_id=str(state["active_thread_id"]),
            target_node="prepare_build_tasks",
            checkpoint_id="source-entry",
            reentry_reason=WorkflowReentryReason.FAILURE_RETRY,
        )
        captured: dict[str, object] = {}

        async def capture_planning(inputs, **kwargs):
            """记录 adapter 的独立 Recovery 参数并继续真实 mainline。"""

            captured.update(kwargs)
            return await run_mainline_planning(inputs, **kwargs)

        adapter = create_async_workflow_planning_adapter(
            policy=self.policy,
            generate_once=self._generate,
            planning_service=capture_planning,
        )
        with (
            patch(
                "app.graph.nodes.task_planning_adapter.load_template_state",
                return_value=_ready_template(self.workspace),
            ),
            bind_recovery_runtime(context),
            bind_node_recovery(state, "prepare_build_tasks"),
        ):
            result = await adapter(state)

        self.assertEqual(
            captured["recovery_source_workflow_run_id"],
            "workflow-source-r1",
        )
        self.assertEqual(result["status"], "requires_user_input")

    async def test_default_graph_binds_async_adapter_after_cutover(self) -> None:
        """未显式注入节点时，默认 Graph 必须绑定 async Planning/Confirm adapter。"""

        sentinel_calls: list[dict] = []

        def adapter_factory(**_: object):
            """返回 authority 探针节点，证明默认 Graph 使用 adapter 工厂。"""

            async def sentinel(state: dict) -> dict:
                sentinel_calls.append(dict(state))
                return {
                    "phase": "prepare_build_tasks",
                    "status": "requires_user_input",
                    "clarification": {"mode": "async-adapter-authority-probe"},
                    "timeline": ["prepare_build_tasks"],
                }

            return sentinel

        with patch(
            "app.graph.workflow.create_async_workflow_planning_adapter",
            new=adapter_factory,
        ):
            graph = build_graph(checkpointer=InMemorySaver())
            result = await graph.ainvoke(
                self._state(execution_scope()),
                config={
                    "configurable": {"thread_id": "thread-async-authority-probe"}
                },
            )

        self.assertEqual(len(sentinel_calls), 1)
        self.assertEqual(
            result["clarification"]["mode"],
            "async-adapter-authority-probe",
        )

    async def test_graph_adapter_creates_pending_and_projects_confirmation_state(self) -> None:
        """无 Entity binding 时 Graph 仍从 Endpoint Design 生成 Pending。"""

        self.assertNotIn("entity_detail_plans", self.plan)

        previous_scope = execution_scope(name="orders")
        current_scope = execution_scope(name="customers")
        formal = confirmed_baseline(self.plan, previous_scope)
        formal_path = Path(write_build_task_plan_json(self._state(current_scope), formal))
        formal_bytes = formal_path.read_bytes()
        adapter = create_async_workflow_planning_adapter(
            policy=self.policy,
            generate_once=self._generate,
        )
        graph = build_graph(
            checkpointer=InMemorySaver(),
            prepare_build_tasks_node=adapter,
        )

        with patch(
            "app.graph.nodes.task_planning_adapter.load_template_state",
            return_value=_ready_template(self.workspace),
        ), patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-async-adapter",
        ):
            result = await graph.ainvoke(
                self._state(current_scope),
                config={
                    "configurable": {"thread_id": "thread-async-adapter"}
                },
            )

        pending = load_pending_build_task_plan(self._state(current_scope))
        identity = validate_pending_self_digest(pending)
        planning_run = load_planning_run(self._state(current_scope))
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["planning_run_id"], "planning-async-adapter")
        self.assertEqual(result["draft_digest"], identity.draft_digest)
        self.assertEqual(result["build_task_plan"], pending)
        self.assertEqual(result["build_task_plan"]["confirmation_status"], "pending")
        self.assertEqual(
            result["build_task_plan_confirmation"]["draftIdentity"],
            {
                "ownerSessionId": "session-async-adapter",
                "planningRunId": identity.planning_run_id,
                "draftDigest": identity.draft_digest,
            },
        )
        self.assertEqual(
            result["dag_generation_progress"]["planningRunId"],
            identity.planning_run_id,
        )
        self.assertEqual(result["build_execution_scope"], current_scope)
        self.assertEqual(
            result["last_persisted_build_execution_scope"],
            previous_scope,
        )
        # Formal authority 与 Pending authority 必须是两个独立字段。
        self.assertEqual(result["build_task_plan_path"], str(formal_path))
        self.assertEqual(
            result["pending_build_task_plan_path"],
            str(build_task_plan_pending_json_path(self._state(current_scope))),
        )
        # 正式基线属于上一 scope，本轮只落盘 Pending；两个 persisted 语义不能混用。
        self.assertFalse(result["build_task_plan_persisted"])
        self.assertTrue(result["pending_build_task_plan_persisted"])
        for key in (
            "build_context",
            "build_units",
            "unit_graph",
            "task_registry",
            "task_graph",
            "tasks",
        ):
            self.assertIn(key, result)
        self.assertEqual(planning_run["planning_run_id"], identity.planning_run_id)
        backend_job = next(
            job
            for job in self.generated_jobs
            if job.context.unit_id
            == "backend:endpoint:customers-api:customers.list"
        )
        catalog_kinds = {entry.kind for entry in backend_job.context.contract_catalog}
        self.assertIn("endpoint_api_design", catalog_kinds)
        self.assertNotIn("entity_binding", catalog_kinds)
        self.assertEqual(
            set(pending["build_units"][backend_job.context.unit_id]["task_ids"]),
            {
                f"{backend_job.context.unit_id}::objects",
                f"{backend_job.context.unit_id}::repository",
                f"{backend_job.context.unit_id}::service",
                f"{backend_job.context.unit_id}::controller",
            },
        )
        self.assertNotIn("::Customer::", json.dumps(pending))
        self.assertEqual(formal_path.read_bytes(), formal_bytes)
        self.assertEqual(
            load_build_task_plan_json(formal_path)["confirmation_status"],
            "confirmed",
        )
        self.assertNotEqual(plain_json(pending), load_build_task_plan_json(formal_path))

    async def test_blocked_round_clears_previous_planning_projection(self) -> None:
        """同一 checkpoint 上一轮成功后进入 blocked，必须清空上一轮 PlanningRun 身份。"""

        scope = execution_scope(name="customers")
        formal = confirmed_baseline(self.plan, execution_scope(name="orders"))
        write_build_task_plan_json(self._state(scope), formal)
        adapter = create_async_workflow_planning_adapter(
            policy=self.policy,
            generate_once=self._generate,
        )
        graph = build_graph(
            checkpointer=InMemorySaver(),
            prepare_build_tasks_node=adapter,
        )
        thread = {"configurable": {"thread_id": "thread-blocked-projection"}}

        with patch(
            "app.graph.nodes.task_planning_adapter.load_template_state",
            return_value=_ready_template(self.workspace),
        ), patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-blocked-first",
        ):
            first = await graph.ainvoke(self._state(scope), config=thread)
            # ProductPlan 退回未确认，让下一轮在同一 checkpoint 上被前置门禁阻断。
            write_json(
                self.workspace,
                ARTIFACT_PATHS["product_plan"],
                {
                    **formal_artifacts(self.plan)["product_plan"],
                    "confirmation_status": "draft",
                },
            )
            second = await graph.ainvoke(self._state(scope), config=thread)

        self.assertEqual(first["planning_run_id"], "planning-blocked-first")
        self.assertTrue(first["dag_generation_progress"])
        self.assertEqual(second["status"], "requires_user_input")
        self.assertEqual(second["clarification"]["mode"], "build_prerequisite_error")
        self.assertEqual(second["planning_run_id"], "")
        self.assertEqual(second["draft_digest"], "")
        self.assertEqual(second["dag_generation_progress"], {})
        self.assertEqual(second["build_task_plan_confirmation"], {})
        self.assertEqual(second["pending_build_task_plan_path"], "")
        self.assertFalse(second["pending_build_task_plan_persisted"])
        self.assertFalse(second["build_task_plan_persisted"])

    async def test_graph_cancellation_reaches_planning_run_without_detached_work(self) -> None:
        """取消 Graph coroutine 必须沿 await stack 标记 Run cancelled 且不写 Pending。"""

        worker_started = asyncio.Event()
        release_worker = asyncio.Event()

        async def wait_in_worker(job, **_: object) -> UnitGenerationAttemptResult:
            """在真实 Unit worker await 点保持运行，直到父 Graph coroutine 被取消。"""

            worker_started.set()
            await release_worker.wait()
            tasks = model_tasks(job)
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        adapter = create_async_workflow_planning_adapter(
            policy=self.policy,
            generate_once=wait_in_worker,
        )
        graph = build_graph(
            checkpointer=InMemorySaver(),
            prepare_build_tasks_node=adapter,
        )
        scope = execution_scope(name="orders")

        with patch(
            "app.graph.nodes.task_planning_adapter.load_template_state",
            return_value=_ready_template(self.workspace),
        ), patch(
            "app.services.build_task_planning_service._new_planning_run_id",
            return_value="planning-async-cancelled",
        ):
            graph_task = asyncio.create_task(
                graph.ainvoke(
                    self._state(scope),
                    config={
                        "configurable": {"thread_id": "thread-async-cancelled"}
                    },
                )
            )
            await asyncio.wait_for(worker_started.wait(), timeout=1)
            graph_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await graph_task

        self.assertIsNone(load_pending_build_task_plan(self._state(scope)))
        self.assertEqual(
            load_planning_run(self._state(scope))["status"],
            "cancelled",
        )


if __name__ == "__main__":
    unittest.main()
