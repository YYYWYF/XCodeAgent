from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.page_detail_plan import (
    create_data_source_detail_plan,
    create_page_detail_plan,
    extract_page_detail_context,
)
from app.services.page_dependencies import (
    page_data_source_ids,
    validate_project_plan_dependencies,
)
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec
from app.workspace.plan_documents import load_project_plan_json, write_project_plan_document


class DetailDesignDocumentsTests(unittest.TestCase):
    """验证详细设计从主计划拆分后的文件与索引契约。"""

    def test_project_plan_keeps_only_selected_detail_references(self) -> None:
        """写入计划后，页面和数据源详情应位于独立文件而非主 JSON。"""

        project_plan = create_project_plan(create_requirement_spec("创建库存管理系统"))
        page = next(
            candidate
            for candidate in project_plan["frontend_pages"]
            if candidate["references"]["endpoint_dependencies"]
        )
        source_id = page_data_source_ids(page, project_plan["api_contracts"])[0]
        page_detail = create_page_detail_plan(
            project_plan,
            extract_page_detail_context(project_plan, page["id"]),
        )
        data_source_detail = create_data_source_detail_plan(project_plan, source_id)
        project_plan["page_detail_plans"] = [page_detail]
        project_plan["data_source_detail_plans"] = [data_source_detail]

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = {"workspace": str(workspace)}
            write_project_plan_document(state, project_plan)
            stored = load_project_plan_json(
                workspace / ".xcodeagent" / "plans" / "project-plan.json"
            )

            self.assertNotIn("page_detail_plans", stored)
            self.assertNotIn("data_source_detail_plans", stored)
            page_reference = next(
                stored_page["detail_design"]
                for stored_page in stored["frontend_pages"]
                if stored_page["id"] == page["id"]
            )
            source_reference = next(
                source["detail_design"]
                for source in stored["data_sources"]
                if source["id"] == source_id
            )
            self.assertEqual(
                page_reference["generation_dependencies"]["endpoint_ids"],
                [item["endpoint_id"] for item in page["references"]["endpoint_dependencies"]],
            )
            self.assertTrue((workspace / page_reference["json_path"]).is_file())
            self.assertTrue((workspace / source_reference["json_path"]).is_file())
            self.assertIn("plans/data-source/", source_reference["json_path"])
            persisted_detail = load_project_plan_json(workspace / page_reference["json_path"])
            self.assertIn("references", persisted_detail)
            self.assertNotIn("source_page_context", persisted_detail)
            self.assertNotIn("agent_note", persisted_detail)
            self.assertNotIn("api_dependencies", persisted_detail)
            self.assertNotIn("data_sources", persisted_detail)

    def test_page_detail_cannot_replace_project_plan_references(self) -> None:
        """模型返回的任意依赖都不得覆盖 ProjectPlan 的页面引用。"""

        project_plan = create_project_plan(create_requirement_spec("创建库存管理系统"))
        page = next(
            candidate
            for candidate in project_plan["frontend_pages"]
            if candidate["references"]["endpoint_dependencies"]
        )
        detail = create_page_detail_plan(
            project_plan,
            extract_page_detail_context(project_plan, page["id"]),
            agent_detail_plan={
                "permissions": ["invented_role"],
                "api_dependencies": [{"endpoint_id": "invented.endpoint"}],
                "page_navigation": [{"target_page_id": "invented_page"}],
            },
        )

        self.assertEqual(detail["permissions"], page["references"]["permissions"])
        self.assertEqual(
            detail["endpoint_dependencies"], page["references"]["endpoint_dependencies"]
        )
        self.assertEqual(detail["navigation_targets"], page["references"]["navigation_targets"])
        self.assertEqual(validate_project_plan_dependencies(project_plan), [])

    def test_project_plan_rejects_duplicate_routes_and_unknown_endpoints(self) -> None:
        """ProjectPlan 必须在页面详情生成前拦截不可解析的页面依赖。"""

        project_plan = create_project_plan(create_requirement_spec("创建库存管理系统"))
        project_plan["frontend_pages"][1]["path"] = project_plan["frontend_pages"][0]["path"]
        project_plan["frontend_pages"][0]["references"]["endpoint_dependencies"] = [
            {"endpoint_id": "missing.endpoint"}
        ]

        errors = validate_project_plan_dependencies(project_plan)

        self.assertTrue(any("Duplicate page path" in error for error in errors))
        self.assertTrue(any("unknown endpoint" in error for error in errors))

    def test_frontend_page_keeps_dependencies_only_in_references(self) -> None:
        """首次生成的页面索引不得并列保存可由 references 或 API 契约推导的字段。"""

        project_plan = create_project_plan(create_requirement_spec("创建库存管理系统"))
        page = project_plan["frontend_pages"][0]

        self.assertEqual(
            set(page),
            {"id", "name", "path", "module_id", "description", "references"},
        )
        self.assertEqual(
            set(page["references"]),
            {"permissions", "endpoint_dependencies", "navigation_targets"},
        )


if __name__ == "__main__":
    unittest.main()
