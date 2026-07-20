from __future__ import annotations

import unittest

from app.services.detail_review import apply_detail_review_submission
from app.services.page_detail_plan import (
    create_page_detail_plan,
    extract_page_detail_context,
)
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec


class DetailReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = create_project_plan(create_requirement_spec("创建库存管理系统"))
        page_context = extract_page_detail_context(
            self.plan,
            self.plan["frontend_pages"][0]["pageId"],
        )
        self.plan["page_detail_plans"] = [
            create_page_detail_plan(self.plan, page_context)
        ]

    def test_contract_controlled_fields_cannot_be_changed(self) -> None:
        pageId = self.plan["page_detail_plans"][0]["pageId"]

        with self.assertRaisesRegex(ValueError, "contract-controlled"):
            apply_detail_review_submission(
                self.plan,
                {
                    "review_status": "confirmed",
                    "target_changes": [
                        {
                            "target_type": "page",
                            "target_id": pageId,
                            "changes": {"response_bindings": []},
                        }
                    ],
                },
            )

    def test_no_change_confirmation_marks_the_whole_plan_confirmed(self) -> None:
        result = apply_detail_review_submission(
            self.plan,
            {"review_status": "confirmed", "target_changes": []},
        )

        self.assertEqual(result["confirmation_status"], "confirmed")
        self.assertEqual(result["page_detail_plans"][0]["status"], "confirmed")

    def test_confirmation_repairs_bindings_from_external_detail_references(self) -> None:
        detail = self.plan["page_detail_plans"][0]
        page = next(
            item
            for item in self.plan["frontend_pages"]
            if item["pageId"] == detail["pageId"]
        )
        detail.pop("api_dependencies", None)
        detail["references"] = {
            "endpoint_dependencies": page["references"]["endpoint_dependencies"]
        }
        detail["response_bindings"] = []

        result = apply_detail_review_submission(
            self.plan,
            {"review_status": "confirmed", "target_changes": []},
        )

        confirmed_detail = result["page_detail_plans"][0]
        self.assertTrue(confirmed_detail["api_dependencies"])
        self.assertTrue(confirmed_detail["response_bindings"])


if __name__ == "__main__":
    unittest.main()
