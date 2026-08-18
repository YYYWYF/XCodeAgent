from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.subgraphs.testing import (
    _repair_scoped_tasks,
    build_testing_subgraph,
    collect_unit_test_targets,
    generate_unit_tests,
    integration_test,
    repair_planning,
    validate_generated_unit_tests,
)
from app.services.test_validation import create_revision_requests


class TestingSubgraphEventsTests(unittest.TestCase):
    """Regression guard: test_events must accumulate across testing-subgraph
    nodes instead of being overwritten by the last node.

    Before the fix, ``ProjectState.test_events`` had no ``add`` reducer, so each
    node's ``test_events`` return value replaced the previous one and the final
    timeline only contained the last node's marker. The frontend test timeline
    (projected from ``test_events``) was therefore incomplete.
    """

    def test_test_events_accumulate_across_all_nodes(self) -> None:
        subgraph = build_testing_subgraph()

        with patch(
            "app.graph.subgraphs.testing.run_integration_checks",
            return_value={
                "test_results": [{"id": "frontend_install", "passed": True}],
                "test_events": ["frontend_install", "backend_install"],
            },
        ), patch(
            "app.graph.subgraphs.testing.evaluate_quality_gate",
            return_value={
                "passed": True,
                "needs_revision": False,
                "revision_requests": [],
            },
        ), patch(
            "app.graph.subgraphs.testing.write_test_report_json",
            return_value="/tmp/test_report.json",
        ), patch(
            "app.graph.subgraphs.testing.plan_repairs_with_repair_planner_agent"
        ) as repair_planner:
            result = subgraph.invoke(
                {
                    "workspace": "/tmp/workspace",
                    "build_summary": {"failed": 0, "pending": 0},
                    "test_results": [],
                    "test_events": [],
                    "code_changes": {},
                    "code_change_sets": [],
                    "timeline": [],
                    "selected_skill_names": ["workflow-skill"],
                }
            )

        events = result.get("test_events", [])
        # Every node contributes a marker; with the add reducer they accumulate
        # instead of being overwritten by the last node.
        self.assertIn("frontend_install", events)
        self.assertIn("backend_install", events)
        self.assertIn("main_quality_gate", events)
        self.assertIn("repair_planning:skipped", events)
        # Order follows node execution order.
        self.assertEqual(
            events,
            [
                "unit_test_targets:collected",
                "unit_test_generation:skipped",
                "frontend_install",
                "backend_install",
                "main_quality_gate",
                "repair_planning:skipped",
            ],
        )
        repair_planner.assert_not_called()

    def test_integration_test_forwards_nested_progress_as_custom_snapshot(self) -> None:
        """验证外层节点会把内部子图回调合并后写入 custom stream。"""

        emitted: list[dict] = []

        def invoke_subgraph(input_state: dict, *, config: dict) -> dict:
            """模拟子图执行并调用运行配置中的瞬态进度回调。"""

            reporter = config["configurable"]["integration_test_progress_reporter"]
            reporter(
                {
                    "status": "running",
                    "check": {
                        "id": "frontend_build",
                        "name": "前端构建检查",
                        "required": True,
                    },
                }
            )
            reporter(
                {
                    "status": "passed",
                    "check": {
                        "id": "frontend_build",
                        "name": "前端构建检查",
                        "required": True,
                        "evidence": "命令执行通过。",
                    },
                }
            )
            return {
                "test_results": [],
                "test_events": [],
                "test_report": {},
                "quality_gate_passed": True,
                "needs_revision": False,
                "revision_requests": [],
                "repair_task_plan": {},
                "repair_tasks": [],
                "integration_next_action": "launch_project",
                "code_changes": {},
                "code_change_sets": [],
            }

        with patch(
            "app.graph.subgraphs.testing.get_stream_writer",
            return_value=emitted.append,
        ), patch(
            "app.graph.subgraphs.testing._testing_subgraph.invoke",
            side_effect=invoke_subgraph,
        ):
            integration_test({"repair_iteration": 0, "max_repair_iterations": 3})

        self.assertEqual(len(emitted), 2)
        self.assertEqual(emitted[-1]["type"], "integration_test.checks")
        self.assertEqual(emitted[-1]["checks"][0]["status"], "passed")

    def test_frontend_only_diff_collects_only_frontend_generation_targets(self) -> None:
        """前端业务源码 diff 不得触发后端测试生成。"""

        result = collect_unit_test_targets(
            {
                "test_generation_input_code_changes": {
                    "files": [
                        {"path": "frontend/src/pages/Orders/index.tsx"},
                        {"path": "frontend/src/pages/Orders/index.less"},
                        {"path": "backend/src/test/java/demo/OrdersTest.java"},
                    ]
                }
            }
        )

        context = result["unit_test_generation_context"]
        self.assertEqual(
            context["source_files"],
            ["frontend/src/pages/Orders/index.tsx"],
        )
        self.assertEqual(context["affected_layers"], ["frontend"])

    def test_frontend_setup_file_is_not_treated_as_a_unit_test_target(self) -> None:
        """Jest setupTests.ts 不是本轮对应测试文件。"""

        result = collect_unit_test_targets(
            {
                "test_generation_input_code_changes": {
                    "files": [{"path": "frontend/tests/setupTests.ts"}]
                }
            }
        )

        context = result["unit_test_generation_context"]
        self.assertFalse(context["has_targets"])
        self.assertEqual(context["existing_test_files"], [])

    def test_frontend_resource_source_is_not_a_generation_target(self) -> None:
        """资源目录中的 TypeScript 辅助文件不应占用测试生成预算。"""

        result = collect_unit_test_targets(
            {
                "test_generation_input_code_changes": {
                    "files": [{"path": "frontend/src/assets/icons.ts"}]
                }
            }
        )

        self.assertFalse(result["unit_test_generation_context"]["has_targets"])

    def test_build_result_changed_files_are_a_compatibility_fallback(self) -> None:
        """旧 Build 节点没有 code_change_sets 时仍能收集业务源码目标。"""

        result = collect_unit_test_targets(
            {
                "build_results": [
                    {"changed_files": [{"path": "backend/src/main/java/demo/OrderService.java"}]}
                ]
            }
        )

        self.assertEqual(
            result["unit_test_generation_context"]["source_files"],
            ["backend/src/main/java/demo/OrderService.java"],
        )

    def test_comment_only_source_diff_is_not_a_generation_target(self) -> None:
        """仅整理注释不应触发单元测试生成。"""

        result = collect_unit_test_targets(
            {
                "test_generation_input_code_changes": {
                    "files": [
                        {
                            "path": "frontend/src/pages/Orders.tsx",
                            "diff": "@@ -1 +1 @@\n-// old note\n+// new note\n",
                        }
                    ]
                }
            }
        )

        self.assertFalse(result["unit_test_generation_context"]["has_targets"])

    def test_backend_infrastructure_and_dto_are_not_behavior_targets(self) -> None:
        """后端基础设施和简单 DTO 不应占用测试文件预算。"""

        result = collect_unit_test_targets(
            {
                "test_generation_input_code_changes": {
                    "files": [
                        {"path": "backend/src/main/java/demo/infrastructure/PageResult.java"},
                        {"path": "backend/src/main/java/demo/OrderDto.java"},
                        {"path": "backend/src/main/java/demo/OrderService.java"},
                    ]
                }
            }
        )

        self.assertEqual(
            result["unit_test_generation_context"]["source_files"],
            ["backend/src/main/java/demo/OrderService.java"],
        )

    def test_generation_result_is_validated_and_preserves_code_change_sets(self) -> None:
        """生成结果会限制文件数量并保留真实测试文件代码差异。"""

        generated_set = {
            "id": "code-change-set:test",
            "status": "applied",
            "workspaceRoot": "/tmp/workspace",
            "files": [{"path": "frontend/tests/page-orders.test.tsx"}],
        }
        with patch(
            "app.graph.subgraphs.testing._invoke_test_generation_agent",
            return_value={
                "status": "completed",
                "summary": "已生成主要页面测试。",
                "affected_layers": ["frontend"],
                "test_files": ["frontend/tests/page-orders.test.tsx"],
                "warnings": [],
                "validation": {"valid": True},
                "code_change_sets": [generated_set],
                "mapping_path": "/tmp/workspace/.xcodeagent/tests/unit-test-manifest.json",
            },
        ):
            generated = generate_unit_tests(
                {
                    "workspace": "/tmp/workspace",
                    "unit_test_generation_context": {
                        "has_targets": True,
                        "affected_layers": ["frontend"],
                    },
                }
            )

        validated = validate_generated_unit_tests(
            {
                "unit_test_generation": generated["unit_test_generation"],
                "unit_test_generation_context": {"affected_layers": ["frontend"]},
                "test_results": [],
            }
        )
        self.assertEqual(generated["unit_test_affected_layers"], ["frontend"])
        self.assertEqual(generated["unit_test_code_change_sets"], [generated_set])
        self.assertEqual(
            generated["unit_test_generation_code_change_sets"],
            [generated_set],
        )
        self.assertEqual(validated["test_results"][0]["id"], "frontend_test_generation")
        self.assertTrue(validated["test_results"][0]["passed"])

    def test_generation_exception_is_a_skipped_zero_test_result(self) -> None:
        """Agent 异常不会阻断旧工作区，而是明确记录零测试跳过。"""

        with patch(
            "app.graph.subgraphs.testing._invoke_test_generation_agent",
            side_effect=RuntimeError("model unavailable"),
        ):
            generated = generate_unit_tests(
                {
                    "workspace": "/tmp/workspace",
                    "unit_test_generation_context": {
                        "has_targets": True,
                        "affected_layers": ["backend"],
                    },
                }
            )

        validated = validate_generated_unit_tests(
            {
                "unit_test_generation": generated["unit_test_generation"],
                "unit_test_generation_context": {"affected_layers": ["backend"]},
                "test_results": [],
            }
        )
        check = validated["test_results"][0]
        self.assertEqual(check["id"], "backend_test_generation")
        self.assertTrue(check["passed"])
        self.assertTrue(check["skipped"])
        self.assertIn("model unavailable", check["evidence"])

    def test_source_and_existing_test_change_still_syncs_through_agent(self) -> None:
        """源码和已有测试同时变化时不能因测试文件已在 diff 中而跳过同步。"""

        with patch(
            "app.graph.subgraphs.testing._invoke_test_generation_agent",
            return_value={
                "status": "completed",
                "affected_layers": ["frontend"],
                "test_files": ["frontend/tests/page-orders.test.tsx"],
                "validation": {"valid": True},
                "code_change_sets": [],
            },
        ) as invoke_agent:
            generated = generate_unit_tests(
                {
                    "workspace": "/tmp/workspace",
                    "unit_test_generation_context": {
                        "has_targets": True,
                        "source_files": ["frontend/src/pages/Orders.tsx"],
                        "affected_layers": ["frontend"],
                        "existing_test_files": ["frontend/tests/page-orders.test.tsx"],
                    },
                }
            )

        invoke_agent.assert_called_once()
        self.assertEqual(
            generated["unit_test_generation"]["test_files"],
            ["frontend/tests/page-orders.test.tsx"],
        )

    def test_invalid_generated_file_cannot_be_reclassified_as_skipped(self) -> None:
        """存在无效测试文件时必须失败，不能套用零测试放行策略。"""

        validated = validate_generated_unit_tests(
            {
                "unit_test_generation": {
                    "status": "skipped",
                    "test_files": ["frontend/tests/orders.test.ts"],
                    "validation": {"valid": False, "invalid_contents": ["orders.test.ts"]},
                    "summary": "测试文件无有效用例",
                },
                "unit_test_generation_context": {"affected_layers": ["frontend"]},
                "test_results": [],
            }
        )

        check = validated["test_results"][0]
        self.assertFalse(check["passed"])
        self.assertFalse(check["skipped"])

    def test_declared_test_file_must_exist_at_validation_boundary(self) -> None:
        """Agent 只返回路径但没有落盘时不能伪装成已生成测试。"""

        validated = validate_generated_unit_tests(
            {
                "workspace": "/tmp/nonexistent-unit-test-workspace",
                "unit_test_generation": {
                    "status": "completed",
                    "test_files": ["frontend/tests/page-orders.test.ts"],
                    "validation": {"valid": True},
                },
                "unit_test_generation_context": {"affected_layers": ["frontend"]},
                "test_results": [],
            }
        )

        check = validated["test_results"][0]
        self.assertFalse(check["passed"])
        self.assertFalse(check["skipped"])

    def test_direct_flow_disables_unit_test_generation(self) -> None:
        """快速修改传入关闭标记后不创建测试目标。"""

        collected = collect_unit_test_targets(
            {
                "unit_test_generation_enabled": False,
                "test_generation_input_code_changes": {
                    "files": [{"path": "frontend/src/pages/Orders.tsx"}]
                },
            }
        )
        self.assertFalse(collected["unit_test_generation_context"]["has_targets"])
        self.assertEqual(collected["unit_test_affected_layers"], [])

    def test_backend_failure_routes_to_backend_owner(self) -> None:
        """后端单测失败必须使用调度器认可的 backend owner。"""

        requests = create_revision_requests(
            [
                {
                    "id": "backend_unit_tests",
                    "name": "后端单元测试",
                    "passed": False,
                    "evidence": "Mockito assertion failed.",
                    "execution": {},
                }
            ]
        )

        self.assertEqual(requests[0]["owner"], "backend")
        self.assertEqual(requests[0]["owners"], ["backend"])

    def test_repair_scope_includes_related_source_and_generated_test(self) -> None:
        """单测失败修复任务应同时允许修改业务源码和对应测试。"""

        tasks = _repair_scoped_tasks(
            {
                "build_execution_slice": {
                    "tasks": [
                        {
                            "id": "orders-service",
                            "owner": "backend",
                            "unit_id": "backend:endpoint:orders:list",
                            "allowed_paths": [
                                "backend/src/main/java/demo/OrdersService.java"
                            ],
                            "target_files": [
                                "backend/src/main/java/demo/OrdersService.java"
                            ],
                        }
                    ]
                },
                "unit_test_generation": {
                    "test_files": [
                        "backend/src/test/java/demo/OrdersServiceTest.java"
                    ]
                },
            }
        )

        self.assertEqual(len(tasks), 1)
        self.assertIn(
            "backend/src/main/java/demo/OrdersService.java",
            tasks[0]["allowed_paths"],
        )
        self.assertIn(
            "backend/src/test/java/demo/OrdersServiceTest.java",
            tasks[0]["allowed_paths"],
        )

    def test_generation_security_failure_stops_repair_planner(self) -> None:
        """测试目录外实际写入属于安全失败，不交给 SmallTask 猜测修复。"""

        with patch(
            "app.graph.subgraphs.testing.plan_repairs_with_repair_planner_agent"
        ) as planner:
            result = repair_planning(
                {
                    "quality_gate_passed": False,
                    "unit_test_generation": {
                        "validation": {
                            "unauthorized_paths": ["backend/pom.xml"]
                        }
                    },
                    "integration_repair_enabled": True,
                }
            )

        self.assertEqual(result["integration_next_action"], "handle_failure")
        self.assertEqual(result["repair_task_plan"]["status"], "terminal_failure")
        planner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
