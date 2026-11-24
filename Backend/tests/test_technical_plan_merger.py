"""最终内容驱动的技术规划 Diff 回归；本模块不再测试或保留旧白名单合并。"""

from copy import deepcopy
import json
import unittest

from app.services.technical_plan_merger import preserve_deletion_records, strip_change_status, technical_plan_revision_diff
from tests.test_technical_plan_content_changes import _base_plan, _contract, _endpoint, _OTHER_ENDPOINT_ID


class TechnicalPlanDiffTests(unittest.TestCase):
    """展示基线不能覆盖候选；新增、修改、删除和无变化完全来自最终内容。"""

    def test_initial_generation_tags_all_nested_endpoints(self) -> None:
        """首轮全部新增，接口不能因为分组级标记而遗漏自身新增状态。"""

        plan = _base_plan()
        display = technical_plan_revision_diff(None, plan)
        self.assertEqual(display["entities"][0]["_change_status"], "added")
        self.assertTrue(all(item["_change_status"] == "added" for item in _contract(display)["endpoints"]))
        self.assertEqual(strip_change_status(display), plan)

    def test_candidate_configuration_and_unmentioned_sections_not_reverted(self) -> None:
        """Diff 只展示已经执行的任意修改，绝不按旧需求判断哪些变化可以保留。"""

        base = _base_plan()
        after = deepcopy(base)
        _contract(after)["name"] = "修改后的分组"
        _contract(after)["base_path"] = "/new"
        after["entities"][0]["fields"] = []
        display = technical_plan_revision_diff(base, after)
        self.assertEqual(strip_change_status(display), after)
        self.assertEqual(_contract(display)["_change_status"], "modified")
        self.assertEqual(display["entities"][0]["_change_status"], "modified")
        self.assertEqual(display["pages"][0]["_change_status"], "unchanged")

    def test_deletion_is_real_absence_and_only_tombstone_is_preserved(self) -> None:
        """remove 的目标不再存活，删除记录只能由真实前后差异产生。"""

        base = _base_plan()
        after = deepcopy(base)
        _contract(after)["endpoints"] = [_endpoint(after)]
        after["entities"][0]["fields"] = []
        persisted = preserve_deletion_records(base, after)
        removed = _endpoint(persisted, _OTHER_ENDPOINT_ID)
        self.assertEqual(removed["_change_status"], "deleted")
        self.assertEqual(persisted["entities"][0]["fields"], [])
        display = technical_plan_revision_diff(base, persisted)
        self.assertEqual(_endpoint(display, _OTHER_ENDPOINT_ID)["_change_status"], "deleted")
        self.assertEqual(strip_change_status(display), persisted)

    def test_previous_revision_kept_even_when_iteration_baseline_is_older(self) -> None:
        """连续修订保存前次结果，较老基线只能影响修改标记。"""

        base = _base_plan()
        current = deepcopy(base)
        _endpoint(current, _OTHER_ENDPOINT_ID)["summary"] = "前次修改"
        _endpoint(current)["summary"] = "本次修改"
        display = technical_plan_revision_diff(base, current)
        self.assertEqual(_endpoint(display, _OTHER_ENDPOINT_ID)["summary"], "前次修改")
        self.assertEqual(_endpoint(display, _OTHER_ENDPOINT_ID)["_change_status"], "modified")
        self.assertEqual(strip_change_status(display), current)

    def test_schema_diff_marks_only_actual_dependent_endpoints(self) -> None:
        """间接 Schema 引用变化传递到依赖接口，独立接口保持未变更。"""

        base = _base_plan()
        _contract(base)["schemas"]["Leaf"] = {"type": "string"}
        _contract(base)["schemas"]["MorningGreetingResponse"]["properties"]["nested"] = {"$ref": "Leaf"}
        after = deepcopy(base)
        _contract(after)["schemas"]["Leaf"] = {"type": "boolean"}
        display = technical_plan_revision_diff(base, after)
        self.assertEqual(_endpoint(display)["_change_status"], "modified")
        self.assertEqual(_endpoint(display, _OTHER_ENDPOINT_ID)["_change_status"], "unchanged")
        self.assertEqual(strip_change_status(display), after)

    def test_strip_nested_markers_keeps_only_current_deletion_contract(self) -> None:
        """字段/参数标记不得进入正式内容，现行删除记录可以保留供开发清理。"""

        plan = _base_plan()
        plan["entities"][0]["fields"][0]["_change_status"] = "modified"
        _endpoint(plan)["_change_status"] = "deleted"
        cleaned = strip_change_status(plan)
        self.assertNotIn("_change_status", json.dumps(cleaned["entities"]))
        self.assertEqual(_endpoint(cleaned)["_change_status"], "deleted")


if __name__ == "__main__":
    unittest.main()
