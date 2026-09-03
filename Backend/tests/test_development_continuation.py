from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    DevelopmentContinuationTarget,
    PendingInteractionType,
    WorkbenchExecutionStatus,
)
from app.protocols.workflow.lifecycle import begin_workflow_lifecycle
from app.protocols.workflow.request import workflow_run_inputs
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    application_lifecycle_payload,
    complete_workbench_execution,
    create_application_lifecycle,
    load_application_lifecycle,
    start_workbench_execution,
    update_workbench_execution,
    write_application_lifecycle,
)
from app.services.development_continuation import (
    issue_development_continuation,
    register_development_continuation,
    validate_entity_binding_continuation,
)
from tests.entity_design_test_utils import confirm_entity_designs


def _technical_plan() -> dict:
    """构造页面依赖单一实体的最小当前 TechnicalPlan。"""

    return {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "entities": [{"id": "Order", "name": "订单", "fields": []}],
        "api_contracts": [
            {
                "id": "orders-api",
                "entity_ids": ["Order"],
                "endpoints": [
                    {
                        "id": "orders.list",
                        "method": "GET",
                        "path": "/api/orders",
                    }
                ],
            }
        ],
        "pages": [
            {
                "pageId": "orders_page",
                "references": {
                    "endpoint_dependencies": [{"endpoint_id": "orders.list"}]
                },
            }
        ],
    }


def _write_technical_plan(workspace: Path, plan: dict) -> None:
    """把测试计划写入 continuation 服务读取的当前正式路径。"""

    path = workspace / ".xcodeagent" / "plans" / "technical-plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


def _prepare_source_execution(workspace: Path) -> None:
    """准备被实体门禁挂起的原页面 execution。"""

    lifecycle = create_application_lifecycle(
        application_id="app-orders",
        application_name="订单应用",
    )
    lifecycle = lifecycle.model_copy(
        update={
            "initialization": lifecycle.initialization.model_copy(
                update={
                    "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    "status": ApplicationLifecycleStatus.COMPLETED,
                }
            )
        }
    )
    write_application_lifecycle(workspace, lifecycle)
    start_workbench_execution(
        workspace,
        scope="page",
        target_id="orders_page",
        page_id="orders_page",
        thread_id="thread-page",
        run_id="run-page",
        phase="development_readiness_gate",
    )
    update_workbench_execution(
        workspace,
        run_id="run-page",
        phase="development_readiness_gate",
        status=WorkbenchExecutionStatus.AWAITING_USER,
        pending_type=PendingInteractionType.ENTITY_SOURCE_BINDING,
        pending_payload={"missing_entities": [{"entity_id": "Order"}]},
    )


