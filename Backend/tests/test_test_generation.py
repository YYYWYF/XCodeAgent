from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.agents.test_generation.agent import _without_disabled_tools
from app.agents.test_generation.generator import (
    _build_prompt,
    _validate_test_files,
    generate_or_update_unit_tests_with_agent,
)
from app.agents.test_generation.scope import (
    ScopedTestGenerationBackend,
    is_test_generation_path_allowed,
)
from deepagents.backends.protocol import GlobResult, GrepResult, LsResult, ReadResult


class TestGenerationTests(unittest.TestCase):
    """覆盖测试生成的尽力放行、缓存和越权写入边界。"""

    def test_prompt_excludes_backend_mapping_layer_tests(self) -> None:
        """测试生成提示必须明确排除 MapStruct 映射层。"""

        prompt = _build_prompt(
            {
                "source_files": [
                    "backend/src/main/java/demo/LeaveRequestService.java"
                ]
            }
        )

        self.assertIn("*Assembler", prompt)
        self.assertIn("*Converter", prompt)
        self.assertIn("*Mapper", prompt)
        self.assertIn("Prefer Service tests", prompt)

    def test_prompt_uses_existing_plain_backend_test_capabilities(self) -> None:
        """Controller 单测仅能复用已有 JUnit/Mockito 能力且不加载 Spring 上下文。"""

        prompt = _build_prompt(
            {
                "source_files": [
                    "backend/src/main/java/demo/OrdersController.java"
                ]
            }
        )

        self.assertIn("JUnit 5 and Mockito", prompt)
        self.assertIn("only test libraries already available", prompt)
        self.assertIn("Never add or request a build dependency", prompt)
        self.assertIn("MockMvcBuilders.standaloneSetup", prompt)
        self.assertIn("Never use or import @WebMvcTest", prompt)
        self.assertIn("@MockBean", prompt)

    def test_prompt_uses_only_current_bounded_artifacts(self) -> None:
        """提示只携带 Build Diff、当前任务范围和 TechnicalPlan JSON。"""

        prompt = _build_prompt(
            {
                "source_files": ["frontend/src/pages/Orders/index.tsx"],
                "code_diff": {
                    "files": [
                        {
                            "path": "frontend/src/pages/Orders/index.tsx",
                            "diff": "+export const Orders = () => null;",
                        }
                    ]
                },
                "existing_test_files": ["frontend/tests/page-orders.test.tsx"],
                "build_task_plan_path": ".xcodeagent/plans/build-task-plan.json",
                "technical_plan_json_path": ".xcodeagent/plans/technical-plan.json",
                "build_execution_scope": {"type": "page", "targetId": "orders"},
                "build_execution_slice": {"task_ids": ["task:orders"]},
                "project_plan_json_path": "stale-project-plan.json",
                "requirement_spec_json_path": "stale-requirement-spec.json",
                "detail_plans": [{"stale": True}],
                "code_graph_index": {"stale": True},
            }
        )

        self.assertIn("frozen Build code diff", prompt)
        self.assertIn(".xcodeagent/plans/build-task-plan.json", prompt)
        self.assertIn(".xcodeagent/plans/technical-plan.json", prompt)
        self.assertIn("+export const Orders", prompt)
        for removed_key in (
            "project_plan_path",
            "project_plan_json_path",
            "requirement_spec_path",
            "requirement_spec_json_path",
            "code_graph_index",
            "page_selection",
            "detail_selection",
            "detail_plans",
        ):
            self.assertNotIn(removed_key, prompt)

    def test_no_target_does_not_create_agent(self) -> None:
        """没有业务源码目标时不调用模型并返回跳过结果。"""

        with patch("app.agents.create_agent_bundle") as factory:
            result = generate_or_update_unit_tests_with_agent(
                {"unit_test_generation_context": {"source_files": []}},
                "/tmp/workspace",
            )

        self.assertEqual(result["status"], "skipped")
        factory.assert_not_called()

    def test_scoped_backend_forwards_skill_and_source_read_operations(self) -> None:
        """测试生成 Backend 必须把 SkillsMiddleware 和源码读取操作转给底层。"""

        delegate = Mock()
        delegate.ls.return_value = LsResult(
            entries=[{"path": "/.xcodeagent/user-skills", "is_dir": True}]
        )
        delegate.read.return_value = ReadResult(
            file_data={
                "content": "/frontend/src/App.tsx:0:2000",
                "encoding": "utf-8",
            }
        )
        delegate.grep.return_value = GrepResult(
            matches=[{"path": "/", "line": "service.get"}]
        )
        delegate.glob.return_value = GlobResult(
            matches=[{"path": "/", "pattern": "/frontend/src/**/*.tsx"}]
        )
        backend = ScopedTestGenerationBackend(delegate)

        self.assertEqual(backend.ls("/.xcodeagent/user-skills").entries[0]["is_dir"], True)
        self.assertEqual(
            backend.read("/frontend/src/App.tsx").file_data["content"],
            "/frontend/src/App.tsx:0:2000",
        )
        self.assertEqual(backend.grep("service.get").matches[0]["line"], "service.get")
        self.assertEqual(backend.glob("/frontend/src/**/*.tsx").matches[0]["pattern"], "/frontend/src/**/*.tsx")

    def test_generated_test_is_captured_and_cached(self) -> None:
        """有效前端测试写入后会捕获变更，并在源码摘要未变时命中缓存。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            received_prompts: list[str] = []
            source = root / "frontend" / "src" / "Orders.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const orders = () => 1;\n", encoding="utf-8")
            (root / "frontend/package.json").write_text(
                json.dumps(
                    {
                        "dependencies": {"react": "18.2.0"},
                        "devDependencies": {
                            "@testing-library/react": "16.2.0"
                        },
                    }
                ),
                encoding="utf-8",
            )

            def invoke(_payload: dict) -> str:
                """模拟 Agent 写入一个主要行为测试。"""

                received_prompts.append(_payload["messages"][0]["content"])
                test_path = root / "frontend" / "tests" / "page-orders.test.ts"
                test_path.parent.mkdir(parents=True, exist_ok=True)
                test_path.write_text(
                    "test('orders', () => expect(1).toBe(1));\n",
                    encoding="utf-8",
                )
                return json.dumps(
                    {
                        "status": "completed",
                        "test_files": ["frontend/tests/page-orders.test.ts"],
                    }
                )

            state = {
                "workspace": workspace,
                "unit_test_generation_context": {
                    "source_files": ["frontend/src/Orders.ts"],
                    "affected_layers": ["frontend"],
                },
            }
            fake_bundle = SimpleNamespace(
                test_generation=SimpleNamespace(invoke=invoke)
            )
            with patch("app.agents.create_agent_bundle", return_value=fake_bundle):
                generated = generate_or_update_unit_tests_with_agent(state, workspace)

            self.assertEqual(generated["status"], "completed")
            self.assertEqual(generated["test_files"], ["frontend/tests/page-orders.test.ts"])
            self.assertIn("@testing-library/react", received_prompts[0])
            self.assertNotIn("@testing-library/user-event", received_prompts[0])
            self.assertTrue((root / ".xcodeagent/cache/unit-test-mappings.json").is_file())
            with patch("app.agents.create_agent_bundle", side_effect=AssertionError()):
                cached = generate_or_update_unit_tests_with_agent(state, workspace)
            self.assertEqual(cached["validation"]["mapping_cache"], "hit")

    def test_agent_exception_is_visible_in_generation_summary(self) -> None:
        """Agent 启动异常必须进入生成摘要，不能伪装成普通无输出。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "frontend/src/Orders.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const orders = 1;\n", encoding="utf-8")

            def invoke(_payload: dict) -> str:
                """模拟测试 Agent 在启动阶段抛出异常。"""

                raise NotImplementedError()

            fake_bundle = SimpleNamespace(
                test_generation=SimpleNamespace(invoke=invoke)
            )
            with patch("app.agents.create_agent_bundle", return_value=fake_bundle):
                result = generate_or_update_unit_tests_with_agent(
                    {
                        "workspace": workspace,
                        "unit_test_generation_context": {
                            "source_files": ["frontend/src/Orders.ts"],
                            "affected_layers": ["frontend"],
                        },
                    },
                    workspace,
                )

        self.assertEqual(result["status"], "skipped")
        self.assertIn("NotImplementedError", result["summary"])
        self.assertTrue(any("Agent 异常" in warning for warning in result["warnings"]))

    def test_production_write_is_a_security_failure(self) -> None:
        """生成 Agent 实际修改生产源码时不能按零测试放行。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "backend" / "src" / "main" / "java" / "demo" / "Orders.java"
            source.parent.mkdir(parents=True)
            source.write_text("package demo;\nclass Orders {}\n", encoding="utf-8")

            def invoke(_payload: dict) -> str:
                """模拟越权写入生产源码。"""

                (root / "backend" / "src" / "main" / "java" / "demo" / "Hack.java").write_text(
                    "package demo;\nclass Hack {}\n", encoding="utf-8"
                )
                return "{}"

            fake_bundle = SimpleNamespace(
                test_generation=SimpleNamespace(invoke=invoke)
            )
            with patch("app.agents.create_agent_bundle", return_value=fake_bundle):
                result = generate_or_update_unit_tests_with_agent(
                    {
                        "workspace": workspace,
                        "unit_test_generation_context": {
                            "source_files": [
                                "backend/src/main/java/demo/Orders.java"
                            ],
                            "affected_layers": ["backend"],
                        },
                    },
                    workspace,
                )

        self.assertEqual(result["status"], "failed")
        self.assertIn("backend/src/main/java/demo/Hack.java", result["validation"]["unauthorized_paths"])

    def test_sensitive_config_write_is_a_security_failure(self) -> None:
        """通用变更快照忽略的敏感配置写入也必须被测试生成阶段拦截。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "frontend/src/Orders.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const orders = 1;\n", encoding="utf-8")

            def invoke(_payload: dict) -> str:
                """模拟 Agent 修改敏感配置。"""

                (root / ".env").write_text("SECRET=bad\n", encoding="utf-8")
                return "{}"

            fake_bundle = SimpleNamespace(
                test_generation=SimpleNamespace(invoke=invoke)
            )
            with patch("app.agents.create_agent_bundle", return_value=fake_bundle):
                result = generate_or_update_unit_tests_with_agent(
                    {
                        "workspace": workspace,
                        "unit_test_generation_context": {
                            "source_files": ["frontend/src/Orders.ts"],
                            "affected_layers": ["frontend"],
                        },
                    },
                    workspace,
                )

        self.assertEqual(result["status"], "failed")
        self.assertIn(".env", result["validation"]["unauthorized_paths"])

    def test_langgraph_checkpoint_writes_are_not_unauthorized_changes(self) -> None:
        """嵌套 Agent 的 checkpoint 写入不得污染测试文件变更验收。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            received_prompts: list[str] = []
            source = root / "frontend/src/apis/leaveTypesApi.ts"
            source.parent.mkdir(parents=True)
            source.write_text(
                "export const getLeaveTypes = () => service.get('/api/leave-types');\n",
                encoding="utf-8",
            )

            def invoke(_payload: dict) -> str:
                """模拟 Agent 同时写入运行时 checkpoint 和合法测试文件。"""

                received_prompts.append(_payload["messages"][0]["content"])
                checkpoint_dir = root / ".xcodeagent/checkpoints"
                checkpoint_dir.mkdir(parents=True)
                for name in (
                    "checkpoints.sqlite",
                    "checkpoints.sqlite-shm",
                    "checkpoints.sqlite-wal",
                ):
                    (checkpoint_dir / name).write_bytes(name.encode("utf-8"))
                test_path = root / "frontend/tests/api-leave-types.test.ts"
                test_path.parent.mkdir(parents=True)
                test_path.write_text(
                    "test('loads leave types', () => expect(true).toBe(true));\n",
                    encoding="utf-8",
                )
                return json.dumps(
                    {
                        "status": "completed",
                        "test_files": ["frontend/tests/api-leave-types.test.ts"],
                    }
                )

            fake_bundle = SimpleNamespace(
                test_generation=SimpleNamespace(invoke=invoke)
            )
            with patch("app.agents.create_agent_bundle", return_value=fake_bundle):
                result = generate_or_update_unit_tests_with_agent(
                    {
                        "workspace": workspace,
                        "unit_test_generation_context": {
                            "source_files": ["frontend/src/apis/leaveTypesApi.ts"],
                            "affected_layers": ["frontend"],
                            "code_diff": {
                                "files": [
                                    {
                                        "path": "frontend/src/apis/leaveTypesApi.ts",
                                        "diff": "+service.get('/api/leave-types')",
                                    }
                                ]
                            },
                        },
                    },
                    workspace,
                )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["validation"].get("unauthorized_paths"), None)
        self.assertEqual(result["test_files"], ["frontend/tests/api-leave-types.test.ts"])
        self.assertIn("service.get('/api/leave-types')", received_prompts[0])

    def test_build_artifacts_are_not_generation_security_failures(self) -> None:
        """测试生成期间刷新的 Maven target 产物不能掩盖已生成的后端测试。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "backend/src/main/java/demo/OrdersService.java"
            source.parent.mkdir(parents=True)
            source.write_text(
                "package demo;\npublic class OrdersService {}\n",
                encoding="utf-8",
            )

            def invoke(_payload: dict) -> str:
                """模拟 Maven/MapStruct 与测试 Agent 同时产生文件。"""

                test_path = root / "backend/src/test/java/demo/OrdersServiceTest.java"
                test_path.parent.mkdir(parents=True)
                test_path.write_text(
                    "package demo;\n"
                    "import org.junit.jupiter.api.Test;\n"
                    "class OrdersServiceTest { @Test void mainPath() {} }\n",
                    encoding="utf-8",
                )
                target_classes = root / "backend/target/classes/demo"
                target_classes.mkdir(parents=True)
                (target_classes / "OrdersService.class").write_bytes(b"class")
                generated_sources = root / "backend/target/generated-sources/annotations/demo"
                generated_sources.mkdir(parents=True)
                (generated_sources / "OrdersServiceImpl.java").write_text(
                    "package demo;\nclass OrdersServiceImpl {}\n",
                    encoding="utf-8",
                )
                return json.dumps(
                    {
                        "status": "completed",
                        "test_files": [
                            "backend/src/test/java/demo/OrdersServiceTest.java"
                        ],
                    }
                )

            fake_bundle = SimpleNamespace(
                test_generation=SimpleNamespace(invoke=invoke)
            )
            with patch("app.agents.create_agent_bundle", return_value=fake_bundle):
                result = generate_or_update_unit_tests_with_agent(
                    {
                        "workspace": workspace,
                        "unit_test_generation_context": {
                            "source_files": [
                                "backend/src/main/java/demo/OrdersService.java"
                            ],
                            "affected_layers": ["backend"],
                        },
                    },
                    workspace,
                )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["test_files"],
            ["backend/src/test/java/demo/OrdersServiceTest.java"],
        )
        self.assertNotIn("unauthorized_paths", result["validation"])
        self.assertEqual(
            [item["path"] for item in result["code_change_sets"][0]["files"]],
            ["backend/src/test/java/demo/OrdersServiceTest.java"],
        )

    def test_formal_internal_artifact_write_remains_a_security_failure(self) -> None:
        """忽略 checkpoint 后仍必须阻断对正式 `.xcodeagent` 计划工件的修改。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "frontend/src/apis/leaveTypesApi.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const getLeaveTypes = () => [];\n", encoding="utf-8")
            plan = root / ".xcodeagent/plans/technical-plan.json"
            plan.parent.mkdir(parents=True)
            plan.write_text('{"status":"confirmed"}\n', encoding="utf-8")

            def invoke(_payload: dict) -> str:
                """模拟 Agent 越权修改正式技术计划。"""

                plan.write_text('{"status":"changed"}\n', encoding="utf-8")
                return "{}"

            fake_bundle = SimpleNamespace(
                test_generation=SimpleNamespace(invoke=invoke)
            )
            with patch("app.agents.create_agent_bundle", return_value=fake_bundle):
                result = generate_or_update_unit_tests_with_agent(
                    {
                        "workspace": workspace,
                        "unit_test_generation_context": {
                            "source_files": ["frontend/src/apis/leaveTypesApi.ts"],
                            "affected_layers": ["frontend"],
                        },
                    },
                    workspace,
                )

        self.assertEqual(result["status"], "failed")
        self.assertIn(
            ".xcodeagent/plans/technical-plan.json",
            result["validation"]["unauthorized_paths"],
        )

    def test_frontend_only_target_rejects_backend_test_write(self) -> None:
        """前端-only 变更不能借生成阶段写入后端测试文件。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "frontend/src/Orders.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const orders = 1;\n", encoding="utf-8")

            def invoke(_payload: dict) -> str:
                """模拟 Agent 误写后端测试。"""

                test_path = root / "backend/src/test/java/demo/OrdersTest.java"
                test_path.parent.mkdir(parents=True)
                test_path.write_text(
                    "package demo;\nimport org.junit.jupiter.api.Test;\nclass OrdersTest { @Test void mainPath() {} }\n",
                    encoding="utf-8",
                )
                return "{}"

            fake_bundle = SimpleNamespace(
                test_generation=SimpleNamespace(invoke=invoke)
            )
            with patch("app.agents.create_agent_bundle", return_value=fake_bundle):
                result = generate_or_update_unit_tests_with_agent(
                    {
                        "workspace": workspace,
                        "unit_test_generation_context": {
                            "source_files": ["frontend/src/Orders.ts"],
                            "affected_layers": ["frontend"],
                        },
                    },
                    workspace,
                )

        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "backend/src/test/java/demo/OrdersTest.java",
            result["validation"]["unaffected_layer_paths"],
        )

    def test_path_scope_and_tool_filter_are_restrictive(self) -> None:
        """测试 Agent 只保留测试文件写入路径并移除命令工具。"""

        self.assertTrue(is_test_generation_path_allowed("/frontend/tests/page-orders.test.ts"))
        self.assertFalse(
            is_test_generation_path_allowed("/frontend/tests/orders/page-orders.test.ts")
        )
        self.assertFalse(
            is_test_generation_path_allowed("/backend/src/test/java/../../main/HackTest.java")
        )
        self.assertFalse(is_test_generation_path_allowed("/frontend/src/pages/Orders.tsx"))
        tools = [SimpleNamespace(name="execute"), SimpleNamespace(name="write_file")]
        self.assertEqual([tool.name for tool in _without_disabled_tools(tools)], ["write_file"])

    def test_existing_backend_test_is_validated_without_being_changed(self) -> None:
        """已有 Java 测试即使本轮未写入也必须保持可执行对应关系。"""

        with tempfile.TemporaryDirectory() as workspace:
            test_path = (
                Path(workspace)
                / "backend/src/test/java/demo/OrdersServiceTest.java"
            )
            test_path.parent.mkdir(parents=True)
            test_path.write_text(
                "package demo;\nimport org.junit.jupiter.api.Test;\nclass OrdersServiceTest { @Test void mainPath() {} }\n",
                encoding="utf-8",
            )
            validation = _validate_test_files(
                workspace,
                ["backend/src/test/java/demo/OrdersServiceTest.java"],
                [],
            )

        self.assertTrue(validation["valid"])
        self.assertEqual(validation["invalid_paths"], [])
        self.assertEqual(validation["invalid_contents"], [])

    def test_backend_spring_context_annotations_are_left_to_compilation(self) -> None:
        """Spring 测试注解只受提示词约束，不在确定性内容校验中失败。"""

        with tempfile.TemporaryDirectory() as workspace:
            test_path = (
                Path(workspace)
                / "backend/src/test/java/demo/OrdersControllerTest.java"
            )
            test_path.parent.mkdir(parents=True)
            test_path.write_text(
                "package demo;\n"
                "import org.junit.jupiter.api.Test;\n"
                "import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;\n"
                "import org.springframework.boot.test.mock.bean.MockBean;\n"
                "@WebMvcTest(OrdersController.class)\n"
                "class OrdersControllerTest {\n"
                "  @MockBean Object service;\n"
                "  @Test void mainPath() {}\n"
                "}\n",
                encoding="utf-8",
            )
            validation = _validate_test_files(
                workspace,
                ["backend/src/test/java/demo/OrdersControllerTest.java"],
                ["backend/src/test/java/demo/OrdersControllerTest.java"],
            )

        self.assertTrue(validation["valid"])
        self.assertEqual(validation["invalid_contents"], [])


if __name__ == "__main__":
    unittest.main()
