"""验证正式技术草稿连续修改及失败后的原稿保留，不调用真实模型。"""

import json
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.graph.nodes.application_revision import (
    _confirm_current_draft,
    _create_technical_plan_draft,
    _handle_revision_interaction,
)
from app.workspace.plan_documents import commit_technical_plan_document
from app.workspace.revision_draft_documents import load_revision_draft, revision_draft_directory
from tests.test_technical_plan_content_changes import _changeset
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class RevisionDraftRevisionsTests(unittest.TestCase):
    """使用真实草稿存储和候选校验，验证连续编辑与失败的持久化边界。"""

    def setUp(self) -> None:
        """准备正式版本及包含第一轮修改的独立待确认草稿。"""

        self.harness = TechnicalPlanningRecoveryJourneyHarness()
        self.addCleanup(self.harness.close)
        self.workspace = self.harness.workspace
        self.active = SimpleNamespace(
            change_id="draft-revisions", request="完善技术架构说明", remaining_artifacts=[],
        )
        self.canonical = Path(commit_technical_plan_document(
            {"workspace": str(self.workspace)},
            {**self.harness.candidate, "confirmation_status": "confirmed"},
        ))
        self.canonical_before = self.canonical.read_bytes()
        first = _changeset(self.harness.candidate, self.active.request, [{
            "op": "replace", "path": "/architecture/frontend",
            "expectedBefore": self.harness.candidate["architecture"]["frontend"],
            "value": self.harness.candidate["architecture"]["frontend"] + " 第一轮增加前端说明。",
        }])
        with patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", return_value=first):
            _create_technical_plan_draft(self.workspace, self.active)
        self.directory = revision_draft_directory(
            self.workspace, change_id=self.active.change_id, artifact_key="technical-plan",
        )

    def _load(self):
        """从磁盘读取当前草稿，避免用上次返回的内存对象代替真实编辑基线。"""

        return load_revision_draft(
            self.workspace, change_id=self.active.change_id, artifact_key="technical-plan",
        )

    def _revise(self, feedback: str):
        """沿真实草稿修改入口提交新意见，仅隔离无关的生命周期进度更新。"""

        with patch("app.graph.nodes.application_revision.update_active_revision_progress"):
            return _handle_revision_interaction(
                self.workspace, self.active,
                {"artifact_key": "technical-plan", "action": "revise", "feedback": feedback},
                source_execution_run_id="draft-revision-run",
            )

    def test_successive_revisions_edit_current_draft_until_explicit_confirmation(self) -> None:
        """连续两轮增量修改继承前面全部内容，比较基线一致且确认前不覆盖正式计划。"""

        original_metadata, _ = self._load()
        for section, feedback in (("backend", "补充后端说明"), ("data", "补充数据说明")):
            with self.subTest(section=section):
                _, previous = self._load()

                def planner(requirement, document, request, *, feedback):
                    """验证模型只收到当前草稿的业务投影，并返回本轮单项操作。"""

                    from app.services.technical_plan_changeset import revision_document
                    self.assertEqual(document, revision_document(previous))
                    self.assertIn(self.active.request, request)
                    self.assertIn(feedback_text, request)
                    old = document["architecture"][section]
                    return _changeset(document, request, [{
                        "op": "replace", "path": f"/architecture/{section}",
                        "expectedBefore": old, "value": old + f" 本轮{feedback_text}。",
                    }])

                feedback_text = feedback
                with patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", side_effect=planner):
                    result = self._revise(feedback)
                metadata, current = self._load()
                self.assertEqual(result["technical_plan_baseline"], previous)
                self.assertEqual(result["technical_plan"], current)
                self.assertEqual(result["status"], "requires_user_input")
                self.assertEqual(current["confirmation_status"], "pending_user_confirmation")
                self.assertIn("第一轮增加前端说明", current["architecture"]["frontend"])
                self.assertIn(feedback, current["architecture"][section])
                self.assertEqual(metadata.base_canonical_sha256, original_metadata.base_canonical_sha256)
                self.assertEqual(metadata.based_on_canonical, original_metadata.based_on_canonical)
                self.assertEqual(self.canonical.read_bytes(), self.canonical_before)

        _confirm_current_draft(self.workspace, self.active, "technical-plan")
        confirmed = json.loads(self.canonical.read_text(encoding="utf-8"))
        self.assertEqual(confirmed["architecture"], current["architecture"])
        self.assertEqual(confirmed["confirmation_status"], "confirmed")
        self.assertFalse(self.directory.exists())

    def test_model_failure_preserves_original_draft_for_confirmation(self) -> None:
        """模型异常后草稿及元数据字节不变，原版本仍可通过真实确认逻辑。"""

        before = {name: (self.directory / name).read_bytes() for name in ("artifact.json", "metadata.json")}
        _, previous = self._load()
        with patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", side_effect=RuntimeError("模型暂不可用")):
            with self.assertRaisesRegex(RuntimeError, "模型暂不可用"):
                self._revise("补充后端说明")
        for name, content in before.items():
            self.assertEqual((self.directory / name).read_bytes(), content)
        self.assertEqual(self.canonical.read_bytes(), self.canonical_before)
        _confirm_current_draft(self.workspace, self.active, "technical-plan")
        confirmed = json.loads(self.canonical.read_text(encoding="utf-8"))
        self.assertEqual(confirmed["architecture"], previous["architecture"])

    def test_invalid_candidate_preserves_original_draft_for_retry(self) -> None:
        """真实校验拒绝新候选后保留旧稿及元数据，随后重试仍从旧稿继续修改。"""

        before = {name: (self.directory / name).read_bytes() for name in ("artifact.json", "metadata.json")}
        _, previous = self._load()
        def invalid_planner(requirement, document, request, *, feedback):
            """返回语法合法但中文规则不合法的操作，验证全局校验保留原稿。"""

            return _changeset(document, request, [{
                "op": "replace", "path": "/architecture/frontend",
                "expectedBefore": document["architecture"]["frontend"],
                "value": "An English explanation without Chinese characters.",
            }])

        with patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", side_effect=invalid_planner):
            with self.assertRaisesRegex(ValueError, "简体中文"):
                self._revise("补充后端说明")
        for name, content in before.items():
            self.assertEqual((self.directory / name).read_bytes(), content)
        self.assertEqual(self.canonical.read_bytes(), self.canonical_before)
        def retry_planner(requirement, document, request, *, feedback):
            """重试始终基于保留的旧草稿，不复用失败候选。"""

            return _changeset(document, request, [{
                "op": "replace", "path": "/architecture/backend",
                "expectedBefore": document["architecture"]["backend"],
                "value": document["architecture"]["backend"] + " 重试补充后端说明。",
            }])

        with patch("app.agents.main.technical_plan_revision.plan_technical_plan_changeset", side_effect=retry_planner) as planner:
            result = self._revise("补充后端说明")
        from app.services.technical_plan_changeset import revision_document
        self.assertEqual(planner.call_args.args[1], revision_document(previous))
        self.assertEqual(result["technical_plan_baseline"], previous)
        self.assertIn("重试补充后端说明", result["technical_plan"]["architecture"]["backend"])
        self.assertEqual(self.canonical.read_bytes(), self.canonical_before)
