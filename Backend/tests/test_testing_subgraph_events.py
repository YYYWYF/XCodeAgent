from __future__ import annotations

import unittest
from unittest.mock import patch

from app.graph.subgraphs.testing import (
    INTEGRATION_REPAIR_PROGRESS_EVENT_TYPE,
    INTEGRATION_TEST_PROGRESS_REPORTER_KEY,
    _check_progress_snapshot_writer,
    _repair_scoped_tasks,
    actual_project_checks,
    build_project_checks,
    build_testing_subgraph,
    collect_unit_test_targets,
    frontend_performance_confirmation,
    frontend_performance_test,
    generate_unit_tests,
    integration_test,
    main_quality_gate,
    repair_planning,
    skip_frontend_performance,
    _source_layer,
    skip_unit_tests,
    unit_test_confirmation,
    validate_generated_unit_tests,
)
from app.graph.subgraphs.unit_testing import build_unit_testing_subgraph
from app.services.test_validation import create_revision_requests
from app.workspace.code_changes import CapturedWorkspaceChanges


class TestingSubgraphEventsTests(unittest.TestCase):
    """Regression guard: test_events must accumulate across testing-subgraph
    nodes instead of being overwritten by the last node.

    Before the fix, ``ProjectState.test_events`` had no ``add`` reducer, so each
    node's ``test_events`` return value replaced the previous one and the final
    timeline only contained the last node's marker. The frontend test timeline
    (projected from ``test_events``) was therefore incomplete.
    """

    def test_mapping_layer_sources_are_not_unit_test_targets(self) -> None:
        """映射层变化不生成单测目标，但 Service 仍可生成。"""

        self.assertIsNone(
            _source_layer(
                "backend/src/main/java/demo/leave/LeaveRequestAssembler.java"
            )
        )
        self.assertIsNone(
            _source_layer(
                "backend/src/main/java/demo/leave/LeaveRequestConverter.java"
            )
        )
        self.assertIsNone(
            _source_layer("backend/src/main/java/demo/leave/LeaveRequestMapper.java")
        )
        self.assertEqual(
            _source_layer(
                "backend/src/main/java/demo/leave/LeaveRequestService.java"
            ),
            "backend",
        )

    def test_failed_quality_gate_reports_repair_before_planner_runs(self) -> None:
        """门禁确认失败后必须立即广播修复准备事件，不等待 RepairPlanner 返回。"""

        emitted: list[dict] = []
        with patch(
            "app.graph.subgraphs.testing.evaluate_quality_gate",
            return_value={
                "passed": False,
                "needs_revision": True,
                "revision_requests": [],
            },
        ), patch(
            "app.graph.subgraphs.testing.write_test_report_json",
            return_value="/tmp/test-report.json",
        ):
            main_quality_gate(
                {
                    "test_results": [{"id": "backend_build", "passed": False}],
                    "integration_repair_enabled": True,
                    "repair_iteration": 0,
                    "max_repair_iterations": 3,
                },
                {
                    "configurable": {
                        INTEGRATION_TEST_PROGRESS_REPORTER_KEY: emitted.append,
                    }
                },
            )

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["type"], INTEGRATION_REPAIR_PROGRESS_EVENT_TYPE)
        self.assertIn("正在分析失败原因", emitted[0]["message"])

    def test_nested_repair_progress_reaches_outer_custom_stream(self) -> None:
        """嵌套 Testing Subgraph 的修复事件必须沿外层进度回调立即写入 custom stream。"""

        emitted: list[dict] = []

        def invoke_subgraph(input_state: dict, *, config: dict) -> dict:
            """模拟质量门禁在 RepairPlanner 前通过配置回调广播修复状态。"""

            reporter = config["configurable"][INTEGRATION_TEST_PROGRESS_REPORTER_KEY]
            reporter(
                {
                    "type": INTEGRATION_REPAIR_PROGRESS_EVENT_TYPE,
                    "message": "质量门禁未通过，正在分析失败原因并准备局部修复。",
                }
            )
            return {
                **input_state,
                "test_results": [],
                "test_events": [],
                "test_report": {},
                "quality_gate_passed": False,
                "needs_revision": True,
                "revision_requests": [],
                "repair_task_plan": {},
                "repair_tasks": [],
                "integration_next_action": "handle_failure",
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

        self.assertEqual(
            emitted,
            [
                {
                    "type": INTEGRATION_REPAIR_PROGRESS_EVENT_TYPE,
                    "message": "质量门禁未通过，正在分析失败原因并准备局部修复。",
                }
            ],
        )

    def test_integration_events_accumulate_across_all_nodes(self) -> None:
        """测试阶段子图只累计集成构建、性能和质量门禁事件。"""

        subgraph = build_testing_subgraph()

        def integration_checks(
            _state: dict,
            *,
            on_progress=None,
            phase: str = "all",
        ) -> dict:
            """按当前集成构建阶段返回检查，验证测试子图不执行单元测试。"""

            if phase == "build":
                return {
                    "test_results": [
                        {"id": "frontend_install", "passed": True},
                        {"id": "backend_install", "passed": True},
                    ],
                    "test_events": ["frontend_install", "backend_install"],
                }
            return {"test_results": [], "test_events": []}

        with patch(
            "app.graph.subgraphs.testing.run_integration_checks",
            side_effect=integration_checks,
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
        # 集成阶段不再包含单元测试生成、确认或执行事件。
        self.assertEqual(
            events,
            [
                "frontend_install",
                "backend_install",
                "frontend_performance_confirmation:auto_skipped_unavailable",
                "frontend_performance:skipped",
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

    def test_integration_confirmation_clears_stale_quality_gate_result(self) -> None:
        """等待性能测试选择时必须清空上一轮通过状态并保留确认载荷。"""

        clarification = {
            "mode": "frontend_performance_confirmation",
            "status": "requires_user_input",
            "message": "是否跳过前端性能测试？",
            "questions": [{"id": "frontend_performance_confirmation"}],
        }

        def invoke_subgraph(input_state: dict, *, config: dict) -> dict:
            """模拟构建完成后停在确认门，并验证输入终态已经重置。"""

            self.assertFalse(input_state["quality_gate_passed"])
            self.assertEqual(input_state["test_report"], {})
            return {
                **input_state,
                "status": "requires_user_input",
                "clarification": clarification,
                "integration_next_action": "await_user_input",
                "test_results": [{"id": "frontend_build", "passed": True}],
                "integration_build_checks_completed": True,
            }

        with patch(
            "app.graph.subgraphs.testing._testing_subgraph.invoke",
            side_effect=invoke_subgraph,
        ):
            result = integration_test(
                {
                    "quality_gate_passed": True,
                    "test_report": {"passed": True},
                    "review_phase_confirmation": {"action": "confirm"},
                    "code_review_result": {
                        "status": "completed",
                        "summary": "上一轮代码审查完成。",
                    },
                    "preview_url": "http://127.0.0.1:3000",
                    "launch_result": {"status": "running"},
                    "acceptance_request": {"status": "requires_user_input"},
                    "acceptance_decision": "accepted",
                    "accepted": True,
                    "repair_iteration": 0,
                    "max_repair_iterations": 3,
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertFalse(result["quality_gate_passed"])
        self.assertEqual(result["clarification"], clarification)
        self.assertEqual(result["integration_next_action"], "await_user_input")
        self.assertEqual(result["preview_url"], "")
        self.assertEqual(result["launch_result"], {})
        self.assertEqual(result["acceptance_request"], {})
        self.assertEqual(result["acceptance_decision"], "")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["review_phase_confirmation"], {})
        self.assertEqual(result["code_review_result"], {})

    def test_performance_confirmation_resume_preserves_integration_build_state(self) -> None:
        """等待性能测试选择时不能清空集成构建缓存，否则恢复后会从头重跑。"""

        clarification = {
            "mode": "frontend_performance_confirmation",
            "status": "requires_user_input",
            "message": "是否跳过前端性能测试？",
            "questions": [{"id": "frontend_performance_confirmation"}],
        }
        cached_build_results = [
            {"id": "frontend_build", "passed": True, "required": True}
        ]

        def invoke_subgraph(input_state: dict, *, config: dict) -> dict:
            """模拟停在性能确认门，并保留输入中的单测状态。"""

            return {
                **input_state,
                "status": "requires_user_input",
                "clarification": clarification,
                "integration_next_action": "await_user_input",
                "test_results": cached_build_results,
                "integration_build_checks_completed": True,
                "integration_build_results": cached_build_results,
            }

        with patch(
            "app.graph.subgraphs.testing._testing_subgraph.invoke",
            side_effect=invoke_subgraph,
        ):
            result = integration_test(
                {
                    "integration_build_checks_completed": True,
                    "integration_build_results": cached_build_results,
                    "frontend_performance_test_enabled": True,
                    "repair_iteration": 0,
                    "max_repair_iterations": 3,
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(result["clarification"], clarification)
        self.assertTrue(result["integration_build_checks_completed"])
        self.assertEqual(result["integration_build_results"], cached_build_results)
        self.assertTrue(result["frontend_performance_test_enabled"])

    def test_progress_snapshot_places_generation_after_frontend_build(self) -> None:
        """实时矩阵即使先收到生成事件，也必须把生成检查稳定排在构建检查之后。"""

        emitted: list[dict] = []
        with patch(
            "app.graph.subgraphs.testing.get_stream_writer",
            return_value=emitted.append,
        ):
            reporter = _check_progress_snapshot_writer()
            reporter(
                {
                    "status": "running",
                    "check": {
                        "id": "frontend_test_generation",
                        "name": "前端单元测试生成检查",
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
                    },
                }
            )

        self.assertEqual(
            [check["id"] for check in emitted[-1]["checks"]],
            ["frontend_build", "frontend_test_generation"],
        )

    def test_subgraph_runs_generation_only_after_build_phase_finishes(self) -> None:
        """Testing Subgraph 的真实节点顺序必须先结束构建，再开始生成检查。"""

        progress_events: list[dict] = []

        def integration_checks(
            _state: dict,
            *,
            on_progress=None,
            phase: str = "all",
        ) -> dict:
            """模拟构建和单测两阶段，并记录每阶段的实时状态。"""

            if phase == "build":
                on_progress(
                    {
                        "status": "running",
                        "check": {
                            "id": "frontend_build",
                            "name": "前端构建检查",
                            "required": True,
                        },
                    }
                )
                on_progress(
                    {
                        "status": "passed",
                        "check": {
                            "id": "frontend_build",
                            "name": "前端构建检查",
                            "required": True,
                        },
                    }
                )
                return {
                    "test_results": [{"id": "frontend_build", "passed": True}],
                    "test_events": ["frontend_build"],
                }
            return {"test_results": [], "test_events": []}

        with (
            patch(
                "app.graph.subgraphs.testing.run_integration_checks",
                side_effect=integration_checks,
            ),
            patch(
                "app.graph.subgraphs.testing._invoke_test_generation_agent",
                return_value={
                    "status": "skipped",
                    "summary": "没有生成测试文件。",
                    "affected_layers": ["frontend"],
                    "test_files": [],
                    "validation": {},
                    "code_change_sets": [],
                },
            ),
            patch(
                "app.graph.subgraphs.testing.evaluate_quality_gate",
                return_value={
                    "passed": True,
                    "needs_revision": False,
                    "revision_requests": [],
                },
            ),
            patch(
                "app.graph.subgraphs.testing.write_test_report_json",
                return_value="/tmp/test-report.json",
            ),
        ):
            result = build_testing_subgraph().invoke(
                {
                    "workspace": "/tmp/workspace",
                    "test_generation_input_code_changes": {
                        "files": [
                            {"path": "frontend/src/pages/Orders.tsx"},
                        ]
                    },
                    "test_results": [],
                    "test_events": [],
                    "code_changes": {},
                    "code_change_sets": [],
                    "unit_test_decision": "run",
                    "timeline": [],
                },
                config={
                    "configurable": {
                        INTEGRATION_TEST_PROGRESS_REPORTER_KEY: progress_events.append,
                    }
                },
            )

        self.assertEqual(
            [(event["check"]["id"], event["status"]) for event in progress_events],
            [
                ("frontend_build", "running"),
                ("frontend_build", "passed"),
                ("frontend_performance", "skipped"),
            ],
        )
        self.assertEqual(result["test_results"][-1]["id"], "frontend_performance")

    def test_unit_test_gate_pauses_before_generation(self) -> None:
        """开发阶段单测节点必须先等待用户决定，不能自动进入测试生成。"""

        with patch(
            "app.graph.subgraphs.testing._invoke_test_generation_agent"
        ) as invoke_agent:
            result = build_unit_testing_subgraph().invoke(
                {
                    "workspace": "/tmp/workspace",
                    "code_changes": {
                        "files": [
                            {"path": "frontend/src/pages/Orders.tsx", "diff": "+return null"}
                        ]
                    },
                    "test_results": [],
                    "test_events": [],
                    "code_change_sets": [],
                    "timeline": [],
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"],
            "unit_test_confirmation",
        )
        invoke_agent.assert_not_called()

    def test_disabled_unit_test_generation_skips_confirmation(self) -> None:
        """快速修改模式关闭测试生成时不应被新的确认门阻塞。"""

        result = unit_test_confirmation({"unit_test_generation_enabled": False})

        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result.get("clarification"), {})
        self.assertIn("unit_test_confirmation:auto_skipped_disabled", result["test_events"])

    def test_frontend_performance_confirmation_pauses_without_decision(self) -> None:
        """单测完成后未选择性能测试时，应暂停并展示确认载荷。"""

        with patch(
            "app.graph.subgraphs.testing.frontend_performance_available",
            return_value=True,
        ), patch(
            "app.graph.subgraphs.testing.frontend_build_passed",
            return_value=True,
        ):
            result = frontend_performance_confirmation(
                {"frontend_performance_test_enabled": True}
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"],
            "frontend_performance_confirmation",
        )
        self.assertEqual(result["integration_next_action"], "await_user_input")

    def test_frontend_performance_confirmation_auto_skips_when_disabled(self) -> None:
        """快速修改流程关闭性能测试时自动跳过确认门。"""

        result = frontend_performance_confirmation(
            {"frontend_performance_test_enabled": False}
        )
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["frontend_performance_decision"], "skip")
        self.assertEqual(
            result["integration_next_action"],
            "skip_frontend_performance",
        )

    def test_subgraph_runs_performance_after_unit_tests_when_confirmed(self) -> None:
        """用户选择继续执行时，性能测试必须位于单测之后、质量门禁之前。"""

        def integration_checks(
            _state: dict,
            *,
            on_progress=None,
            phase: str = "all",
        ) -> dict:
            """按阶段返回构建与单测检查。"""

            if phase == "build":
                return {
                    "test_results": [
                        {"id": "frontend_build", "passed": True, "required": True}
                    ],
                    "test_events": ["frontend_build"],
                }
            return {
                "test_results": [
                    {"id": "frontend_unit_tests", "passed": True, "required": True}
                ],
                "test_events": ["frontend_unit_tests"],
            }

        with patch(
            "app.graph.subgraphs.testing.run_integration_checks",
            side_effect=integration_checks,
        ), patch(
            "app.graph.subgraphs.testing.frontend_performance_available",
            return_value=True,
        ), patch(
            "app.graph.subgraphs.testing.frontend_build_passed",
            return_value=True,
        ), patch(
            "app.graph.subgraphs.testing.run_frontend_performance_check",
            return_value={
                "test_results": [
                    {
                        "id": "frontend_performance",
                        "passed": True,
                        "blocking": False,
                    }
                ],
                "test_events": ["frontend_performance"],
            },
        ) as performance_runner, patch(
            "app.graph.subgraphs.testing.evaluate_quality_gate",
            return_value={
                "passed": True,
                "needs_revision": False,
                "revision_requests": [],
            },
        ), patch(
            "app.graph.subgraphs.testing.write_test_report_json",
            return_value="/tmp/test_report.json",
        ):
            result = build_testing_subgraph().invoke(
                {
                    "workspace": "/tmp/workspace",
                    "test_results": [],
                    "test_events": [],
                    "code_changes": {},
                    "code_change_sets": [],
                    "unit_test_decision": "run",
                    "frontend_performance_decision": "run",
                    "timeline": [],
                }
            )

        self.assertEqual(result["test_results"][-1]["id"], "frontend_performance")
        self.assertIn("frontend_performance_confirmation:run", result["test_events"])
        self.assertIn("frontend_performance", result["test_events"])
        self.assertTrue(result["quality_gate_passed"])
        self.assertEqual(result["integration_next_action"], "review_phase_confirmation")
        performance_runner.assert_called_once()

    def test_subgraph_skips_performance_when_user_skips(self) -> None:
        """用户选择跳过性能测试时只记录 skipped，不启动前端。"""

        with patch(
            "app.graph.subgraphs.testing.run_integration_checks",
            return_value={
                "test_results": [
                    {"id": "frontend_build", "passed": True, "required": True}
                ],
                "test_events": ["frontend_build"],
            },
        ), patch(
            "app.graph.subgraphs.testing.frontend_performance_available",
            return_value=True,
        ), patch(
            "app.graph.subgraphs.testing.frontend_build_passed",
            return_value=True,
        ), patch(
            "app.graph.subgraphs.testing.run_frontend_performance_check"
        ) as performance_runner, patch(
            "app.graph.subgraphs.testing.evaluate_quality_gate",
            return_value={
                "passed": True,
                "needs_revision": False,
                "revision_requests": [],
            },
        ), patch(
            "app.graph.subgraphs.testing.write_test_report_json",
            return_value="/tmp/test_report.json",
        ):
            result = build_testing_subgraph().invoke(
                {
                    "workspace": "/tmp/workspace",
                    "test_results": [],
                    "test_events": [],
                    "code_changes": {},
                    "code_change_sets": [],
                    "unit_test_decision": "skip",
                    "frontend_performance_decision": "skip",
                    "timeline": [],
                }
            )

        results_by_id = {check["id"]: check for check in result["test_results"]}
        self.assertTrue(results_by_id["frontend_performance"]["skipped"])
        self.assertTrue(results_by_id["frontend_performance"]["passed"])
        self.assertFalse(results_by_id["frontend_performance"]["blocking"])
        self.assertIn("frontend_performance:skipped", result["test_events"])
        performance_runner.assert_not_called()

    def test_skip_unit_tests_does_not_invoke_generation_or_test_runner(self) -> None:
        """用户选择跳过后只记录 skipped 检查，不调用生成和单测命令。"""

        with patch(
            "app.graph.subgraphs.testing._invoke_test_generation_agent"
        ) as invoke_agent, patch(
            "app.graph.subgraphs.unit_testing.evaluate_quality_gate",
            return_value={
                "passed": True,
                "needs_revision": False,
                "revision_requests": [],
            },
        ), patch(
            "app.graph.subgraphs.unit_testing.write_test_report_json",
            return_value="/tmp/test-report.json",
        ):
            result = build_unit_testing_subgraph().invoke(
                {
                    "workspace": "/tmp/workspace",
                    "code_changes": {
                        "files": [
                            {"path": "frontend/src/pages/Orders.tsx", "diff": "+return null"}
                        ]
                    },
                    "unit_test_decision": "skip",
                    "test_results": [],
                    "test_events": [],
                    "code_change_sets": [],
                    "timeline": [],
                }
            )

        results_by_id = {
            check["id"]: check for check in result["test_results"]
        }
        self.assertTrue(results_by_id["frontend_test_generation"]["skipped"])
        self.assertTrue(results_by_id["frontend_unit_tests"]["skipped"])
        self.assertEqual(result["unit_test_next_action"], "test_phase_confirmation")
        invoke_agent.assert_not_called()

    def test_confirmed_integration_resume_reuses_completed_build_checks(self) -> None:
        """测试阶段确认恢复时复用已完成集成构建快照，避免再次安装和构建。"""

        cached = [{"id": "frontend_build", "passed": True, "skipped": False}]
        with patch(
            "app.graph.subgraphs.testing.run_integration_checks"
        ) as run_checks:
            result = build_project_checks(
                {
                    "integration_build_checks_completed": True,
                    "integration_build_results": cached,
                    "test_results": cached,
                },
                config={},
            )

        self.assertEqual(result["test_results"], cached)
        self.assertEqual(result["test_events"], ["integration_build:reused_after_confirmation"])
        run_checks.assert_not_called()

    def test_generation_publishes_running_and_terminal_matrix_states(self) -> None:
        """TestGeneration Agent 调用前后必须分别发布 loading 与完成状态。"""

        progress_events: list[dict] = []
        config = {
            "configurable": {
                "integration_test_progress_reporter": progress_events.append,
            }
        }
        with patch(
            "app.graph.subgraphs.testing._invoke_test_generation_agent",
            return_value={
                "status": "completed",
                "summary": "已生成页面加载测试。",
                "affected_layers": ["frontend"],
                "test_files": ["frontend/tests/page-load.test.tsx"],
                "validation": {"valid": True},
                "code_change_sets": [],
            },
        ):
            generated = generate_unit_tests(
                {
                    "unit_test_generation_context": {
                        "has_targets": True,
                        "affected_layers": ["frontend"],
                    },
                },
                config=config,
            )
        validate_generated_unit_tests(
            {
                "unit_test_generation": generated["unit_test_generation"],
                "unit_test_generation_context": {"affected_layers": ["frontend"]},
                "test_results": [],
            },
            config=config,
        )

        self.assertEqual(
            [event["status"] for event in progress_events],
            ["running", "passed"],
        )
        self.assertEqual(
            progress_events[0]["check"]["id"],
            "frontend_test_generation",
        )
        self.assertIn(
            "正在调用 TestGeneration Agent",
            progress_events[0]["check"]["evidence"],
        )

    def test_final_results_place_generation_and_unit_tests_after_build(self) -> None:
        """完成态矩阵按每层构建、生成检查、单元测试的顺序返回。"""

        with patch(
            "app.graph.subgraphs.testing.run_integration_checks",
            return_value={
                "test_results": [
                    {"id": "frontend_unit_tests", "passed": True},
                    {"id": "backend_unit_tests", "passed": True},
                ],
                "test_events": [],
            },
        ):
            result = actual_project_checks(
                {
                    "test_results": [
                        {"id": "frontend_install", "passed": True},
                        {"id": "frontend_build", "passed": True},
                        {"id": "backend_build", "passed": True},
                        {"id": "frontend_test_generation", "passed": True},
                        {"id": "backend_test_generation", "passed": True},
                    ]
                },
                config={},
            )

        self.assertEqual(
            [check["id"] for check in result["test_results"]],
            [
                "frontend_install",
                "frontend_build",
                "backend_build",
                "frontend_test_generation",
                "backend_test_generation",
                "frontend_unit_tests",
                "backend_unit_tests",
            ],
        )

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

    def test_collected_targets_include_actual_generated_code_diff(self) -> None:
        """构建完成后测试上下文必须保留目标源码的真实 diff，而不只保留路径。"""

        source_diff = (
            "@@ -1 +1,2 @@\n"
            "-export const getLeaveTypes = () => [];\n"
            "+export const getLeaveTypes = () => service.get('/api/leave-types');\n"
        )
        result = collect_unit_test_targets(
            {
                "test_generation_input_code_changes": {
                    "id": "code-change-set:build",
                    "files": [
                        {
                            "id": "file:leave-api",
                            "path": "frontend/src/apis/leaveTypesApi.ts",
                            "changeType": "modified",
                            "additions": 1,
                            "deletions": 1,
                            "diff": source_diff,
                        },
                        {
                            "path": "frontend/src/styles/theme.less",
                            "diff": "+@brand-color: purple;\n",
                        },
                    ],
                },
                "test_generation_input_code_change_sets": [
                    {
                        "id": "code-change-set:task-001",
                        "files": [
                            {
                                "id": "file:leave-api",
                                "path": "frontend/src/apis/leaveTypesApi.ts",
                                "changeType": "modified",
                                "diff": source_diff,
                            }
                        ],
                    }
                ],
            }
        )

        code_diff = result["unit_test_generation_context"]["code_diff"]
        self.assertEqual(code_diff["change_set_ids"], [
            "code-change-set:build",
            "code-change-set:task-001",
        ])
        self.assertEqual(len(code_diff["files"]), 1)
        self.assertEqual(
            code_diff["files"][0]["path"],
            "frontend/src/apis/leaveTypesApi.ts",
        )
        self.assertIn("service.get('/api/leave-types')", code_diff["files"][0]["diff"])

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
                },
                config={},
            )

        validated = validate_generated_unit_tests(
            {
                "unit_test_generation": generated["unit_test_generation"],
                "unit_test_generation_context": {"affected_layers": ["frontend"]},
                "test_results": [],
            },
            config={},
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
                },
                config={},
            )

        validated = validate_generated_unit_tests(
            {
                "unit_test_generation": generated["unit_test_generation"],
                "unit_test_generation_context": {"affected_layers": ["backend"]},
                "test_results": [],
            },
            config={},
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
                },
                config={},
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
            },
            config={},
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
            },
            config={},
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
            tasks[0]["target_files"],
        )
        self.assertIn("backend", tasks[0]["allowed_paths"])

    def test_backend_build_failure_authorizes_backend_directory(self) -> None:
        """构建失败修复必须授权用户 workspace 的 backend 目录并保留 pom.xml 提示。"""

        tasks = _repair_scoped_tasks(
            {
                "build_execution_slice": {"tasks": []},
                "test_results": [
                    {
                        "id": "backend_build",
                        "passed": False,
                        "blocking": True,
                        "evidence": "compilation failure",
                    }
                ],
            }
        )

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["owner"], "backend")
        self.assertIn("backend", tasks[0]["allowed_paths"])
        self.assertIn("backend/pom.xml", tasks[0]["target_files"])

    def test_blocking_build_failure_auto_skips_unit_test_confirmation(self) -> None:
        """存在阻塞性构建失败时，单元测试确认必须自动跳过，不再等待用户。"""

        result = unit_test_confirmation(
            {
                "test_results": [
                    {"id": "backend_build", "passed": False, "blocking": True}
                ]
            }
        )

        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["clarification"], {})
        self.assertEqual(result["unit_test_decision"], "skip")
        self.assertEqual(result["integration_next_action"], "skip_unit_tests")
        self.assertIn(
            "unit_test_confirmation:auto_skipped_blocking_failure",
            result["test_events"],
        )

    def test_frontend_performance_confirmation_auto_skips_on_blocking_failure(
        self,
    ) -> None:
        """任何阻塞性检查失败时，性能确认必须自动跳过并直达质量门禁。"""

        result = frontend_performance_confirmation(
            {
                "frontend_performance_test_enabled": True,
                "test_results": [
                    {"id": "backend_build", "passed": False, "blocking": True},
                    {"id": "frontend_build", "passed": True, "blocking": True},
                ],
            }
        )

        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["clarification"], {})
        self.assertEqual(result["frontend_performance_decision"], "skip")
        self.assertEqual(result["integration_next_action"], "skip_frontend_performance")
        self.assertIn(
            "frontend_performance_confirmation:auto_skipped_blocking_failure",
            result["test_events"],
        )

    def test_subgraph_blocking_failure_reaches_repair_without_user_input(self) -> None:
        """阻塞失败必须跳过两个确认，直接产出 small_task_repair 修复任务。"""

        with patch(
            "app.graph.subgraphs.testing.run_integration_checks",
            return_value={
                "test_results": [
                    {"id": "backend_build", "passed": False, "blocking": True}
                ],
                "test_events": ["backend_build"],
            },
        ), patch(
            "app.graph.subgraphs.testing._invoke_test_generation_agent"
        ) as invoke_agent, patch(
            "app.graph.subgraphs.testing.evaluate_quality_gate",
            return_value={
                "passed": False,
                "needs_revision": True,
                "revision_requests": [
                    {
                        "id": "revision:backend_build",
                        "owner": "backend",
                        "owners": ["backend"],
                        "reason": "后端构建检查",
                        "failed_check": {
                            "id": "backend_build",
                            "name": "后端构建检查",
                            "passed": False,
                            "evidence": "compilation failure",
                        },
                    }
                ],
            },
        ), patch(
            "app.graph.subgraphs.testing.write_test_report_json",
            return_value="/tmp/test-report.json",
        ), patch(
            "app.graph.subgraphs.testing.capture_agent_file_changes",
            side_effect=lambda **kwargs: CapturedWorkspaceChanges(
                value=kwargs["action"](),
                code_change_set=None,
            ),
        ), patch(
            "app.graph.subgraphs.testing.plan_repairs_with_repair_planner_agent",
            return_value={
                "version": "0.1.0",
                "status": "ready",
                "decision": "repair",
                "planId": "plan-1",
                "tasks": [
                    {
                        "id": "repair:plan-1:1:backend_build:backend",
                        "owner": "backend",
                        "allowed_paths": ["backend"],
                        "target_files": ["backend/pom.xml"],
                        "status": "pending",
                    }
                ],
            },
        ), patch(
            "app.graph.subgraphs.testing.write_repair_task_plan_json",
            return_value="/tmp/repair-task-plan.json",
        ):
            result = build_testing_subgraph().invoke(
                {
                    "unit_test_generation_enabled": True,
                    "test_results": [],
                    "test_events": [],
                    "code_changes": {},
                    "code_change_sets": [],
                    "timeline": [],
                }
            )

        self.assertEqual(result["integration_next_action"], "small_task_repair")
        self.assertEqual(len(result["repair_tasks"]), 1)
        self.assertNotIn("requires_user_input", [result.get("status")])
        self.assertIn(
            "frontend_performance_confirmation:auto_skipped_blocking_failure",
            result["test_events"],
        )
        invoke_agent.assert_not_called()

    def test_repair_planning_auto_dispatches_bounded_candidates(self) -> None:
        """Planner 声明终止时，只要存在目录内候选任务就自动派发 SmallTask。"""

        with patch(
            "app.graph.subgraphs.testing.capture_agent_file_changes",
            side_effect=lambda **kwargs: CapturedWorkspaceChanges(
                value=kwargs["action"](),
                code_change_set=None,
            ),
        ), patch(
            "app.graph.subgraphs.testing.plan_repairs_with_repair_planner_agent",
            return_value={
                "version": "0.1.0",
                "status": "terminal_failure",
                "decision": "terminal_failure",
                "planId": "plan-1",
                "requestedPaths": ["backend"],
                "tasks": [],
                "candidateTasks": [
                    {
                        "id": "repair:plan-1:1:backend_build:backend",
                        "owner": "backend",
                        "allowed_paths": ["backend"],
                        "target_files": ["backend/pom.xml"],
                        "status": "pending",
                    }
                ],
                "reason": "unclear",
            },
        ), patch(
            "app.graph.subgraphs.testing.write_repair_task_plan_json",
            return_value="/tmp/repair-task-plan.json",
        ):
            result = repair_planning(
                {
                    "quality_gate_passed": False,
                    "test_report": {"passed": False},
                    "revision_requests": [],
                }
            )

        self.assertEqual(result["integration_next_action"], "small_task_repair")
        self.assertEqual(len(result["repair_tasks"]), 1)
        self.assertEqual(result["repair_task_plan"]["decision"], "repair")
        self.assertTrue(result["repair_task_plan"]["auto_dispatched_candidate"])

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
