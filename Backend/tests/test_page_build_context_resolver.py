from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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
    """构造以 API 契约为权威、仅页面详情外置的 ProjectPlan。"""

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
            {"id": "orders"},
            {"id": "customers"},
        ],
        "api_contracts": [
            {
                "id": "orders-api",
                "data_source_id": "orders",
                "endpoints": [{"id": "orders.list"}],
            },
            {
                "id": "customers-api",
                "data_source_id": "customers",
                "endpoints": [{"id": "customers.list"}],
            },
        ],
    }
    _write_json(plan_path, plan)
    return plan, plan_path


class PageBuildContextResolverTests(unittest.TestCase):
    def test_page_context_uses_project_plan_contract_without_endpoint_detail(self) -> None:
        """页面依赖已在 ProjectPlan 中声明时不要求独立 endpoint 详情文件。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))

            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertEqual(context["endpoint_ids"], ["orders.list"])
        self.assertEqual(context["data_source_ids"], ["orders"])
        self.assertEqual(context["page_detail"]["pageId"], "orders")
        self.assertEqual(context["direct_endpoint_details"], [])
        self.assertEqual(context["source_refs"]["endpoint_details"], [])
        self.assertIn("data-source:orders", context["required_unit_ids"])
        self.assertNotIn("data-source:customers", context["required_unit_ids"])
        self.assertIn("app:auth-guard", context["required_unit_ids"])

    def test_page_context_loads_confirmed_endpoint_detail_as_optional_context(self) -> None:
        """存在已确认 endpoint 详情时只把当前页面直接依赖的详情作为补充上下文。"""

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            plan, plan_path = _project_plan(workspace_path)
            detail_path = ".xcodeagent/plans/endpoints/endpoint--orders-api--orders.list.json"
            plan["api_contracts"][0]["endpoints"][0]["detail_design"] = _detail_ref(detail_path)
            _write_json(
                workspace_path / detail_path,
                {
                    "endpoint_id": "orders.list",
                    "data_source_id": "orders",
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

    def test_data_source_context_uses_project_plan_contract_without_endpoint_detail(self) -> None:
        """数据源 scope 直接使用所属 ProjectPlan endpoints，不要求独立详情文件。"""

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
        self.assertEqual(context["direct_endpoint_details"], [])
        self.assertEqual(context["required_unit_ids"], ["app:backend-bootstrap", "data-source:orders"])

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
            ["app:backend-bootstrap", "data-source:orders", "endpoint:orders-api:orders.list"],
        )

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

    def test_page_context_ignores_missing_optional_endpoint_detail_file(self) -> None:
        """可选 endpoint 详情引用失效时仍以 ProjectPlan API 契约继续构建。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))
            plan["api_contracts"][0]["endpoints"][0]["detail_design"] = _detail_ref(
                ".xcodeagent/plans/endpoints/missing.json"
            )

            context = resolve_target_build_context(
                plan,
                target_type="page",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertEqual(context["endpoint_ids"], ["orders.list"])
        self.assertEqual(context["direct_endpoint_details"], [])


if __name__ == "__main__":
    unittest.main()
