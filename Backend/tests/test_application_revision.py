from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.domain.application_revision import RevisionContinuationRequest
from app.domain.application_revision import RevisionImpact, RevisionTarget
from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    PendingInteractionType,
    WorkbenchExecutionStatus,
)
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    create_application_lifecycle,
    transition_application_lifecycle,
    start_workbench_execution,
    update_workbench_execution,
    write_application_lifecycle,
    load_application_lifecycle,
    end_workbench_execution,
)
from app.services.application_revision_lifecycle import (
    consume_revision_continuation,
    discard_active_revision,
    issue_revision_continuation,
    register_revision_impact,
    submit_revision_impact,
    update_active_revision_progress,
)
from app.services.artifact_invalidation import (
    ArtifactInvalidationError,
    assert_confirmed_artifact_closure,
    stale_artifact_keys,
)
from app.services.revision_drafts import (
    confirm_revision_draft,
    create_revision_draft,
    discard_current_revision_draft,
    save_revision_draft_markdown,
)
from app.services.revision_routing import enforce_revision_routing
from app.graph.application_planning_revision import analyze_design_intent
from app.protocols.workflow.revision import (
    bind_revision_draft_interaction,
    parse_revision_draft_interaction,
)
from app.protocols.workflow.lifecycle import (
    begin_workflow_lifecycle,
    project_workflow_lifecycle_boundary,
)
from app.protocols.workflow.definition import workflow_capabilities
from app.workspace.revision_draft_documents import revision_draft_directory


def _candidate(route: str, **overrides: object) -> dict[str, object]:
    """构造一个符合当前统一路由合同的模型候选。"""

    return {
        "route": route,
        "owner": "frontend" if route == "implementation_fix" else "none",
        "reason": "测试路由",
        "confidence": 0.95,
        "candidatePaths": [],
        "affectedArtifactKeys": [],
        "affectedResourceKeys": [],
        "questions": [],
        **overrides,
    }


