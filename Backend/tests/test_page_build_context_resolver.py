from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.build_context_resolver import resolve_target_build_context
from app.services.page_dependencies import validate_project_plan_dependencies
from app.graph.nodes.tasks import _scoped_contract_validation_plan
from tests.entity_design_test_utils import confirm_entity_designs


def _write_json(path: Path, payload: dict) -> None:
    """把测试计划写入临时工作区。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project_plan(workspace: Path) -> tuple[dict, Path]:
    """构造带页面实现契约、TechnicalPlan Endpoint 与实体绑定的计划。"""

    plan_path = workspace / ".xcodeagent/plans/project-plan.json"
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
                "fields": [],
            },
            {
                "id": "Customer",
                "name": "Customer",
                "fields": [],
            },
        ],
        "api_contracts": [
            {
                "id": "orders-api",
                "entity_ids": ["Order"],
                "endpoints": [
                    {
                        "id": "orders.list",
                    }
                ],
            },
            {
                "id": "customers-api",
                "entity_ids": ["Customer"],
                "endpoints": [
                    {
                        "id": "customers.list",
                    }
                ],
            },
        ],
    }
    plan = confirm_entity_designs(plan, source_type="database")
    _write_json(plan_path, plan)
    return plan, plan_path


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
        self.assertEqual(context["entity_ids"], ["Order"])
        self.assertEqual(context["entity_designs"][0]["data_source_type"], "database")
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
                "Order": {"type": "object", "properties": {}}
            }
            plan["frontend_pages"][0]["path"] = "/orders"
            plan["api_contracts"][0]["endpoints"][0].update(
                {
                    "method": "GET",
                    "path": "/orders",
                    "response_schema_ref": "Order",
                }
            )
            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )
            validation_plan = _scoped_contract_validation_plan(plan, context)

        self.assertEqual(context["entity_ids"], ["Order"])
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
        self.assertEqual(context["entity_ids"], ["Order"])
        self.assertEqual(context["entity_designs"][0]["entity_id"], "Order")
        self.assertEqual(context["direct_endpoint_contracts"][0]["id"], "orders.list")
        self._assert_no_source_or_contract_fields(context)
        self.assertFalse(any(unit.startswith("database:") for unit in context["required_unit_ids"]))
        self.assertEqual(
            context["required_unit_ids"],
            ["backend:bootstrap", "backend:endpoint:orders-api:orders.list"],
        )

    def test_external_api_endpoint_context_omits_database_bootstrap(self) -> None:
        """纯外部 API endpoint 只要求自身后端 Unit。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            plan = confirm_entity_designs(plan, source_type="external_api")
            _write_json(plan_path, plan)

            context = resolve_target_build_context(
                plan,
                target_type="endpoint",
                target_id="orders.list",
                api_contract_id="orders-api",
                project_plan_path=plan_path,
            )

        self.assertEqual(
            context["required_unit_ids"],
            ["backend:endpoint:orders-api:orders.list"],
        )
        self.assertNotIn("backend:bootstrap", context["required_unit_ids"])

    def test_external_api_page_context_omits_database_bootstrap(self) -> None:
        """纯外部 API 页面仍要求接口实现，但不要求数据库 bootstrap。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            plan = confirm_entity_designs(plan, source_type="external_api")
            _write_json(plan_path, plan)

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
        self.assertNotIn("backend:bootstrap", context["required_unit_ids"])

    def test_endpoint_context_rejects_missing_entity_design(self) -> None:
        """绑定实体尚未完成并确认实体设计时，endpoint 上下文必须给出可定位错误。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            plan.pop("entity_detail_plans", None)

            with self.assertRaisesRegex(ValueError, "绑定实体 Order 缺少已确认实体设计"):
                resolve_target_build_context(
                    plan,
                    target_type="endpoint",
                    target_id="orders.list",
                    api_contract_id="orders-api",
                    project_plan_path=plan_path,
                )

    def test_endpoint_context_rejects_empty_entity_binding(self) -> None:
        """契约未绑定任何实体时，endpoint 上下文必须给出可定位错误。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            plan["api_contracts"][0]["entity_ids"] = []

            with self.assertRaisesRegex(ValueError, "未绑定任何实体"):
                resolve_target_build_context(
                    plan,
                    target_type="endpoint",
                    target_id="orders.list",
                    api_contract_id="orders-api",
                    project_plan_path=plan_path,
                )

    def test_static_page_context_only_requires_frontend_data_module(self) -> None:
        """Static 页面不要求后端、数据库或业务 Endpoint Unit。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            plan = confirm_entity_designs(plan, source_type="static")
            _write_json(plan_path, plan)

            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertIn("frontend:data:static", context["required_unit_ids"])
        self.assertNotIn("backend:bootstrap", context["required_unit_ids"])
        self.assertFalse(any(unit.startswith("database:") for unit in context["required_unit_ids"]))
        self.assertFalse(any(unit.startswith("backend:endpoint:") for unit in context["required_unit_ids"]))

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
