"""技术规划只消费结构化数据，多余 Markdown 不影响保存及正式产物确认。"""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.protocols.workflow.projection import _workflow_confirmation_artifact
from app.services.application_planning_persistence import confirm_application_planning_artifacts
from app.services.ended_workspace import workspace_fingerprint
from app.workspace.plan_documents import commit_technical_plan_document
from tests.test_application_page_planning import _confirmed_state
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class TechnicalPlanJsonStorageTests(unittest.TestCase):
    """验证没有 Markdown 与遗留任意 Markdown 的行为完全一致。"""

    def test_formal_revision_previews_and_confirms_the_stored_candidate(self) -> None:
        """正式修订生成结构化预览，等待确认后才覆盖正式数据，不读取多余 Markdown。"""

        from app.domain.application_planning_recovery import application_planning_sha256
        from app.graph.nodes.application_revision import _create_technical_plan_draft, _confirm_current_draft, _await_revision_draft
        from app.protocols.workflow.presentation import workflow_presentation
        from app.workspace.content_artifacts import read_artifact
        from app.workspace.revision_draft_documents import load_revision_draft

        harness = TechnicalPlanningRecoveryJourneyHarness()
        try:
            state = {"workspace": str(harness.workspace)}
            path = Path(commit_technical_plan_document(state, {
                **harness.candidate, "confirmation_status": "confirmed",
            }))
            before = path.read_bytes()
            markdown = path.with_suffix(".md")
            markdown.write_bytes(b"\xff")
            candidate = deepcopy(harness.candidate)
            candidate["architecture"]["frontend"] += " 本轮修订保留全部接口类型。"
            active = SimpleNamespace(change_id="revision", request="完善技术架构说明")
            with patch("app.graph.nodes.application_revision.plan_project_with_chat_model", return_value=candidate):
                draft = _create_technical_plan_draft(harness.workspace, active)
            self.assertNotIn("markdown", draft)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(draft["technicalPlan"]["confirmation_status"], "pending_user_confirmation")
            _, stored = load_revision_draft(harness.workspace, change_id="revision", artifact_key="technical-plan")
            self.assertEqual(draft["draftSha256"], application_planning_sha256(stored))
            with patch("app.graph.nodes.application_revision.update_active_revision_progress"):
                pending = _await_revision_draft(harness.workspace, active.change_id, draft)
            public = workflow_presentation({"state": pending}, workspace=str(harness.workspace))
            references = public["state"]["artifactRefs"]
            self.assertEqual(read_artifact(harness.workspace, references["technical_plan"]), stored)
            self.assertNotIn("technicalPlan", public["state"]["revision_draft"])
            self.assertNotIn("technicalPlan", public["state"]["clarification"]["revisionDraft"])
            _confirm_current_draft(harness.workspace, active, "technical-plan")
            confirmed = json.loads(path.read_text())
            self.assertEqual(confirmed["confirmation_status"], "confirmed")
            self.assertEqual(confirmed["architecture"], candidate["architecture"])
            self.assertEqual(markdown.read_bytes(), b"\xff")
        finally:
            harness.close()

    def test_extra_markdown_does_not_invalidate_stopped_planning(self) -> None:
        """停止后新增或修改多余 Markdown 不阻断继续，真实 JSON 变化仍使指纹失效。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = _confirmed_state(root)
            before = workspace_fingerprint(root)
            markdown = root / ".devagentstudio/plans/technical-plan.md"
            markdown.write_bytes(b"\xff")
            draft = root / ".devagentstudio/drafts/revisions/change/technical-plan/artifact.md"
            draft.parent.mkdir(parents=True)
            draft.write_bytes(b"\xff")
            self.assertEqual(workspace_fingerprint(root), before)
            Path(state["technical_plan_json_path"]).write_text('{"changed":true}')
            self.assertNotEqual(workspace_fingerprint(root), before)

    def test_commit_and_confirmation_ignore_extra_markdown(self) -> None:
        """确认只读取 JSON；即使多余 Markdown 无法解码，也不读取、拒绝或改写它。"""

        for extra in (False, True):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                state = _confirmed_state(workspace)
                markdown = workspace / ".devagentstudio/plans/technical-plan.md"
                if extra:
                    markdown.write_bytes(b"\xff")
                plan = state["technical_plan"]
                path = commit_technical_plan_document(state, plan)
                self.assertEqual(json.loads(Path(path).read_text()), plan)
                confirmation = confirm_application_planning_artifacts(state)
                self.assertEqual(set(confirmation["artifacts"]["technicalPlan"]), {"json"})
                self.assertEqual(confirmation["artifacts"]["technicalPlan"]["json"]["path"],
                                 ".devagentstudio/plans/technical-plan.json")
                if extra:
                    self.assertEqual(markdown.read_bytes(), b"\xff")
                else:
                    self.assertFalse(markdown.exists())

    def test_technical_review_does_not_publish_markdown(self) -> None:
        """技术规划审阅由结构化快照承载，不投影磁盘 Markdown 为确认正文。"""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "technical-plan.md"
            path.write_bytes(b"\xff")
            self.assertIsNone(_workflow_confirmation_artifact({
                "status": "requires_user_input", "phase": "technical_planning",
                "technical_plan_path": str(path),
                "clarification": {"status": "requires_user_input", "mode": "technical_plan_confirmation"},
            }))
