from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from app.domain.application_lifecycle import PendingInteractionType, utc_now
from app.services.application_lifecycle import create_application_lifecycle

# 该节点函数名以 test_ 开头，pytest 会把模块级同名符号当作测试收集，故取别名。
from app.graph.nodes.lifecycle import test_phase_confirmation as run_test_phase_confirmation
from app.graph.workflow import route_test_phase_confirmation
from app.domain.development_artifacts import (
    DevelopmentArtifactProgress, EntityDevelopmentProgress, TestEntryGate,
)
from app.graph.nodes.planning import (
    _entity_source_binding_implementation, entity_source_binding,
)
from app.protocols.workflow.projection import (
    _workflow_progress_summary,
    _workflow_summary,
    _workflow_visual_payload,
)
from app.protocols.workflow.request import _server_resume_values, workflow_run_inputs
from app.protocols.workflow.runtime import _source_entity_test_entry


class WorkflowTestPhaseConfirmationTests(unittest.TestCase):
    """覆盖 Build 状态恢复与测试阶段确认按钮契约。"""

    def setUp(self) -> None:
        """隔离此组 Build 证据单测的持久化边界，全量门禁另有真实文件回归。"""

        completion = patch("app.graph.nodes.lifecycle.complete_initial_development", return_value=
            create_application_lifecycle(application_id="test", application_name="测试"))
        completion.start()
        self.addCleanup(completion.stop)
        # 本轮 scope 兜底登记同样访问真实工作区，默认返回同一份内存生命周期，
        # 避免确认节点的单元测试写出磁盘文件。
        scope_completion = patch(
            "app.graph.nodes.lifecycle.complete_scope_target_development",
            return_value=create_application_lifecycle(application_id="test", application_name="测试"),
        )
        scope_completion.start()
        self.addCleanup(scope_completion.stop)
        gate = patch("app.graph.nodes.lifecycle.test_entry_gate", return_value=TestEntryGate(
            allowed=True, total=1, completed=1, pending=0, inProgress=0,
            blockers=[], reason=None,
        ))
        gate.start()
        self.addCleanup(gate.stop)

    def test_empty_public_build_summary_does_not_override_completed_state(self) -> None:
        """公开结果中的空摘要不得覆盖已完成事实，未完成时也不得伪造空摘要。"""

        # 续接值只从服务端 checkpoint 取 snake_case 事实，不接受前端投影别名。
        self.assertEqual(
            _server_resume_values({"build_summary": {"status": "completed", "failed": 0}})[
                "build_summary"
            ],
            {"status": "completed", "failed": 0},
        )
        # 续接值只丢弃 None；空摘要由公开投影层判定"没有事实"，不会投影成 buildSummary。
        self.assertEqual(_server_resume_values({"build_summary": {}}), {"build_summary": {}})
        self.assertIsNone(_server_resume_values({"build_summary": None}).get("build_summary"))
        # 公开投影侧同一条规则：空摘要不得被投影成 buildSummary。
        self.assertNotIn(
            "buildSummary",
            _workflow_progress_summary({"build_summary": {}}, []),
        )

    def test_confirmed_final_entity_can_enter_test_without_build_unit(self) -> None:
        """最后完成的实体使用正式绑定事实进入同一测试确认门。"""

        lifecycle = create_application_lifecycle(application_id="test", application_name="测试")
        lifecycle.development_artifacts.entities["product"] = EntityDevelopmentProgress(
            initialDevelopmentStatus="completed"
        )
        state = {
            "selected_entity_id": "product",
            "entity_test_entry_id": "product",
            "build_execution_scope": {"type": "data_source", "targetId": "product"},
            "test_phase_confirmation": {},
        }
        with patch("app.graph.nodes.lifecycle.require_test_entry", return_value=lifecycle):
            waiting = run_test_phase_confirmation(state)
            confirmed = run_test_phase_confirmation({
                **state, "test_phase_confirmation": {"action": "confirm"},
            })
        self.assertEqual(waiting["clarification"]["mode"], "test_phase_confirmation")
        self.assertEqual(route_test_phase_confirmation(waiting), "await_user_input")
        self.assertEqual(confirmed["status"], "completed")
        self.assertEqual(route_test_phase_confirmation({**state, **confirmed}), "integration_test")
        self.assertNotIn("build_summary", confirmed)
        with patch("app.graph.nodes.lifecycle.require_test_entry", return_value=lifecycle):
            mismatch = run_test_phase_confirmation({
                **state,
                "build_execution_scope": {"type": "data_source", "targetId": "other"},
                "test_phase_confirmation": {"action": "confirm"},
            })
        self.assertEqual(mismatch["status"], "failed")

    def test_entity_test_handoff_reads_only_matching_server_checkpoint(self) -> None:
        """跨会话测试确认只继承有待确认交互的实体执行标记。"""

        execution = SimpleNamespace(
            scope="data_source",
            target_id="product",
            thread_id="entity-thread",
            pending_interaction=SimpleNamespace(
                type=PendingInteractionType.TEST_PHASE_CONFIRMATION
            ),
        )
        lifecycle = SimpleNamespace(active_executions={"entity-run": execution})
        graph = SimpleNamespace(aget_state=lambda _: async_checkpoint())

        async def async_checkpoint() -> SimpleNamespace:
            """模拟服务端保存的实体确认 checkpoint。"""

            return SimpleNamespace(values={"entity_test_entry_id": "product"})

        with patch("app.protocols.workflow.runtime.load_application_lifecycle", return_value=lifecycle):
            self.assertEqual(
                asyncio.run(_source_entity_test_entry(graph, "/workspace", "entity-run")),
                "product",
            )
            execution.pending_interaction.type = PendingInteractionType.ENTITY_SOURCE_BINDING
            self.assertEqual(
                asyncio.run(_source_entity_test_entry(graph, "/workspace", "entity-run")),
                "",
            )

    def test_final_entity_confirmation_projects_test_button_in_same_conversation(self) -> None:
        """实体写盘后门禁放行时，原对话以测试确认卡收尾。"""

        state = {
            "workspace": "/workspace",
            "selected_entity_id": "product",
            "pending_project_plan": {"entities": []},
            "entity_source_binding_submission": {"review_status": "confirmed"},
            "build_execution_scope": {"type": "data_source", "targetId": "product"},
        }
        gate = TestEntryGate(
            allowed=True, total=1, completed=1, pending=0, inProgress=0,
            blockers=[], reason=None,
        )
        with (
            patch("app.graph.nodes.planning._pending_entity_design_validation_errors", return_value=[]),
            patch("app.graph.nodes.planning.apply_entity_source_binding_submission", return_value={"entities": []}),
            patch("app.graph.nodes.planning._execute_confirmed_entity_database_operations", return_value={"entities": []}),
            patch("app.graph.nodes.planning.write_project_plan_document", return_value="plan.md"),
            patch("app.graph.nodes.planning._project_plan_json_path_for_state", return_value="plan.json"),
            patch("app.graph.nodes.planning.refresh_development_artifacts"),
            patch("app.graph.nodes.planning.test_entry_gate", return_value=gate),
            patch("app.graph.nodes.planning._selected_detail_plans", return_value=[]),
            patch("app.graph.nodes.planning._entity_design_confirmed_payload", return_value={"mode": "entity_source_binding"}),
        ):
            result = _entity_source_binding_implementation(state)
            gate.allowed = False
            blocked = _entity_source_binding_implementation(state)
        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"]["mode"], "test_phase_confirmation")
        self.assertEqual(result["entity_test_entry_id"], "product")
        self.assertEqual(blocked["status"], "completed")
        self.assertEqual(blocked["entity_test_entry_id"], "")
        with patch(
            "app.graph.nodes.planning._entity_source_binding_implementation",
            return_value=result.copy(),
        ):
            projected = entity_source_binding({"selected_entity_id": "product"})
        self.assertEqual(projected["phase"], "test_phase_confirmation")

    def test_missing_build_summary_is_omitted_from_public_projection(self) -> None:
        """增量状态未携带 Build 摘要时不得投影伪造的空对象。"""

        result = {"phase": "unit_test", "status": "completed"}
        progress = _workflow_progress_summary(result, [])
        summary = _workflow_summary(result, [])
        payload = _workflow_visual_payload(
            run_id="run-unit-test",
            thread_id="thread-development",
            summary=summary,
            events=[],
            result=result,
        )

        self.assertNotIn("buildSummary", progress)
        self.assertNotIn("buildSummary", summary)
        self.assertNotIn("buildSummary", payload["state"])

    def test_passing_unit_tests_reach_confirmation_button_state(self) -> None:
        """单测通过后必须进入待确认状态并返回前端按钮所需载荷。"""

        inputs = workflow_run_inputs(
            {
                "clarificationAnswers": {
                    "unit_test_confirmation": {"selected": "run"}
                },
                # 继续执行只接受 resumeReference；Build 完成事实由服务端 checkpoint 提供。
                "resumeFrom": "unit_test",
            }
        )
        confirmation = run_test_phase_confirmation(
            {
                **inputs["resume_values"],
                # 同 thread 恢复时该事实由服务端 checkpoint 合并，不由客户端回传。
                "build_summary": {
                    "status": "completed",
                    "completed": 8,
                    "failed": 0,
                },
                "application_name": "年龄录入",
                "unit_test_gate_passed": True,
                "build_execution_scope": {
                    "type": "page",
                    "targetId": "page_age_entry",
                    "targetLabel": "年龄录入页",
                },
            }
        )

        self.assertEqual(inputs["resume_from"], "unit_test")
        self.assertNotIn("build_summary", inputs["resume_values"])
        self.assertEqual(confirmation["status"], "requires_user_input")
        self.assertEqual(
            confirmation["clarification"]["mode"],
            "test_phase_confirmation",
        )
        self.assertEqual(
            confirmation["clarification"]["testTarget"],
            {
                "type": "page",
                "id": "page_age_entry",
                "label": "年龄录入页",
            },
        )
        self.assertEqual(
            route_test_phase_confirmation(confirmation),
            "await_user_input",
        )

    def test_server_deletion_result_survives_test_handoff_without_build(self) -> None:
        """删除结果只沿服务端续接，跨测试线程无需 Build 结果或开发阶段单测报告。"""

        source = {
            "build_execution_scope": {"type": "page", "targetId": "return_card"},
            "deleted_page_result": {"pageId": "return_card", "completed": True},
        }
        values = _server_resume_values(source)
        self.assertEqual(values["deleted_page_result"], source["deleted_page_result"])
        lifecycle = create_application_lifecycle(application_id="test", application_name="测试")
        lifecycle.development_artifacts.pages["return_card"] = DevelopmentArtifactProgress(
            initialDevelopmentStatus="completed", completedAt=utc_now(),
            completedRunId="delete-run", completedThreadId="delete-thread",
        )
        with patch("app.graph.nodes.lifecycle.require_test_entry", return_value=lifecycle):
            result = run_test_phase_confirmation({**values, "test_phase_confirmation": {"action": "confirm"}})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["integration_next_action"], "integration_test")
        self.assertTrue(result["unit_test_gate_passed"])
        self.assertEqual(route_test_phase_confirmation({**values, **result}), "integration_test")

    def test_completed_server_tasks_recover_a_corrupted_build_summary(self) -> None:
        """旧 checkpoint 已被空摘要污染时应从服务端 Build 任务事实恢复确认卡。"""

        confirmation = run_test_phase_confirmation(
            {
                "build_summary": {},
                "tasks": [
                    {"id": "backend-service", "status": "completed"},
                    {"id": "frontend-page", "status": "already_satisfied"},
                ],
                "build_results": [
                    {"task_id": "backend-service", "status": "completed"},
                    {"task_id": "frontend-page", "status": "already_satisfied"},
                ],
                "unit_test_gate_passed": True,
                "application_name": "年龄录入",
            }
        )

        self.assertEqual(confirmation["status"], "requires_user_input")
        self.assertEqual(confirmation["build_summary"]["status"], "completed")
        self.assertEqual(
            confirmation["clarification"]["mode"],
            "test_phase_confirmation",
        )

    def test_confirmation_message_distinguishes_skipped_unit_tests(self) -> None:
        """测试阶段确认按实际单测决策展示跳过或门禁完成文案。"""

        gate = TestEntryGate(
            allowed=True, total=1, completed=1, pending=0, inProgress=0,
            blockers=[], reason=None,
        )
        for decision in ("skip", "run"):
            with (
                self.subTest(decision=decision),
                patch("app.graph.nodes.lifecycle.test_entry_gate", return_value=gate),
            ):
                confirmation = run_test_phase_confirmation({
                    "build_summary": {"status": "completed", "failed": 0},
                    "unit_test_gate_passed": True,
                    "unit_test_decision": decision,
                })
                message = confirmation["clarification"]["message"]
                self.assertEqual(confirmation["status"], "requires_user_input")
                if decision == "skip":
                    self.assertIn("单元测试已跳过", message)
                    self.assertNotIn("单元测试门禁已完成", message)
                else:
                    self.assertIn("单元测试门禁已完成", message)
                    self.assertNotIn("单元测试已跳过", message)

    def test_completed_scope_target_is_registered_before_gate(self) -> None:
        """开发完成的本轮目标必须先登记完成，否则门禁会把它算作缺口而直接结束。"""

        registered: list[dict[str, Any]] = []
        lifecycle = create_application_lifecycle(application_id="test", application_name="测试")
        lifecycle.development_artifacts.pages["card_return"] = DevelopmentArtifactProgress(
            initialDevelopmentStatus="pending"
        )
        gate = TestEntryGate(
            allowed=False, total=1, completed=0, pending=1, inProgress=0,
            blockers=[], reason="完成全部开发产物后可进入测试，当前 0/1。",
        )

        def fake_scope_completion(workspace: str, *, scope: dict[str, Any] | None,
                                  run_id: str, thread_id: str) -> Any:
            """模拟兜底登记：本轮目标登记完成后，门禁才变为放行。"""

            registered.append({"scope": scope, "run_id": run_id, "thread_id": thread_id})
            progress = lifecycle.development_artifacts.pages["card_return"]
            progress.initial_development_status = "completed"
            progress.completed_at = utc_now()
            progress.completed_run_id = run_id
            progress.completed_thread_id = thread_id
            gate.allowed = True
            gate.completed = 1
            gate.pending = 0
            gate.reason = None
            return lifecycle

        state = {
            "build_summary": {"status": "completed", "failed": 0},
            "unit_test_gate_passed": True,
            "build_execution_scope": {"type": "page", "targetId": "card_return"},
            "active_run_id": "run-1",
            "active_thread_id": "thread-1",
        }
        with (
            patch(
                "app.graph.nodes.lifecycle.complete_scope_target_development",
                side_effect=fake_scope_completion,
            ),
            patch("app.graph.nodes.lifecycle.test_entry_gate", return_value=gate),
        ):
            confirmation = run_test_phase_confirmation(state)

        self.assertEqual(
            registered,
            [{
                "scope": {"type": "page", "targetId": "card_return"},
                "run_id": "run-1",
                "thread_id": "thread-1",
            }],
        )
        self.assertEqual(confirmation["status"], "requires_user_input")
        self.assertEqual(confirmation["clarification"]["mode"], "test_phase_confirmation")
        self.assertEqual(route_test_phase_confirmation(confirmation), "await_user_input")

    def test_completed_build_run_recovers_pending_tasks_overwritten_by_debug(self) -> None:
        """调试快照把任务重置为 pending 时应从同一 Build Run 的证据恢复。"""

        task_ids = ["backend-service", "frontend-page"]
        confirmation = run_test_phase_confirmation(
            {
                "build_summary": {},
                "build_run_id": "build-run-1",
                "build_run_plan_sha256": "sha256-plan-1",
                "build_execution_slice": {
                    "task_ids": task_ids,
                    "summary": {
                        "total": 2,
                        "completed": 2,
                        "pending": 0,
                        "running": 0,
                        "failed": 0,
                    },
                },
                "tasks": [
                    {"id": "backend-service", "status": "pending"},
                    {"id": "frontend-page", "status": "pending"},
                ],
                "build_results": [
                    {"task_id": "backend-service", "status": "failed"},
                    {"task_id": "backend-service", "status": "already_satisfied"},
                    {"task_id": "frontend-page", "status": "completed"},
                ],
                "unit_test_gate_passed": True,
                "application_name": "年龄录入",
            }
        )

        self.assertEqual(confirmation["status"], "requires_user_input")
        self.assertEqual(confirmation["build_summary"]["status"], "completed")
        self.assertEqual(confirmation["build_summary"]["completed"], 2)
        self.assertEqual(
            confirmation["clarification"]["mode"],
            "test_phase_confirmation",
        )

    def test_unit_test_debug_drops_client_build_runtime_state(self) -> None:
        """单测节点调试不得把公开快照中的 pending Build 状态写回 checkpoint。"""

        inputs = workflow_run_inputs(
            {
                # 节点调试只指定恢复入口并加载产物，不接受客户端状态快照。
                "workflowDebug": {"enabled": True, "resumeFrom": "unit_test"},
            }
        )

        self.assertEqual(inputs["resume_from"], "unit_test")
        self.assertNotIn("tasks", inputs["resume_values"])
        self.assertNotIn("build_results", inputs["resume_values"])
        self.assertNotIn("build_summary", inputs["resume_values"])


if __name__ == "__main__":
    unittest.main()
