"""验证开发单测的输入范围、模型边界和平台复测。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from deepagents.backends import FilesystemBackend
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langsmith import tracing_context

from app.agents.repair_planner.integration import decide_integration_repair
from app.agents.small_task.agent import create_integration_repair_agent
from app.agents.test_generation.generator import _build_prompt
from app.graph.subgraphs.unit_testing import unit_repair_planning
from app.observability import build_instrumentation as observe
from app.observability.model_usage_callback import BuildModelUsageCallback
from app.services.small_task import build_small_task_packet, execute_small_task_batch
from app.services.test_validation import evaluate_quality_gate
from tests.test_integration_repair_progress import ReadLoopModel


def _failed_test(text: str) -> dict:
    """构造真实命令形状的可修复前端失败证据。"""
    return {
        "id": "frontend_unit_tests", "name": "前端单元测试", "passed": False,
        "required": True, "failure_category": "test_failure", "evidence": text,
        "execution": {"cwd": "frontend", "returncode": 1, "stderr_tail": text},
    }


def _repair_state(root: Path, check: dict) -> dict:
    """构造单测报告及现有任务范围。"""
    report = evaluate_quality_gate(test_results=[check], source="unit_test")
    return {
        "workspace": str(root), "active_run_id": "unit-run",
        "unit_test_report": report, "unit_test_results": [check], "test_results": [check],
        "revision_requests": report["revision_requests"], "unit_test_quality_gate_passed": False,
    }


def _write_broken_test(root: Path) -> Path:
    """准备带缺失括号的可复现 Jest 文件。"""
    path = root / "frontend/tests/clock.test.ts"
    path.parent.mkdir(parents=True)
    path.write_text("describe('clock', () => {\n};\n", encoding="utf-8")
    return path


class UnitTestAgentOptimizationTests(unittest.TestCase):
    def test_prompt_projects_only_matching_behavior_and_preserves_capabilities(self) -> None:
        """大任务切片不会截断依赖能力，且保留与真实源码匹配的行为。"""
        source = "frontend/src/clock.ts"
        prompt = _build_prompt({
            "source_files": [source, "backend/src/main/java/Clock.java"],
            "existing_test_files": ["frontend/tests/clock.test.ts"],
            "build_execution_slice": {
                "target_unit_ids": ["page:clock"], "task_ids": ["clock"],
                "tasks": [{"id": "clock", "implementation_document": "unused" * 50_000,
                           "change_scope": [{"path": source, "description": "返回 ISO 时间"},
                                            {"path": "frontend/src/other.ts", "description": "irrelevant"}]}],
            },
            "frontend_test_capabilities": {"available_packages": ["jest"]},
            "code_diff": {"files": [{"path": source, "diff": '+return "time"\\\n'}]},
        })
        context = json.loads(prompt.split("TestGenerationContext:\n", 1)[1])
        self.assertEqual(context["build_execution_slice"]["source_behaviors"], [
            {"path": source, "behavior": "返回 ISO 时间"},
        ])
        self.assertEqual(context["frontend_test_capabilities"]["available_packages"], ["jest"])
        self.assertNotIn("unused", prompt)
        self.assertNotIn("irrelevant", prompt)

    def test_planner_uses_result_tool_and_stops_after_two_rounds(self) -> None:
        """结构化错误只允许再试一次，且请求只绑定结果工具。"""
        with TemporaryDirectory() as workspace:
            root = Path(workspace)
            _write_broken_test(root)
            check = _failed_test("tests/clock.test.ts:2:2 - error TS1005: ')' expected.")
            state = _repair_state(root, check)
            model = ReadLoopModel(responses=[AIMessage(content="no result"), AIMessage(
                content="", tool_calls=[{"name": "UnitTestRepairDecision", "id": "decision", "args": {
                    "decision": "repair", "escalationKind": None, "strategy": "补齐括号。",
                    "reason": "", "failure_handling": "由平台重跑。",
                }}],
            )])
            with patch("app.agents.repair_planner.integration.create_chat_model", return_value=model) as create:
                result = decide_integration_repair(
                    workspace=root, runtime_root=root / ".devagentstudio/runtime/unit-tests",
                    requests=state["revision_requests"], scoped_tasks=[], selected_skills=[],
                    repair_attempt=1, settings=object(), business_stage="unit_test",
                )
            self.assertEqual(result["decision"], "repair")
            self.assertEqual(model.tool_sets, [{"UnitTestRepairDecision"}] * 2)
            self.assertEqual(create.call_args.kwargs["max_retries_override"], 0)

    def test_planner_validates_provider_json_without_repeating_request(self) -> None:
        """供应商返回结构化正文时接受合法决策，省去格式重试。"""
        with TemporaryDirectory() as workspace:
            root = Path(workspace)
            model = ReadLoopModel(responses=[AIMessage(
                content='{"decision":"repair","strategy":"修复测试源码"}',
            )])
            with patch("app.agents.repair_planner.integration.create_chat_model", return_value=model):
                result = decide_integration_repair(
                    workspace=root, runtime_root=root / ".devagentstudio/runtime/unit-tests",
                    requests=[], scoped_tasks=[], selected_skills=[],
                    repair_attempt=1, settings=object(), business_stage="unit_test",
                )
            self.assertEqual(result["decision"], "repair")
            self.assertEqual(model.tool_sets, [{"UnitTestRepairDecision"}])

    def test_planner_receives_evidence_of_a_declared_missing_package(self) -> None:
        """只提供命中本地清单的缺失包名，避免把可安装依赖误判成环境阻塞。"""
        with TemporaryDirectory() as workspace:
            root = Path(workspace)
            manifest = root / "frontend/package.json"
            manifest.parent.mkdir()
            manifest.write_text('{"devDependencies":{"fixture-package":"file:vendor/fixture-package"}}')
            state = _repair_state(root, _failed_test("Error: Cannot find module 'fixture-package'"))
            with patch("app.agents.repair_planner.integration.create_chat_model"), patch(
                "app.agents.repair_planner.integration.invoke_repair_result",
                return_value=SimpleNamespace(
                    decision="repair", model_dump=lambda: {"decision": "repair"},
                ),
            ) as invoke:
                decide_integration_repair(
                    workspace=root, runtime_root=root / ".devagentstudio/runtime/unit-tests",
                    requests=state["revision_requests"], scoped_tasks=[], selected_skills=[],
                    repair_attempt=1, settings=object(), business_stage="unit_test",
                )
            evidence = json.loads(invoke.call_args.kwargs["evidence"])
            self.assertEqual(evidence["dependencyRecovery"], {
                "manifest": "frontend/package.json", "missingDeclaredPackage": "fixture-package",
            })
            self.assertNotIn("file:vendor", invoke.call_args.kwargs["evidence"])

    def test_model_error_preserves_reason_and_does_not_create_repair_plan(self) -> None:
        """模型服务拒绝沿原错误处理退出，单测规划不得派发或扣费。"""
        with TemporaryDirectory() as workspace:
            root = Path(workspace)
            state = _repair_state(root, _failed_test("tests/clock.test.ts:2:2 - error TS1005"))
            error = RuntimeError("HTTP 402 Insufficient Balance")
            with patch(
                "app.agents.repair_planner.integration.invoke_repair_result", side_effect=error,
            ) as invoke, patch("app.agents.repair_planner.integration.create_chat_model"), patch(
                "app.graph.subgraphs.testing.write_repair_task_plan_json",
            ) as write, self.assertRaises(RuntimeError) as raised:
                unit_repair_planning(state)
            self.assertIs(raised.exception, error)
            invoke.assert_called_once()
            write.assert_not_called()
            self.assertNotIn("unit_test_repair_attempts", state)

    def test_code_repair_production_agent_edits_without_test_command(self) -> None:
        """真实工具循环只修代码，由外层单测决定真实通过状态。"""
        with TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = _write_broken_test(root)
            manifest = root / "frontend/package.json"
            manifest.write_text('{"scripts":{"test":"jest"}}', encoding="utf-8")
            check = _failed_test("\x1b[96mtests/clock.test.ts\x1b[0m:2:2 - error TS1005: ')' expected.")
            state = _repair_state(root, check)
            with patch(
                "app.agents.repair_planner.planner.decide_integration_repair",
                return_value={"decision": "repair", "strategy": "补齐括号。"},
            ):
                plan = unit_repair_planning(state)
            task = plan["repair_tasks"][0]
            state["repair_task_plan"] = plan["unit_test_repair_task_plan"]
            self.assertEqual(build_small_task_packet(task, state, source="unit_test.small_task")[
                "candidateFiles"
            ], ["frontend/tests/clock.test.ts"])
            model = ReadLoopModel(responses=[
                AIMessage(content="", tool_calls=[
                    {"name": "edit_file", "id": "edit-manifest", "args": {
                        "file_path": "/frontend/package.json", "old_string": "jest", "new_string": "skip",
                    }},
                    {"name": "edit_file", "id": "edit-test", "args": {
                        "file_path": "/frontend/tests/clock.test.ts", "old_string": "};", "new_string": "});",
                    }},
                ]),
                AIMessage(content='{"status":"completed","summary":"补齐括号","verification":[]}'),
            ])
            backend = FilesystemBackend(root_dir=workspace, virtual_mode=True)
            agent = create_integration_repair_agent(
                model, workspace, user_skills_backend=backend, agent_memory_backend=backend,
                required_user_skills_prompt="", unit_test=True,
            )
            with tracing_context(enabled=False), patch(
                "app.agents.create_agent_bundle",
                return_value=SimpleNamespace(small_task=None, unit_test_code_repair=agent),
            ):
                result = execute_small_task_batch(state=state, tasks=[task], source="unit_test.small_task")
            self.assertEqual(result["results"][0]["status"], "completed")
            self.assertEqual(result["results"][0]["changedFiles"], ["frontend/tests/clock.test.ts"])
            self.assertTrue(model.tool_sets)
            for tools in model.tool_sets:
                self.assertFalse(tools & {"execute", "delete_file", "task"})
            self.assertTrue(source.read_text(encoding="utf-8").endswith("});\n"))
            self.assertEqual(manifest.read_text(encoding="utf-8"), '{"scripts":{"test":"jest"}}')
            self.assertFalse(state["unit_test_report"]["passed"])

    def test_dependency_or_out_of_scope_error_keeps_command_repair(self) -> None:
        """带位置的缺依赖与无授权文件也不能选用纯代码修复。"""
        with TemporaryDirectory() as workspace:
            root = Path(workspace)
            _write_broken_test(root)
            task = {"id": "fix", "owner": "frontend", "allowed_paths": ["frontend"],
                    "source_ref": {"failed_check_id": "frontend_unit_tests"}}
            for text, paths in [
                ("tests/clock.test.ts:2:2 - error TS2307: Cannot find module 'jest'", ["frontend"]),
                ("tests/clock.test.ts:2:2 - error TS1005", ["frontend/src"]),
            ]:
                task["allowed_paths"] = paths
                state = _repair_state(root, _failed_test(text))
                task["failure_evidence"] = state["revision_requests"][0]
                packet = build_small_task_packet(task, state, source="unit_test.small_task")
                self.assertNotIn("verificationMode", packet)
                self.assertIn("testReport", packet["confirmedContext"])

    def test_junit_stack_location_uses_only_unique_authorized_test(self) -> None:
        """JUnit 简写路径只匹配唯一任务文件，同名不明确时保留原修复路径。"""
        with TemporaryDirectory() as workspace:
            root = Path(workspace)
            test_paths = [f"backend/src/test/java/{package}/ClockTest.java" for package in ("a", "b")]
            for path in test_paths:
                test_file = root / path
                test_file.parent.mkdir(parents=True)
                test_file.touch()
            check = {
                "id": "backend_unit_tests", "name": "后端单元测试", "passed": False, "required": True,
                "failure_category": "test_failure", "evidence": "org.opentest4j.AssertionFailedError",
                "execution": {"cwd": "backend", "returncode": 1, "stderr_tail":
                              "org.opentest4j.AssertionFailedError\n at a.ClockTest.clamp(ClockTest.java:15)"},
            }
            state = _repair_state(root, check)
            task = {"id": "fix", "owner": "backend", "allowed_paths": ["backend"],
                    "source_ref": {"failed_check_id": "backend_unit_tests"},
                    "failure_evidence": state["revision_requests"][0]}
            source = "unit_test.small_task"
            self.assertEqual(build_small_task_packet({**task, "target_files": test_paths[:1]}, state, source=source)[
                "candidateFiles"
            ], test_paths[:1])
            self.assertNotIn("verificationMode", build_small_task_packet(
                {**task, "target_files": test_paths}, state, source=source,
            ))

    def test_trace_records_first_prompt_rounds_and_missing_usage(self) -> None:
        """单测观测记录真实字节、失败终态和模型轮次，日志不保存正文。"""
        handler = BuildModelUsageCallback("test-provider", "test-model")
        messages = [[SystemMessage(content="单测"), HumanMessage(content="private fixture")]]
        with patch.object(observe.telemetry, "enabled", return_value=True), patch.object(
            observe.telemetry, "publish",
        ) as publish, observe.integration_repair_trace(
            "unit_test", state={"active_run_id": "unit-run", "active_thread_id": "unit-thread"},
            business_stage="unit_test", repair_round=1,
        ):
            with observe.integration_repair_trace("generate_unit_tests", business_stage="unit_test"):
                for _ in range(2):
                    call_id = uuid4()
                    handler.on_chat_model_start({}, messages, run_id=call_id)
                    handler.on_llm_error(RuntimeError("failure"), run_id=call_id)
                with observe.unit_test_child_trace("unit_test.mapping_cache_hit"):
                    pass
            with observe.unit_test_child_trace("frontend_unit_tests", kind="tool"):
                pass
        rows = [call.args[0] for call in publish.call_args_list]
        model = [row for row in rows if row["data"]["span_kind"] == "model"]
        self.assertEqual([row["context"]["extra"]["model_round"] for row in model], [1, 2])
        self.assertEqual(model[0]["data"]["model"]["input_tokens"], None)
        self.assertEqual(model[0]["data"]["status"], "failed")
        prompts = [row["data"]["metrics"] for row in rows if row["data"]["run_name"] == "initial_prompt"]
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["initial_prompt_bytes"], len("单测\nprivate fixture".encode()))
        self.assertNotIn("private fixture", json.dumps(rows))
