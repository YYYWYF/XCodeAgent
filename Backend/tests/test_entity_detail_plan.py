from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.entity_source_binding import (
    apply_entity_source_binding_submission,
    entity_source_binding_payload,
)
from app.services.entity_detail_plan import (
    attach_entity_detail_plan,
    create_entity_detail_plan,
)
from app.services.project_plan import create_project_plan
from app.services.requirement_spec import create_requirement_spec
from app.workspace.detail_design_documents import (
    externalize_detail_designs,
    hydrate_external_detail_designs,
)


def _plan_with_entities() -> dict:
    """构造包含实体定义的 ProjectPlan 测试夹具。"""

    return create_project_plan(create_requirement_spec("创建商品管理系统"))


class EntityDetailPlanTests(unittest.TestCase):
    def test_create_entity_detail_plan_from_confirmed_definition(self) -> None:
        """实体详细设计由已确认实体定义确定性组装，包含字段、表结构与验收标准。"""

        plan = _plan_with_entities()
        entity = {
            **plan["entities"][0],
            "fields": [
                *plan["entities"][0]["fields"],
                {
                    "label": "商品编码",
                    "name": "product_code",
                    "type": "text",
                    "required": True,
                },
            ],
        }
        detail = create_entity_detail_plan(plan, entity)

        self.assertEqual(detail["entity_id"], entity["id"])
        self.assertEqual(detail["status"], "pending_user_confirmation")
        self.assertEqual(detail["design_source"], "deterministic_entity_design")
        self.assertTrue(detail["fields"])
        self.assertTrue(all("column_type" in field for field in detail["fields"]))
        self.assertIsNotNone(detail["table_design"])
        self.assertEqual(
            detail["table_design"]["name"],
            detail["entity_id"].lower(),
        )
        self.assertTrue(detail["acceptance_criteria"])
        self.assertIn("unique", [rule["rule_type"] for rule in detail["business_rules"]])

    def test_entity_detail_plan_rejects_unknown_entity_id(self) -> None:
        """缺少有效实体 id 时实体详细设计必须报错。"""

        plan = _plan_with_entities()
        with self.assertRaises(ValueError):
            create_entity_detail_plan(plan, {"id": " ", "name": "无效实体"})

    def test_entity_detail_workspace_round_trip(self) -> None:
        """实体详情外置到 plans/entities/ 后可从轻量 ProjectPlan 回读。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity)
        plan_with_detail = attach_entity_detail_plan(plan, detail)

        with tempfile.TemporaryDirectory(prefix="entity-test-") as raw:
            workspace = Path(raw)
            compact = externalize_detail_designs(
                {"workspace": str(workspace)},
                plan_with_detail,
            )
            plan_path = workspace / ".xcodeagent" / "plans" / "project-plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(
                json.dumps(compact, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            hydrated = hydrate_external_detail_designs(plan_path, compact)

            entity_ref = next(
                item.get("source_binding")
                for item in compact["entities"]
                if item.get("id") == entity["id"]
            )
            self.assertTrue(entity_ref["json_path"].endswith("entity--User.json"))
            self.assertTrue(
                (workspace / ".xcodeagent" / "plans" / "entities" / "entity--User.md").is_file()
            )
            self.assertEqual(len(hydrated["entity_detail_plans"]), 1)
            self.assertEqual(
                hydrated["entity_detail_plans"][0]["entity_id"],
                entity["id"],
            )

    def test_detail_review_payload_includes_entity(self) -> None:
        """选中实体时审核载荷只投射该实体，并标记缺失状态。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity)
        plan_with_detail = attach_entity_detail_plan(plan, detail)

        payload = entity_source_binding_payload(
            plan_with_detail,
            selected_entity_id=entity["id"],
            detail_target_type="entity",
        )
        self.assertEqual(payload["review"]["summary"]["selectedEntityId"], entity["id"])
        self.assertEqual(payload["review"]["summary"]["entity_count"], 1)
        self.assertEqual(payload["review"]["entities"][0]["entity_id"], entity["id"])
        self.assertFalse(payload["review"]["summary"]["missingSelectedEntityPlan"])

        missing = entity_source_binding_payload(
            plan,
            selected_entity_id="Missing",
            detail_target_type="entity",
        )
        self.assertTrue(missing["review"]["summary"]["missingSelectedEntityPlan"])

    def test_apply_detail_review_submission_confirms_entity(self) -> None:
        """确认实体审核后实体详情与计划实体 detail_status 同步为 confirmed。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity)
        plan_with_detail = attach_entity_detail_plan(plan, detail)

        confirmed = apply_entity_source_binding_submission(
            plan_with_detail,
            {
                "review_status": "confirmed",
                "target_changes": [],
                "overall_note": "实体设计确认",
            },
            selected_entity_id=entity["id"],
        )
        self.assertEqual(confirmed["entity_detail_plans"][0]["status"], "confirmed")
        self.assertTrue(confirmed["entity_detail_plans"][0]["approved"])
        confirmed_entity = next(
            item for item in confirmed["entities"] if item.get("id") == entity["id"]
        )
        self.assertEqual(confirmed_entity["detail_status"], "confirmed")

    def test_entity_detail_patch_rejects_contract_controlled_fields(self) -> None:
        """实体字段由项目计划控制，审核阶段不允许修改字段定义。"""

        plan = _plan_with_entities()
        entity = plan["entities"][0]
        detail = create_entity_detail_plan(plan, entity)
        plan_with_detail = attach_entity_detail_plan(plan, detail)

        with self.assertRaises(ValueError):
            apply_entity_source_binding_submission(
                plan_with_detail,
                {
                    "review_status": "confirmed",
                    "target_changes": [
                        {
                            "target_type": "entity",
                            "target_id": entity["id"],
                            "changes": {"fields": []},
                        }
                    ],
                },
                selected_entity_id=entity["id"],
            )


if __name__ == "__main__":
    unittest.main()
