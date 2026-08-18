from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.test_generation.agent import _without_disabled_tools
from app.agents.test_generation.generator import (
    _validate_test_files,
    generate_or_update_unit_tests_with_agent,
)
from app.agents.test_generation.scope import is_test_generation_path_allowed


class TestGenerationTests(unittest.TestCase):
    """覆盖测试生成的尽力放行、缓存和越权写入边界。"""

    def test_no_target_does_not_create_agent(self) -> None:
        """没有业务源码目标时不调用模型并返回跳过结果。"""

        with patch("app.agents.create_agent_bundle") as factory:
            result = generate_or_update_unit_tests_with_agent(
                {"unit_test_generation_context": {"source_files": []}},
                "/tmp/workspace",
            )

        self.assertEqual(result["status"], "skipped")
        factory.assert_not_called()

    def test_generated_test_is_captured_and_cached(self) -> None:
        """有效前端测试写入后会捕获变更，并在源码摘要未变时命中缓存。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "frontend" / "src" / "Orders.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const orders = () => 1;\n", encoding="utf-8")

            def invoke(_payload: dict) -> str:
                """模拟 Agent 写入一个主要行为测试。"""

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
            self.assertTrue((root / ".xcodeagent/cache/unit-test-mappings.json").is_file())
            with patch("app.agents.create_agent_bundle", side_effect=AssertionError()):
                cached = generate_or_update_unit_tests_with_agent(state, workspace)
            self.assertEqual(cached["validation"]["mapping_cache"], "hit")

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


if __name__ == "__main__":
    unittest.main()
