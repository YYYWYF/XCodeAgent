from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from app.domain.application_lifecycle import (
    PendingInteraction,
    PendingInteractionType,
    WorkbenchExecution,
    WorkbenchExecutionStatus,
)
from app.domain.api_design import EndpointApiDesign
from app.graph.nodes.tasks import _build_task_plan_confirmation_payload
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.api_design import endpoint_field_nodes
from app.services.build_context_resolver import resolve_confirmation_context
from app.services.planning_refresh_recovery import resolve_planning_refresh_state
from app.workspace.endpoint_design_documents import (
    technical_plan_sha256,
    write_endpoint_design,
)
from app.workspace.planning_run_documents import write_planning_run_atomic
from app.workspace.task_documents import (
    load_pending_build_task_plan,
    write_build_task_plan_json,
    write_pending_build_task_plan_atomic,
)
from tests.planning_run_fixtures import run
from tests.test_pending_build_task_plan_documents import _validated_plan


def _write_json(workspace: Path, relative_path: str, value: dict) -> None:
    """向隔离工作区写入当前正式上下文测试产物。"""

    path = workspace / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _formal_confirmation_context() -> tuple[dict, dict, dict, dict]:
    """构造同时覆盖 Endpoint 与 Page confirmation 的当前正式产物。"""

    endpoint = {
        "id": "listOrders",
        "method": "GET",
        "path": "/api/orders",
        "summary": "查询订单",
        "parameters": [
            {"name": "status", "in": "query", "required": False},
        ],
        "request_schema_ref": "OrderListRequest",
        "response_schema_ref": "OrderListResponse",
        "error_codes": ["INVALID_STATUS"],
        "authentication": {"required": True},
    }
    technical_plan = {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "pages": [
            {
                "pageId": "orders",
                "references": {
                    "endpoint_dependencies": [{"endpoint_id": "listOrders"}],
                },
            },
        ],
        "api_contracts": [
            {
                "id": "orders-api",
                "entity_ids": [],
                "schemas": {
                    "OrderListRequest": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                    },
                    "OrderListResponse": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                },
                "endpoints": [endpoint],
            },
        ],
        "entities": [],
    }
    requirement_spec = {
        "confirmation_status": "confirmed",
        "app_info": {"name": "订单应用", "summary": "查询订单。"},
        "user_roles": [],
        "feature_modules": [],
        "acceptance_criteria": [],
    }
    product_plan = {
        "confirmation_status": "confirmed",
        "app": {"name": "订单应用", "summary": "查询订单。"},
        "business_flows": [],
        "product_acceptance_criteria": [],
        "pages": [
            {
                "pageId": "orders",
                "name": "订单列表",
                "path": "/orders",
                "description": "查询订单列表。",
                "actions": [],
                "navigation_targets": [],
                "acceptance_criteria": ["用户可以按状态查询订单。"],
            },
        ],
    }
    ui_designs = {"confirmation_status": "skipped", "pages": []}
    return technical_plan, requirement_spec, product_plan, ui_designs


def _write_formal_confirmation_context(workspace: Path) -> None:
    """写入正式 TechnicalPlan 及其当前 Endpoint Design 依赖。"""

    technical_plan, requirement_spec, product_plan, ui_designs = (
        _formal_confirmation_context()
    )
    _write_json(workspace, ".xcodeagent/plans/technical-plan.json", technical_plan)
    _write_json(
        workspace,
        ".xcodeagent/specs/requirement-spec.json",
        requirement_spec,
    )
    _write_json(workspace, ".xcodeagent/plans/product-plan.json", product_plan)
    _write_json(workspace, ".xcodeagent/specs/ui-designs.json", ui_designs)
    contract = technical_plan["api_contracts"][0]
    endpoint = contract["endpoints"][0]
    field_mappings = []
    for field in endpoint_field_nodes(contract, endpoint):
        endpoint_field = {
            key: field[key]
            for key in ("side", "location", "path", "type", "required", "description")
        }
        source_field = {
            "sourceType": "database",
            "sourceId": "orders-database",
            "schema": "app",
            "table": "orders",
            "column": field["path"].replace(".", "_").replace("[]", "_items"),
            "type": field["type"],
            "usage": "filter" if field["side"] == "request" else "read",
        }
        field_mappings.append(
            {
                "endpointField": endpoint_field,
                "mappingType": "source_mapping",
                "processingType": "direct",
                "sourceFields": [source_field],
            }
        )
    write_endpoint_design(
        workspace,
        EndpointApiDesign.model_validate(
            {
                "schemaVersion": "endpoint-field-mapping.v3",
                "artifactType": "endpoint-field-mapping",
                "status": "confirmed",
                "confirmationStatus": "confirmed",
                "artifactRevision": "0123456789abcdef0123456789abcdef",
                "apiContractId": contract["id"],
                "endpointId": endpoint["id"],
                "endpointContract": endpoint,
                "fieldMappings": field_mappings,
                "sourceSnapshots": [],
                "basedOn": [
                    {
                        "artifactKey": "technical-plan",
                        "sha256": technical_plan_sha256(workspace),
                    }
                ],
                "confirmedAt": datetime.now(UTC),
            }
        ),
    )


