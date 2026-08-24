from __future__ import annotations

import tempfile
import unittest

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    ExecutionResourceClaim,
    ExecutionResourceType,
    PendingInteractionType,
    WorkbenchExecutionStatus,
)
from app.protocols.workflow.lifecycle import (
    begin_workflow_lifecycle,
    project_workflow_lifecycle_boundary,
)
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    create_application_lifecycle,
    end_workbench_execution,
    expand_workbench_execution_resources,
    start_workbench_execution,
    stop_workbench_execution,
    update_workbench_execution,
    write_application_lifecycle,
)
from app.services.execution_resource_scope import resolve_execution_resource_claims
from tests.entity_design_test_utils import confirm_entity_designs


class ExecutionResourceLockTests(unittest.TestCase):
    """验证页面、API、数据源资源锁的解析和完整生命周期。"""

    def test_page_claims_include_related_page_api_and_data_source(self) -> None:
        """页面执行应锁住主页面、导航页面、API 契约及其数据源。"""

        claims = resolve_execution_resource_claims(
            _project_plan(),
            {"type": "page", "targetId": "orders"},
        )

        self.assertEqual(
            {f"{claim.type.value}:{claim.target_id}" for claim in claims},
            {
                "page:orders",
                "page:order-detail",
                "page:order-search",
                "api_contract:orders-api",
                "api_contract:inventory-api",
                "data_source:database",
                "page:inventory",
            },
        )

    def test_shared_api_is_recorded_without_blocking_parallel_page(self) -> None:
        """共享 API 只更新登记归属，不得阻断另一个页面启动。"""

        with tempfile.TemporaryDirectory() as directory:
            _write_ready_lifecycle(directory)
            plan = _project_plan()
            start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-orders",
                run_id="run-orders",
                phase="build",
                resource_claims=resolve_execution_resource_claims(
                    plan, {"type": "page", "targetId": "orders"}
                ),
            )

            overlapping = start_workbench_execution(
                directory,
                scope="page",
                target_id="order-search",
                page_id="order-search",
                thread_id="thread-search",
                run_id="run-search",
                phase="build",
                resource_claims=resolve_execution_resource_claims(
                    plan, {"type": "page", "targetId": "order-search"}
                ),
            )
            self.assertEqual(
                overlapping.resource_locks.api_contracts["orders-api"].run_id,
                "run-search",
            )

            parallel = start_workbench_execution(
                directory,
                scope="page",
                target_id="help",
                page_id="help",
                thread_id="thread-help",
                run_id="run-help",
                phase="build",
                resource_claims=resolve_execution_resource_claims(
                    plan, {"type": "page", "targetId": "help"}
                ),
            )
            self.assertEqual(
                set(parallel.active_executions),
                {"run-orders", "run-search", "run-help"},
            )

    def test_stopped_execution_keeps_locks_until_explicit_end(self) -> None:
        """停止或等待恢复不等于结束，资源锁必须保留到显式结束。"""

        with tempfile.TemporaryDirectory() as directory:
            _write_ready_lifecycle(directory)
            start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-orders",
                run_id="run-orders",
                phase="build",
                resource_claims=resolve_execution_resource_claims(
                    _project_plan(), {"type": "page", "targetId": "orders"}
                ),
            )
            stopped = stop_workbench_execution(directory, run_id="run-orders")
            self.assertEqual(stopped.resource_locks.pages["order-detail"].run_id, "run-orders")
            self.assertEqual(stopped.resource_locks.api_contracts["orders-api"].run_id, "run-orders")

            ended = end_workbench_execution(directory, run_id="run-orders")
            self.assertEqual(ended.resource_locks.pages, {})
            self.assertEqual(ended.resource_locks.api_contracts, {})
            self.assertEqual(ended.resource_locks.data_sources, {})

    def test_resume_transfers_repair_expansion_to_new_run(self) -> None:
        """恢复使用新 runId 时应原子转移旧执行的完整扩展锁集合。"""

        with tempfile.TemporaryDirectory() as directory:
            _write_ready_lifecycle(directory)
            initial = resolve_execution_resource_claims(
                _project_plan(), {"type": "page", "targetId": "orders"}
            )
            start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-orders",
                run_id="run-old",
                phase="build",
                resource_claims=initial,
            )
            expand_workbench_execution_resources(
                directory,
                run_id="run-old",
                resource_claims=[
                    ExecutionResourceClaim(type=ExecutionResourceType.PAGE, targetId="audit")
                ],
            )

            resumed = start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-orders",
                run_id="run-new",
                phase="build",
                replaces_run_id="run-old",
                resource_claims=initial,
            )

            self.assertNotIn("run-old", resumed.active_executions)
            self.assertEqual(resumed.resource_locks.pages["audit"].run_id, "run-new")
            self.assertIn("page:audit", resumed.active_executions["run-new"].resource_keys)

    def test_stopped_workflow_retry_transfers_locks_to_new_run(self) -> None:
        """普通停止后的继续执行应由显式旧 runId 原子接管资源锁。"""

        with tempfile.TemporaryDirectory() as directory:
            _write_ready_lifecycle(directory)
            claims = resolve_execution_resource_claims(
                _project_plan(), {"type": "page", "targetId": "orders"}
            )
            start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-orders",
                run_id="run-old",
                phase="build",
                resource_claims=claims,
            )
            stop_workbench_execution(directory, run_id="run-old")

            payload = begin_workflow_lifecycle(
                {
                    "workspace": directory,
                    "resume_values": {
                        "selectedPageId": "orders",
                        "build_execution_scope": {
                            "type": "page",
                            "targetId": "orders",
                        },
                        "execution_resource_claims": [
                            claim.model_dump(mode="json", by_alias=True)
                            for claim in claims
                        ],
                        "resume_execution_run_id": "run-old",
                    },
                },
                thread_id="thread-orders",
                run_id="run-new",
                phase="build",
            )

            assert payload is not None
            self.assertNotIn("run-old", payload["activeExecutions"])
            self.assertEqual(
                payload["activeExecutions"]["run-new"]["status"],
                "running",
            )
            self.assertEqual(
                payload["resourceLocks"]["apiContracts"]["orders-api"]["runId"],
                "run-new",
            )

    def test_debug_resume_can_replace_plan_adjustment_execution(self) -> None:
        """任务拆分失败后的调试恢复应允许接管等待计划调整的旧执行。"""

        with tempfile.TemporaryDirectory() as directory:
            _write_ready_lifecycle(directory)
            start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-orders",
                run_id="run-old",
                phase="prepare_build_tasks",
            )
            waiting = update_workbench_execution(
                directory,
                run_id="run-old",
                phase="prepare_build_tasks",
                status=WorkbenchExecutionStatus.AWAITING_USER,
                pending_type=PendingInteractionType.PLAN_ADJUSTMENT,
                pending_payload={"mode": "build_task_plan_generation_error"},
            )
            self.assertEqual(
                waiting.active_executions["run-old"].status,
                WorkbenchExecutionStatus.AWAITING_USER,
            )

            payload = begin_workflow_lifecycle(
                {
                    "workspace": directory,
                    "workflow_debug_enabled": True,
                    "resume_values": {
                        "selectedPageId": "orders",
                        "build_execution_scope": {
                            "type": "page",
                            "targetId": "orders",
                        },
                        "resume_execution_run_id": "run-old",
                    },
                },
                thread_id="thread-orders",
                run_id="run-new",
                phase="prepare_build_tasks",
            )

            assert payload is not None
            self.assertNotIn("run-old", payload["activeExecutions"])
            self.assertEqual(
                payload["activeExecutions"]["run-new"]["status"],
                "running",
            )

    def test_stopped_workflow_retry_cannot_take_another_thread_locks(self) -> None:
        """显式恢复令牌不能跨对话接管已经停止的资源锁。"""

        with tempfile.TemporaryDirectory() as directory:
            _write_ready_lifecycle(directory)
            start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-orders",
                run_id="run-old",
                phase="build",
            )
            stop_workbench_execution(directory, run_id="run-old")

            with self.assertRaises(ApplicationLifecycleConflictError):
                begin_workflow_lifecycle(
                    {
                        "workspace": directory,
                        "resume_values": {
                            "selectedPageId": "orders",
                            "build_execution_scope": {
                                "type": "page",
                                "targetId": "orders",
                            },
                            "resume_execution_run_id": "run-old",
                        },
                    },
                    thread_id="thread-other",
                    run_id="run-new",
                    phase="build",
                )

    def test_repair_expansion_overwrites_resource_registration_without_blocking(self) -> None:
        """修复扩展遇到已有登记时仍完整写入，并把同键归属更新为当前运行。"""

        with tempfile.TemporaryDirectory() as directory:
            _write_ready_lifecycle(directory)
            start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-orders",
                run_id="run-orders",
                phase="build",
            )
            start_workbench_execution(
                directory,
                scope="page",
                target_id="help",
                page_id="help",
                thread_id="thread-help",
                run_id="run-help",
                phase="build",
            )

            state = expand_workbench_execution_resources(
                directory,
                run_id="run-orders",
                resource_claims=[
                    ExecutionResourceClaim(
                        type=ExecutionResourceType.API_CONTRACT,
                        targetId="new-api",
                    ),
                    ExecutionResourceClaim(type=ExecutionResourceType.PAGE, targetId="help"),
                ],
            )
            self.assertEqual(state.resource_locks.api_contracts["new-api"].run_id, "run-orders")
            self.assertEqual(state.resource_locks.pages["help"].run_id, "run-orders")

    def test_approved_repair_confirmation_adds_structured_resources_on_resume(self) -> None:
        """批准修复确认后，新 run 应同时接管旧锁并增加结构化资源。"""

        with tempfile.TemporaryDirectory() as directory:
            _write_ready_lifecycle(directory)
            start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-orders",
                run_id="run-old",
                phase="build",
            )
            waiting = update_workbench_execution(
                directory,
                run_id="run-old",
                phase="build",
                status=WorkbenchExecutionStatus.AWAITING_USER,
                pending_type=PendingInteractionType.REPAIR_SCOPE_CONFIRMATION,
                pending_payload={
                    "mode": "repair_scope_confirmation",
                    "requestedResources": [
                        {"type": "api_contract", "targetId": "audit-api"}
                    ],
                },
            )
            pending = waiting.active_executions["run-old"].pending_interaction
            assert pending is not None

            payload = begin_workflow_lifecycle(
                {
                    "workspace": directory,
                    "request": "批准修复范围",
                    "resume_values": {
                        "selectedPageId": "orders",
                        "build_execution_scope": {"type": "page", "targetId": "orders"},
                        "execution_resource_claims": [
                            {"type": "page", "targetId": "orders", "role": "primary", "reason": "primary_target"}
                        ],
                        "lifecycle_interaction_submission": {
                            "runId": "run-old",
                            "id": pending.id,
                            "basedOnRevision": pending.based_on_revision,
                        },
                    },
                },
                thread_id="thread-orders",
                run_id="run-new",
                phase="build",
            )

            assert payload is not None
            self.assertEqual(payload["resourceLocks"]["apiContracts"]["audit-api"]["runId"], "run-new")
            self.assertEqual(
                payload["resourceLocks"]["apiContracts"]["audit-api"]["reason"],
                "repair_expansion",
            )

    def test_test_phase_confirmation_can_take_over_waiting_execution_in_new_thread(self) -> None:
        """开发完成确认应允许测试新对话接管 awaiting_user execution。"""

        with tempfile.TemporaryDirectory() as directory:
            _write_ready_lifecycle(directory)
            start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-orders",
                run_id="run-old",
                phase="test_phase_confirmation",
            )
            waiting = update_workbench_execution(
                directory,
                run_id="run-old",
                phase="test_phase_confirmation",
                status=WorkbenchExecutionStatus.AWAITING_USER,
                pending_type=PendingInteractionType.TEST_PHASE_CONFIRMATION,
                pending_payload={
                    "mode": "test_phase_confirmation",
                    "testTarget": {
                        "type": "page",
                        "id": "orders",
                        "label": "订单页",
                    },
                },
            )
            pending = waiting.active_executions["run-old"].pending_interaction
            assert pending is not None

            payload = begin_workflow_lifecycle(
                {
                    "workspace": directory,
                    "resume_values": {
                        "build_execution_scope": {"type": "page", "targetId": "orders"},
                        "resume_execution_run_id": "run-old",
                    },
                },
                thread_id="thread-tests-orders",
                run_id="run-new",
                phase="test_phase_confirmation",
            )

            assert payload is not None
            self.assertNotIn("run-old", payload["activeExecutions"])
            self.assertEqual(
                payload["activeExecutions"]["run-new"]["threadId"],
                "thread-tests-orders",
            )

            projected = project_workflow_lifecycle_boundary(
                directory,
                run_id="run-new",
                node_name="test_phase_confirmation",
                update={"status": "completed"},
            )

            assert projected is not None
            self.assertEqual(
                projected["activeExecutions"]["run-new"]["phase"],
                "integration_test",
            )

    def test_other_resumable_execution_cannot_move_to_new_thread(self) -> None:
        """非测试确认的可恢复执行不得跨对话接管。"""

        with tempfile.TemporaryDirectory() as directory:
            _write_ready_lifecycle(directory)
            start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="thread-orders",
                run_id="run-old",
                phase="detail_confirmation",
            )
            update_workbench_execution(
                directory,
                run_id="run-old",
                phase="detail_confirmation",
                status=WorkbenchExecutionStatus.STOPPED,
            )

            with self.assertRaises(ApplicationLifecycleConflictError):
                begin_workflow_lifecycle(
                    {
                        "workspace": directory,
                        "resume_values": {
                            "build_execution_scope": {
                                "type": "page",
                                "targetId": "orders",
                            },
                            "resume_execution_run_id": "run-old",
                        },
                    },
                    thread_id="thread-new",
                    run_id="run-new",
                    phase="detail_confirmation",
                )


