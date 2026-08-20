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


def _detail_ref(path: str, *, status: str = "confirmed") -> dict:
    """构造外置详情 artifact 的轻量引用。"""

    return {
        "status": status,
        "json_path": path,
        "sha256": f"sha-{path}",
    }


def _write_json(path: Path, payload: dict) -> None:
    """把测试详情写入临时工作区。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project_plan(workspace: Path) -> tuple[dict, Path]:
    """构造 PageDetail 与 EndpointDetail 均独立外置的 ProjectPlan。"""

    plan_path = workspace / ".xcodeagent/plans/project-plan.json"
    _write_json(
        workspace / ".xcodeagent/plans/pages/page--orders.json",
        {
            "pageId": "orders",
            "status": "confirmed",
            "references": {"endpoint_dependencies": [{"endpoint_id": "orders.list"}]},
        },
    )
    _write_json(
        workspace / ".xcodeagent/plans/pages/page--customers.json",
        {
            "pageId": "customers",
            "status": "confirmed",
            "references": {"endpoint_dependencies": [{"endpoint_id": "customers.list"}]},
        },
    )
    for contract_id, endpoint_id in (
        ("orders-api", "orders.list"),
        ("customers-api", "customers.list"),
    ):
        _write_json(
            workspace
            / f".xcodeagent/plans/endpoints/endpoint--{contract_id}--{endpoint_id}.json",
            {
                "api_contract_id": contract_id,
                "endpoint_id": endpoint_id,
                "status": "confirmed",
            },
        )
    plan = {
        "frontend_pages": [
            {
                "pageId": "orders",
                "detail_design": _detail_ref(".xcodeagent/plans/pages/page--orders.json"),
                "references": {"permissions": ["admin"]},
            },
            {
                "pageId": "customers",
                "detail_design": _detail_ref(".xcodeagent/plans/pages/page--customers.json"),
                "references": {"permissions": ["admin"]},
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
                        "detail_design": _detail_ref(
                            ".xcodeagent/plans/endpoints/endpoint--orders-api--orders.list.json"
                        ),
                    }
                ],
            },
            {
                "id": "customers-api",
                "entity_ids": ["Customer"],
                "endpoints": [
                    {
                        "id": "customers.list",
                        "detail_design": _detail_ref(
                            ".xcodeagent/plans/endpoints/endpoint--customers-api--customers.list.json"
                        ),
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
            plan["page_implementation_contracts"] = [
                {
                    "schema_version": "page-implementation-contract.v1",
                    "pageId": "orders",
                    "uiDesignRef": {"path": ".xcodeagent/ui-design/pages/Orders/index.tsx"},
                    "requiredEndpointIds": ["orders.list"],
                }
            ]
            plan["frontend_pages"][0].pop("detail_design", None)

            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertIsNone(context["page_detail"])
        self.assertEqual(
            context["page_implementation_contract"]["pageId"],
            "orders",
        )
        self.assertEqual(context["required_endpoint_ids"], ["orders.list"])

    def test_page_context_requires_and_loads_endpoint_details(self) -> None:
        """页面 scope 必须加载 requiredEndpoints 对应的独立 EndpointDetail。"""

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
        self.assertEqual(context["page_detail"]["pageId"], "orders")
        self.assertEqual(
            [detail["endpoint_id"] for detail in context["direct_endpoint_details"]],
            ["orders.list"],
        )
        self.assertEqual(
            [reference["id"] for reference in context["source_refs"]["endpoint_details"]],
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

    def test_page_context_only_loads_direct_endpoint_detail(self) -> None:
        """页面 scope 只加载当前页面 requiredEndpoints 对应的详情。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            detail_path = ".xcodeagent/plans/endpoints/endpoint--orders-api--orders.list.json"
            plan["api_contracts"][0]["endpoints"][0]["detail_design"] = _detail_ref(detail_path)
            _write_json(
                workspace_path / detail_path,
                {
                    "api_contract_id": "orders-api",
                    "endpoint_id": "orders.list",
                    "status": "confirmed",
                },
            )

            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertEqual(
            [detail["endpoint_id"] for detail in context["direct_endpoint_details"]],
            ["orders.list"],
        )
        self.assertEqual(
            [reference["id"] for reference in context["source_refs"]["endpoint_details"]],
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

    def test_page_context_rejects_missing_required_endpoint_detail(self) -> None:
        """页面 requiredEndpoint 缺少独立详情时必须返回可定位错误。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))
            plan["api_contracts"][0]["endpoints"][0].pop("detail_design")

            with self.assertRaisesRegex(ValueError, "EndpointDetail orders.list is missing"):
                resolve_target_build_context(
                    plan,
                    target_type="page",
                    target_id="orders",
                    project_plan_path=plan_path,
                )

    def test_endpoint_context_requires_current_confirmed_endpoint_detail(self) -> None:
        """endpoint scope 只暴露当前接口详情和它对应的 endpoint Unit。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            detail_path = ".xcodeagent/plans/endpoints/endpoint--orders-api--orders.list.json"
            plan["api_contracts"][0]["endpoints"][0]["detail_design"] = _detail_ref(detail_path)
            _write_json(
                workspace_path / detail_path,
                {
                    "api_contract_id": "orders-api",
                    "endpoint_id": "orders.list",
                    "status": "confirmed",
                    "interface_design": {"route": "GET /orders"},
                },
            )

            context = resolve_target_build_context(
                plan,
                target_type="endpoint",
                target_id="orders.list",
                api_contract_id="orders-api",
                project_plan_path=plan_path,
            )

        self.assertIsNone(context["page_detail"])
        self.assertEqual(context["target"]["type"], "endpoint")
        self.assertEqual(context["target"]["api_contract_id"], "orders-api")
        self.assertEqual(context["endpoint_ids"], ["orders.list"])
        self.assertEqual(context["entity_ids"], ["Order"])
        self.assertEqual(context["entity_designs"][0]["entity_id"], "Order")
        self.assertEqual(context["direct_endpoint_details"][0]["endpoint_id"], "orders.list")
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
        """页面外置详情引用未知 endpoint 时返回明确错误。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            _write_json(
                workspace_path / ".xcodeagent/plans/pages/page--orders.json",
                {
                    "pageId": "orders",
                    "status": "confirmed",
                    "references": {"endpoint_dependencies": [{"endpoint_id": "orders.unknown"}]},
                },
            )

            with self.assertRaisesRegex(ValueError, "unknown endpoint orders.unknown"):
                resolve_target_build_context(
                    plan,
                    target_type="page",
                    target_id="orders",
                    project_plan_path=plan_path,
                )

    def test_page_context_rejects_missing_page_detail_file(self) -> None:
        """页面详情引用文件不存在时返回明确错误。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))
            plan["frontend_pages"][0]["detail_design"] = _detail_ref(
                ".xcodeagent/plans/pages/missing.json"
            )

            with self.assertRaisesRegex(ValueError, "PageDetail orders detail file does not exist"):
                resolve_target_build_context(
                    plan,
                    target_type="page",
                    target_id="orders",
                    project_plan_path=plan_path,
                )

    def test_page_context_rejects_unconfirmed_external_page_detail(self) -> None:
        """页面外置详情未确认时返回明确错误。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            _write_json(
                workspace_path / ".xcodeagent/plans/pages/page--orders.json",
                {"pageId": "orders", "status": "draft"},
            )

            with self.assertRaisesRegex(ValueError, "PageDetail orders external detail is not confirmed"):
                resolve_target_build_context(
                    plan,
                    target_type="page",
                    target_id="orders",
                    project_plan_path=plan_path,
                )

    def test_page_context_rejects_missing_endpoint_detail_file(self) -> None:
        """requiredEndpoint 详情引用失效时不得生成缺少接口任务的页面 DAG。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))
            plan["api_contracts"][0]["endpoints"][0]["detail_design"] = _detail_ref(
                ".xcodeagent/plans/endpoints/missing.json"
            )

            with self.assertRaisesRegex(
                ValueError,
                "EndpointDetail orders.list detail file does not exist",
            ):
                resolve_target_build_context(
                    plan,
                    target_type="page",
                    target_id="orders",
                    project_plan_path=plan_path,
                )


if __name__ == "__main__":
    unittest.main()
