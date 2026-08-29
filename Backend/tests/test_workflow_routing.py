from __future__ import annotations

import unittest

from app.graph.workflow import (
    build_graph,
    route_application_revision,
    route_acceptance,
    route_acceptance_phase_confirmation,
    route_authorization_bootstrap,
    route_build_result,
    route_development_readiness,
    route_entity_source_binding,
    route_prepare_build_tasks,
    route_project_planning,
    route_review_phase_confirmation,
    route_code_review,
    route_small_task_result,
    route_test_phase_confirmation,
    route_test_validation,
    route_workflow_start,
)
from app.graph.nodes.lifecycle import test_phase_confirmation as run_test_phase_confirmation
from app.graph.nodes.code_review import review_phase_confirmation
from app.graph.nodes.code_review import acceptance_phase_confirmation
from app.protocols.workflow.lifecycle import _pending_interaction
from app.domain.application_lifecycle import PendingInteractionType


class WorkflowRoutingTests(unittest.TestCase):
    def test_workflow_start_defaults_to_development_readiness(self) -> None:
        self.assertEqual(
            route_workflow_start({}),
            "development_readiness_gate",
        )

    def test_workflow_start_can_resume_from_entity_source_binding(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "entity_source_binding"}),
            "entity_source_binding",
        )

    def test_workflow_start_can_resume_from_project_planning(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "project_planning"}),
            "project_planning",
        )

    def test_workflow_start_can_resume_from_application_revision(self) -> None:
        """正式修订动作必须进入已注册的 application_revision 节点。"""

        self.assertEqual(
            route_workflow_start({"resume_from": "application_revision"}),
            "application_revision",
        )
        self.assertIn("application_revision", build_graph(checkpointer=None).get_graph().nodes)

    def test_application_revision_enters_development_readiness_after_artifacts_confirmed(self) -> None:
        """草稿确认门停图，正式产物确认后先经过开发就绪门禁。"""

        self.assertEqual(
            route_application_revision({"status": "requires_user_input"}),
            "await_user_input",
        )
        self.assertEqual(
            route_application_revision({"status": "revision_artifacts_confirmed"}),
            "development_readiness_gate",
        )

    def test_project_planning_waits_for_confirmation(self) -> None:
        self.assertEqual(
            route_project_planning({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_project_planning_continues_to_development_readiness(self) -> None:
        self.assertEqual(
            route_project_planning({"status": "completed"}),
            "development_readiness_gate",
        )

    def test_workflow_start_can_resume_from_prepare_build_tasks(self) -> None:
        self.assertEqual(
            route_workflow_start({"resume_from": "prepare_build_tasks"}),
            "prepare_build_tasks",
        )

    def test_workflow_start_can_resume_from_database_context(self) -> None:
        """旧数据库上下文恢复标识直接兼容到任务准备节点。"""

        self.assertEqual(
            route_workflow_start({"resume_from": "inspect_database_context"}),
            "prepare_build_tasks",
        )

    def test_development_readiness_waits_for_user_input(self) -> None:
        self.assertEqual(
            route_development_readiness({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_development_readiness_continues_when_ready(self) -> None:
        self.assertEqual(
            route_development_readiness({"status": "completed"}),
            "inspect_workspace",
        )

    def test_entity_source_binding_always_ends_as_independent_interaction(self) -> None:
        """实体数据源绑定确认后不自动进入页面/API开发。"""

        self.assertEqual(
            route_entity_source_binding(
                {"status": "completed", "selected_entity_id": "product"}
            ),
            "await_user_input",
        )

    def test_prepare_build_tasks_waits_for_project_plan_confirmation(self) -> None:
        self.assertEqual(
            route_prepare_build_tasks({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_prepare_build_tasks_continues_to_build_after_confirmation(self) -> None:
        self.assertEqual(
            route_prepare_build_tasks({"status": "completed"}),
            "build",
        )

    def test_prepare_build_tasks_enters_authorization_bootstrap_when_enabled(self) -> None:
        """权限开启的已确认技术规划必须在 Build 前经过平台 Bootstrap。"""

        self.assertEqual(
            route_prepare_build_tasks(
                {
                    "status": "completed",
                    "technical_plan": {
                        "artifact_type": "technical-plan",
                        "confirmation_status": "confirmed",
                        "authorization_manifest": {
                            "enabled": True,
                            "fingerprint": "sha256:test",
                        },
                    },
                }
            ),
            "authorization_bootstrap",
        )
        self.assertEqual(route_authorization_bootstrap({"status": "completed"}), "build")
        self.assertEqual(route_authorization_bootstrap({"status": "failed"}), "handle_failure")

    def test_integration_test_passes_to_review_confirmation(self) -> None:
        self.assertEqual(
            route_test_validation({"quality_gate_passed": True}),
            "review_phase_confirmation",
        )

    def test_review_confirmation_requires_structured_completion(self) -> None:
        self.assertEqual(
            route_review_phase_confirmation({"status": "requires_user_input"}),
            "await_user_input",
        )
        self.assertEqual(
            route_review_phase_confirmation(
                {"status": "completed", "quality_gate_passed": True}
            ),
            "code_review",
        )
        self.assertEqual(
            route_code_review({"status": "completed"}),
            "acceptance_phase_confirmation",
        )

    def test_acceptance_phase_confirmation_requires_structured_completion(self) -> None:
        """代码审查成功后必须先停在验收阶段确认门。"""

        base = {
            "code_review_result": {"status": "completed"},
            "code_review_repair_result": {"status": "not_required"},
        }
        waiting = acceptance_phase_confirmation(base)
        self.assertEqual(waiting["status"], "requires_user_input")
        self.assertEqual(
            waiting["clarification"]["mode"], "acceptance_phase_confirmation"
        )
        self.assertEqual(
            route_acceptance_phase_confirmation({"status": "requires_user_input"}),
            "await_user_input",
        )
        completed = acceptance_phase_confirmation(
            {**base, "acceptance_phase_confirmation": {"action": "confirm"}}
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(route_acceptance_phase_confirmation(completed), "acceptance")

    def test_code_review_issues_pause_for_repair_confirmation(self) -> None:
        """代码审查发现问题时停在当前审查 thread 等待一键修复。"""

        self.assertEqual(
            route_code_review({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_build_only_enters_unit_test_after_complete_summary(self) -> None:
        """构建切片完整成功后必须先进入开发阶段单测门禁。"""

        self.assertEqual(
            route_build_result({"build_summary": {"status": "completed"}}),
            "unit_test",
        )

    def test_test_phase_confirmation_waits_for_user_input(self) -> None:
        """开发完成确认门在用户操作前必须暂停主 Workflow。"""

        self.assertEqual(
            route_test_phase_confirmation({"status": "requires_user_input"}),
            "await_user_input",
        )

    def test_test_phase_confirmation_enters_integration_test_after_confirm(self) -> None:
        """用户确认进入测试阶段后才允许执行集成测试。"""

        self.assertEqual(
            route_test_phase_confirmation(
                {
                    "status": "completed",
                    "build_summary": {"status": "completed"},
                    "unit_test_gate_passed": True,
                }
            ),
            "integration_test",
        )

    def test_test_phase_confirmation_resets_integration_repair_budget(self) -> None:
        """进入测试阶段时不应继承 Build 阶段已经消耗的修复次数。"""

        result = run_test_phase_confirmation(
            {
                "build_summary": {"status": "completed"},
                "unit_test_gate_passed": True,
                "test_phase_confirmation": {"action": "confirm"},
                "repair_iteration": 3,
            }
        )

        self.assertEqual(result["repair_iteration"], 0)
        self.assertEqual(result["max_repair_iterations"], 3)
        self.assertEqual(result["repair_return_node"], "integration_test")

    def test_test_phase_confirmation_rejects_incomplete_build(self) -> None:
        """确认节点不能被直接恢复到未完成的 Build。"""

        result = run_test_phase_confirmation(
            {"build_summary": {"status": "running"}, "application_name": "测试应用"}
        )

        self.assertEqual(result["status"], "failed")
        self.assertNotIn("clarification", result)

    def test_test_phase_confirmation_projects_stable_page_target(self) -> None:
        """确认卡应投影当前页面的稳定 ID 和显示名称。"""

        result = run_test_phase_confirmation(
            {
                "build_summary": {"status": "completed"},
                "unit_test_gate_passed": True,
                "build_execution_scope": {"type": "page", "targetId": "orders"},
                "project_plan": {"pages": [{"pageId": "orders", "name": "订单页"}]},
            }
        )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["testTarget"],
            {"type": "page", "id": "orders", "label": "订单页"},
        )

    def test_test_phase_confirmation_projects_endpoint_target_name(self) -> None:
        """接口测试目标优先使用接口名称而不是自然语言确认文本。"""

        result = run_test_phase_confirmation(
            {
                "build_summary": {"status": "completed"},
                "unit_test_gate_passed": True,
                "build_execution_scope": {"type": "endpoint", "targetId": "orders.list"},
                "project_plan": {
                    "api_contracts": [
                        {
                            "id": "orders-api",
                            "endpoints": [
                                {
                                    "id": "orders.list",
                                    "name": "订单列表接口",
                                    "method": "GET",
                                    "path": "/orders",
                                }
                            ],
                        }
                    ]
                },
            }
        )

        self.assertEqual(
            result["clarification"]["testTarget"],
            {"type": "endpoint", "id": "orders.list", "label": "订单列表接口"},
        )

    def test_test_phase_confirmation_uses_typed_lifecycle_interaction(self) -> None:
        """测试阶段确认应投影为独立的生命周期待交互类型。"""

        interaction_type, payload = _pending_interaction(
            {
                "clarification": {
                    "mode": "test_phase_confirmation",
                    "testTarget": {"type": "application", "id": "app", "label": "应用"},
                }
            }
        )

        self.assertEqual(interaction_type, PendingInteractionType.TEST_PHASE_CONFIRMATION)
        self.assertEqual(payload["testTarget"]["id"], "app")

    def test_frontend_performance_confirmation_uses_typed_lifecycle_interaction(self) -> None:
        """性能测试选择应投影为可恢复的独立生命周期交互类型。"""

        interaction_type, payload = _pending_interaction(
            {
                "clarification": {
                    "mode": "frontend_performance_confirmation",
                    "status": "requires_user_input",
                }
            }
        )

        self.assertEqual(
            interaction_type,
            PendingInteractionType.FRONTEND_PERFORMANCE_CONFIRMATION,
        )
        self.assertEqual(payload["mode"], "frontend_performance_confirmation")

    def test_code_review_repair_confirmation_uses_typed_lifecycle_interaction(self) -> None:
        """一键修复确认应投影为同一审查线程内可恢复的生命周期交互。"""

        interaction_type, payload = _pending_interaction(
            {
                "clarification": {
                    "mode": "code_review_repair_confirmation",
                    "status": "requires_user_input",
                }
            }
        )

        self.assertEqual(
            interaction_type,
            PendingInteractionType.CODE_REVIEW_REPAIR_CONFIRMATION,
        )
        self.assertEqual(payload["mode"], "code_review_repair_confirmation")

    def test_acceptance_phase_confirmation_uses_typed_lifecycle_interaction(self) -> None:
        """验收阶段确认应投影为可跨审查 thread 接管的生命周期交互。"""

        interaction_type, payload = _pending_interaction(
            {
                "clarification": {
                    "mode": "acceptance_phase_confirmation",
                    "status": "requires_user_input",
                }
            }
        )

        self.assertEqual(
            interaction_type,
            PendingInteractionType.ACCEPTANCE_PHASE_CONFIRMATION,
        )
        self.assertEqual(payload["mode"], "acceptance_phase_confirmation")

    def test_build_confirmation_stops_for_user_input(self) -> None:
        """构建需要扩大范围时必须停留等待用户确认。"""

        self.assertEqual(
            route_build_result({"build_summary": {"status": "requires_confirmation"}}),
            "await_user_input",
        )

    def test_incomplete_build_routes_to_failure_instead_of_testing(self) -> None:
        """待执行、阻塞或终止失败的构建不得伪装成可测试状态。"""

        for status in ("in_progress", "needs_repair", "failed"):
            with self.subTest(status=status):
                self.assertEqual(
                    route_build_result({"build_summary": {"status": status}}),
                    "handle_failure",
                )

    def test_integration_test_repair_plan_enters_small_task_agent(self) -> None:
        self.assertEqual(
            route_test_validation(
                {
                    "quality_gate_passed": False,
                    "integration_next_action": "small_task_repair",
                }
            ),
            "small_task_repair",
        )

    def test_integration_test_confirmation_waits_for_user(self) -> None:
        self.assertEqual(
            route_test_validation(
                {
                    "quality_gate_passed": False,
                    "integration_next_action": "await_user_input",
                }
            ),
            "await_user_input",
        )

    def test_integration_test_confirmation_overrides_stale_quality_gate(self) -> None:
        """重试测试时上一轮通过结果不得让本轮确认门直接进入预览。"""

        self.assertEqual(
            route_test_validation(
                {
                    "status": "requires_user_input",
                    "quality_gate_passed": True,
                    "integration_next_action": "await_user_input",
                }
            ),
            "await_user_input",
        )

    def test_integration_test_terminal_failure_routes_to_failure(self) -> None:
        self.assertEqual(
            route_test_validation(
                {
                    "quality_gate_passed": False,
                    "integration_next_action": "handle_failure",
                }
            ),
            "handle_failure",
        )

    def test_successful_small_task_returns_to_integration_test(self) -> None:
        """局部修复成功后必须重新进入集成测试，不能沿用上一轮 failed 状态。"""

        self.assertEqual(
            route_small_task_result(
                {
                    "status": "in_progress",
                    "small_task_route": "integration_test",
                    "integration_next_action": "integration_test",
                }
            ),
            "integration_test",
        )

    def test_acceptance_requires_structured_accepted_state(self) -> None:
        """最终完成节点只能由结构化验收通过状态解锁。"""

        self.assertEqual(route_acceptance({"accepted": True}), "finalize_project")
        self.assertEqual(route_acceptance({"accepted": False}), "await_user_input")


if __name__ == "__main__":
    unittest.main()