class RevisionRoutingTests(unittest.TestCase):
    """验证五类路由、两条 formal branch 和不可绕过的确定性升级。"""

    def test_local_bug_remains_implementation_fix(self) -> None:
        """已确认语义内的按钮 Bug 应继续走 SmallTask 实现修复。"""

        result = enforce_revision_routing(
            _candidate("implementation_fix"),
            user_request="订单删除按钮点击没有反应，请修复。",
        )

        self.assertEqual(result.candidate.route.value, "implementation_fix")
        self.assertIsNone(result.impact)

    def test_revision_run_registration_does_not_skip_draft_confirmation(self) -> None:
        """application_revision 运行登记不能把待确认修订提前写成 building。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = lifecycle.model_copy(
                update={
                    "initialization": lifecycle.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, lifecycle)
            register_revision_impact(
                directory,
                interaction_id="impact_1",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run",
                request="修改技术规划",
                target=RevisionTarget(type="application"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["application"],
                    reason="技术契约变化",
                ),
            )
            active = submit_revision_impact(
                directory,
                interaction_id="impact_1",
                decision="approved",
            )
            assert active is not None
            update_active_revision_progress(
                directory,
                change_id=active.change_id,
                status="awaiting_user",
                current_artifact="technical-plan",
            )

            begin_workflow_lifecycle(
                {"workspace": directory, "resume_values": {}},
                thread_id="workbench-thread",
                run_id="run-1",
                phase="application_revision",
            )

            current = load_application_lifecycle(directory)
            assert current is not None and current.active_formal_revision is not None
            self.assertEqual(current.active_formal_revision.status, "awaiting_user")

    def test_formal_product_operation_uses_original_design_branch(self) -> None:
        """模型判定产品语义变化后必须从 ProductPlan 返回原设计规划流程。"""

        result = enforce_revision_routing(
            _candidate(
                "formal_revision",
                owner="none",
                formalBranch="design_stage_revision",
                revisionType="product_behavior_change",
                earliestArtifact="product-plan",
            ),
            user_request="订单列表新增批量归档操作。",
            target={"type": "page", "pageId": "orders"},
        )

        self.assertEqual(result.candidate.route.value, "formal_revision")
        self.assertEqual(result.candidate.formal_branch.value, "design_stage_revision")
        self.assertEqual(result.candidate.earliest_artifact.value, "product-plan")
        assert result.impact is not None
        self.assertIn("page:orders", result.impact.affected_resources)

    def test_formal_contract_and_database_changes_use_workbench_branch(self) -> None:
        """模型判定 API 与数据库语义变化后必须从 TechnicalPlan 进入工作台草稿。"""

        for request, revision_type in (
            ("接口响应字段增加 archivedAt", "technical_contract_change"),
            ("数据源从 mock 改为 MySQL", "data_source_change"),
        ):
            with self.subTest(request=request):
                result = enforce_revision_routing(
                    _candidate(
                        "formal_revision",
                        owner="none",
                        formalBranch="workbench_plan_revision",
                        revisionType=revision_type,
                        earliestArtifact="technical-plan",
                    ),
                    user_request=request,
                )
                self.assertEqual(
                    result.candidate.formal_branch.value,
                    "workbench_plan_revision",
                )
                self.assertEqual(result.candidate.earliest_artifact.value, "technical-plan")

    def test_formal_route_keeps_transitive_downstream_artifact_closure(self) -> None:
        """单次分类 JSON 仍须由服务端补齐最早产物的下游闭包。"""

        from app.agents.direct_modification import _normalize_direct_modification_decision

        projected = _normalize_direct_modification_decision(
            {
                "route": "formal_revision",
                "formalBranch": "design_stage_revision",
                "revisionType": "product_behavior_change",
                "earliestArtifact": "product-plan",
                "owner": "frontend",
                "confidence": 0.9,
                "reason": "新增批量归档操作会改变已确认的产品行为。",
                "clarificationQuestion": "",
                "candidatePaths": [],
                "affectedArtifactKeys": [],
                "affectedResourceKeys": [],
                "questions": [],
            },
            user_request="新增批量归档操作。",
        )

        self.assertEqual(projected.intent, "formal_revision")
        self.assertEqual(
            projected.affected_artifact_keys,
            ("product-plan", "ui-design", "technical-plan"),
        )

    def test_explicit_formal_revision_does_not_call_impact_analyzer(self) -> None:
        """正式修订应直接使用分类 JSON，不再调用第二个模型生成影响证据。"""

        from unittest.mock import patch

        from app.agents.direct_modification import _normalize_direct_modification_decision
        from app.domain.change_impact import (
            AnalysisStatus,
            AtomicChange,
            ChangeImpactAnalysis,
            CodeScanEvidence,
        )
        from app.graph.nodes.direct_modification import classify_direct_modification

        decision = _normalize_direct_modification_decision(
            {
                "response": "",
                "route": "formal_revision",
                "formalBranch": "design_stage_revision",
                "revisionType": "requirement_scope_change",
                "earliestArtifact": "requirement-spec",
                "owner": "frontend",
                "confidence": 0.80,
                "reason": "用户请求新增一个页面，这改变了产品范围。",
                "clarificationQuestion": "",
                "candidatePaths": [],
                "affectedArtifactKeys": [],
                "affectedResourceKeys": [],
                "questions": [],
            },
            user_request="我想新增页面订单管理页",
        )
        analysis = ChangeImpactAnalysis(
            analysisStatus=AnalysisStatus.INSUFFICIENT_EVIDENCE,
            requestSummary="当前 JSON 没有该新增页面的既有事实。",
            atomicChanges=[
                AtomicChange(
                    changeId="C1",
                    requestedChange="新增页面",
                    contractImpact="unknown",
                    contractEvidence=[],
                    codeScan=CodeScanEvidence(
                        performed=False,
                        reason="没有既有事实可供代码扫描。",
                        findings=[],
                    ),
                )
            ],
            earliestAffectedContractStage=None,
            invalidatedContracts=[],
        )
        with patch(
            "app.graph.nodes.direct_modification.classify_direct_modification_intent",
            return_value=decision,
        ), patch(
            "app.graph.nodes.direct_modification.analyze_change_impact",
            return_value=analysis,
        ) as analyzer:
            update = classify_direct_modification(
                {
                    "request": "我想新增页面订单管理页",
                    "change_impact_enabled": True,
                }
            )

        self.assertEqual(update["status"], "requires_user_input")
        self.assertEqual(update["conversation_intent"], "formal_revision")
        self.assertEqual(
            update["clarification"]["mode"],
            "revision_impact_confirmation",
        )
        self.assertEqual(
            update["revision_impact"]["earliestArtifact"],
            "requirement-spec",
        )
        self.assertEqual(
            update["revision_impact"]["affectedArtifacts"],
            ["requirement-spec", "product-plan", "ui-design", "technical-plan"],
        )
        self.assertEqual(update["revision_impact"]["evidence"], [])
        analyzer.assert_not_called()

    def test_design_revision_enters_lifecycle_artifact_without_second_classifier(self) -> None:
        """已批准 design revision 应直接进入 lifecycle.currentArtifact 对应节点。"""

        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            ).model_copy(
                update={
                    "initialization": create_application_lifecycle(
                        application_id="app-1",
                        application_name="任务中心",
                        initialization_thread_id="planning-thread",
                    ).initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, lifecycle)
            register_revision_impact(
                directory,
                interaction_id="impact-design",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run",
                request="把订单页改成双列布局",
                target=RevisionTarget(type="page", pageId="orders"),
                impact=RevisionImpact(
                    formalBranch="design_stage_revision",
                    revisionType="ui_visual_change",
                    earliestArtifact="ui-design",
                    affectedArtifacts=["ui-design", "technical-plan"],
                    affectedResources=["page:orders"],
                    reason="页面视觉目标变化",
                ),
            )
            active = submit_revision_impact(
                directory,
                interaction_id="impact-design",
                decision="approved",
            )
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.current_artifact, "ui-design")
            self.assertEqual(active.remaining_artifacts, ["technical-plan"])

            with patch(
                "app.graph.application_planning_revision.classify_design_conversation",
            ) as classify_design_conversation:
                update = analyze_design_intent(
                    {
                        "request": "把订单页改成双列布局",
                        "workspace": directory,
                        "active_run_id": "run-design",
                        "requirement_spec": {"confirmation_status": "confirmed"},
                        "product_plan": {"confirmation_status": "confirmed"},
                        "ui_designs": {"confirmation_status": "confirmed"},
                    }
                )
            classify_design_conversation.assert_not_called()

        self.assertEqual(update["design_change_target"], "ui_confirmation")
        self.assertEqual(update["design_change_generation_target"], "ui_confirmation")
        self.assertEqual(update["design_change_affected_page_ids"], ["orders"])
        self.assertEqual(update["resume_from"], "")
        self.assertIn("currentArtifact", update["design_change_reason"])
        self.assertEqual(update["technical_plan"], {})
        self.assertEqual(update["technical_plan_path"], "")
        self.assertEqual(update["technical_plan_json_path"], "")
        self.assertEqual(update["revision_continuation"], {})

    def test_old_route_and_short_continuation_token_are_rejected(self) -> None:
        """current-contract-only 不接受旧枚举或不透明度不足的 continuation。"""

        with self.assertRaises(ValidationError):
            enforce_revision_routing(
                _candidate("workspace_change"),
                user_request="修复按钮",
            )
        with self.assertRaises(ValidationError):
            RevisionContinuationRequest.model_validate(
                {"changeId": "chg_1", "token": "client-node-name"}
            )

    def test_health_capabilities_publish_controlled_revision_actions(self) -> None:
        """健康元数据必须声明受控 revision actions 且禁止客户端选择节点。"""

        capabilities = workflow_capabilities()
        actions = capabilities["workflowActions"]
        self.assertFalse(actions["clientNodeSelectionAllowed"])
        self.assertTrue(
            {
                "start_revision",
                "submit_revision_interaction",
                "continue_revision_build",
            }.issubset(actions["values"])
        )
        self.assertIn(
            "application-revision",
            capabilities["eventProtocol"]["eventTypes"],
        )


class RevisionDraftTests(unittest.TestCase):
    """验证隔离草稿、基线保护、确认覆盖和仅草稿放弃语义。"""

    def test_save_confirm_and_discard_preserve_contract(self) -> None:
        """保存不确认，确认覆盖 canonical，放弃只删除当前草稿。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            plans = workspace / ".xcodeagent" / "plans"
            plans.mkdir(parents=True)
            canonical_md = plans / "technical-plan.md"
            canonical_json = plans / "technical-plan.json"
            upstream = plans / "product-plan.json"
            canonical_md.write_text("# old\n", encoding="utf-8")
            canonical_json.write_text(
                json.dumps({"confirmation_status": "confirmed", "title": "old"}),
                encoding="utf-8",
            )
            upstream.write_text(json.dumps({"confirmation_status": "confirmed"}), encoding="utf-8")
            create_revision_draft(
                workspace,
                change_id="chg_1",
                artifact_key="technical-plan",
                kind="technical_plan",
                target_id="application",
                canonical_json_path=canonical_json,
                markdown="# new\n",
                artifact={"title": "generated"},
                based_on_paths={"product-plan": upstream},
            )
            draft_hash = save_revision_draft_markdown(
                workspace,
                change_id="chg_1",
                artifact_key="technical-plan",
                markdown="# edited\n",
            )
            self.assertEqual(canonical_md.read_text(encoding="utf-8"), "# old\n")
            self.assertEqual(len(draft_hash), 64)

            result = confirm_revision_draft(
                workspace,
                change_id="chg_1",
                artifact_key="technical-plan",
                canonical_markdown_path=canonical_md,
                canonical_json_path=canonical_json,
                based_on_paths={"product-plan": upstream},
                synchronize_markdown=lambda markdown, artifact: {
                    **artifact,
                    "title": markdown.strip("# \n"),
                },
                validate_artifact=lambda artifact: self.assertEqual(
                    artifact["title"], "edited"
                ),
            )
            self.assertEqual(result["confirmationStatus"], "confirmed")
            self.assertEqual(canonical_md.read_text(encoding="utf-8"), "# edited\n")
            self.assertFalse(
                revision_draft_directory(
                    workspace,
                    change_id="chg_1",
                    artifact_key="technical-plan",
                ).exists()
            )

            create_revision_draft(
                workspace,
                change_id="chg_2",
                artifact_key="technical-plan",
                kind="technical_plan",
                target_id="application",
                canonical_json_path=canonical_json,
                markdown="# abandoned\n",
                artifact={"title": "abandoned"},
                based_on_paths={"product-plan": upstream},
            )
            canonical_before = canonical_json.read_bytes()
            discard_current_revision_draft(
                workspace,
                change_id="chg_2",
                artifact_key="technical-plan",
            )
            self.assertEqual(canonical_json.read_bytes(), canonical_before)

    def test_changed_upstream_rejects_confirmation(self) -> None:
        """草稿生成后直接上游变化必须拒绝确认，不能套用旧草稿。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "technical-plan.json"
            markdown = root / "technical-plan.md"
            upstream = root / "product-plan.json"
            canonical.write_text('{"confirmation_status":"confirmed"}', encoding="utf-8")
            markdown.write_text("# old", encoding="utf-8")
            upstream.write_text('{"revision":1}', encoding="utf-8")
            create_revision_draft(
                root,
                change_id="chg_1",
                artifact_key="technical-plan",
                kind="technical_plan",
                target_id="application",
                canonical_json_path=canonical,
                markdown="# new",
                artifact={},
                based_on_paths={"product-plan": upstream},
            )
            upstream.write_text('{"revision":2}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "直接上游已变化"):
                confirm_revision_draft(
                    root,
                    change_id="chg_1",
                    artifact_key="technical-plan",
                    canonical_markdown_path=markdown,
                    canonical_json_path=canonical,
                    based_on_paths={"product-plan": upstream},
                    synchronize_markdown=lambda _markdown, artifact: artifact,
                    validate_artifact=lambda _artifact: None,
                )


class ArtifactInvalidationTests(unittest.TestCase):
    """验证 basedOn 直接哈希传播和 Build confirmed-only 门禁。"""

    def test_hash_mismatch_propagates_to_transitive_downstream(self) -> None:
        """直接上游失配后应确定性传播到其全部下游。"""

        old_hash = "1" * 64
        new_hash = "2" * 64
        technical_hash = "3" * 64
        artifacts = {
            "technical-plan": {
                "confirmation_status": "confirmed",
                "basedOn": [{"artifactKey": "product-plan", "sha256": old_hash}],
            },
            "build-plan": {
                "confirmation_status": "confirmed",
                "basedOn": [{"artifactKey": "technical-plan", "sha256": technical_hash}],
            },
        }
        hashes = {
            "product-plan": new_hash,
            "technical-plan": technical_hash,
        }
        self.assertEqual(
            stale_artifact_keys(artifacts, canonical_hashes=hashes),
            ["build-plan", "technical-plan"],
        )
        with self.assertRaises(ArtifactInvalidationError):
            assert_confirmed_artifact_closure(artifacts, canonical_hashes=hashes)


class RevisionLifecycleTests(unittest.TestCase):
    """验证 impact 审批边界和 design continuation 的绑定与一次性消费。"""

    def test_technical_plan_revision_uses_current_api_contract_validator_signature(self) -> None:
        """正式草稿确认只能按当前单参数合同校验 TechnicalPlan API Contract。"""

        from unittest.mock import patch

        from app.graph.nodes.application_revision import _validate_technical_plan

        artifact = {"artifact_type": "technical-plan"}
        requirement_spec: dict[str, object] = {}
        product_plan: dict[str, object] = {}
        ui_designs: dict[str, object] = {}
        with patch(
            "app.graph.nodes.application_revision.materialize_technical_plan_runtime",
            return_value=artifact,
        ), patch(
            "app.graph.nodes.application_revision.validate_project_plan_dependencies",
            return_value=[],
        ), patch(
            "app.graph.nodes.application_revision.validate_api_contract_consistency",
            return_value=[],
        ), patch(
            "app.graph.nodes.application_revision.validate_project_plan_datasource_policy",
            return_value=[],
        ), patch(
            "app.graph.nodes.application_revision.validate_technical_plan_api_contracts",
            return_value=[],
        ) as validate_api_contracts, patch(
            "app.graph.nodes.application_revision.validate_page_implementation_contracts",
            return_value=[],
        ):
            _validate_technical_plan(
                artifact,
                requirement_spec,
                product_plan,
                ui_designs,
            )

        validate_api_contracts.assert_called_once_with(artifact)

    def test_impact_approval_and_continuation_are_single_use(self) -> None:
        """确认前无 change，确认后复用原 thread，token 只能消费一次。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = lifecycle.model_copy(
                update={
                    "initialization": lifecycle.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, lifecycle)
            pending = register_revision_impact(
                directory,
                interaction_id="impact_1",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run",
                request="新增批量归档",
                target=RevisionTarget(type="page", pageId="orders"),
                impact=RevisionImpact(
                    formalBranch="design_stage_revision",
                    revisionType="product_behavior_change",
                    earliestArtifact="product-plan",
                    affectedArtifacts=["product-plan", "ui-design", "technical-plan"],
                    affectedResources=["page:orders"],
                    reason="新增产品操作",
                ),
            )
            self.assertEqual(pending.status, "pending")
            self.assertEqual(pending.source_thread_id, "conversation-thread")
            self.assertEqual(pending.source_run_id, "conversation-run")
            active = submit_revision_impact(
                directory,
                interaction_id="impact_1",
                decision="approved",
            )
            assert active is not None
            self.assertEqual(active.planning_thread_id, "planning-thread")
            self.assertEqual(active.source_thread_id, "conversation-thread")
            self.assertEqual(active.source_run_id, "conversation-run")

            technical_plan = Path(directory) / "technical-plan.json"
            technical_plan.write_text(
                '{"artifact_type":"technical-plan","confirmation_status":"confirmed"}',
                encoding="utf-8",
            )
            token, issued = issue_revision_continuation(
                directory,
                change_id=active.change_id,
                technical_plan_path=technical_plan,
            )
            self.assertGreaterEqual(len(token), 32)
            self.assertIsNone(issued.continuation_consumed_at)
            consumed = consume_revision_continuation(
                directory,
                change_id=active.change_id,
                token=token,
                technical_plan_path=technical_plan,
            )
            self.assertEqual(consumed.status, "building")
            with self.assertRaisesRegex(ValueError, "已消费"):
                consume_revision_continuation(
                    directory,
                    change_id=active.change_id,
                    token=token,
                    technical_plan_path=technical_plan,
                )

    def test_workbench_continuation_starts_development_without_planning_execution(self) -> None:
        """独立 application_planning 完成 TechnicalPlan 后可直接启动开发执行。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = lifecycle.model_copy(
                update={
                    "initialization": lifecycle.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, lifecycle)
            register_revision_impact(
                directory,
                interaction_id="impact_workbench",
                source_thread_id="source-thread",
                source_run_id="source-run",
                request="修改 TechnicalPlan",
                target=RevisionTarget(type="application"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["application"],
                    reason="技术契约变化",
                ),
            )
            active = submit_revision_impact(
                directory,
                interaction_id="impact_workbench",
                decision="approved",
            )
            assert active is not None
            technical_plan = Path(directory) / "technical-plan.json"
            technical_plan.write_text(
                '{"artifact_type":"technical-plan","confirmation_status":"confirmed"}',
                encoding="utf-8",
            )
            token, issued = issue_revision_continuation(
                directory,
                change_id=active.change_id,
                technical_plan_path=technical_plan,
            )
            self.assertIsNone(issued.continuation_source_run_id)
            consumed = consume_revision_continuation(
                directory,
                change_id=active.change_id,
                token=token,
                technical_plan_path=technical_plan,
            )

            begin_workflow_lifecycle(
                {
                    "workspace": directory,
                    "workflow_action": "continue_revision_build",
                    "resume_values": {
                        "build_execution_scope": {
                            "type": "application",
                            "targetId": "application",
                        },
                    },
                },
                thread_id="development-thread",
                run_id="development-run",
                phase="application_revision",
            )

            current = load_application_lifecycle(directory)
            assert current is not None
            self.assertIn("development-run", current.active_executions)
            self.assertEqual(
                current.resource_locks.application.run_id,
                "development-run",
            )
            from unittest.mock import patch
            from app.graph.nodes.application_revision import start_application_revision

            with patch(
                "app.graph.nodes.application_revision.plan_project_with_chat_model"
            ) as planner, patch(
                "app.graph.nodes.application_revision._current_plan_state",
                return_value={},
            ):
                result = start_application_revision({"workspace": directory})
            planner.assert_not_called()
            self.assertEqual(result["status"], "revision_artifacts_confirmed")

    def test_main_workflow_impact_persists_and_final_acceptance_releases_revision(self) -> None:
        """主 Workflow SmallTask impact 应绑定 lifecycle，最终验收后释放 active lease。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = lifecycle.model_copy(
                update={
                    "initialization": lifecycle.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, lifecycle)
            start_workbench_execution(
                directory,
                scope="application",
                target_id="application",
                page_id=None,
                thread_id="workbench-thread",
                run_id="run-1",
                phase="small_task_repair",
            )
            update = {
                "status": "requires_user_input",
                "request": "新增订单归档接口",
                "change_target": {"type": "application"},
                "revision_impact": {
                    "interactionId": "impact_1",
                    "formalBranch": "workbench_plan_revision",
                    "revisionType": "technical_contract_change",
                    "earliestArtifact": "technical-plan",
                    "affectedArtifacts": ["technical-plan"],
                    "affectedResources": ["application"],
                    "reason": "需要新增接口契约",
                    "risks": ["所有受影响正式产物必须重新确认后才能进入 Build。"],
                    "status": "pending",
                },
                "clarification": {
                    "mode": "revision_impact_confirmation",
                },
            }
            payload = project_workflow_lifecycle_boundary(
                directory,
                run_id="run-1",
                node_name="small_task_repair",
                update=update,
            )
            persisted = load_application_lifecycle(directory)
            assert persisted is not None
            self.assertIsNotNone(payload)
            self.assertIsNotNone(persisted.pending_revision_impact)
            assert persisted.pending_revision_impact is not None
            self.assertEqual(
                persisted.pending_revision_impact.based_on_lifecycle_revision,
                persisted.revision,
            )
            active = submit_revision_impact(
                directory,
                interaction_id="impact_1",
                decision="approved",
            )
            assert active is not None
            update_active_revision_progress(
                directory,
                change_id=active.change_id,
                status="building",
                current_artifact=None,
            )
            completion_update = {"status": "completed"}
            project_workflow_lifecycle_boundary(
                directory,
                run_id="run-1",
                node_name="finalize_project",
                update=completion_update,
            )
            completed = load_application_lifecycle(directory)
            assert completed is not None
            self.assertIsNone(completed.active_formal_revision)
            self.assertEqual(
                completion_update["application_revision_completion"]["changeId"],
                active.change_id,
            )

    def test_discarded_revision_closes_workbench_execution_and_resource_locks(self) -> None:
        """丢弃正式草稿后必须同时释放 execution、资源锁和 formal lease。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = lifecycle.model_copy(
                update={
                    "initialization": lifecycle.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, lifecycle)
            register_revision_impact(
                directory,
                interaction_id="impact_discard",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run",
                request="修改技术规划",
                target=RevisionTarget(type="application"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["application"],
                    reason="技术契约变化",
                ),
            )
            active = submit_revision_impact(
                directory,
                interaction_id="impact_discard",
                decision="approved",
            )
            assert active is not None
            start_workbench_execution(
                directory,
                scope="application",
                target_id="application",
                page_id=None,
                thread_id="workbench-thread",
                run_id="run-discard",
                phase="application_revision",
            )

            # application_revision 节点已经完成草稿删除并释放 formal lease；
            # 生命周期边界负责收口剩余的工作台 execution。
            discard_active_revision(directory, change_id=active.change_id)
            update = {
                "status": "discarded",
                "change_id": active.change_id,
            }
            payload = project_workflow_lifecycle_boundary(
                directory,
                run_id="run-discard",
                node_name="application_revision",
                update=update,
            )

            persisted = load_application_lifecycle(directory)
            assert persisted is not None
            self.assertIsNotNone(payload)
            self.assertEqual(persisted.active_executions, {})
            self.assertIsNone(persisted.active_formal_revision)
            self.assertIsNone(persisted.resource_locks.application)
            self.assertEqual(persisted.resource_locks.pages, {})
            self.assertEqual(persisted.resource_locks.endpoints, {})
            self.assertEqual(persisted.resource_locks.api_contracts, {})
            self.assertEqual(persisted.resource_locks.data_sources, {})
            self.assertEqual(
                update["application_revision_discarded"],
                {"changeId": active.change_id, "status": "discarded"},
            )

    def test_discard_close_is_idempotent_when_execution_was_already_removed(self) -> None:
        """重复收到 discard 边界时只返回当前快照，不递增版本或影响其他运行。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = lifecycle.model_copy(
                update={
                    "initialization": lifecycle.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, lifecycle)
            start_workbench_execution(
                directory,
                scope="application",
                target_id="application",
                page_id=None,
                thread_id="thread-1",
                run_id="run-1",
                phase="application_revision",
            )
            first = end_workbench_execution(directory, run_id="run-1")
            second = end_workbench_execution(directory, run_id="run-1", missing_ok=True)
            self.assertEqual(second.revision, first.revision)
            self.assertEqual(second.active_executions, {})

    def test_explicit_end_releases_failed_formal_revision_for_next_request(self) -> None:
        """用户明确结束失败的正式修改后，下一次 impact 可以正常登记。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = lifecycle.model_copy(
                update={
                    "initialization": lifecycle.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, lifecycle)
            register_revision_impact(
                directory,
                interaction_id="impact-failed",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run",
                request="修改技术规划",
                target=RevisionTarget(type="page", pageId="dashboard"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["page:dashboard"],
                    reason="技术契约变化",
                ),
            )
            active = submit_revision_impact(
                directory,
                interaction_id="impact-failed",
                decision="approved",
            )
            assert active is not None
            start_workbench_execution(
                directory,
                scope="page",
                target_id="dashboard",
                page_id="dashboard",
                thread_id="revision-thread",
                run_id="revision-run",
                phase="application_revision",
            )
            update_active_revision_progress(
                directory,
                change_id=active.change_id,
                status="failed",
                current_artifact="technical-plan",
            )

            ended = end_workbench_execution(directory, run_id="revision-run")

            self.assertIsNone(ended.active_formal_revision)
            self.assertEqual(ended.active_executions, {})
            pending = register_revision_impact(
                directory,
                interaction_id="impact-next",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run-next",
                request="再次修改技术规划",
                target=RevisionTarget(type="page", pageId="dashboard"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["page:dashboard"],
                    reason="再次修改技术契约",
                ),
            )
            self.assertEqual(pending.interaction_id, "impact-next")

    def test_next_impact_replaces_only_orphaned_failed_formal_revision(self) -> None:
        """旧结束动作遗留的孤立失败 lease 可自愈，可恢复失败运行仍保持独占。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = lifecycle.model_copy(
                update={
                    "initialization": lifecycle.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, lifecycle)
            register_revision_impact(
                directory,
                interaction_id="impact-old",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run-old",
                request="旧技术规划修改",
                target=RevisionTarget(type="page", pageId="dashboard"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["page:dashboard"],
                    reason="旧技术契约变化",
                ),
            )
            active = submit_revision_impact(
                directory,
                interaction_id="impact-old",
                decision="approved",
            )
            assert active is not None
            update_active_revision_progress(
                directory,
                change_id=active.change_id,
                status="failed",
                current_artifact="technical-plan",
            )

            pending = register_revision_impact(
                directory,
                interaction_id="impact-new",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run-new",
                request="新技术规划修改",
                target=RevisionTarget(type="page", pageId="dashboard"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["page:dashboard"],
                    reason="新技术契约变化",
                ),
            )

            self.assertEqual(pending.interaction_id, "impact-new")
            persisted = load_application_lifecycle(directory)
            assert persisted is not None
            self.assertIsNone(persisted.active_formal_revision)
            self.assertEqual(
                persisted.pending_revision_impact.interaction_id,
                "impact-new",
            )

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-2",
                application_name="订单中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = lifecycle.model_copy(
                update={
                    "initialization": lifecycle.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, lifecycle)
            register_revision_impact(
                directory,
                interaction_id="impact-recoverable",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run",
                request="修改订单技术规划",
                target=RevisionTarget(type="page", pageId="orders"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["page:orders"],
                    reason="订单技术契约变化",
                ),
            )
            active = submit_revision_impact(
                directory,
                interaction_id="impact-recoverable",
                decision="approved",
            )
            assert active is not None
            start_workbench_execution(
                directory,
                scope="page",
                target_id="orders",
                page_id="orders",
                thread_id="revision-thread",
                run_id="revision-run",
                phase="application_revision",
            )
            update_active_revision_progress(
                directory,
                change_id=active.change_id,
                status="failed",
                current_artifact="technical-plan",
            )

            with self.assertRaisesRegex(
                ApplicationLifecycleConflictError,
                "当前 application 已有 formal revision 正在进行",
            ):
                register_revision_impact(
                    directory,
                    interaction_id="impact-conflict",
                    source_thread_id="conversation-thread",
                    source_run_id="conversation-run-2",
                    request="并发修改订单技术规划",
                    target=RevisionTarget(type="page", pageId="orders"),
                    impact=RevisionImpact(
                        formalBranch="workbench_plan_revision",
                        revisionType="technical_contract_change",
                        earliestArtifact="technical-plan",
                        affectedArtifacts=["technical-plan"],
                        affectedResources=["page:orders"],
                        reason="并发技术契约变化",
                    ),
                )

    def test_explicit_end_of_unrelated_execution_keeps_formal_revision(self) -> None:
        """结束其他页面运行时不能误释放当前 formal revision。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = lifecycle.model_copy(
                update={
                    "initialization": lifecycle.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(directory, lifecycle)
            register_revision_impact(
                directory,
                interaction_id="impact-orders",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run",
                request="修改订单页技术规划",
                target=RevisionTarget(type="page", pageId="orders"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["page:orders"],
                    reason="订单技术契约变化",
                ),
            )
            active = submit_revision_impact(
                directory,
                interaction_id="impact-orders",
                decision="approved",
            )
            assert active is not None
            start_workbench_execution(
                directory,
                scope="page",
                target_id="profile",
                page_id="profile",
                thread_id="profile-thread",
                run_id="profile-run",
                phase="build",
            )

            ended = end_workbench_execution(directory, run_id="profile-run")

            self.assertIsNotNone(ended.active_formal_revision)
            assert ended.active_formal_revision is not None
            self.assertEqual(ended.active_formal_revision.change_id, active.change_id)

    def test_draft_interaction_binds_current_lifecycle_and_markdown_hash(self) -> None:
        """草稿动作必须同时匹配 active change、pending interaction 和最新 Markdown。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
                initialization_thread_id="planning-thread",
            )
            lifecycle = lifecycle.model_copy(
                update={
                    "initialization": lifecycle.initialization.model_copy(
                        update={
                            "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                            "status": ApplicationLifecycleStatus.COMPLETED,
                        }
                    )
                }
            )
            write_application_lifecycle(workspace, lifecycle)
            register_revision_impact(
                workspace,
                interaction_id="impact_1",
                source_thread_id="conversation-thread",
                source_run_id="conversation-run",
                request="修改技术规划",
                target=RevisionTarget(type="application"),
                impact=RevisionImpact(
                    formalBranch="workbench_plan_revision",
                    revisionType="technical_contract_change",
                    earliestArtifact="technical-plan",
                    affectedArtifacts=["technical-plan"],
                    affectedResources=["application"],
                    reason="技术契约变化",
                ),
            )
            active = submit_revision_impact(
                workspace,
                interaction_id="impact_1",
                decision="approved",
            )
            assert active is not None
            plans = workspace / ".xcodeagent" / "plans"
            plans.mkdir(parents=True)
            canonical = plans / "technical-plan.json"
            upstream = plans / "product-plan.json"
            canonical.write_text('{"confirmation_status":"confirmed"}', encoding="utf-8")
            upstream.write_text('{"confirmation_status":"confirmed"}', encoding="utf-8")
            create_revision_draft(
                workspace,
                change_id=active.change_id,
                artifact_key="technical-plan",
                kind="technical_plan",
                target_id="application",
                canonical_json_path=canonical,
                markdown="# draft",
                artifact={},
                based_on_paths={"product-plan": upstream},
            )
            update_active_revision_progress(
                workspace,
                change_id=active.change_id,
                status="awaiting_user",
                current_artifact="technical-plan",
            )
            start_workbench_execution(
                workspace,
                scope="application",
                target_id="application",
                page_id=None,
                thread_id="workbench-thread",
                run_id="run-1",
                phase="application_revision",
            )
            current = update_workbench_execution(
                workspace,
                run_id="run-1",
                phase="application_revision",
                status=WorkbenchExecutionStatus.AWAITING_USER,
                pending_type=PendingInteractionType.REVISION_DRAFT_CONFIRMATION,
                pending_payload={"mode": "revision_draft_confirmation"},
            )
            pending = current.active_executions["run-1"].pending_interaction
            assert pending is not None
            interaction = parse_revision_draft_interaction(
                {
                    "changeId": active.change_id,
                    "interactionId": pending.id,
                    "basedOnLifecycleRevision": pending.based_on_revision,
                    "artifactKey": "technical-plan",
                    "draftSha256": hashlib.sha256(b"# draft").hexdigest(),
                    "action": "confirm",
                }
            )
            assert interaction is not None
            binding = bind_revision_draft_interaction(workspace, interaction)
            self.assertEqual(binding["lifecycleSubmission"]["runId"], "run-1")
            stale = interaction.model_copy(update={"draft_sha256": "0" * 64})
            with self.assertRaisesRegex(ValueError, "draftSha256"):
                bind_revision_draft_interaction(workspace, stale)


if __name__ == "__main__":
    unittest.main()
