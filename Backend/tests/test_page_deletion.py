"""开发阶段页面删除的回归：预演只读、执行精确、其它路由不受影响。

删除一个页面只改变计划目录，基线里已生成的页面代码与设计稿不会自己消失；
本模块把清理做成确定性操作，并由用户确认后才执行。
"""

import json
import tempfile
import unittest
from pathlib import Path

from app.services.page_deletion import (
    delete_page_artifacts,
    page_directory_name,
    preview_page_deletion,
)

_ROUTES = """export const PAGE_ROUTES = [
  // DEVAGENTSTUDIO_BUSINESS_ROUTES_START
  {
    path: "home",
    label: "欢迎页",
  },
  {
    path: "cancel_completed",
    label: "已完成界面",
  },
  {
    path: "card_return",
    label: "门禁卡归还页面",
  },
  // DEVAGENTSTUDIO_BUSINESS_ROUTES_END
];
"""


class PageDeletionTests(unittest.TestCase):
    """以真实目录结构验证预演与删除，不触碰计划产物。"""

    def setUp(self) -> None:
        """准备一个含两个页面、一条设计稿和变更快照的最小工作区。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.root = self.workspace
        (self.root / "frontend/src/pages/CancelCompleted").mkdir(parents=True)
        (self.root / "frontend/src/pages/CancelCompleted/index.tsx").write_text("export default () => null", encoding="utf-8")
        (self.root / "frontend/src/pages/CardReturn").mkdir(parents=True)
        (self.root / "frontend/src/pages/CardReturn/index.tsx").write_text(
            "/** 已完成注销界面路由，对应产品页面 cancel_completed */", encoding="utf-8"
        )
        (self.root / "frontend/src/constants").mkdir(parents=True)
        (self.root / "frontend/src/constants/routes.tsx").write_text(_ROUTES, encoding="utf-8")
        (self.root / ".devagentstudio/ui-design/pages/CancelCompleted").mkdir(parents=True)
        (self.root / ".devagentstudio/plans").mkdir(parents=True)
        (self.root / ".devagentstudio/plans/product-plan.json").write_text(
            json.dumps({
                "confirmation_status": "confirmed",
                "pages": [
                    {"pageId": "cancel_completed", "name": "已完成界面"},
                    {"pageId": "card_return", "name": "门禁卡归还页面"},
                ],
            }),
            encoding="utf-8",
        )
        (self.root / ".devagentstudio/development-change-snapshot.json").write_text(
            json.dumps({"pages": {"cancel_completed": "deleted", "card_return": "modified"}}), encoding="utf-8"
        )

    def test_page_id_maps_to_directory_name(self) -> None:
        """目录名规则与前端 `pageDirectoryFromPath` 一致。"""

        self.assertEqual(page_directory_name("cancel_completed"), "CancelCompleted")
        self.assertEqual(page_directory_name("home"), "Home")

    def test_preview_lists_targets_without_touching_files(self) -> None:
        """预演只读：列出目录、路由与引用，不修改任何文件。"""

        before = (self.root / "frontend/src/constants/routes.tsx").read_text(encoding="utf-8")
        preview = preview_page_deletion(self.root, "cancel_completed")

        self.assertEqual(preview.page_label, "已完成界面")
        self.assertEqual(
            preview.directories,
            ["frontend/src/pages/CancelCompleted", ".devagentstudio/ui-design/pages/CancelCompleted"],
        )
        self.assertEqual(len(preview.route_entries), 1)
        # 其它页面里对该页面的引用只报告，不自动改写。
        self.assertEqual(preview.referenced_by, ["frontend/src/pages/CardReturn/index.tsx"])
        self.assertTrue(preview.removable)
        self.assertEqual((self.root / "frontend/src/constants/routes.tsx").read_text(encoding="utf-8"), before)
        self.assertTrue((self.root / "frontend/src/pages/CancelCompleted").is_dir())

    def test_delete_removes_only_that_page(self) -> None:
        """执行后目标目录与路由条目消失，其它页面与路由保持完好。"""

        result = delete_page_artifacts(self.root, "cancel_completed")

        self.assertEqual(len(result["directories"]), 2)
        self.assertFalse((self.root / "frontend/src/pages/CancelCompleted").exists())
        self.assertFalse((self.root / ".devagentstudio/ui-design/pages/CancelCompleted").exists())
        routes = (self.root / "frontend/src/constants/routes.tsx").read_text(encoding="utf-8")
        self.assertNotIn("cancel_completed", routes)
        self.assertIn('path: "home"', routes)
        self.assertIn('path: "card_return"', routes)
        # 其它页面文件不被删除。
        self.assertTrue((self.root / "frontend/src/pages/CardReturn/index.tsx").is_file())

    def test_repeat_delete_reports_nothing_removable(self) -> None:
        """已清理过的页面再次预演时不可删，避免空操作被当成成功。"""

        delete_page_artifacts(self.root, "cancel_completed")
        self.assertFalse(preview_page_deletion(self.root, "cancel_completed").removable)


if __name__ == "__main__":
    unittest.main()


class DeletePageNodeTests(unittest.TestCase):
    """开发阶段点击已删除页面时，图入口应转入删除节点而不是常规开发。"""

    def setUp(self) -> None:
        """复用上面的最小工作区，并补上快照与计划。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for relative in ("frontend/src/pages/CancelCompleted", "frontend/src/pages/CardReturn",
                         "frontend/src/constants", ".devagentstudio/plans",
                         ".devagentstudio/ui-design/pages/CancelCompleted"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        (self.root / "frontend/src/pages/CancelCompleted/index.tsx").write_text("x", encoding="utf-8")
        (self.root / "frontend/src/pages/CardReturn/index.tsx").write_text("y", encoding="utf-8")
        (self.root / "frontend/src/constants/routes.tsx").write_text(_ROUTES, encoding="utf-8")
        (self.root / ".devagentstudio/plans/product-plan.json").write_text(
            json.dumps({
                "confirmation_status": "confirmed",
                "pages": [
                    {"pageId": "cancel_completed", "name": "已完成界面"},
                    {"pageId": "card_return", "name": "门禁卡归还页面"},
                ],
            }),
            encoding="utf-8",
        )
        # 门禁还要读已确认的技术规划（接口/实体目录），这里给一份最小可用版本。
        (self.root / ".devagentstudio/plans/technical-plan.json").write_text(
            json.dumps({
                "confirmation_status": "confirmed",
                "artifact_type": "technical-plan",
                "entities": [],
                "api_contracts": [],
                "pages": [
                    {"pageId": "cancel_completed"},
                    {"pageId": "card_return"},
                ],
            }),
            encoding="utf-8",
        )
        (self.root / ".devagentstudio/development-change-snapshot.json").write_text(
            json.dumps({"pages": {"cancel_completed": "deleted", "card_return": "modified"}}), encoding="utf-8"
        )
        # 删除节点要读测试门禁，因此需要一份真实的 lifecycle 快照。
        from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
        from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle

        lifecycle = create_application_lifecycle(application_id="test", application_name="测试")
        lifecycle.initialization.stage = ApplicationLifecycleStage.READY_FOR_WORKBENCH
        lifecycle.initialization.status = ApplicationLifecycleStatus.COMPLETED
        write_application_lifecycle(self.root, lifecycle)

    def _state(self, page_id: str) -> dict:
        """构造一次页面开发请求的最小状态。"""

        return {
            "workspace": str(self.root),
            "selectedPageId": page_id,
            "active_run_id": f"delete-{page_id}",
            "active_thread_id": f"delete-thread-{page_id}",
            "build_execution_scope": {"type": "page", "targetId": page_id},
        }

    def test_route_sends_deleted_page_to_delete_node(self) -> None:
        """已删除页面走删除节点，未删除页面仍走常规开发门禁。"""

        from app.graph.workflow import route_workflow_start

        self.assertEqual(route_workflow_start(self._state("cancel_completed")), "delete_development_page")
        self.assertEqual(route_workflow_start(self._state("card_return")), "api_design_readiness_gate")

    def test_delete_node_removes_artifacts_and_opens_test_gate(self) -> None:
        """删除节点清掉代码 / 设计稿 / 路由；其余产物未完成时结束本轮开发。"""

        from app.graph.nodes.development_readiness import delete_development_page

        update = delete_development_page(self._state("cancel_completed"))

        self.assertEqual(update["status"], "completed")
        self.assertEqual(update["build_summary"]["status"], "completed")
        # 夹具里 card_return 尚未开发完成，门禁未通过时本轮开发到删除为止。
        self.assertEqual(update["integration_next_action"], "end")
        self.assertFalse((self.root / "frontend/src/pages/CancelCompleted").exists())
        self.assertFalse((self.root / ".devagentstudio/ui-design/pages/CancelCompleted").exists())
        routes = (self.root / "frontend/src/constants/routes.tsx").read_text(encoding="utf-8")
        self.assertNotIn("cancel_completed", routes)
        self.assertIn('path: "card_return"', routes)

    def test_delete_node_is_idempotent(self) -> None:
        """重复进入时不报错，也不重复计数。"""

        from app.graph.nodes.development_readiness import delete_development_page

        delete_development_page(self._state("cancel_completed"))
        again = delete_development_page(self._state("cancel_completed"))

        self.assertEqual(again["deleted_page_result"]["directories"], [])
        self.assertEqual(again["deleted_page_result"]["routeEntries"], [])
        self.assertEqual(again["status"], "completed")

    def test_delete_node_opens_test_gate_when_all_others_completed(self) -> None:
        """其余产物全部完成时，删除后应产出可确认的测试门，而不是直接结束。"""

        from app.graph.nodes.development_readiness import delete_development_page
        from app.services.application_lifecycle import start_workbench_execution
        from app.services.development_artifacts import complete_initial_development

        # 把唯一剩余页面标成已完成且未变更，使门禁放行。
        (self.root / ".devagentstudio/development-change-snapshot.json").write_text(
            json.dumps({"pages": {"cancel_completed": "deleted", "card_return": "unchanged"}}), encoding="utf-8"
        )
        start_workbench_execution(
            self.root, scope="page", target_id="card_return", page_id="card_return",
            thread_id="thread-card-return", run_id="run-card-return",
            phase="api_design_readiness_gate", initial_development_entry=True,
        )
        complete_initial_development(self.root, run_id="run-card-return")

        update = delete_development_page(self._state("cancel_completed"))

        self.assertEqual(update["phase"], "test_phase_confirmation")
        self.assertEqual(update["status"], "requires_user_input")
        self.assertEqual(update["clarification"]["mode"], "test_phase_confirmation")
        self.assertEqual(update["integration_next_action"], "await_user_input")

    def test_page_cleanup_guides_pending_endpoint_deletion_before_tests(self) -> None:
        """页面删完但接口仍待删除时，结束本次开发并交接接口，不弹测试确认。"""

        from app.graph.nodes.development_readiness import delete_development_page
        plan_path = self.root / ".devagentstudio/plans/technical-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["api_contracts"] = [{"id": "return_api", "endpoints": [{"id": "submit"}]}]
        plan_path.write_text(json.dumps(plan))
        snapshot_path = self.root / ".devagentstudio/development-change-snapshot.json"
        snapshot_path.write_text(json.dumps({
            "pages": {"cancel_completed": "deleted", "card_return": "modified"},
            "endpoints": {"return_api": {"submit": "deleted"}},
        }))
        # 另一个页面已开发完成；待删除接口也曾被记录为“已重做”，模拟二次修订前的旧证据。
        from app.services.application_lifecycle import (
            load_application_lifecycle,
            start_workbench_execution,
            write_application_lifecycle,
        )
        from app.services.development_artifacts import complete_initial_development
        from app.services.development_change_snapshot import write_change_snapshot

        start_workbench_execution(
            self.root, scope="page", target_id="card_return", page_id="card_return",
            thread_id="thread-card-return", run_id="run-card-return",
            phase="api_design_readiness_gate", initial_development_entry=True,
        )
        complete_initial_development(self.root, run_id="run-card-return")
        start_workbench_execution(
            self.root, scope="endpoint", target_id="submit", api_contract_id="return_api",
            page_id="card_return",
            thread_id="thread-submit", run_id="run-submit",
            phase="api_design_readiness_gate", initial_development_entry=True,
        )
        complete_initial_development(self.root, run_id="run-submit")
        lifecycle = load_application_lifecycle(self.root)
        lifecycle.extensions = {
            **(lifecycle.extensions or {}),
            "redevelopedChangedTargets": ["page:card_return", "endpoint:return_api:submit"],
        }
        write_application_lifecycle(self.root, lifecycle)
        # 新快照确认时必须作废旧快照的已重做记录，否则待删除接口会被门禁误判为未变更。
        write_change_snapshot(
            self.root,
            technical_plan={
                "pages": [
                    {"pageId": "cancel_completed", "_change_status": "deleted"},
                    {"pageId": "card_return", "_change_status": "modified"},
                ],
                "entities": [],
                "api_contracts": [{
                    "id": "return_api",
                    "_change_status": "modified",
                    "endpoints": [{"id": "submit", "_change_status": "deleted"}],
                }],
            },
        )
        update = delete_development_page(self._state("cancel_completed"))
        self.assertEqual(update["status"], "completed")
        self.assertEqual(update["integration_next_action"], "end")
        self.assertEqual(update["clarification"]["mode"], "development_target_selection")
        blockers = update["clarification"]["testEntryGate"]["blockers"]
        self.assertTrue(any(target.get("endpointId") == "submit" for target in blockers))
        self.assertTrue(update["deleted_page_result"]["completed"])
