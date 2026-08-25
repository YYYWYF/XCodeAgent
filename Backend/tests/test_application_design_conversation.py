from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from app.graph.application_planning_workflow import _route_requirements, _route_start
from app.graph.application_planning_revision import (
    analyze_design_intent,
    begin_current_artifact_revision,
    cleared_design_change_context,
    design_artifact_node_state,
    design_node_update,
    earliest_available_design_target,
    is_design_change,
    prepare_ui_revision_state,
    route_design_intent,
)
from app.protocols.application_page_planning import (
    application_page_planning_capabilities,
)
from app.protocols.workflow.projection import _workflow_next_nodes, _workflow_start_node
from app.protocols.workflow.request import workflow_run_inputs
from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.services.application_lifecycle import (
    ensure_application_lifecycle,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
    restart_application_planning_lifecycle,
)


class ApplicationDesignConversationTests(unittest.TestCase):
    """验证底部设计输入回到原 application_planning Graph。"""

    def test_capabilities_publish_existing_artifact_state_field(self) -> None:
        """能力元数据必须声明首次生成与重新生成所依据的公开状态字段。"""

        capabilities = application_page_planning_capabilities()

        self.assertEqual(
            capabilities["designChange"]["existingArtifactsStateField"],
            "design_change_existing_artifacts",
        )

    def test_design_change_request_starts_original_graph_intent_node(self) -> None:
        """设计变更必须以类型化交互恢复当前原生中断。"""

        result = workflow_run_inputs(
            {
                "message": "把首页改成运营工作台",
                "workflowScope": "application_planning",
                "applicationPlanningInteraction": {
                    "gateId": "ui_designs:revision-1",
                    "artifact": "ui_designs",
                    "artifactRevision": "revision-1",
                    "action": "design_change",
                    "request": "把首页改成运营工作台",
                },
            }
        )

        self.assertEqual(result["workflow_scope"], "application_planning")
        self.assertEqual(result["resume_from"], "")
        self.assertEqual(
            result["application_planning_interaction"]["action"],
            "design_change",
        )

    def test_design_change_rejects_non_planning_scope(self) -> None:
        """设计产物变更不能被发送到主开发 Workflow。"""

        with self.assertRaisesRegex(ValueError, "application_planning"):
            workflow_run_inputs(
                {
                    "message": "修改需求",
                    "workflowScope": "workflow",
                    "applicationPlanningInteraction": {
                        "gateId": "requirement_spec:revision-1",
                        "artifact": "requirement_spec",
                        "artifactRevision": "revision-1",
                        "action": "design_change",
                        "request": "修改需求",
                    },
                }
            )

    def test_ui_design_action_bypasses_design_intent_analysis(self) -> None:
        """UI 卡片动作必须作为当前 UI 中断的显式恢复载荷。"""

        result = workflow_run_inputs(
            {
                "message": "请根据本轮确认继续创建规划。",
                "forwardedProps": {
                    "workflowScope": "application_planning",
                    "applicationPlanningInteraction": {
                        "gateId": "ui_designs:revision-1",
                        "artifact": "ui_designs",
                        "artifactRevision": "revision-1",
                        "action": "ui_action",
                        "uiAction": {
                            "pageId": "dashboard_page",
                            "action": "regenerate",
                        },
                    },
                },
            }
        )

        self.assertEqual(result["resume_from"], "")
        self.assertEqual(
            result["application_planning_interaction"]["ui_action"],
            {"pageId": "dashboard_page", "action": "regenerate"},
        )

    @patch("app.graph.application_planning_revision.classify_design_conversation")
    def test_intent_analysis_restarts_original_requirement_stage(
        self,
        classify,
    ) -> None:
        """需求意图应调用真实生命周期服务，从后续阶段回到分析并保存原始输入。"""

        classify.return_value = SimpleNamespace(
            target="requirements",
            reason="页面清单发生变化",
            affected_page_ids=["orders"],
            response="",
        )
        with TemporaryDirectory() as workspace:
            ensure_application_lifecycle(
                workspace,
                application_id="app-1",
                application_name="测试应用",
                initialization_thread_id="planning-thread",
            )
            for stage, status in (
                (
                    ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                    ApplicationLifecycleStatus.RUNNING,
                ),
                (
                    ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
                    ApplicationLifecycleStatus.RUNNING,
                ),
                (
                    ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
                    ApplicationLifecycleStatus.AWAITING_USER,
                ),
                (
                    ApplicationLifecycleStage.GENERATING_PRODUCT_PLAN,
                    ApplicationLifecycleStatus.RUNNING,
                ),
                (
                    ApplicationLifecycleStage.AWAITING_PRODUCT_PLAN_CONFIRMATION,
                    ApplicationLifecycleStatus.AWAITING_USER,
                ),
                (
                    ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
                    ApplicationLifecycleStatus.RUNNING,
                ),
                (
                    ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
                    ApplicationLifecycleStatus.AWAITING_USER,
                ),
            ):
                persist_application_lifecycle_transition(
                    workspace,
                    stage=stage,
                    status=status,
                )

            update = analyze_design_intent(
                {
                    "request": "新增报表页",
                    "workspace": workspace,
                    "active_run_id": "run-1",
                    "requirement_spec": {"confirmation_status": "confirmed"},
                    "product_plan": {"confirmation_status": "confirmed"},
                }
            )
            persisted = load_application_lifecycle(workspace)

        self.assertEqual(update["workflow_scope"], "application_planning")
        self.assertTrue(update["design_change_submission"])
        self.assertEqual(update["design_change_target"], "requirements")
        self.assertEqual(
            update["lifecycle"]["initialization"]["stage"],
            "analyzing_requirement",
        )
        self.assertEqual(update["design_change_request"], "新增报表页")
        self.assertEqual(update["design_change_generation_target"], "requirements")
        self.assertEqual(
            update["design_change_existing_artifacts"],
            {
                "requirements": True,
                "product_planning": True,
                "ui_confirmation": False,
                "technical_planning": False,
            },
        )
        self.assertEqual(route_design_intent(update), "requirements")
        assert persisted is not None
        self.assertEqual(
            persisted.initialization.stage,
            ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
        )
        self.assertEqual(persisted.active_run_id, "run-1")

    def test_first_node_application_uses_original_change_request(self) -> None:
        """只有服务端指定的一次性目标节点读取原始变更。"""

        base_state = {
            "request": "正确，继续规划",
            "design_change_request": "新增报表页",
            "design_change_generation_target": "requirements",
            "design_change_generation_request": "新增报表页",
        }
        first = design_artifact_node_state(base_state, "requirements")
        resumed = design_artifact_node_state(
            {
                **base_state,
                "application_planning_interaction": {"action": "confirm"},
            },
            "requirements",
        )

        self.assertEqual(first["request"], "新增报表页")
        self.assertEqual(resumed["request"], "正确，继续规划")

    def test_design_node_update_keeps_revision_projection_context(self) -> None:
        """修订节点更新必须持续携带前端实时状态所需的变更上下文。"""

        update = design_node_update(
            {
                "design_change_request": "新增报表页",
                "design_change_target": "requirements",
                "design_change_reason": "页面清单发生变化",
                "design_change_generation_target": "requirements",
                "design_change_generation_request": "新增报表页",
                "design_change_existing_artifacts": {
                    "requirements": True,
                    "product_planning": True,
                    "ui_confirmation": True,
                    "technical_planning": False,
                },
            },
            "requirements",
            {"status": "requires_user_input"},
        )

        self.assertTrue(update["design_change_submission"])
        self.assertEqual(update["design_change_request"], "新增报表页")
        self.assertEqual(update["design_change_target"], "requirements")
        self.assertEqual(update["design_change_generation_target"], "requirements")
        self.assertFalse(
            update["design_change_existing_artifacts"]["technical_planning"]
        )

    def test_current_artifact_revision_clears_terminal_planning_confirmation(self) -> None:
        """重新生成任一设计产物时必须撤销旧终态，禁止客户端误启动模板初始化。"""

        update = begin_current_artifact_revision(
            {
                "application_planning_confirmation": {
                    "confirmedAt": "2026-08-20T00:00:00Z",
                },
                "requirement_spec": {"confirmation_status": "confirmed"},
            },
            node_name="requirements",
            request="新增财务复核角色",
        )

        self.assertEqual(update["application_planning_confirmation"], {})
        self.assertFalse(update["requirements_confirmed"])

    def test_confirmed_revision_advances_downstream_generation_cursor(self) -> None:
        """上游新版本确认后应重做下游，但不能把原修改文本重复套给下游。"""

        update = design_node_update(
            {
                "design_change_request": "新增报表页",
                "design_change_target": "requirements",
                "design_change_reason": "页面清单发生变化",
                "design_change_generation_target": "requirements",
                "design_change_generation_request": "新增报表页",
                "design_change_existing_artifacts": {"product_planning": True},
                "application_planning_interaction": {"action": "confirm"},
            },
            "requirements",
            {"status": "completed"},
        )

        self.assertEqual(update["design_change_generation_target"], "product_planning")
        self.assertIn("最新上游产物", update["design_change_generation_request"])
        downstream = design_artifact_node_state(
            {**update, "request": "正确，继续规划"},
            "product_planning",
        )
        self.assertNotEqual(downstream["request"], "新增报表页")
        self.assertIn("ProductPlan", downstream["request"])

    def test_terminal_snapshot_drops_design_change_context(self) -> None:
        """已终结快照禁止回传设计变更上下文，防止旧变更指令在后续轮次复活。"""

        result = workflow_run_inputs(
            {
                "message": "正确，继续规划",
                "workflowScope": "application_planning",
                "resumeState": {
                    "summary": {"status": "completed", "phase": "technical_planning"},
                    "state": {
                        "design_change_submission": True,
                        "design_change_request": "新增报表页",
                        "design_change_target": "requirements",
                        "design_change_reason": "页面清单发生变化",
                        "design_change_affected_page_ids": ["reports"],
                        "design_change_applied_nodes": ["requirements"],
                        "design_change_existing_artifacts": {"requirements": True},
                    },
                },
            }
        )

        resume_values = result["resume_values"]
        self.assertNotIn("design_change_request", resume_values)
        self.assertNotIn("design_change_target", resume_values)
        self.assertNotIn("design_change_reason", resume_values)
        self.assertNotIn("design_change_affected_page_ids", resume_values)
        self.assertNotIn("design_change_applied_nodes", resume_values)
        self.assertNotIn("design_change_existing_artifacts", resume_values)
        self.assertNotIn("design_change_submission", resume_values)

    def test_awaiting_snapshot_does_not_rehydrate_design_change_context(self) -> None:
        """等待态也必须以服务端 checkpoint 为权威，禁止前端回灌修订上下文。"""

        result = workflow_run_inputs(
            {
                "message": "只要两个角色就够了",
                "workflowScope": "application_planning",
                "resumeState": {
                    "summary": {
                        "status": "requires_user_input",
                        "phase": "requirements",
                    },
                    "state": {
                        "design_change_submission": True,
                        "design_change_request": "新增报表页",
                        "design_change_target": "requirements",
                        "design_change_reason": "页面清单发生变化",
                        "design_change_generation_target": "",
                        "design_change_existing_artifacts": {"requirements": True},
                    },
                },
            }
        )

        resume_values = result["resume_values"]
        self.assertNotIn("design_change_request", resume_values)
        self.assertNotIn("design_change_target", resume_values)
        self.assertNotIn("design_change_generation_target", resume_values)
        self.assertEqual(result["resume_from"], "requirements")

    def test_cleared_design_change_context_disarms_revision_state(self) -> None:
        """清空后的上下文必须让 is_design_change 判定为否。"""

        dirty_state = {
            "design_change_submission": True,
            "design_change_request": "新增报表页",
            "design_change_target": "requirements",
            "design_change_reason": "页面清单发生变化",
            "design_change_affected_page_ids": ["reports"],
            "design_change_generation_target": "requirements",
            "design_change_generation_request": "新增报表页",
            "design_change_existing_artifacts": {"requirements": True},
        }

        cleared_state = {**dirty_state, **cleared_design_change_context()}

        self.assertFalse(is_design_change(cleared_state))
        self.assertFalse(cleared_state["design_change_submission"])
        self.assertEqual(cleared_state["design_change_generation_target"], "")
        self.assertEqual(cleared_state["design_change_generation_request"], "")
        self.assertEqual(cleared_state["design_change_existing_artifacts"], {})

    def test_requirement_confirmation_routes_to_product_planning(self) -> None:
        """新需求确认完成后必须沿原 Graph 进入产品规划。"""

        self.assertEqual(
            _route_requirements(
                {
                    "requirement_spec": {"confirmation_status": "confirmed"},
                    "clarification": {"status": "clear"},
                }
            ),
            "product_planning",
        )
        self.assertEqual(
            _workflow_next_nodes(
                "requirements",
                {"status": "completed", "workflow_scope": "application_planning"},
            ),
            ["product_planning"],
        )

    def test_pending_requirement_routes_to_merged_product_confirmation(self) -> None:
        """需求确认与产品规划确认合并：待确认需求直接进产品规划，由产品规划门一次确认。"""

        for clarification in (None, {"status": "clear"}):
            with self.subTest(clarification=clarification):
                self.assertEqual(
                    _route_requirements(
                        {
                            "requirement_spec": {
                                "confirmation_status": "pending_user_confirmation"
                            },
                            "clarification": clarification,
                        }
                    ),
                    "product_planning",
                )

    def test_requirement_clarification_questions_still_suspend(self) -> None:
        """需求澄清问答仍挂起在原审阅门，等待用户回答后再进产品规划。"""

        self.assertEqual(
            _route_requirements(
                {
                    "requirement_spec": {"confirmation_status": "pending_user_input"},
                    "clarification": {
                        "status": "requires_user_input",
                        "mode": "ask_user_question",
                    },
                }
            ),
            "requirements_review",
        )
        self.assertEqual(
            _route_requirements(
                {
                    "requirement_spec": {
                        "confirmation_status": "pending_user_confirmation"
                    },
                    "clarification": {
                        "status": "requires_user_input",
                        "mode": "requirement_document_confirmation",
                    },
                }
            ),
            "product_planning",
        )

    def test_router_cannot_skip_unconfirmed_upstream_artifacts(self) -> None:
        """意图 Agent 不能越过尚未确认的上游产物。"""

        self.assertEqual(
            earliest_available_design_target(
                "ui_confirmation",
                requirement_spec={"confirmation_status": "pending_user_confirmation"},
                product_plan={"confirmation_status": "confirmed"},
            ),
            "requirements",
        )
        self.assertEqual(
            earliest_available_design_target(
                "ui_confirmation",
                requirement_spec={"confirmation_status": "confirmed"},
                product_plan={"confirmation_status": "pending_user_confirmation"},
            ),
            "product_planning",
        )

    def test_ui_revision_reuses_existing_pages_incrementally(self) -> None:
        """页面集合不变时应把自然语言变更转换为原 UI 节点增量动作。"""

        state = prepare_ui_revision_state(
            {
                "design_change_request": "把订单页筛选区改为折叠面板",
                "design_change_affected_page_ids": ["orders"],
                "product_plan": {"pages": [{"pageId": "orders"}]},
                "ui_designs": {
                    "confirmation_status": "confirmed",
                    "pages": [{"pageId": "orders", "status": "confirmed"}],
                },
            }
        )

        self.assertEqual(state["ui_design_action"]["action"], "adjust_pages")
        self.assertEqual(state["ui_design_action"]["pageIds"], ["orders"])
        self.assertEqual(
            state["ui_designs"]["confirmation_status"],
            "pending_user_confirmation",
        )

    def test_ui_revision_rebuilds_when_page_set_changes(self) -> None:
        """需求变更导致页面集合变化时必须让原 UI 节点重建设计稿。"""

        state = prepare_ui_revision_state(
            {
                "design_change_request": "新增报表页",
                "product_plan": {
                    "pages": [{"pageId": "orders"}, {"pageId": "reports"}]
                },
                "ui_designs": {
                    "confirmation_status": "confirmed",
                    "pages": [{"pageId": "orders", "status": "confirmed"}],
                },
            }
        )

        self.assertIsNone(state["ui_designs"])
        self.assertIsNone(state["ui_design_action"])

    def test_lifecycle_can_reopen_requirement_analysis_from_ui_confirmation(self) -> None:
        """原规划停在 UI 确认时允许受控回退到需求分析阶段。"""

        with TemporaryDirectory() as workspace:
            ensure_application_lifecycle(
                workspace,
                application_id="app-1",
                application_name="测试应用",
                initialization_thread_id="planning-thread",
            )
            transitions = [
                (
                    ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                    ApplicationLifecycleStatus.RUNNING,
                ),
                (
                    ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
                    ApplicationLifecycleStatus.RUNNING,
                ),
                (
                    ApplicationLifecycleStage.AWAITING_REQUIREMENT_CONFIRMATION,
                    ApplicationLifecycleStatus.AWAITING_USER,
                ),
                (
                    ApplicationLifecycleStage.GENERATING_PRODUCT_PLAN,
                    ApplicationLifecycleStatus.RUNNING,
                ),
                (
                    ApplicationLifecycleStage.AWAITING_PRODUCT_PLAN_CONFIRMATION,
                    ApplicationLifecycleStatus.AWAITING_USER,
                ),
                (
                    ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
                    ApplicationLifecycleStatus.RUNNING,
                ),
                (
                    ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
                    ApplicationLifecycleStatus.AWAITING_USER,
                ),
            ]
            for stage, status in transitions:
                persist_application_lifecycle_transition(
                    workspace,
                    stage=stage,
                    status=status,
                )

            restarted = restart_application_planning_lifecycle(
                workspace,
                stage=ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                active_run_id="revision-run",
            )

        self.assertEqual(
            restarted.initialization.stage,
            ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
        )
        self.assertEqual(
            restarted.initialization.status,
            ApplicationLifecycleStatus.RUNNING,
        )
        self.assertEqual(restarted.initialization.thread_id, "planning-thread")
        self.assertEqual(restarted.active_run_id, "revision-run")


if __name__ == "__main__":
    unittest.main()
