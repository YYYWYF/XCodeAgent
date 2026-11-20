"""后台设计完成后，旧审阅只能刷新，不能确认未审阅的新版本。"""

from copy import deepcopy
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command

from app.graph.application_planning_interrupts import (
    application_planning_review_payload, planning_stage_entry,
    prepare_application_planning_review_resume, ui_confirmation_review,
)
from app.graph.application_planning_workflow import _route_ui_confirmation
from app.graph.nodes.ui_confirmation import _apply_ui_design_action, ui_confirmation
from app.graph.state import ProjectState
from app.services.ui_design_generation_pool import UiDesignGenerationPool
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path
from tests.test_agent_ui_design_confirmation import confirmation_fixture


class UiDesignReviewRefreshTests(unittest.IsolatedAsyncioTestCase):
    """通过真实 interrupt 往返核对新版本必须再次显式确认。"""

    async def test_refresh_does_not_wait_for_active_project_writer(self) -> None:
        """生成或调整持锁时只读清单，刷新不能排队、重写文件或另启生成。"""
        with TemporaryDirectory() as directory:
            state, current, _project = confirmation_fixture(directory)
            pool = UiDesignGenerationPool(concurrency=1)
            lock = pool.agent_project_lock(str(Path(directory).resolve()))
            manifest_path = ui_designs_json_path(state)
            before = {path: path.read_bytes() for path in manifest_path.parent.iterdir() if path.is_file()}
            for pending in ({"home", "chat"}, set()):
                with self.subTest(pending=pending):
                    async with lock:
                        with patch("app.graph.nodes.ui_confirmation.get_ui_design_generation_pool", return_value=pool), \
                             patch.object(pool, "pending_page_ids", return_value=pending), \
                             patch.object(pool, "submit", side_effect=AssertionError("unexpected generation")), \
                             patch("app.graph.nodes.ui_confirmation.write_ui_designs_json", side_effect=AssertionError("unexpected write")):
                            refreshed = await asyncio.wait_for(
                                _apply_ui_design_action(state, {"pages": []}, {"action": "refresh"}),
                                timeout=0.3,
                            )
                        self.assertEqual(refreshed, current)
                        self.assertTrue(lock.locked())
                        self.assertFalse(any(page.get("asset_confirmation") for page in refreshed["pages"]))
            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_ready_status_does_not_authorize_changed_asset(self) -> None:
        """新旧稿都是 ready 时，仍按真实资产差异刷新，不能仅比较终态。"""
        with TemporaryDirectory() as directory:
            state, current, _project = confirmation_fixture(directory)
            state["ui_designs"] = deepcopy(current)
            state["ui_designs"]["pages"][0]["design_asset"]["revision"] = "c" * 64
            state["clarification"] = {"mode": "ui_design_confirmation", "status": "requires_user_input"}
            payload = application_planning_review_payload(state, "ui_confirmation")
            submission = {key: payload[key] for key in ("gateId", "artifact", "artifactRevision")}
            with patch("app.graph.application_planning_interrupts.interrupt", return_value={**submission, "action": "confirm"}):
                decision = prepare_application_planning_review_resume(state, "ui_confirmation")
            self.assertEqual(decision.target_node, "ui_confirmation")
            self.assertEqual(decision.update["application_planning_interaction"]["action"], "ui_action")
            self.assertEqual(decision.update["ui_design_action"], {"action": "refresh"})
            self.assertEqual(load_ui_designs_json(ui_designs_json_path(state)), current)

    async def test_background_completion_refreshes_stale_confirm_without_advancing(self) -> None:
        """界面已读到 ready、checkpoint 仍 queued 时，应重新审阅而非执行失败。"""
        with TemporaryDirectory() as directory:
            state, current, _project = confirmation_fixture(directory)
            state["application_planning_interaction"] = {}
            stale = deepcopy(current)
            stale["pages"][1]["status"] = "queued"
            for page in stale["pages"]:
                page["design_asset"]["revision"] = "c" * 64
            state["ui_designs"] = stale
            state["clarification"] = {"mode": "ui_design_confirmation", "status": "requires_user_input"}
            builder = StateGraph(ProjectState)
            builder.add_node("ui_confirmation_review", ui_confirmation_review)
            builder.add_node("ui_confirmation", ui_confirmation)
            builder.add_node("planning_stage_entry", planning_stage_entry)

            def unexpected_revision(_state: ProjectState) -> dict:
                """刷新和确认不能开始任何修订或技术生成。"""
                raise AssertionError("unexpected downstream execution")

            builder.add_node("design_intent_analysis", unexpected_revision)
            builder.add_edge(START, "ui_confirmation_review")
            builder.add_conditional_edges("ui_confirmation", _route_ui_confirmation)
            graph = builder.compile(checkpointer=InMemorySaver())
            config = {"configurable": {"thread_id": "background-completion"}}
            first = await graph.ainvoke(state, config)
            review = first["__interrupt__"][0].value
            submission = {key: review[key] for key in ("gateId", "artifact", "artifactRevision")}
            refreshed = await graph.ainvoke(Command(resume={**submission, "action": "confirm"}), config)
            self.assertEqual(refreshed["status"], "requires_user_input")
            self.assertEqual(refreshed["ui_designs"], current)
            self.assertFalse(refreshed.get("technical_plan"))
            self.assertFalse(any(page.get("asset_confirmation") for page in refreshed["ui_designs"]["pages"]))
            self.assertEqual(load_ui_designs_json(ui_designs_json_path(state)), current)
            latest_review = refreshed["__interrupt__"][0].value
            self.assertEqual(latest_review["phase"], "ui_confirmation")
            self.assertNotEqual(latest_review["gateId"], review["gateId"])
            # 新稿经第二次显式确认后仍停在计划阶段入口，不自动进入技术规划。
            latest_submission = {key: latest_review[key] for key in ("gateId", "artifact", "artifactRevision")}
            confirmed = await graph.ainvoke(Command(resume={**latest_submission, "action": "confirm"}), config)
            self.assertEqual(confirmed["ui_designs"]["confirmation_status"], "confirmed")
            self.assertEqual(confirmed["__interrupt__"][0].value["phase"], "planning_stage_entry")
            self.assertFalse(confirmed.get("technical_plan"))


if __name__ == "__main__":
    unittest.main()
