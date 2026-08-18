from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.graph.nodes.tasks import _scoped_contract_validation_plan
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.build_context_resolver import resolve_target_build_context


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
    for contract_id, endpoint_id, source_id in (
        ("orders-api", "orders.list", "orders"),
        ("customers-api", "customers.list", "customers"),
    ):
        _write_json(
            workspace
            / f".xcodeagent/plans/endpoints/endpoint--{contract_id}--{endpoint_id}.json",
            {
                "api_contract_id": contract_id,
                "endpoint_id": endpoint_id,
                "data_source_id": source_id,
                "status": "confirmed",
                "data_origin": {
                    "source_type": "database",
                    "effective_source": {"kind": "mysql_existing"},
                },
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
        "data_sources": [
            {"id": "orders", "type": "database"},
            {"id": "customers", "type": "database"},
        ],
        "api_contracts": [
            {
                "id": "orders-api",
                "data_source_id": "orders",
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
                "data_source_id": "customers",
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
    _write_json(plan_path, plan)
    return plan, plan_path


class PageBuildContextResolverTests(unittest.TestCase):
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
        self.assertEqual(context["api_contract_ids"], ["orders-api"])
        self.assertEqual(context["data_source_ids"], ["orders"])
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
        self.assertIn("database:orders", context["required_unit_ids"])
        self.assertNotIn("database:customers", context["required_unit_ids"])
        self.assertIn(
            "backend:endpoint:orders-api:orders.list",
            context["required_unit_ids"],
        )
        self.assertIn("frontend:auth-guard", context["required_unit_ids"])

    def test_page_context_limits_shared_data_source_to_direct_contract(self) -> None:
        """同一数据源对应多个契约时，页面 scope 只投射直接依赖的契约。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))
            plan["api_contracts"][1]["data_source_id"] = "orders"
            plan["api_contracts"][0]["schemas"] = {
                "Order": {"type": "object", "properties": {}}
            }
            plan["api_contracts"][0]["endpoints"][0].update(
                {
                    "method": "GET",
                    "path": "/orders",
                    "response_schema_ref": "Order",
                }
            )
            plan["data_sources"][0]["schema_refs"] = [
                "orders-api#/schemas/Order",
                "customers-api#/schemas/Customer",
            ]

            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )
            validation_plan = _scoped_contract_validation_plan(plan, context)

        self.assertEqual(context["data_source_ids"], ["orders"])
        self.assertEqual(context["api_contract_ids"], ["orders-api"])
        self.assertEqual(
            [contract["id"] for contract in validation_plan["api_contracts"]],
            ["orders-api"],
        )
        self.assertEqual(
            validation_plan["data_sources"][0]["schema_refs"],
            ["orders-api#/schemas/Order"],
        )
        self.assertEqual(
            [
                endpoint["id"]
                for contract in validation_plan["api_contracts"]
                for endpoint in contract["endpoints"]
            ],
            ["orders.list"],
        )
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
                    "data_source_id": "orders",
                    "status": "confirmed",
                    "data_origin": {
                        "source_type": "database",
                        "effective_source": {"kind": "mysql_existing"},
                    },
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

    def test_data_source_context_loads_available_endpoint_detail(self) -> None:
        """数据源 scope 保持原逻辑，并加载可用的独立 EndpointDetail。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))

            context = resolve_target_build_context(
                plan,
                target_type="data_source",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertIsNone(context["page_detail"])
        self.assertEqual(context["endpoint_ids"], ["orders.list"])
        self.assertEqual(
            [detail["endpoint_id"] for detail in context["direct_endpoint_details"]],
            ["orders.list"],
        )
        self.assertEqual(context["required_unit_ids"], ["database:orders"])

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
                    "data_source_id": "orders",
                    "status": "confirmed",
                    "interface_design": {"route": "GET /orders"},
                    "data_origin": {
                        "source_type": "database",
                        "effective_source": {"kind": "mysql_existing"},
                    },
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
        self.assertEqual(context["api_contract_ids"], ["orders-api"])
        self.assertEqual(context["direct_endpoint_details"][0]["endpoint_id"], "orders.list")
        self.assertEqual(
            context["required_unit_ids"],
            ["backend:bootstrap", "database:orders", "backend:endpoint:orders-api:orders.list"],
        )

    def test_static_page_context_only_requires_frontend_data_module(self) -> None:
        """Static 页面不要求后端、数据库或业务 Endpoint Unit。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            plan["data_sources"] = [
                {**source, "type": "static"} for source in plan["data_sources"]
            ]
            detail_path = workspace_path / ".xcodeagent/plans/endpoints/endpoint--orders-api--orders.list.json"
            detail = json.loads(detail_path.read_text(encoding="utf-8"))
            detail["data_origin"] = {
                "source_type": "static",
                "effective_source": {"kind": "frontend_mock"},
            }
            _write_json(detail_path, detail)

            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertIn("frontend:data:orders", context["required_unit_ids"])
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