class DevelopmentContinuationTests(unittest.TestCase):
    """验证实体绑定独立 execution 后只按服务端合同恢复原开发运行。"""

    def test_page_continuation_replaces_source_execution_without_stale_entity_target(self) -> None:
        """页面门禁、实体确认和续接必须回到原 thread 的页面开发入口。"""

        with tempfile.TemporaryDirectory(prefix="development-continuation-") as raw:
            workspace = Path(raw)
            _write_technical_plan(workspace, _technical_plan())
            _prepare_source_execution(workspace)
            continuation = register_development_continuation(
                workspace,
                source_thread_id="thread-page",
                source_run_id="run-page",
                request="开始开发页面：订单列表",
                target=DevelopmentContinuationTarget(
                    type="page",
                    pageId="orders_page",
                    label="订单列表",
                ),
                required_entity_ids=["Order"],
            )
            with self.assertRaisesRegex(
                ApplicationLifecycleConflictError,
                "独立 execution thread",
            ):
                validate_entity_binding_continuation(
                    workspace,
                    continuation_id=continuation.id,
                    entity_id="Order",
                    binding_thread_id="thread-page",
                )
            validate_entity_binding_continuation(
                workspace,
                continuation_id=continuation.id,
                entity_id="Order",
                binding_thread_id="thread-entity",
            )
            with patch(
                "app.protocols.workflow.request._project_plan_start_values",
                return_value={"project_plan": _technical_plan()},
            ):
                binding_inputs = workflow_run_inputs(
                    {
                        "threadId": "thread-entity",
                        "forwardedProps": {
                            "workspaceRoot": str(workspace),
                            "workflowAction": "start_entity_binding",
                            "developmentContinuation": {"id": continuation.id},
                            "selectedEntityId": "Order",
                            "detailTargetType": "entity",
                            "buildExecutionScope": {
                                "type": "data_source",
                                "targetId": "Order",
                            },
                        },
                    }
                )
            begin_workflow_lifecycle(
                binding_inputs,
                thread_id="thread-entity",
                run_id="run-entity",
                phase="entity_source_binding",
            )
            binding_lifecycle = load_application_lifecycle(workspace)
            assert binding_lifecycle is not None
            self.assertEqual(
                set(binding_lifecycle.active_executions),
                {"run-page", "run-entity"},
            )

            pending = issue_development_continuation(
                workspace,
                continuation_id=continuation.id,
            )
            self.assertEqual(pending["status"], "awaiting_entity_binding")
            self.assertEqual(pending["remainingEntityIds"], ["Order"])

            confirmed_plan = confirm_entity_designs(_technical_plan(), source_type="static")
            _write_technical_plan(workspace, confirmed_plan)
            complete_workbench_execution(
                workspace,
                run_id="run-entity",
                phase="entity_source_binding",
            )
            ready = issue_development_continuation(
                workspace,
                continuation_id=continuation.id,
            )
            with patch(
                "app.protocols.workflow.request._project_plan_start_values",
                return_value={"project_plan": confirmed_plan},
            ):
                inputs = workflow_run_inputs(
                    {
                        "threadId": "thread-page",
                        "resumeState": {
                            "state": {
                                "selected_entity_id": "Order",
                                "detail_target_type": "entity",
                            }
                        },
                        "forwardedProps": {
                            "workspaceRoot": str(workspace),
                            "workflowAction": "continue_after_entity_binding",
                            "developmentContinuation": {
                                "id": continuation.id,
                                "token": ready["token"],
                            },
                        },
                    }
                )

            self.assertEqual(inputs["request"], "开始开发页面：订单列表")
            self.assertEqual(inputs["resume_from"], "development_readiness_gate")
            self.assertEqual(inputs["resume_values"]["selectedPageId"], "orders_page")
            self.assertEqual(inputs["resume_values"]["detail_target_type"], "page")
            self.assertEqual(inputs["resume_values"]["resume_execution_run_id"], "run-page")
            self.assertEqual(inputs["resume_values"]["selected_entity_id"], "")
            # 请求解析只读验证，原运行还未被接替时 token 仍可使用。
            self.assertEqual(
                load_application_lifecycle(workspace).development_continuations[continuation.id].status,
                "ready",
            )
            # 运行登记写盘失败必须同时保留 token 和原 execution，允许同一次点击重试。
            with patch("app.services.application_lifecycle.write_application_lifecycle", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    begin_workflow_lifecycle(
                        inputs, thread_id="thread-page", run_id="failed-start", phase="development_readiness_gate"
                    )
            unchanged = load_application_lifecycle(workspace)
            self.assertEqual(unchanged.development_continuations[continuation.id].status, "ready")
            self.assertIn("run-page", unchanged.active_executions)
            self.assertNotIn("failed-start", unchanged.active_executions)

            begin_workflow_lifecycle(
                inputs,
                thread_id="thread-page",
                run_id="run-page-continued",
                phase="development_readiness_gate",
            )
            current = load_application_lifecycle(workspace)
            assert current is not None
            self.assertNotIn("run-page", current.active_executions)
            self.assertEqual(
                current.active_executions["run-page-continued"].target_id,
                "orders_page",
            )
            self.assertEqual(
                current.development_continuations[continuation.id].status,
                "consumed",
            )
            self.assertNotIn(
                "developmentContinuations",
                application_lifecycle_payload(current),
            )

            with self.assertRaisesRegex(
                ApplicationLifecycleConflictError,
                "已消费或当前不可用",
            ):
                workflow_run_inputs(
                    {
                        "threadId": "thread-page",
                        "forwardedProps": {
                            "workspaceRoot": str(workspace),
                            "workflowAction": "continue_after_entity_binding",
                            "developmentContinuation": {
                                "id": continuation.id,
                                "token": ready["token"],
                            },
                        },
                    }
                )


if __name__ == "__main__":
    unittest.main()
