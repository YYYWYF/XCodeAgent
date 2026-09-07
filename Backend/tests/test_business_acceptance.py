"""DAG 业务验收检查编译、来源追溯和修复继承测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from app.services.business_acceptance import (
    BUSINESS_ACCEPTANCE_KINDS,
    business_acceptance_contract_errors,
    compile_business_acceptance,
    compile_repair_business_acceptance,
    _entities_from_endpoint_designs,
    _external_designs,
    _external_operations_from_field_mappings,
)
from app.services.engineering_acceptance import compile_engineering_acceptance
from app.services.engineering_acceptance_verifier import verify_engineering_acceptance


def _formal_context() -> dict:
    """构造覆盖 API、页面和 Endpoint API 设计的最小正式上下文。"""

    return {
        "project_plan": {
            "api_contracts": [
                {
                    "id": "orders-api",
                    "schemas": {
                        "OrderRequest": {
                            "type": "object",
                            "required": ["status"],
                            "properties": {"status": {"type": "string"}},
                        },
                        "OrderResponse": {
                            "type": "object",
                            "required": ["id"],
                            "properties": {"id": {"type": "string"}},
                        },
                    },
                    "endpoints": [
                        {
                            "id": "orders.list",
                            "method": "GET",
                            "path": "/orders",
                            "request_schema_ref": "#/schemas/OrderRequest",
                            "response_schema_ref": "#/schemas/OrderResponse",
                            "parameters": [],
                        }
                    ],
                }
            ],
            "page_implementation_contracts": [
                {
                    "pageId": "orders",
                    "requiredEndpointIds": ["orders.list"],
                }
            ],
            "executable_details": {
                "endpoint_designs": [_endpoint_api_design()],
            },
            "endpoint_detail_plans": [_endpoint_detail()],
            "entity_detail_plans": [_entity_detail()],
        },
        "endpoint_ids": ["orders.list"],
        "entity_ids": ["Order"],
        "endpoint_designs": [_endpoint_api_design()],
        "page_implementation_contract": {
            "pageId": "orders",
            "requiredEndpointIds": ["orders.list"],
        },
        "direct_endpoint_details": [_endpoint_detail()],
    }


def _endpoint_detail() -> dict:
    """构造可投射 operation 语义的 EndpointDetail。"""

    return {
        "endpoint_id": "orders.list",
        "api_contract_id": "orders-api",
        "status": "confirmed",
        "endpoint_decision": {
            "operation_semantics": {
                "operation_kind": "list",
                "selector": {"source": "query", "fields": ["status"]},
                "target_cardinality": "many",
                "transaction_required": False,
                "success_status_code": 200,
            }
        },
        "interface_design": {"response_format": {"status_code": 200}},
    }


def _entity_detail() -> dict:
    """构造包含数据库绑定和外部映射的已确认实体设计。"""

    return {
        "entity_id": "Order",
        "entity_name": "Order",
        "status": "confirmed",
        "data_source_type": "external_api",
        "fields": [
            {"name": "id", "type": "string", "required": True},
            {"name": "status", "type": "string", "required": True},
        ],
        "database_design": {
            "bindings": [
                {
                    "entity_field": "id",
                    "table": "orders",
                    "table_column": "order_id",
                    "rule": "direct",
                }
            ]
        },
        "external_api_design": {
            "connection": {
                "base_url": "https://api.example.com",
                "base_url_config_key": "integrations.orders.base-url",
                "timeout_ms": 10000,
                "headers": [],
            },
            "operations": [
                {
                    "operation_id": "order-list",
                    "name": "查询订单",
                    "endpoint_refs": [
                        {"api_contract_id": "orders-api", "endpoint_id": "orders.list"}
                    ],
                    "api_info": {
                        "method": "GET",
                        "path": "/upstream/orders",
                        "parameters": [
                            {"name": "page", "in": "query", "type": "number", "required": True},
                        ],
                        "headers": [],
                        "request_body": None,
                        "response_body": {"data": {"id": "string", "state": {"value": "open"}}},
                    },
                    "response_handling": {
                        "entity_payload": True,
                        "cardinality": "object",
                        "payload_path": "data",
                        "success_status_codes": [200],
                    },
                    "field_mappings": [
                        {"source_field": "data.id", "entity_field": "id", "rule": "manual"},
                        {
                            "source_field": "data.state.value",
                            "entity_field": "status",
                            "rule": "manual",
                        },
                    ],
                },
            ],
        },
    }


def _endpoint_api_design() -> dict:
    """构造同时包含数据库和外部 API 自包含字段映射的 Endpoint 设计。"""

    api_id = {"side": "response", "location": "response_body", "path": "items[].id", "type": "string", "required": True, "description": ""}
    api_status = {"side": "response", "location": "response_body", "path": "items[].status", "type": "string", "required": True, "description": ""}
    return {
        "schemaVersion": "endpoint-field-mapping.v1",
        "artifactType": "endpoint-field-mapping",
        "status": "confirmed",
        "confirmationStatus": "confirmed",
        "apiContractId": "orders-api",
        "endpointId": "orders.list",
        "endpointContract": {
            "id": "orders.list",
            "method": "GET",
            "path": "/orders",
            "successStatusCode": 200,
        },
        "sceneEntities": [{
            "id": "scene:Order", "name": "订单", "templateEntityId": "Order",
            "fields": [
                {"id": "id", "name": "id", "type": "string", "required": True},
                {"id": "status", "name": "status", "type": "string", "required": True},
            ],
        }],
        "fieldMappings": [
            {"endpointField": api_id, "mappingType": "through_entity", "entityField": {"entityId": "scene:Order", "fieldId": "id", "path": "id", "type": "string"}, "sourceField": {"sourceType": "database", "sourceId": "orders-db", "schema": "app", "table": "orders", "column": "order_id", "type": "string", "usage": "read", "description": ""}},
            {"endpointField": api_status, "mappingType": "through_entity", "entityField": {"entityId": "scene:Order", "fieldId": "status", "path": "status", "type": "string"}, "sourceField": {"sourceType": "external_api", "sourceId": "orders-upstream", "directoryId": "orders-directory", "operationId": "order-list", "section": "response_body", "path": "data.state.value", "type": "string", "description": ""}},
        ],
        "sourceSnapshots": [
            {
                "sourceType": "database",
                "sourceId": "orders-db",
                "name": "订单数据库",
                "details": {"table": "orders", "columns": ["order_id"]},
            },
            {
                "sourceType": "external_api",
                "sourceId": "orders-upstream",
                "name": "订单上游",
                "details": {
                    "directoryId": "orders-directory",
                    "connection": {
                        "baseUrl": "https://api.example.com",
                        "baseUrlConfigKey": "integrations.orders.base-url",
                        "timeoutMs": 10000,
                        "headers": [],
                    },
                    "operation": {
                        "operationId": "order-list",
                        "name": "查询订单",
                        "method": "GET",
                        "path": "/upstream/orders",
                        "pathParameters": [],
                        "queryParameters": [
                            {"name": "page", "in": "query", "type": "number", "required": True}
                        ],
                        "headers": [],
                        "requestStructure": {},
                        "responseStructure": {
                            "type": "object",
                            "fields": [
                                {"path": "data.id", "type": "string"},
                                {"path": "data.state.value", "type": "string"},
                            ],
                        },
                    },
                },
            },
        ],
        "basedOn": [{"artifactKey": "technical-plan", "sha256": "a" * 64}],
        "confirmedAt": "2026-09-04T00:00:00Z",
    }


def _task(kind: str, *, path: str, owner: str, unit_id: str, target_id: str = "") -> dict:
    """构造带跨平台路径和交付物声明的候选 Build Task。"""

    return {
        "id": f"task-{kind.replace('.', '-')}",
        "kind": "code",
        "owner": owner,
        "unit_id": unit_id,
        "allowed_paths": [path],
        "target_files": [path],
        "change_scope": [{"operation": "modify", "path": path}],
        "source_refs": {
            "endpoint_ids": ["orders.list"],
            "entity_ids": ["Order"],
        },
        "deliverables": [
            {
                "id": f"deliverable-{kind.replace('.', '-')}",
                "kind": kind,
                "target_id": target_id,
                "paths": [path],
                "provides": [kind],
            }
        ],
    }


class BusinessAcceptanceCompilationTests(unittest.TestCase):
    """验证业务验收从 fieldMappings 派生，不再依赖图节点或边。"""

    def test_mixed_endpoint_sources_are_projected_per_scene_field(self) -> None:
        """混合数据库与外部 API 来源时，每个场景字段只保留自身映射来源。"""

        task = _task("backend.domain_mapping", path="backend/src/domain/Order.java", owner="backend", unit_id="backend:orders", target_id="Order")
        compiled = compile_business_acceptance([task], _formal_context())[0]
        fields = compiled["business_acceptance_checks"][0]["expected"]["entities"][0]["fields"]
        self.assertEqual({item["name"]: item["source_types"] for item in fields}, {"id": ["database"], "status": ["external_api"]})

    def test_external_operations_are_derived_from_self_contained_rows(self) -> None:
        """外部 Operation 仅由字段行中嵌入的 sourceField 聚合。"""

        design = _endpoint_api_design()
        mappings = [row for row in design["fieldMappings"] if row["sourceField"]["sourceType"] == "external_api"]
        operations = _external_operations_from_field_mappings(design, mappings, "orders-api", "orders.list", entity_payload=True)
        self.assertEqual([item["operation_id"] for item in operations], ["order-list"])
        self.assertEqual(operations[0]["field_mappings"][0]["entity_field"], "status")

    def test_business_description_is_projected(self) -> None:
        """业务说明映射进入独立验收期望。"""

        design = _endpoint_api_design()
        design["fieldMappings"].append({
            "endpointField": {"side": "request", "location": "query", "path": "page", "type": "integer", "required": False, "description": ""},
            "mappingType": "business_description",
            "businessDescription": "分页页码",
        })
        entities = _entities_from_endpoint_designs([design])
        self.assertEqual(entities[0]["entity_id"], "scene:Order")
        formal = {"endpoint_designs": [design], "entity_details": entities}
        self.assertIn("分页页码", str(formal["endpoint_designs"][0]["fieldMappings"]))

    def test_old_graph_shape_is_not_read(self) -> None:
        """旧 nodes/mappings 结构不会被业务验收逻辑静默转换。"""

        design = _endpoint_api_design()
        design["nodes"] = []
        design["mappings"] = []
        self.assertEqual(_entities_from_endpoint_designs([design])[0]["fields"][0]["source_types"], ["database"])

    def test_target_page_key_requires_entry_in_all_page_task_path_fields(self) -> None:
        """目标页构建必须用 page_key 的入口并在三类路径声明中保持一致。"""

        entry = "frontend/src/pages/AssetList/index.tsx"
        task = {
            "id": "page:asset_list::implementation",
            "owner": "frontend",
            "unit_id": "page:asset_list",
            "change_scope": [{"operation": "add", "path": entry}],
            "allowed_paths": [entry],
            "deliverables": [
                {
                    "id": "page:asset_list::implementation::deliverable",
                    "kind": "frontend.page",
                    "target_id": "asset_list",
                    "paths": [entry],
                    "provides": ["asset_list.render"],
                }
            ],
        }
        context = {"target": {"type": "page", "id": "asset_list", "page_key": "AssetList"}}

        self.assertEqual(business_acceptance_contract_errors(task, context=context), [])
        task["allowed_paths"] = []
        self.assertTrue(
            any("allowed_paths" in error for error in business_acceptance_contract_errors(task, context=context))
        )

    """验证业务检查只由平台按正式输入生成。"""

    def test_all_phase_two_kinds_compile(self) -> None:
        """九种白名单交付物均应生成对应的确定性业务检查。"""

        cases = [
            ("frontend.api_module", "frontend", "frontend:api-client", "frontend/src/apis/orders.ts"),
            ("frontend.page", "frontend", "page:orders", "frontend/src/pages/Orders/index.tsx"),
            ("backend.domain_mapping", "backend", "backend:orders", "backend/src/domain/Order.java"),
            ("backend.repository", "backend", "backend:orders", "backend/src/repository/OrderRepository.java"),
            ("backend.application_service", "backend", "backend:orders", "backend/src/service/OrderService.java"),
            ("backend.endpoint_controller", "backend", "backend:orders.list", "backend/src/controller/OrderController.java"),
            ("backend.external_api_client", "backend", "backend:orders", "backend/src/client/OrderClient.java"),
            ("backend.external_api_mapping", "backend", "backend:orders", "backend/src/mapper/OrderMapper.java"),
        ]
        compiled = compile_business_acceptance(
            [
                _task(
                    kind,
                    path=path.replace("/", "\\"),
                    owner=owner,
                    unit_id=unit_id,
                    target_id="orders.list" if kind == "backend.endpoint_controller" else "Order",
                )
                for kind, owner, unit_id, path in cases
            ],
            _formal_context(),
        )
        actual = {check["kind"] for task in compiled for check in task["business_acceptance_checks"]}
        self.assertEqual(actual, set(BUSINESS_ACCEPTANCE_KINDS) - {"frontend.static_data_contract"})

    def test_task_uses_endpoint_design_instead_of_global_entity_details(self) -> None:
        """后端任务只追溯 Endpoint API 设计，不继承全局实体设计。"""

        context = _formal_context()
        context["entity_ids"] = ["Order", "Customer"]
        context["project_plan"]["entity_detail_plans"].append(
            {
                **_entity_detail(),
                "entity_id": "Customer",
                "entity_name": "Customer",
            }
        )
        tasks = [
            _task(
                "backend.domain_mapping",
                path="backend/src/domain/Order.java",
                owner="backend",
                unit_id="backend:endpoint:orders-api:orders.list",
                target_id="Order",
            ),
            _task(
                "backend.repository",
                path="backend/src/repository/OrderRepository.java",
                owner="backend",
                unit_id="backend:endpoint:orders-api:orders.list",
                target_id="Order",
            ),
        ]

        compiled = compile_business_acceptance(tasks, context)

        for task in compiled:
            api_design_source_ids = {
                source["target_id"]
                for check in task["business_acceptance_checks"]
                for source in check["sources"]
                if source["artifact"] == "api_design"
            }
            self.assertEqual(api_design_source_ids, {"orders.list"})
            self.assertFalse(
                any(
                    source["artifact"] == "entity_design"
                    for check in task["business_acceptance_checks"]
                    for source in check["sources"]
                )
            )
            self.assertFalse(
                any(
                    "references entity" in error
                    for error in business_acceptance_contract_errors(task)
                )
            )

    def test_sibling_domain_deliverables_compile_one_aggregate_check(self) -> None:
        """Entity、PO、DTO 和 Converter 应共同承担一条领域映射检查。"""

        task = _task(
            "backend.domain_mapping",
            path="backend/src/domain/Order.java",
            owner="backend",
            unit_id="backend:orders",
            target_id="Order",
        )
        sibling_paths = [
            "backend/src/po/OrderPO.java",
            "backend/src/dto/OrderDTO.java",
            "backend/src/converter/OrderConverter.java",
        ]
        for index, path in enumerate(sibling_paths, start=1):
            task["deliverables"].append(
                {
                    "id": f"order-domain-{index}",
                    "kind": "backend.domain_mapping",
                    "target_id": "Order",
                    "paths": [path],
                    "provides": [f"order.domain.{index}"],
                }
            )
            task["allowed_paths"].append(path)
            task["target_files"].append(path)
            task["change_scope"].append({"operation": "modify", "path": path})

        compiled = compile_business_acceptance([task], _formal_context())[0]

        self.assertEqual(len(compiled["business_acceptance_checks"]), 1)
        self.assertEqual(
            compiled["business_acceptance_checks"][0]["target_paths"],
            ["backend/src/domain/Order.java", *sibling_paths],
        )
        self.assertEqual(business_acceptance_contract_errors(compiled), [])

    def test_compiled_contract_has_traceable_sources_and_no_criteria_projection(self) -> None:
        """业务检查必须带正式来源哈希，且编译器不生成 Build Task acceptance_criteria。"""

        task = _task(
            "frontend.api_module",
            path="frontend\\src\\apis\\orders.ts",
            owner="frontend",
            unit_id="frontend:api-client",
        )
        task["acceptance_criteria"] = ["模型不应控制平台验收"]
        compiled = compile_business_acceptance([task], _formal_context())[0]
        check = compiled["business_acceptance_checks"][0]
        self.assertNotEqual(check["sources"][0]["sha256"], "")
        self.assertEqual(check["verification"]["mode"], "deterministic")
        self.assertNotIn("verification_commands", check)

    def test_invalid_deliverable_and_check_scope_are_rejected(self) -> None:
        """交付物越权、owner 不匹配和未知业务检查引用必须被拒绝。"""

        task = _task(
            "frontend.api_module",
            path="frontend/src/apis/orders.ts",
            owner="backend",
            unit_id="backend:orders",
        )
        task["deliverables"][0]["paths"] = ["../outside.ts"]
        task["business_acceptance_checks"] = [
            {
                "id": "business:invalid",
                "deliverable_id": "missing-deliverable",
                "kind": "unknown.kind",
                "sources": [],
                "target_paths": ["../outside.ts"],
                "verification": {"mode": "agent", "verifier": "guess"},
                "required": False,
                "verification_stage": "runtime",
                "expected": {},
            }
        ]
        errors = business_acceptance_contract_errors(task)
        self.assertTrue(any("outside the task scope" in error for error in errors))
        self.assertTrue(any("invalid owner or Unit" in error for error in errors))
        self.assertTrue(any("unknown.kind" in error for error in errors))
        self.assertTrue(any("unknown deliverable" in error for error in errors))

    def test_repair_inherits_business_checks_without_changing_expected(self) -> None:
        """Repair Task 必须深拷贝父任务业务检查并保持 expected 不变。"""

        parent = compile_business_acceptance(
            [
                _task(
                    "frontend.api_module",
                    path="frontend/src/apis/orders.ts",
                    owner="frontend",
                    unit_id="frontend:api-client",
                )
            ],
            _formal_context(),
        )[0]
        repair = compile_repair_business_acceptance(
            {"id": "repair:task", "kind": "repair", "business_acceptance_checks": []},
            parent,
        )
        self.assertEqual(
            repair["business_acceptance_checks"],
            parent["business_acceptance_checks"],
        )
        repair["business_acceptance_checks"][0]["expected"] = {"tampered": True}
        self.assertNotEqual(
            repair["business_acceptance_checks"][0]["expected"],
            parent["business_acceptance_checks"][0]["expected"],
        )

    def test_external_api_acceptance_projects_only_linked_operation_and_merged_headers(self) -> None:
        """外部 API 验收只投射当前 Endpoint 操作，且操作 Header 覆盖共享值。"""

        context = _formal_context()
        snapshot = context["endpoint_designs"][0]["sourceSnapshots"][1]
        snapshot["details"]["connection"]["headers"] = [
            {"name": "X-Locale", "value": "en-US"}
        ]
        snapshot["details"]["operation"]["headers"] = [
            {"name": "x-locale", "value": "zh-CN"}
        ]
        task = _task(
            "backend.external_api_client",
            path="backend/src/client/OrderClient.java",
            owner="backend",
            unit_id="backend:orders",
            target_id="Order",
        )

        compiled = compile_business_acceptance([task], context)[0]
        expected = compiled["business_acceptance_checks"][0]["expected"]

        self.assertEqual(
            [item["operation_id"] for item in expected["external_apis"]],
            ["order-list"],
        )
        self.assertEqual(
            expected["external_apis"][0]["api_info"]["headers"],
            [{"name": "x-locale", "value": "zh-CN"}],
        )

    def test_page_engineering_checks_cover_entry_export_placeholder_and_component(self) -> None:
        """页面工程检查应阻断缺少 default export、占位内容或不可达组件。"""

        task = _task(
            "frontend.page",
            path="frontend/src/pages/Orders/index.tsx",
            owner="frontend",
            unit_id="page:orders",
        )
        task["deliverables"][0]["paths"].append("frontend/src/pages/Orders/OrderTable.tsx")
        compiled = compile_engineering_acceptance([task], _formal_context())[0]
        kinds = [check["kind"] for check in compiled["acceptance_checks"]]
        self.assertIn("page_entry", kinds)
        self.assertIn("page_default_export", kinds)
        self.assertIn("page_placeholder", kinds)
        self.assertIn("page_component_reachability", kinds)
        with tempfile.TemporaryDirectory() as temp_dir:
            entry = Path(temp_dir) / "frontend/src/pages/Orders/index.tsx"
            component = Path(temp_dir) / "frontend/src/pages/Orders/OrderTable.tsx"
            component.parent.mkdir(parents=True)
            entry.write_text(
                "import OrderTable from './OrderTable'\nexport default function Orders() { return <OrderTable /> }",
                encoding="utf-8",
            )
            component.write_text("export default function OrderTable() { return null }", encoding="utf-8")
            evidence, errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={
                    "files": [
                        {"path": "frontend/src/pages/Orders/index.tsx", "changeType": "modified"},
                        {"path": "frontend/src/pages/Orders/OrderTable.tsx", "changeType": "modified"},
                    ]
                },
                workspace_root=temp_dir,
            )
        self.assertFalse(errors)
        self.assertTrue(all(item["status"] == "passed" for item in evidence))


if __name__ == "__main__":
    unittest.main()