def _write_ready_lifecycle(directory: str) -> None:
    """写入可进入工作台的最小生命周期测试快照。"""

    state = create_application_lifecycle(application_id="app-1", application_name="商城")
    state = state.model_copy(
        update={
            "initialization": state.initialization.model_copy(
                update={
                    "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    "status": ApplicationLifecycleStatus.COMPLETED,
                }
            )
        }
    )
    write_application_lifecycle(directory, state)


def _project_plan() -> dict:
    """返回覆盖共享 API、导航依赖和独立页面的最小正式计划（实体设计已确认）。"""

    plan = {
        "frontend_pages": [
            {
                "pageId": "orders",
                "references": {
                    "endpoint_dependencies": [{"endpoint_id": "orders.list"}],
                    "navigation_targets": [{"targetPageId": "order-detail"}],
                },
            },
            {
                "pageId": "order-search",
                "references": {
                    "endpoint_dependencies": [{"endpoint_id": "orders.list"}],
                },
            },
            {"pageId": "order-detail", "references": {}},
            {
                "pageId": "inventory",
                "references": {
                    "endpoint_dependencies": [{"endpoint_id": "inventory.list"}]
                },
            },
            {"pageId": "help", "references": {}},
        ],
        "api_contracts": [
            {
                "id": "orders-api",
                "entity_ids": ["Order"],
                "endpoints": [{"id": "orders.list"}],
            },
            {
                "id": "inventory-api",
                "entity_ids": ["Inventory"],
                "endpoints": [{"id": "inventory.list"}],
            },
        ],
        "entities": [
            {"id": "Order", "name": "Order", "fields": []},
            {"id": "Inventory", "name": "Inventory", "fields": []},
        ],
    }
    return confirm_entity_designs(plan)


if __name__ == "__main__":
    unittest.main()
