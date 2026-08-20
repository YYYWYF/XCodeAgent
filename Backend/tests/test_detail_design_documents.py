from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.page_detail_plan import (
    create_endpoint_detail_plan,
    create_page_detail_plan,
    extract_endpoint_detail_context,
    extract_page_detail_context,
)
from app.services.detail_review import apply_detail_review_submission, detail_review_payload
from app.services.page_dependencies import validate_project_plan_dependencies
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec
from app.workspace.plan_documents import load_project_plan_json, write_project_plan_document
from tests.entity_design_test_utils import confirm_entity_designs


class DetailDesignDocumentsTests(unittest.TestCase):
    """验证详细设计从主计划拆分后的文件与索引契约。"""

    def test_project_plan_keeps_only_selected_detail_references(self) -> None:
        """写入计划后，页面和 EndpointDetail 应独立落盘并通过路径关联。"""

        project_plan = confirm_entity_designs(
            create_project_plan(create_requirement_spec("创建库存管理系统"))
        )
        page = next(
            candidate
            for candidate in project_plan["frontend_pages"]
            if candidate["references"]["endpoint_dependencies"]
        )
        page_detail = create_page_detail_plan(
            project_plan,
            extract_page_detail_context(project_plan, page["pageId"]),
        )
        endpoint_details = [
            create_endpoint_detail_plan(
                project_plan,
                extract_endpoint_detail_context(
                    project_plan,
                    next(
                        contract["id"]
                        for contract in project_plan["api_contracts"]
                        if any(
                            endpoint.get("id") == dependency["endpoint_id"]
                            for endpoint in contract.get("endpoints", [])
                        )
                    ),
                    dependency["endpoint_id"],
                ),
            )
            for dependency in page_detail["endpoint_dependencies"]
        ]
        project_plan["page_detail_plans"] = [page_detail]
        project_plan["endpoint_detail_plans"] = endpoint_details

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = {"workspace": str(workspace)}
            write_project_plan_document(state, project_plan)
            stored = load_project_plan_json(
                workspace / ".xcodeagent" / "plans" / "project-plan.json"
            )

            self.assertNotIn("page_detail_plans", stored)
            self.assertNotIn("endpoint_detail_plans", stored)
            page_reference = next(
                stored_page["detail_design"]
                for stored_page in stored["frontend_pages"]
                if stored_page["pageId"] == page["pageId"]
            )
            self.assertEqual(
                page_reference["generation_dependencies"]["endpoint_ids"],
                [item["endpoint_id"] for item in page["references"]["endpoint_dependencies"]],
            )
            self.assertTrue((workspace / page_reference["json_path"]).is_file())
            persisted_detail = load_project_plan_json(workspace / page_reference["json_path"])
            self.assertIn("references", persisted_detail)
            self.assertNotIn("source_page_context", persisted_detail)
            self.assertNotIn("agent_note", persisted_detail)
            self.assertNotIn("api_dependencies", persisted_detail)
            self.assertNotIn("data_sources", persisted_detail)
            endpoint_refs = persisted_detail["references"]["endpoint_detail_refs"]
            self.assertEqual(len(endpoint_refs), len(endpoint_details))
            self.assertTrue(
                all((workspace / item["json_path"]).is_file() for item in endpoint_refs)
            )
            self.assertTrue(
                all(
                    "plans/endpoints/" in item["json_path"].replace("\\", "/")
                    for item in endpoint_refs
                )
            )
            self.assertNotIn("interface_design", persisted_detail)
            endpoint_artifact = load_project_plan_json(workspace / endpoint_refs[0]["json_path"])
            self.assertIn("interface_design", endpoint_artifact)
            page_markdown = (workspace / page_reference["markdown_path"]).read_text(
                encoding="utf-8"
            )
            self.assertIn(endpoint_refs[0]["json_path"], page_markdown)

            hydrated = load_project_plan_json(
                workspace / ".xcodeagent" / "plans" / "project-plan.json",
                hydrate_detail_designs=True,
            )
            review = detail_review_payload(hydrated, selectedPageId=page["pageId"])
            self.assertEqual(
                review["review"]["pages"][0]["page_goal"],
                page_detail["page_goal"],
            )
            self.assertEqual(
                review["review"]["pages"][0]["api_dependencies"],
                page_detail["endpoint_dependencies"],
            )

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
            extract_page_detail_context(project_plan, page["pageId"]),
            agent_detail_plan={
                "permissions": ["invented_role"],
                "api_dependencies": [{"endpoint_id": "invented.endpoint"}],
                "page_navigation": [{"targetPageId": "invented_page"}],
            },
        )

        self.assertEqual(detail["permissions"], page["references"]["permissions"])
        self.assertEqual(
            detail["endpoint_dependencies"], page["references"]["endpoint_dependencies"]
        )
        self.assertEqual(detail["navigation_targets"], page["references"]["navigation_targets"])
        self.assertEqual(validate_project_plan_dependencies(project_plan), [])

    def test_hydration_restores_api_dependencies_from_project_plan(self) -> None:
        """外置详情缺少依赖时应从主计划恢复，避免契约校验误报。"""

        project_plan = create_project_plan(create_requirement_spec("创建库存管理系统"))
        page = next(
            candidate
            for candidate in project_plan["frontend_pages"]
            if candidate["references"]["endpoint_dependencies"]
        )
        page_detail = create_page_detail_plan(
            project_plan,
            extract_page_detail_context(project_plan, page["pageId"]),
        )
        page_detail["endpoint_dependencies"] = []
        project_plan["page_detail_plans"] = [page_detail]

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            plan_path = workspace / ".xcodeagent" / "plans" / "project-plan.json"
            write_project_plan_document({"workspace": str(workspace)}, project_plan)
            hydrated = load_project_plan_json(plan_path, hydrate_detail_designs=True)

        hydrated_detail = hydrated["page_detail_plans"][0]
        self.assertEqual(
            [item["endpoint_id"] for item in hydrated_detail["api_dependencies"]],
            [
                item["endpoint_id"]
                for item in page["references"]["endpoint_dependencies"]
            ],
        )
        self.assertEqual(validate_api_contract_consistency(hydrated), [])

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
            {"pageId", "name", "path", "module_id", "description", "references"},
        )
        self.assertEqual(
            set(page["references"]),
            {"permissions", "endpoint_dependencies", "navigation_targets"},
        )

    def test_selected_page_review_only_contains_matching_page_detail(self) -> None:
        """单页审核载荷不得混入其他页面详情。"""

        project_plan = confirm_entity_designs(
            create_project_plan(create_requirement_spec("创建库存管理系统"))
        )
        pages_with_sources = [
            page
            for page in project_plan["frontend_pages"]
            if (page.get("references") or {}).get("endpoint_dependencies")
        ]
        first_page, second_page = pages_with_sources[:2]
        first_detail = create_page_detail_plan(
            project_plan,
            extract_page_detail_context(project_plan, first_page["pageId"]),
        )
        second_detail = create_page_detail_plan(
            project_plan,
            extract_page_detail_context(project_plan, second_page["pageId"]),
        )
        project_plan["page_detail_plans"] = [first_detail, second_detail]

        review = detail_review_payload(project_plan, selectedPageId=second_page["pageId"])

        self.assertEqual(
            [item["target_id"] for item in review["review"]["pages"]],
            [second_page["pageId"]],
        )

    def test_selected_page_confirmation_only_confirms_matching_detail(self) -> None:
        """单页审核提交不得确认其他页面的待审核详情。"""

        project_plan = create_project_plan(create_requirement_spec("创建库存管理系统"))
        first_page, second_page = project_plan["frontend_pages"][:2]
        first_detail = create_page_detail_plan(
            project_plan,
            extract_page_detail_context(project_plan, first_page["pageId"]),
        )
        second_detail = create_page_detail_plan(
            project_plan,
            extract_page_detail_context(project_plan, second_page["pageId"]),
        )
        first_detail["status"] = "pending_user_confirmation"
        first_detail["approved"] = False
        second_detail["status"] = "pending_user_confirmation"
        second_detail["approved"] = False
        project_plan["page_detail_plans"] = [first_detail, second_detail]

        confirmed = apply_detail_review_submission(
            project_plan,
            {
                "review_status": "confirmed",
                "target_changes": [],
            },
            selectedPageId=second_page["pageId"],
        )

        page_statuses = {
            detail["pageId"]: detail["status"]
            for detail in confirmed["page_detail_plans"]
        }
        frontend_page_statuses = {
            page["pageId"]: page.get("detail_status")
            for page in confirmed["frontend_pages"]
        }
        self.assertEqual(page_statuses[first_page["pageId"]], "pending_user_confirmation")
        self.assertEqual(page_statuses[second_page["pageId"]], "confirmed")
        self.assertNotEqual(frontend_page_statuses[first_page["pageId"]], "confirmed")
        self.assertEqual(frontend_page_statuses[second_page["pageId"]], "confirmed")


if __name__ == "__main__":
    unittest.main()
