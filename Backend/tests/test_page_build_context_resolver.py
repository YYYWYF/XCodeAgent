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
    """构造只保存外置详情引用的 ProjectPlan。"""

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
    _write_json(
        workspace / ".xcodeagent/plans/data-source/data-source--orders.json",
        {"data_source_id": "orders", "status": "confirmed", "entities": [{"name": "Order"}]},
    )
    _write_json(
        workspace / ".xcodeagent/plans/data-source/data-source--customers.json",
        {"data_source_id": "customers", "status": "confirmed", "entities": [{"name": "Customer"}]},
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
            {
                "id": "orders",
                "detail_design": _detail_ref(".xcodeagent/plans/data-source/data-source--orders.json"),
            },
            {
                "id": "customers",
                "detail_design": _detail_ref(".xcodeagent/plans/data-source/data-source--customers.json"),
            },
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
    def test_page_context_loads_only_direct_external_data_source_details(self) -> None:
        """页面 scope 只按当前页面外置详情加载直接数据源详情。"""

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
        self.assertEqual(
            [detail["data_source_id"] for detail in context["direct_data_source_details"]],
            ["orders"],
        )
        self.assertIn("data-source:orders", context["required_unit_ids"])
        self.assertNotIn("data-source:customers", context["required_unit_ids"])
        self.assertIn("app:auth-guard", context["required_unit_ids"])

    def test_data_source_context_loads_external_detail_without_page_details(self) -> None:
        """数据源 scope 只读取目标数据源外置详情，不反向加载页面详情。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))

            context = resolve_target_build_context(
                plan,
                target_type="data_source",
                target_id="orders",
                project_plan_path=plan_path,
            )

        self.assertIsNone(context["page_detail"])
        self.assertEqual(context["data_source_detail"]["data_source_id"], "orders")
        self.assertEqual(context["required_unit_ids"], ["app:backend-bootstrap", "data-source:orders"])

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

    def test_page_context_rejects_missing_data_source_detail_file(self) -> None:
        """页面依赖的数据源详情文件不存在时返回明确错误。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan, plan_path = _project_plan(Path(workspace))
            plan["data_sources"][0]["detail_design"] = _detail_ref(
                ".xcodeagent/plans/data-source/missing.json"
            )

            with self.assertRaisesRegex(ValueError, "DataSourceDetail orders detail file does not exist"):
                resolve_target_build_context(
                    plan,
                    target_type="page",
                    target_id="orders",
                    project_plan_path=plan_path,
                )


if __name__ == "__main__":
    unittest.main()
