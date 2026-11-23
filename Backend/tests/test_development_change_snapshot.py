"""技术规划变更快照与已重做证据的作用域回归。"""

from __future__ import annotations

import tempfile
from copy import deepcopy
import unittest
from unittest.mock import patch
from pathlib import Path

from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.development_change_snapshot import (
    build_development_change_snapshot, read_change_snapshot, write_change_snapshot,
)
from app.services.product_plan_carryover import write_product_plan_carryover
from app.services.technical_plan_carryover import write_technical_plan_carryover


class DevelopmentChangeSnapshotTests(unittest.TestCase):
    """新快照作废旧证据，相同快照重复确认保持幂等。"""

    def setUp(self) -> None:
        """创建带真实 lifecycle 的隔离工作区。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        write_application_lifecycle(
            self.workspace,
            create_application_lifecycle(application_id="test", application_name="测试"),
        )

    def _plan(self) -> dict:
        """构造包含页面、实体和接口变更标记的最小展示计划。"""

        return {
            "pages": [{"pageId": "home", "_change_status": "modified"}],
            "entities": [{"id": "greeting", "_change_status": "unchanged"}],
            "api_contracts": [{
                "id": "greeting_api",
                "_change_status": "modified",
                "endpoints": [{"id": "greeting_api.get", "_change_status": "modified"}],
            }],
        }

    def _product(self) -> dict:
        """构造带业务内容的当前正式产品页面。"""

        return {"pages": [{"pageId": "home", "name": "食堂评价", "description": "两栏表单", "actions": []}]}

    def _seed_baseline(self, technical: dict, product: dict) -> None:
        """冻结上一迭代产品页面及技术规划，测试中不推进该基线。"""

        write_technical_plan_carryover(self.workspace, source_branch="v1_0", plan=deepcopy(technical))
        write_product_plan_carryover(self.workspace, source_branch="v1_0", pages={
            page["pageId"]: {"requirement": page, "definition": {}, "development_page": page}
            for page in product["pages"]
        })

    def _snapshot(self, technical: dict, product: dict | None = None) -> dict:
        """执行生产开发 diff，不写磁盘或调用模型。"""

        return build_development_change_snapshot(self.workspace, technical_plan=technical, product_plan=product or self._product())

    def test_initial_iteration_ignores_review_unchanged_after_repeated_dialogue(self) -> None:
        """首轮多次修改使计划审阅标未变更，开发仍应按无基线全部新增。"""

        technical = self._plan()
        technical["pages"][0]["_change_status"] = "unchanged"
        snapshot = self._snapshot(technical)
        self.assertEqual(snapshot["pages"], {"home": "added"})
        self.assertEqual(snapshot["endpoints"], {"greeting_api": {"greeting_api.get": "added"}})
        self.assertEqual(snapshot["entities"], {"greeting": "added"})

    def test_iteration_baseline_is_not_last_review_and_reverted_changes_become_unchanged(self) -> None:
        """累积修改始终对比上一迭代；恢复原值后可重新判未变更，不信旧徽标。"""

        technical = self._plan()
        self._seed_baseline(technical, self._product())
        product = self._product()
        product["pages"][0]["description"] = "多食堂评价和建议"
        technical["pages"][0]["_change_status"] = "unchanged"
        self.assertEqual(self._snapshot(technical, product)["pages"]["home"], "modified")
        product["pages"][0]["description"] = "两栏表单"
        technical["pages"][0]["_change_status"] = "modified"
        self.assertEqual(self._snapshot(technical, product)["pages"]["home"], "unchanged")

    def test_product_only_change_updates_page_without_rewriting_technical_references(self) -> None:
        """ProductPlan 业务改变且技术引用不变时页面仍须开发。"""

        technical = self._plan()
        self._seed_baseline(technical, self._product())
        product = self._product()
        product["pages"][0]["actions"] = [{"actionId": "submit", "description": "新增提交确认"}]
        self.assertEqual(self._snapshot(technical, product)["pages"]["home"], "modified")

    def test_schema_change_propagates_to_dependent_page_and_preserves_unrelated_endpoint(self) -> None:
        """响应类型实际改变时接口及依赖页面都标修改，未引用接口不受影响。"""

        technical = self._plan()
        contract = technical["api_contracts"][0]
        contract["schemas"] = {"Response": {"type": "object", "properties": {"state": {"type": "string"}}}, "Other": {"type": "string"}}
        contract["endpoints"][0]["response_schema_ref"] = "Response"
        contract["endpoints"].append({"id": "other", "response_schema_ref": "Other"})
        technical["pages"][0]["references"] = {"action_implementations": [{"stepBindings": [{"endpointId": "greeting_api.get"}]}]}
        self._seed_baseline(technical, self._product())
        contract["schemas"]["Response"]["properties"]["state"]["type"] = "boolean"
        snapshot = self._snapshot(technical)
        self.assertEqual(snapshot["endpoints"]["greeting_api"], {"greeting_api.get": "modified", "other": "unchanged"})
        self.assertEqual(snapshot["pages"]["home"], "modified")

    def test_removed_records_are_retained_for_development_cleanup(self) -> None:
        """最终目录省略旧目标时快照仍保留删除目标；新增目标不被旧审阅标记禁用。"""

        self._seed_baseline(self._plan(), self._product())
        technical = {"pages": [{"pageId": "new"}], "entities": [], "api_contracts": []}
        product = {"pages": [{"pageId": "new", "_change_status": "unchanged"}]}
        snapshot = self._snapshot(technical, product)
        self.assertEqual(snapshot["pages"], {"new": "added", "home": "deleted"})
        self.assertEqual(snapshot["entities"], {"greeting": "deleted"})
        self.assertEqual(snapshot["endpoints"], {"greeting_api": {"greeting_api.get": "deleted"}})

    def test_identical_reordered_content_ignores_review_markers(self) -> None:
        """带身份集合重排和展示标记变化不能产生假的开发变更。"""

        technical = self._plan()
        technical["entities"][0]["fields"] = [{"name": "a"}, {"name": "b"}]
        self._seed_baseline(technical, self._product())
        technical["entities"][0]["fields"].reverse()
        technical["api_contracts"][0]["_change_status"] = "added"
        self.assertEqual(self._snapshot(technical)["entities"]["greeting"], "unchanged")
        self.assertEqual(self._snapshot(technical)["endpoints"]["greeting_api"]["greeting_api.get"], "unchanged")

    def test_invalid_directory_or_write_failure_does_not_publish_success(self) -> None:
        """重复身份或持久化失败不能被吞掉后宣告开发就绪。"""

        with self.assertRaisesRegex(ValueError, "唯一"):
            self._snapshot(self._plan(), {"pages": [{"pageId": "same"}, {"pageId": "same"}]})
        with patch("app.services.development_change_snapshot.os.replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(ValueError, "写入失败"):
                write_change_snapshot(self.workspace, technical_plan=self._plan(), product_plan=self._product())
        self.assertIsNone(read_change_snapshot(self.workspace))

    def _seed_redeveloped(self) -> None:
        """写入上一份快照留下的已重做证据。"""

        lifecycle = load_application_lifecycle(self.workspace)
        lifecycle.extensions = {"redevelopedChangedTargets": ["page:home"]}
        write_application_lifecycle(self.workspace, lifecycle)

    def test_new_facts_reset_but_same_facts_preserve_redeveloped_targets(self) -> None:
        """变化快照清空旧证据；相同快照再次写入不重复清空。"""

        self._seed_redeveloped()
        write_change_snapshot(self.workspace, technical_plan=self._plan(), product_plan=self._product())
        self.assertNotIn(
            "redevelopedChangedTargets",
            load_application_lifecycle(self.workspace).extensions,
        )

        self._seed_redeveloped()
        write_change_snapshot(self.workspace, technical_plan=self._plan(), product_plan=self._product())
        self.assertEqual(
            load_application_lifecycle(self.workspace).extensions["redevelopedChangedTargets"],
            ["page:home"],
        )

        changed = self._plan()
        changed["pages"][0]["_change_status"] = "deleted"
        write_change_snapshot(self.workspace, technical_plan=changed, product_plan=self._product())
        self.assertNotIn(
            "redevelopedChangedTargets",
            load_application_lifecycle(self.workspace).extensions,
        )


if __name__ == "__main__":
    unittest.main()
