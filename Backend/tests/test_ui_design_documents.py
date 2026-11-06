"""设计说明编辑必须保留内部资产并重新走正式修订，不能把文字直接当成原型。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_agent_ui_design_confirmation import confirmation_fixture
from app.domain.application_planning_interaction import ApplicationPlanningInteraction
from app.graph.application_planning_interrupts import application_planning_review_payload, prepare_application_planning_review_resume
from app.graph.nodes.ui_confirmation import ui_confirmation
from app.services.agent_ui_design_confirmation import require_confirmed_agent_designs
from app.workspace.spec_documents import write_ui_designs_json
from app.workspace.ui_design_documents import prepare_ui_markdown_revision, ui_designs_markdown_path


class UiDesignDocumentTests(unittest.IsolatedAsyncioTestCase):
    """用真实 Markdown、JSON 和当前 review envelope 验证同步与准入边界。"""

    def edit_notes(self, state: dict, text: str) -> None:
        """模拟用户只编辑一个页面的可见修改要求。"""
        path = ui_designs_markdown_path(state)
        path.write_text(path.read_text().replace('<!-- ui-notes:0:start -->\n',
            '<!-- ui-notes:0:start -->\n' + text), encoding="utf-8")

    def test_edit_preserves_hidden_assets_and_unrelated_page(self) -> None:
        """修改文档只撤销目标确认，不覆盖共享资产和其他页面记录。"""
        with TemporaryDirectory() as directory:
            state, manifest, _ = confirmation_fixture(directory)
            original = deepcopy(manifest)
            self.edit_notes(state, "侧栏宽度改为 320px")
            candidate, request = prepare_ui_markdown_revision(state, manifest)
            self.assertIn("320px", request)
            self.assertEqual(candidate["pages"][0]["design_asset"], original["pages"][0]["design_asset"])
            self.assertEqual(candidate["pages"][1], original["pages"][1])
            self.assertEqual(candidate["pages"][0]["status"], "pending")
            self.assertEqual(manifest, original)

    def test_background_writes_preserve_unsaved_document_edits(self) -> None:
        """后台推进状态、更新来源时，用户文字仍等待确认入口处理。"""
        with TemporaryDirectory() as directory:
            state, manifest, _ = confirmation_fixture(directory)
            self.edit_notes(state, "隐藏搜索框")
            updated = deepcopy(manifest)
            updated["pages"][1]["status"] = "queued"
            write_ui_designs_json(state, updated)
            _candidate, request = prepare_ui_markdown_revision(state, updated)
            self.assertIn("隐藏搜索框", request)

    def test_provenance_edits_are_rejected(self) -> None:
        """不能通过编辑 Markdown hash、页面身份或状态批准另一份资产。"""
        with TemporaryDirectory() as directory:
            state, manifest, _ = confirmation_fixture(directory)
            path = ui_designs_markdown_path(state)
            path.write_text(path.read_text().replace("## 页面 home", "## 页面 forged"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "来源记录"):
                prepare_ui_markdown_revision(state, manifest)

    def test_native_confirm_with_markdown_edit_routes_to_design_intent(self) -> None:
        """用户点确认时先同步 JSON，再沿原语义路由处理视觉、产品或技术变更。"""
        with TemporaryDirectory() as directory:
            state, manifest, _ = confirmation_fixture(directory)
            state["clarification"] = {"mode": "ui_design_confirmation", "status": "requires_user_input"}
            payload = application_planning_review_payload(state, "ui_confirmation")
            self.edit_notes(state, "侧栏宽度改为 320px")
            submission = ApplicationPlanningInteraction(gateId=payload["gateId"], artifact="ui_designs",
                artifactRevision=payload["artifactRevision"], action="confirm")
            with patch("app.graph.application_planning_interrupts.interrupt", return_value=submission.model_dump(by_alias=True)):
                decision = prepare_application_planning_review_resume(state, "ui_confirmation")
            self.assertEqual(decision.target_node, "design_intent_analysis")
            self.assertEqual(decision.update["ui_designs"]["confirmation_status"], "pending_user_confirmation")
            self.assertIn("320px", decision.update["request"])
            self.assertEqual(decision.update["ui_designs"]["pages"][1], manifest["pages"][1])

    async def test_downstream_rechecks_confirmed_assets_and_document(self) -> None:
        """正式确认后，磁盘变更或未同步 Markdown 都不能继续被技术规划消费。"""
        for target in ("source", "markdown", "package_resources", "missing_asset", "duplicate_page"):
            with self.subTest(target=target), TemporaryDirectory() as directory:
                state, _manifest, project = confirmation_fixture(directory)
                result = await ui_confirmation(state)
                confirmed = result["ui_designs"]
                require_confirmed_agent_designs(state, confirmed)
                if target == "source":
                    (project / "src/App.tsx").write_text("changed", encoding="utf-8")
                elif target == "package_resources":
                    (project / "node_modules/@za38.03/agent-ui-runtime/changed.js").write_text("changed", encoding="utf-8")
                elif target == "missing_asset":
                    for page in confirmed["pages"]:
                        page.pop("design_asset")
                elif target == "duplicate_page":
                    confirmed["pages"].append(deepcopy(confirmed["pages"][0]))
                else:
                    self.edit_notes(state, "新的视觉要求")
                with self.assertRaises(ValueError):
                    require_confirmed_agent_designs(state, confirmed)

    async def test_node_cannot_bypass_unsynchronized_markdown(self) -> None:
        """即使直接进入确认节点，也不能批准未同步到当前原型的编辑文字。"""
        with TemporaryDirectory() as directory:
            state, _manifest, _ = confirmation_fixture(directory)
            self.edit_notes(state, "新增的视觉要求")
            result = await ui_confirmation(state)
            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["ui_designs"]["confirmation_status"], "pending_user_confirmation")
            self.assertTrue(result["clarification"]["validation_errors"])


if __name__ == "__main__":
    unittest.main()
