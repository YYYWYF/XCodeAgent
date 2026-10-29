"""技术规划 Markdown 确认必须沿用当前应用配置和已确认产品规划。"""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation, ApplicationPlanningRecoveryBoundary,
    application_planning_boundary_payload,
)
from app.graph.nodes.application_revision import _confirm_current_draft
from app.graph.nodes.application_technical_planning import technical_planning_confirm
from app.services.application_config import read_application_config
from app.services.application_lifecycle import persist_application_lifecycle_transition
from app.services.revision_drafts import create_revision_draft, save_revision_draft_markdown
from app.services.technical_plan_topology_selection import select_technical_plan_topology
from app.workspace.plan_documents import commit_technical_plan_document, render_project_plan_markdown
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness


class TechnicalMarkdownConfirmationConfigTests(unittest.TestCase):
    """使用真实配置、文档和确认节点，只替换外部同步模型。"""

    def setUp(self) -> None:
        """准备配置版本为 2 的已选架构技术规划，并写入待确认生命周期。"""

        self.harness = TechnicalPlanningRecoveryJourneyHarness()
        self.addCleanup(self.harness.close)
        self.root = self.harness.workspace / ".devagentstudio"
        application_path = self.root / "application.json"
        application = json.loads(application_path.read_text(encoding="utf-8"))
        application["configRevision"] = 2
        application_path.write_text(json.dumps(application), encoding="utf-8")
        self.application = read_application_config(self.harness.workspace)
        self.plan = select_technical_plan_topology(
            {**self.harness.candidate, "sourceConfigRevision": 2},
            "backend_direct", self.application,
        )
        self.state: dict[str, Any] = {
            "workspace": str(self.harness.workspace),
            "workflow_scope": "application_planning",
            "active_run_id": self.harness.source_run_id,
            "requirement_spec": self.harness.requirement_spec,
            "requirement_spec_path": str(self.root / "specs/requirement-spec.md"),
            "product_plan": self.harness.product_plan,
            "product_plan_path": str(self.root / "plans/product-plan.md"),
            "ui_designs": self.harness.ui_designs,
            "technical_plan": self.plan,
            "application_planning_interaction": {"action": "confirm"},
            "application_planning_recovery_boundary": application_planning_boundary_payload(
                operation_id="markdown-confirm-config", operation=ApplicationPlanningOperation.INITIAL,
                boundary=ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED,
                request="", candidate=self.plan,
            ),
        }
        markdown, document = commit_technical_plan_document(self.state, self.plan)
        self.state.update(technical_plan_path=markdown, technical_plan_json_path=document)
        self.edited_markdown = render_project_plan_markdown(self.plan) + "\n人工补充说明。\n"
        persist_application_lifecycle_transition(
            self.harness.workspace,
            stage=ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            active_run_id=self.harness.source_run_id,
        )

    def test_confirmation_keeps_current_config_revision_and_selected_topology(self) -> None:
        """无业务变更的 Markdown 同步仍保持版本 2，并可正式确认原架构。"""

        Path(self.state["technical_plan_path"]).write_text(self.edited_markdown, encoding="utf-8")
        with patch("app.agents.main.document_sync._invoke_sync_model", return_value=deepcopy(self.plan)):
            result = technical_planning_confirm(self.state)
        self.assertEqual(result["status"], "completed")
        confirmed = result["technical_plan"]
        self.assertEqual(confirmed["sourceConfigRevision"], 2)
        self.assertEqual(confirmed["topology"], self.plan["topology"])
        self.assertEqual(confirmed["confirmation_status"], "confirmed")
        self.assertEqual(read_application_config(self.harness.workspace), self.application)

    def test_confirmation_still_rejects_an_actual_config_change(self) -> None:
        """真实配置变更仍在同步模型调用前被拒绝，不能以修复为由改写旧版本。"""

        self.state["technical_plan"] = {**self.plan, "sourceConfigRevision": 1}
        with patch("app.agents.main.document_sync._invoke_sync_model") as model:
            with self.assertRaisesRegex(ValueError, "应用配置已变化"):
                technical_planning_confirm(self.state)
        model.assert_not_called()

    def test_runtime_markdown_confirmation_preserves_the_selected_architecture(self) -> None:
        """Runtime 文档同步不会因默认配置版本退回架构选择或 Java 技术栈。"""

        runtime = select_technical_plan_topology(self.plan, "agent_runtime_direct", self.application)
        self.state["technical_plan"] = runtime
        self.state["application_planning_recovery_boundary"] = application_planning_boundary_payload(
            operation_id="runtime-markdown-confirm", operation=ApplicationPlanningOperation.INITIAL,
            boundary=ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED,
            request="", candidate=runtime,
        )
        commit_technical_plan_document(self.state, runtime)
        Path(self.state["technical_plan_path"]).write_text(
            render_project_plan_markdown(runtime) + "\n人工补充说明。\n", encoding="utf-8",
        )
        with patch("app.agents.main.document_sync._invoke_sync_model", return_value=deepcopy(runtime)):
            result = technical_planning_confirm(self.state)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["technical_plan"]["sourceConfigRevision"], 2)
        self.assertEqual(result["technical_plan"]["topology"], runtime["topology"])
        self.assertEqual(result["technical_plan"]["architecture"], runtime["architecture"])

    def test_revision_draft_sync_keeps_current_config_and_product_context(self) -> None:
        """正式修订草稿经真实落盘确认后仍绑定当前配置版本和权限 Manifest。"""

        change_id = "markdown-config-revision"
        create_revision_draft(
            self.harness.workspace, change_id=change_id, artifact_key="technical-plan",
            kind="technical_plan", target_id="application",
            canonical_json_path=self.root / "plans/technical-plan.json",
            markdown=render_project_plan_markdown(self.plan), artifact=self.plan,
            based_on_paths={
                "product-plan": self.root / "plans/product-plan.json",
                "ui-design": self.root / "specs/ui-designs.json",
            },
        )
        save_revision_draft_markdown(
            self.harness.workspace, change_id=change_id, artifact_key="technical-plan",
            markdown=self.edited_markdown,
        )
        with patch("app.agents.main.document_sync._invoke_sync_model", return_value=deepcopy(self.plan)):
            _confirm_current_draft(self.harness.workspace, SimpleNamespace(change_id=change_id), "technical-plan")
        confirmed = json.loads((self.root / "plans/technical-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(confirmed["sourceConfigRevision"], 2)
        self.assertEqual(confirmed["authorization_manifest"], self.plan["authorization_manifest"])
        self.assertEqual(confirmed["confirmation_status"], "confirmed")
        self.assertEqual(read_application_config(self.harness.workspace), self.application)