class PlanningRefreshRecoveryTests(unittest.TestCase):
    """验证刷新只恢复尚未被终态事实压制的唯一 PendingPlan。"""

    def setUp(self) -> None:
        """为每例创建独立工作区和一致的 PlanningRun 身份。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = temporary.name
        self.state = {"workspace": self.workspace}
        self.planning = run().model_copy(
            update={
                "planning_run_id": "planning-refresh-run",
                "workflow_run_id": "workflow-refresh-run",
                "thread_id": "thread-refresh-run",
                "input_fingerprint": "b" * 64,
                "base_confirmed_plan_digest": None,
            }
        )

    def _write_planning(self) -> None:
        """写入当前 active PlanningRun 的严格轻量快照。"""

        write_planning_run_atomic(self.state, self.planning)

    def _write_pending(self, *, build_execution_scope: dict | None = None) -> dict:
        """写入与当前 PlanningRun 身份一致的合法 PendingPlan。"""

        write_pending_build_task_plan_atomic(
            self.state,
            _validated_plan(),
            owner_session_id="session-refresh-owner",
            planning_run_id=self.planning.planning_run_id,
            workflow_run_id=self.planning.workflow_run_id,
            base_confirmed_plan_digest=None,
            input_fingerprint=self.planning.input_fingerprint,
            build_execution_scope=(
                build_execution_scope or self.planning.build_execution_scope
            ),
            created_at=self.planning.updated_at,
            planning_provenance={
                "schema_version": "planning-provenance.v2",
                "review_task_ids": list(_validated_plan().get("task_registry", {})),
                "platform_task_ids": [],
                "new_task_ids": list(_validated_plan().get("task_registry", {})),
                "reused_task_ids": [],
            },
        )
        pending = load_pending_build_task_plan(self.state)
        assert pending is not None
        return pending

    def _resolve(self) -> dict:
        """调用按 Pending 与终态身份解析的公共刷新解析器。"""

        return resolve_planning_refresh_state(self.workspace)

    def _write_confirmed_formal(self, planning_run_id: str, draft_digest: str) -> None:
        """写入带精确 confirmed_from 的 Formal residue。"""

        write_build_task_plan_json(
            self.state,
            {
                "schema_version": "build-dag.v4",
                "status": "ready",
                "unit_graph": {
                    "schema_version": "build-unit-graph.v3",
                    "nodes": [],
                    "edges": [],
                    "validation": {"is_valid": True, "errors": []},
                },
                "confirmation_status": "confirmed",
                "confirmed_from": {
                    "planning_run_id": planning_run_id,
                    "draft_digest": draft_digest,
                },
                "task_graph": {"validation": {"is_valid": True, "errors": []}},
            },
        )

    def _write_abandoned_marker(self, planning_run_id: str, draft_digest: str) -> None:
        """写入与 Pending 身份精确匹配的 authoritative Abandon residue。"""

        lifecycle = create_application_lifecycle(
            application_id="app-refresh",
            application_name="Planning refresh",
        ).model_copy(
            update={
                "extensions": {
                    "planningResultLifecycle": {
                        "schemaVersion": "planning-result-lifecycle.v1",
                        "status": "abandoned",
                        "planningRunId": planning_run_id,
                        "draftDigest": draft_digest,
                    }
                }
            }
        )
        write_application_lifecycle(self.workspace, lifecycle)

    def test_case_a_pending_plan_is_authoritative(self) -> None:
        """存在 PendingPlan 时返回确认态，并直接使用其中冻结的身份。"""

        pending = self._write_pending()

        recovered = self._resolve()

        self.assertEqual(recovered["source"], "pending_plan")
        self.assertEqual(recovered["status"], "awaiting_confirmation")
        self.assertEqual(recovered["planningRunId"], self.planning.planning_run_id)
        self.assertEqual(recovered["workflowRunId"], self.planning.workflow_run_id)
        self.assertNotIn("threadId", recovered)
        self.assertEqual(recovered["ownerSessionId"], "session-refresh-owner")
        self.assertEqual(
            recovered["buildExecutionScope"],
            self.planning.build_execution_scope,
        )
        self.assertEqual(
            recovered["confirmation"]["draftIdentity"]["ownerSessionId"],
            "session-refresh-owner",
        )
        self.assertEqual(
            recovered["draftDigest"],
            pending["draft_identity"]["draft_digest"],
        )
        self.assertEqual(
            recovered["confirmation"]["taskPlan"]["confirmationStatus"],
            "pending",
        )

    def test_endpoint_pending_refresh_preserves_full_target_review(self) -> None:
        """Endpoint Pending 刷新必须从当前正式契约恢复完整接口详情。"""

        _write_formal_confirmation_context(Path(self.workspace))
        scope = {
            "type": "endpoint",
            "targetId": "listOrders",
            "apiContractId": "orders-api",
        }
        self._write_pending(build_execution_scope=scope)

        recovered = self._resolve()
        confirmation = recovered["confirmation"]
        target = confirmation["targetReview"]["target"]

        self.assertEqual(target["type"], "endpoint")
        self.assertEqual(target["id"], "listOrders")
        self.assertEqual(target["apiContractId"], "orders-api")
        self.assertEqual(target["method"], "GET")
        self.assertEqual(target["path"], "/api/orders")
        self.assertEqual(target["parameters"][0]["name"], "status")
        self.assertEqual(target["requestSchemaRef"], "OrderListRequest")
        self.assertEqual(target["responseSchemaRef"], "OrderListResponse")
        self.assertEqual(target["errorCodes"], ["INVALID_STATUS"])
        self.assertTrue(target["authentication"]["required"])

    def test_page_pending_refresh_preserves_related_endpoint_review(self) -> None:
        """Page Pending 刷新必须恢复页面验收和关联 Endpoint 详情。"""

        _write_formal_confirmation_context(Path(self.workspace))
        scope = {"type": "page", "targetId": "orders"}
        self._write_pending(build_execution_scope=scope)

        recovered = self._resolve()
        review = recovered["confirmation"]["targetReview"]
        endpoint = review["relatedEndpoints"][0]

        self.assertEqual(review["target"]["type"], "page")
        self.assertEqual(review["target"]["id"], "orders")
        self.assertEqual(review["target"]["label"], "订单列表")
        self.assertEqual(review["target"]["path"], "/orders")
        self.assertEqual(
            review["target"]["acceptanceCriteria"],
            ["用户可以按状态查询订单。"],
        )
        self.assertEqual(endpoint["id"], "listOrders")
        self.assertEqual(endpoint["method"], "GET")
        self.assertEqual(endpoint["path"], "/api/orders")
        self.assertEqual(endpoint["parameters"], [{"name": "status", "in": "query", "required": False}])
        self.assertEqual(endpoint["requestSchemaRef"], "OrderListRequest")
        self.assertEqual(endpoint["responseSchemaRef"], "OrderListResponse")

    def test_refresh_target_review_matches_live_confirmation_projection(self) -> None:
        """相同正式上下文下，刷新投影与实时确认投影的 targetReview 必须一致。"""

        _write_formal_confirmation_context(Path(self.workspace))
        scope = {
            "type": "endpoint",
            "targetId": "listOrders",
            "apiContractId": "orders-api",
        }
        pending = self._write_pending(build_execution_scope=scope)
        context = resolve_confirmation_context(self.workspace, scope)
        live = _build_task_plan_confirmation_payload(
            pending,
            scope,
            project_plan=context["project_plan"],
            build_context=context["build_context"],
        )

        recovered = self._resolve()

        self.assertEqual(
            live["targetReview"],
            recovered["confirmation"]["targetReview"],
        )

    def test_application_pending_refresh_keeps_simple_target_review(self) -> None:
        """Application scope 继续只展示应用身份，不要求人为补充接口详情。"""

        scope = {"type": "application", "targetId": "application"}
        self._write_pending(build_execution_scope=scope)

        confirmation = self._resolve()["confirmation"]

        self.assertEqual(
            confirmation["targetReview"]["target"],
            {"type": "application", "id": "application", "label": "application"},
        )
        self.assertNotIn("errors", confirmation)

    def test_missing_formal_endpoint_context_is_explicit_recovery_error(self) -> None:
        """正式 Endpoint 消失时不能静默投影 method/path 为空的合法确认卡。"""

        _write_json(
            Path(self.workspace),
            ".xcodeagent/plans/technical-plan.json",
            {
                "artifact_type": "technical-plan",
                "confirmation_status": "confirmed",
                "api_contracts": [],
                "pages": [],
            },
        )
        scope = {
            "type": "endpoint",
            "targetId": "listOrders",
            "apiContractId": "orders-api",
        }
        self._write_pending(build_execution_scope=scope)

        recovered = self._resolve()
        confirmation = recovered["confirmation"]

        self.assertEqual(recovered["status"], "awaiting_confirmation")
        self.assertIn("errors", confirmation)
        self.assertIn(
            "无法从最新正式 TechnicalPlan 重建目标详情",
            confirmation["errors"][0],
        )
        self.assertNotIn("targetReview", confirmation)
        self.assertEqual(
            confirmation["taskPlan"]["reviewTasks"][0]["reviewRole"],
            "new",
        )
        self.assertIn("confirm", confirmation["actionValues"])

    def test_case_b_missing_pending_is_idle_even_with_active_planning_run(self) -> None:
        """没有 PendingPlan 时，旧 PlanningRun 不能伪造待确认状态。"""

        self._write_planning()

        recovered = resolve_planning_refresh_state(
            self.workspace,
        )

        self.assertEqual(recovered["source"], "none")
        self.assertEqual(recovered["status"], "idle")

    def test_case_c_legacy_task_plan_confirmation_is_ignored(self) -> None:
        """没有 PendingPlan 时，application-lifecycle 的旧确认交互也必须返回 idle。"""

        timestamp = datetime(2026, 9, 11, tzinfo=UTC)
        lifecycle = create_application_lifecycle(
            application_id="app-refresh",
            application_name="Planning refresh",
        )
        execution = WorkbenchExecution(
            scope="page",
            targetId="orders",
            threadId="legacy-thread",
            runId="legacy-workflow-run",
            phase="prepare_build_tasks",
            status=WorkbenchExecutionStatus.AWAITING_USER,
            pendingInteraction=PendingInteraction(
                id="legacy-task-plan-confirmation",
                type=PendingInteractionType.TASK_PLAN_CONFIRMATION,
                basedOnRevision=lifecycle.revision,
                payload={"mode": "build_task_plan_confirmation"},
                createdAt=timestamp,
            ),
            startedAt=timestamp,
            updatedAt=timestamp,
        )
        lifecycle = lifecycle.model_copy(
            update={
                "active_run_id": execution.run_id,
                "active_executions": {execution.run_id: execution},
            }
        )
        write_application_lifecycle(self.workspace, lifecycle)
        persisted = load_application_lifecycle(self.workspace)
        self.assertIsNotNone(persisted)

        recovered = resolve_planning_refresh_state(
            self.workspace,
        )

        self.assertEqual(recovered["source"], "none")
        self.assertEqual(recovered["status"], "idle")

    def test_case_b_confirmed_pending_residue_is_not_actionable(self) -> None:
        """Formal 已精确确认当前 Pending 时，重启恢复不得再次展示确认。"""

        pending = self._write_pending()
        identity = pending["draft_identity"]
        self._write_confirmed_formal(
            identity["planning_run_id"],
            identity["draft_digest"],
        )

        recovered = self._resolve()

        self.assertEqual(recovered["source"], "none")
        self.assertEqual(recovered["status"], "idle")
        self.assertNotIn("confirmation", recovered)

    def test_case_c_abandoned_pending_residue_is_not_actionable(self) -> None:
        """Abandon tombstone 已精确终结当前 Pending 时，重启恢复不得复活它。"""

        pending = self._write_pending()
        identity = pending["draft_identity"]
        self._write_abandoned_marker(
            identity["planning_run_id"],
            identity["draft_digest"],
        )

        recovered = self._resolve()

        self.assertEqual(recovered["source"], "none")
        self.assertEqual(recovered["status"], "idle")
        self.assertNotIn("confirmation", recovered)

    def test_case_d_different_pending_identity_survives_formal_residue(self) -> None:
        """Formal 终结 A 不能使精确身份不同的当前 Pending B 失效。"""

        pending = self._write_pending()
        identity = pending["draft_identity"]
        self._write_confirmed_formal("planning-formal-a", "a" * 64)
        self._write_abandoned_marker("planning-abandoned-a", "b" * 64)

        recovered = self._resolve()

        self.assertEqual(recovered["source"], "pending_plan")
        self.assertEqual(recovered["status"], "awaiting_confirmation")
        self.assertEqual(recovered["planningRunId"], identity["planning_run_id"])
        self.assertEqual(recovered["draftDigest"], identity["draft_digest"])


if __name__ == "__main__":
    unittest.main()
