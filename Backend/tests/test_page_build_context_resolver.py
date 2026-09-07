from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.domain.api_design import EndpointApiDesign
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.build_context_resolver import resolve_target_build_context
from app.services.page_dependencies import validate_project_plan_dependencies
from app.graph.nodes.tasks import _scoped_contract_validation_plan
from app.workspace.endpoint_design_documents import technical_plan_sha256, write_endpoint_design
from tests.entity_design_test_utils import confirm_entity_designs


def _write_json(path: Path, payload: dict) -> None:
    """把测试计划写入临时工作区。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project_plan(workspace: Path) -> tuple[dict, Path]:
    """构造带页面实现契约、TechnicalPlan Endpoint 与 API 设计的计划。"""

    plan_path = workspace / ".xcodeagent/plans/technical-plan.json"
    plan = {
        "frontend_pages": [
            {
                "pageId": "orders",
                "references": {"permissions": ["admin"]},
            },
            {
                "pageId": "customers",
                "references": {"permissions": ["admin"]},
            },
        ],
        "page_implementation_contracts": [
            {
                "schema_version": "page-implementation-contract.v1",
                "pageId": "orders",
                "uiDesignRef": {"path": ".xcodeagent/ui-design/pages/Orders/index.tsx"},
                "requiredEndpointIds": ["orders.list"],
            },
            {
                "schema_version": "page-implementation-contract.v1",
                "pageId": "customers",
                "uiDesignRef": {"path": ".xcodeagent/ui-design/pages/Customers/index.tsx"},
                "requiredEndpointIds": ["customers.list"],
            },
        ],
        "entities": [
            {
                "id": "Order",
                "name": "Order",
                "fields": [{"name": "id", "type": "string"}],
            },
            {
                "id": "Customer",
                "name": "Customer",
                "fields": [{"name": "id", "type": "string"}],
            },
        ],
        "api_contracts": [
            {
                "id": "orders-api",
                "entity_ids": ["Order"],
                "schemas": {"OrderResponse": {"type": "object", "properties": {"id": {"type": "string"}}}},
                "endpoints": [
                    {
                        "id": "orders.list",
                        "response_schema_ref": "OrderResponse",
                    }
                ],
            },
            {
                "id": "customers-api",
                "entity_ids": ["Customer"],
                "schemas": {"CustomerResponse": {"type": "object", "properties": {"id": {"type": "string"}}}},
                "endpoints": [
                    {
                        "id": "customers.list",
                        "response_schema_ref": "CustomerResponse",
                    }
                ],
            },
        ],
    }
    plan = confirm_entity_designs(plan, source_type="database")
    _write_json(plan_path, plan)
    _write_endpoint_designs(workspace, plan, source_type="database")
    return plan, plan_path


def _write_endpoint_designs(workspace: Path, plan: dict, *, source_type: str) -> None:
    """为全部 Endpoint 写入 fieldMappings 形式的当前版 API 设计。"""

    for contract in plan.get("api_contracts") or []:
        contract_id = str(contract.get("id") or "")
        entity_id = str((contract.get("entity_ids") or ["Order"])[0])
        for endpoint in contract.get("endpoints") or []:
            endpoint_id = str(endpoint.get("id") or "")
            scene_id = f"scene:{entity_id}"
            endpoint_field = {
                "side": "response",
                "location": "response_body",
                "path": "id",
                "type": "string",
                "required": False,
                "description": "",
            }
            entity_field = {
                "entityId": scene_id,
                "fieldId": "id",
                "path": "id",
                "type": "string",
            }
            data_field = (
                {
                    "sourceType": "external_api",
                    "sourceId": "test-external-api",
                    "directoryId": "orders",
                    "operationId": "orders.list",
                    "section": "response_body",
                    "path": "data.id",
                    "type": "string",
                }
                if source_type == "external_api"
                else {
                    "sourceType": "database",
                    "sourceId": "test-database",
                    "schema": "app",
                    "table": entity_id.lower(),
                    "column": "id",
                    "type": "string",
                }
            )
            write_endpoint_design(
                workspace,
                EndpointApiDesign.model_validate(
                    {
                        "schemaVersion": "endpoint-field-mapping.v1",
                        "artifactType": "endpoint-field-mapping",
                        "status": "confirmed",
                        "confirmationStatus": "confirmed",
                        "apiContractId": contract_id,
                        "endpointId": endpoint_id,
                        "artifactRevision": "0123456789abcdef0123456789abcdef",
                        "endpointContract": endpoint,
                        "sceneEntities": [{
                            "id": scene_id,
                            "name": entity_id,
                            "templateEntityId": entity_id,
                            "fields": [{"id": "id", "name": "id", "label": "id", "type": "string"}],
                        }],
                        "fieldMappings": [{
                            "endpointField": endpoint_field,
                            "mappingType": "through_entity",
                            "entityField": entity_field,
                            "sourceField": data_field,
                        }],
                        "sourceSnapshots": [
                            {
                                "sourceType": source_type,
                                "sourceId": data_field["sourceId"],
                                "name": "测试来源",
                                "details": {},
                            }
                        ],
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


class PageBuildContextResolverTests(unittest.TestCase):
    def _assert_no_source_or_contract_fields(self, context: dict) -> None:
        """endpoint/page 上下文不得携带数据源类型或契约清单字段。"""

        for key in (
            "api_contract_ids",
            "data_source_ids",
            "entity_source_types",
            "database_source_ids",
            "database_endpoint_refs",
        ):
            self.assertNotIn(key, context)

    def test_page_context_uses_implementation_contract_without_page_detail(self) -> None:
        """新版 TechnicalPlan 应直接用页面实现契约解析接口，不要求 PageDetail 文件。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertNotIn("page_detail", context)
        self.assertEqual(
            context["page_implementation_contract"]["pageId"],
            "orders",
        )
        self.assertEqual(context["required_endpoint_ids"], ["orders.list"])

    def test_page_context_uses_technical_plan_endpoint_contract(self) -> None:
        """页面 scope 直接加载 requiredEndpoints 对应的 TechnicalPlan 契约。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))

            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertEqual(context["endpoint_ids"], ["orders.list"])
        self.assertEqual(context["entity_ids"], ["scene:Order"])
        self.assertEqual(context["source_types"], ["database"])
        self.assertEqual(context["endpoint_designs"][0]["sourceSnapshots"][0]["sourceType"], "database")
        self.assertEqual(context["page_implementation_contract"]["pageId"], "orders")
        self.assertEqual(
            [endpoint["id"] for endpoint in context["direct_endpoint_contracts"]],
            ["orders.list"],
        )
        self.assertEqual(
            [reference["id"] for reference in context["source_refs"]["technical_plan_endpoints"]],
            ["orders.list"],
        )
        self.assertEqual(context["required_endpoint_ids"], ["orders.list"])
        self._assert_no_source_or_contract_fields(context)
        self.assertFalse(any(unit.startswith("database:") for unit in context["required_unit_ids"]))
        self.assertIn("backend:bootstrap", context["required_unit_ids"])
        self.assertIn(
            "backend:endpoint:orders-api:orders.list",
            context["required_unit_ids"],
        )
        self.assertIn("frontend:auth-guard", context["required_unit_ids"])
        self.assertNotIn("frontend:route-registry", context["required_unit_ids"])

    def test_page_context_limits_shared_data_source_to_direct_contract(self) -> None:
        """同一数据源对应多个契约时，页面 scope 只投射直接依赖的契约。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))
            plan["api_contracts"][0]["schemas"] = {
                "Order": {"type": "object", "properties": {"id": {"type": "string"}}}
            }
            plan["frontend_pages"][0]["path"] = "/orders"
            plan["api_contracts"][0]["endpoints"][0].update(
                {
                    "method": "GET",
                    "path": "/orders",
                    "response_schema_ref": "Order",
                }
            )
            _write_endpoint_designs(workspace, plan, source_type="database")
            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )
            validation_plan = _scoped_contract_validation_plan(plan, context)

        self.assertEqual(context["entity_ids"], ["scene:Order"])
        self._assert_no_source_or_contract_fields(context)
        self.assertEqual(
            [contract["id"] for contract in validation_plan["api_contracts"]],
            ["orders-api"],
        )
        self.assertEqual(
            [
                endpoint["id"]
                for contract in validation_plan["api_contracts"]
                for endpoint in contract["endpoints"]
            ],
            ["orders.list"],
        )
        self.assertNotIn("data_sources", validation_plan)
        self.assertEqual(validation_plan["entities"], [{"id": "Order"}])
        self.assertEqual(validate_project_plan_dependencies(validation_plan), [])
        self.assertEqual(validate_api_contract_consistency(validation_plan), [])

    def test_page_context_only_loads_direct_endpoint_contract(self) -> None:
        """页面 scope 只加载当前页面 requiredEndpoints 对应的 TechnicalPlan 契约。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))

            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertEqual(
            [endpoint["id"] for endpoint in context["direct_endpoint_contracts"]],
            ["orders.list"],
        )
        self.assertEqual(
            [reference["id"] for reference in context["source_refs"]["technical_plan_endpoints"]],
            ["orders.list"],
        )

    def test_data_source_context_is_rejected_after_entity_source_migration(self) -> None:
        """数据源归属迁移到实体设计后，不再接受独立 data_source 构建 scope。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))

            with self.assertRaisesRegex(ValueError, "Unsupported build target type"):
                resolve_target_build_context(
                    plan,
                    target_type="data_source",
                    target_id="database",
                    project_plan_path=plan_path,
                )

    def test_page_context_uses_endpoint_contract_without_external_artifact(self) -> None:
        """页面 requiredEndpoint 直接消费 TechnicalPlan 契约。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))
            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )
            self.assertEqual(context["direct_endpoint_contracts"][0]["id"], "orders.list")

    def test_endpoint_context_uses_current_technical_plan_contract(self) -> None:
        """endpoint scope 只暴露当前 TechnicalPlan 接口和对应 Unit。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))

            context = resolve_target_build_context(
                plan,
                target_type="endpoint",
                target_id="orders.list",
                api_contract_id="orders-api",
                project_plan_path=plan_path,
            )

        self.assertNotIn("page_detail", context)
        self.assertEqual(context["target"]["type"], "endpoint")
        self.assertEqual(context["target"]["api_contract_id"], "orders-api")
        self.assertEqual(context["endpoint_ids"], ["orders.list"])
        self.assertEqual(context["entity_ids"], ["scene:Order"])
        self.assertEqual(context["endpoint_designs"][0]["endpointId"], "orders.list")
        self.assertEqual(context["direct_endpoint_contracts"][0]["id"], "orders.list")
        self._assert_no_source_or_contract_fields(context)
        self.assertFalse(any(unit.startswith("database:") for unit in context["required_unit_ids"]))
        self.assertEqual(
            context["required_unit_ids"],
            ["backend:bootstrap", "backend:endpoint:orders-api:orders.list"],
        )

    def test_external_api_endpoint_context_requires_openfeign_bootstrap(self) -> None:
        """纯外部 API endpoint 要求 OpenFeign bootstrap 与自身后端 Unit。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            plan = confirm_entity_designs(plan, source_type="external_api")
            _write_json(plan_path, plan)
            _write_endpoint_designs(workspace_path, plan, source_type="external_api")

            context = resolve_target_build_context(
                plan,
                target_type="endpoint",
                target_id="orders.list",
                api_contract_id="orders-api",
                project_plan_path=plan_path,
            )

        self.assertEqual(
            context["required_unit_ids"],
            ["backend:bootstrap", "backend:endpoint:orders-api:orders.list"],
        )
        self.assertIn("backend:bootstrap", context["required_unit_ids"])

    def test_external_api_page_context_requires_openfeign_bootstrap(self) -> None:
        """纯外部 API 页面要求 OpenFeign bootstrap 与接口实现。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            plan = confirm_entity_designs(plan, source_type="external_api")
            _write_json(plan_path, plan)
            _write_endpoint_designs(workspace_path, plan, source_type="external_api")

            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertIn(
            "backend:endpoint:orders-api:orders.list",
            context["required_unit_ids"],
        )
        self.assertIn("backend:bootstrap", context["required_unit_ids"])

    def test_endpoint_context_rejects_missing_api_design(self) -> None:
        """Endpoint 尚未完成当前版 API 设计时，上下文必须给出可定位错误。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            for design_path in (workspace_path / ".xcodeagent/plans/endpoints").glob("*"):
                design_path.unlink()

            with self.assertRaisesRegex(ValueError, "缺少当前版已确认动态映射"):
                resolve_target_build_context(
                    plan,
                    target_type="endpoint",
                    target_id="orders.list",
                    api_contract_id="orders-api",
                    project_plan_path=plan_path,
                )

    def test_endpoint_context_allows_zero_entity_semantic_references(self) -> None:
        """复杂 API 业务允许不引用实体字段，实体不再是全局门禁。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            context = resolve_target_build_context(
                plan,
                target_type="endpoint",
                target_id="orders.list",
                api_contract_id="orders-api",
                project_plan_path=plan_path,
            )
            self.assertEqual(context["entity_ids"], ["scene:Order"])

    def test_legacy_static_entity_design_does_not_change_api_build_units(self) -> None:
        """旧实体静态设计不影响 Endpoint API 设计决定的 Build Unit。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            plan = confirm_entity_designs(plan, source_type="static")
            _write_json(plan_path, plan)
            _write_endpoint_designs(workspace_path, plan, source_type="database")

            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertNotIn("frontend:data:static", context["required_unit_ids"])
        self.assertIn("backend:bootstrap", context["required_unit_ids"])
        self.assertFalse(any(unit.startswith("database:") for unit in context["required_unit_ids"]))
        self.assertTrue(any(unit.startswith("backend:endpoint:") for unit in context["required_unit_ids"]))

    def test_page_context_rejects_unknown_endpoint(self) -> None:
        """页面实现契约引用未知 endpoint 时返回明确错误。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))
            plan["page_implementation_contracts"][0]["requiredEndpointIds"] = [
                "orders.unknown"
            ]

            with self.assertRaisesRegex(ValueError, "unknown endpoint orders.unknown"):
                resolve_target_build_context(
                    plan,
                    target_type="page",
                    target_id="orders",
                    project_plan_path=plan_path,
                )

if __name__ == "__main__":
    unittest.main()
