"""正式修订与设置确认失败的生产协调链回归，不调用真实模型或业务服务。"""

from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.graph.nodes.application_revision import _create_technical_plan_draft, _confirm_current_draft, _validate_technical_plan
from app.services.technical_plan_topology_selection import select_technical_plan_topology, validate_selected_topology
from app.services.revision_drafts import save_revision_draft_markdown
from app.workspace.plan_documents import commit_technical_plan_document, load_project_plan_json
from app.workspace.revision_draft_documents import load_revision_draft
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class MainFlowFollowupTests(unittest.TestCase):
    """验证候选生成和 Markdown 同步都不能丢失已确认 Runtime 服务边界。"""

    def test_revision_and_edited_confirmation_keep_runtime_topology(self):
        """走真实草稿保存和确认，仅隔离模型及与拓扑无关的业务校验。"""

        h = TechnicalPlanningRecoveryJourneyHarness()
        self.addCleanup(h.close)
        formal = select_technical_plan_topology(h.candidate, "agent_runtime_direct", h.application_config)
        formal["confirmation_status"] = "confirmed"
        commit_technical_plan_document({"workspace": str(h.workspace)}, formal)
        candidate = deepcopy(formal)
        candidate.pop("topology")
        active = SimpleNamespace(change_id="runtime-revision", request="保留原架构并修订说明")

        def validate_topology(artifact, *_args):
            """单独验证本次回归目标，不让既有业务夹具影响拓扑门禁结论。"""
            validate_selected_topology(artifact, h.application_config)

        with (patch("app.graph.nodes.application_revision.plan_project_with_chat_model", return_value=candidate),
              patch("app.graph.nodes.application_revision._validate_technical_plan", side_effect=validate_topology)):
            _create_technical_plan_draft(h.workspace, active)
            _, _, draft = load_revision_draft(h.workspace, change_id=active.change_id, artifact_key="technical-plan")
            self.assertEqual(draft["topology"]["type"], "agent_runtime_direct")
            validate_selected_topology(draft, h.application_config)
            save_revision_draft_markdown(h.workspace, change_id=active.change_id,
                                         artifact_key="technical-plan", markdown="# 用户编辑后的技术规划")
            with patch("app.agents.main.document_sync._invoke_sync_model", return_value=candidate):
                _confirm_current_draft(h.workspace, active, "technical-plan")
        saved = load_project_plan_json(h.workspace / ".devagentstudio/plans/technical-plan.json")
        validate_selected_topology(saved, h.application_config)
        self.assertEqual(saved["confirmation_status"], "confirmed")
        self.assertEqual(saved["topology"]["type"], "agent_runtime_direct")

    def test_missing_topology_is_rejected_before_other_revision_checks(self):
        """正式确认不可把丢失服务图的规划作为普通 Java 产物提交。"""

        with self.assertRaisesRegex(ValueError, "选择应用架构"):
            _validate_technical_plan({}, {}, {}, {}, {})
