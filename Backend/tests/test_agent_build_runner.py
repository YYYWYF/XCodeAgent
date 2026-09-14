from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deepagents.backends import FilesystemBackend

from app.agents.agent_runtime.agent import create_agent_runtime_agent
from app.agents.agent_runtime.generator import generate_agent_runtime_with_deep_agent
from app.graph.subgraphs.build import _runner_for_owner


class AgentBuildRunnerTests(unittest.TestCase):
    """验证七模块 DAG 使用受限的模板感知 Agent CodeRunner。"""

    def test_agent_owner_has_dedicated_runner(self) -> None:
        """构建调度器必须继续为 agent owner 注册独立执行边界。"""

        runner = _runner_for_owner("agent")

        self.assertIsNotNone(runner)
        self.assertEqual(runner[0], "agent.deep_agent")
        self.assertEqual(runner[1].__name__, "generate_agent_runtime_with_deep_agent")

    def test_agent_runtime_runner_returns_strict_module_result(self) -> None:
        """CodeRunner 必须调用受限 Agent，并保留模块实现动作和执行身份。"""

        task = {
            "id": "agent:inventory_assistant::prompt",
            "unit_id": "agent:inventory_assistant",
            "owner": "agent",
            "task_type": "agent.code",
            "source_refs": {"agent_module": "prompt"},
        }
        with (
            patch(
                "app.agents.agent_runtime.generator._invoke_live_agent_runtime",
                return_value=(
                    '{"task_results":[{"task_id":"agent:inventory_assistant::prompt",'
                    '"status":"completed","summary":"已更新模板 Prompt 组合入口",'
                    '"implementation_action":"modify"}]}'
                ),
            ) as invoke,
            patch(
                "app.agents.agent_runtime.generator.Settings.from_env",
                return_value=SimpleNamespace(model_name="test-model"),
            ),
        ):
            results = generate_agent_runtime_with_deep_agent(
                project_plan={"agent_contracts": [{"agentId": "inventory_assistant"}]},
                build_task_plan={},
                tasks=[task],
                workspace="/tmp/generated-agent",
            )

        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(results[0]["implementation_action"], "modify")
        self.assertEqual(results[0]["executed_by"]["model"], "test-model")
        self.assertEqual(invoke.call_args.kwargs["workspace"], "/tmp/generated-agent")

    def test_agent_runtime_agent_does_not_expose_unrestricted_shell(self) -> None:
        """Agent Runtime 只能通过权限中间件控制的文件工具修改 sidecar。"""

        with tempfile.TemporaryDirectory() as workspace:
            user_skills_backend = FilesystemBackend(root_dir=workspace, virtual_mode=True)
            agent_memory_backend = FilesystemBackend(root_dir=workspace, virtual_mode=True)
            with patch(
                "app.agents.agent_runtime.agent.create_deep_agent",
                side_effect=lambda **kwargs: kwargs,
            ):
                agent = create_agent_runtime_agent(
                    "model",
                    workspace,
                    user_skills_backend=user_skills_backend,
                    agent_memory_backend=agent_memory_backend,
                )

        tool_names = [str(getattr(tool, "name", "")) for tool in agent["tools"]]
        self.assertNotIn("execute", tool_names)
        self.assertNotIn("middleware", agent)
        self.assertIn(
            "/.xcodeagent/builtin-skills/agent-runtime-generate/SKILL.md",
            agent["system_prompt"],
        )


if __name__ == "__main__":
    unittest.main()
