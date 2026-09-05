"""验证 Template Reconcile 的最小 lifecycle CAS 防重入。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.domain.application_revision import (
    ActiveFormalRevision,
    EarliestRevisionArtifact,
    FormalRevisionBranch,
    RevisionTarget,
)
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    create_application_lifecycle,
    load_application_lifecycle,
    write_application_lifecycle,
)
from app.services.template_reconcile.finalization import (
    claim_template_reconcile_finalization,
    mark_template_reconcile_failed,
)


def _active_revision(
    *,
    status: str = "drafting",
    formal_branch: FormalRevisionBranch = FormalRevisionBranch.WORKBENCH_PLAN_REVISION,
    current_artifact: str = EarliestRevisionArtifact.TECHNICAL_PLAN.value,
) -> ActiveFormalRevision:
    """构造最小 active formal revision，供 CAS 行为测试使用。"""

    return ActiveFormalRevision(
        changeId="chg_template_reconcile",
        formalBranch=formal_branch,
        sourceThreadId="thread-source",
        sourceRunId="run-source",
        request="为应用补充模板能力",
        target=RevisionTarget(type="application"),
        impactInteractionId="impact-1",
        planningThreadId="thread-planning",
        status=status,
        currentArtifact=current_artifact,
    )


def _workspace_with_active_revision(
    root: Path,
    *,
    status: str = "drafting",
    formal_branch: FormalRevisionBranch = FormalRevisionBranch.WORKBENCH_PLAN_REVISION,
    current_artifact: str = EarliestRevisionArtifact.TECHNICAL_PLAN.value,
) -> None:
    """写入带 active formal revision 的最小 lifecycle 文件。"""

    lifecycle = create_application_lifecycle(
        application_id="app-template-reconcile",
        application_name="模板能力测试",
    )
    lifecycle = lifecycle.model_copy(
        update={
            "revision": lifecycle.revision + 1,
            "active_formal_revision": _active_revision(
                status=status,
                formal_branch=formal_branch,
                current_artifact=current_artifact,
            ),
        }
    )
    write_application_lifecycle(root, lifecycle, expected_revision=0)


class TemplateReconcileFinalizationTests(unittest.TestCase):
    """验证同一 changeId 只会首次取得 Reconcile 进入权。"""

    def test_claim_is_idempotent_for_same_change_id(self) -> None:
        """确认重复调用只返回既有状态，不会再次改变 lifecycle revision。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _workspace_with_active_revision(root)
            first = claim_template_reconcile_finalization(
                root,
                change_id="chg_template_reconcile",
            )
            revision_after_first = load_application_lifecycle(root).revision
            second = claim_template_reconcile_finalization(
                root,
                change_id="chg_template_reconcile",
            )
            self.assertTrue(first.acquired)
            self.assertFalse(second.acquired)
            self.assertEqual(load_application_lifecycle(root).revision, revision_after_first)

    def test_rejects_other_change_and_continuation_state(self) -> None:
        """确认其他 changeId 和已签发 continuation 都不能进入 Reconcile。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _workspace_with_active_revision(root)
            with self.assertRaises(ApplicationLifecycleConflictError):
                claim_template_reconcile_finalization(root, change_id="chg_other")
            lifecycle = load_application_lifecycle(root)
            active = lifecycle.active_formal_revision.model_copy(
                update={"continuation_token_sha256": "a" * 64}
            )
            write_application_lifecycle(
                root,
                lifecycle.model_copy(
                    update={
                        "revision": lifecycle.revision + 1,
                        "active_formal_revision": active,
                    }
                ),
                expected_revision=lifecycle.revision,
            )
            with self.assertRaises(ApplicationLifecycleConflictError):
                claim_template_reconcile_finalization(
                    root,
                    change_id="chg_template_reconcile",
                )

    def test_marks_claimed_reconcile_as_failed(self) -> None:
        """确认只有已取得进入权的 Reconcile 能转换到失败状态。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _workspace_with_active_revision(root)
            claim_template_reconcile_finalization(root, change_id="chg_template_reconcile")
            failed = mark_template_reconcile_failed(root, change_id="chg_template_reconcile")
            self.assertEqual(failed.status, "template_reconcile_failed")

    def test_design_revision_requires_confirmed_technical_plan_progress(self) -> None:
        """设计阶段只有已确认的 TechnicalPlan 才能进入模板能力更新。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _workspace_with_active_revision(
                root,
                status="design_planning",
                formal_branch=FormalRevisionBranch.DESIGN_STAGE_REVISION,
                current_artifact="product-plan",
            )
            plan_path = root / ".xcodeagent" / "plans" / "technical-plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(
                '{"artifact_type":"technical-plan","confirmation_status":"confirmed"}',
                encoding="utf-8",
            )
            with self.assertRaises(ApplicationLifecycleConflictError):
                claim_template_reconcile_finalization(
                    root,
                    change_id="chg_template_reconcile",
                    technical_plan_path=plan_path,
                )

            lifecycle = load_application_lifecycle(root)
            active = lifecycle.active_formal_revision.model_copy(
                update={"current_artifact": "technical-plan"}
            )
            write_application_lifecycle(
                root,
                lifecycle.model_copy(
                    update={
                        "revision": lifecycle.revision + 1,
                        "active_formal_revision": active,
                    }
                ),
                expected_revision=lifecycle.revision,
            )
            claim = claim_template_reconcile_finalization(
                root,
                change_id="chg_template_reconcile",
                technical_plan_path=plan_path,
            )
            self.assertTrue(claim.acquired)
