"""验证实体中文展示约束与数据库注释链路，保持技术标识不变。"""

from copy import deepcopy
import unittest

from app.services.entity_definitions import entity_mysql_target_table
from app.services.entity_display_language import validate_entity_display_language


class EntityDisplayLanguageTests(unittest.TestCase):
    """只校验本地数据，不调用模型、数据库或 SaaS。"""

    def plan(self) -> dict:
        """构造包含英文技术标识和中文展示文案的技术规划。"""

        return {"artifact_type": "technical-plan", "entities": [{
            "id": "AttendanceRecord", "name": "考勤记录",
            "description": "记录员工每日签到、签退时间与考勤状态。",
            "table_name": "1acd4cafd9dc_attendance_record",
            "fields": [{"name": "employee_id", "label": "员工 ID",
                        "description": "关联员工的唯一 ID。", "type": "text", "required": True}],
        }]}

    def test_chinese_display_keeps_identifiers_and_database_comments(self) -> None:
        """中文约束不改技术标识，目标表和列注释使用中文文案。"""

        plan = self.plan()
        original = deepcopy(plan)
        self.assertEqual(validate_entity_display_language(plan, builtin=True), [])
        table = entity_mysql_target_table(plan["entities"][0])
        self.assertEqual(table["name"], "1acd4cafd9dc_attendance_record")
        self.assertEqual(table["comment"], plan["entities"][0]["description"])
        self.assertEqual(table["columns"][1]["comment"], "员工 ID")
        self.assertEqual(plan, original)

    def test_english_or_missing_display_fields_are_rejected(self) -> None:
        """实体名称、说明和字段标签、说明均不能是纯英文或空值。"""

        for is_field, key in [(False, "name"), (False, "description"), (True, "label"), (True, "description")]:
            for value in ["Daily attendance record", "", None, 123]:
                with self.subTest(is_field=is_field, key=key, value=value):
                    plan = self.plan()
                    entity = plan["entities"][0]
                    target = entity["fields"][0] if is_field else entity
                    target[key] = value
                    errors = validate_entity_display_language(plan, builtin=True)
                    self.assertEqual(len(errors), 1)
                    self.assertIn(key, errors[0])
                    self.assertIn("AttendanceRecord", errors[0])

    def test_other_artifacts_are_outside_this_constraint(self) -> None:
        """不把技术规划实体约束施加到其他正式产物。"""

        plan = self.plan()
        plan["artifact_type"] = "project-plan"
        plan["entities"][0]["description"] = "English text"
        self.assertEqual(validate_entity_display_language(plan, builtin=True), [])

    def test_external_database_keeps_english_display_fields(self) -> None:
        """外置库实体不受内置库中文文案规则影响。"""

        plan = self.plan()
        plan["entities"][0]["description"] = "Daily attendance record"
        self.assertEqual(validate_entity_display_language(plan, builtin=False), [])
