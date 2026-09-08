from __future__ import annotations

import unittest
import tempfile
from unittest.mock import patch

from deepagents.backends import FilesystemBackend

from app.agents.agent_runtime.agent import create_agent_runtime_agent
from app.agents.agent_runtime.generator import _agent_runtime_generation_prompt
from app.graph.subgraphs.build import _runner_for_owner


class AgentBuildRunnerTests(unittest.TestCase):
    """验证 Agent owner 使用独立 Python CodeRunner 与正式技术契约。"""

    def test_agent_owner_has_dedicated_runner(self) -> None:
        """构建调度器必须为 agent owner 注册独立 Deep Agent 执行器。"""

        runner = _runner_for_owner("agent")

        self.assertIsNotNone(runner)
        self.assertEqual(runner[0], "agent.deep_agent")
        self.assertEqual(runner[1].__name__, "generate_agent_runtime_with_deep_agent")

    def test_agent_runner_prompt_uses_contract_and_path_boundary(self) -> None:
        """Agent 执行提示必须读取 Agent Contract 且只能写 agent-runtime。"""

        prompt = _agent_runtime_generation_prompt(
            project_plan={
                "api_contracts": [
                    {
                        "id": "inventory_api",
                        "schemas": {
                            "InventoryStatus": {
                                "type": "object",
                                "properties": {"sku": {"type": "string"}},
                            }
                        },
                        "endpoints": [
                            {
                                "id": "inventory_api.get_status",
                                "method": "GET",
                                "path": "/api/inventory/{sku}",
                                "response_schema_ref": "InventoryStatus",
                            }
                        ],
                    },
                    {
                        "id": "unrelated_api",
                        "schemas": {},
                        "endpoints": [],
                    },
                ],
                "agent_contracts": [
                    {
                        "agentId": "inventory_assistant",
                        "runtime": {
                            "language": "Python",
                            "pythonVersion": "3.12",
                            "framework": "DeepAgents",
                        },
                        "invocation": {
                            "transport": "ag-ui-sse",
                            "gatewayEndpointId": "inventory_api.agent_message",
                            "internalPath": "/internal/agents/inventory_assistant/run",
                        },
                        "agentSettings": {
                            "prompt": {
                                "systemPrompt": "解释当前库存状态。",
                            },
                            "model": {
                                "selection": "project_default",
                            },
                            "memory": {
                                "shortTerm": {
                                    "enabled": True,
                                    "store": "sqlite",
                                }
                            },
                            "tools": {
                                "enabled": True,
                                "bindings": [
                                    {
                                        "toolId": "get_inventory_status",
                                        "endpoint": {
                                            "apiContractId": "inventory_api",
                                            "endpointId": "inventory_api.get_status",
                                            "method": "GET",
                                            "path": "/api/inventory/{sku}",
                                            "responseSchemaRef": "inventory_api.InventoryStatus",
                                        },
                                    }
                                ],
                            },
                        },
                        "artifacts": {
                            "agentPath": "agent-runtime/src/app/agent/inventory_assistant.py",
                            "toolAdapterPath": "agent-runtime/src/app/tools/inventory_assistant_tools.py",
                            "testPath": "agent-runtime/tests/test_inventory_assistant.py",
                        },
                    }
                ]
            },
            tasks=[
                {
                    "id": "agent:inventory_assistant::implementation",
                    "unit_id": "agent:inventory_assistant",
                    "allowed_paths": [
                        "agent-runtime/src/app/agent/inventory_assistant.py",
                        "agent-runtime/src/app/tools/inventory_assistant_tools.py",
                        "agent-runtime/tests/test_inventory_assistant.py",
                    ],
                    "source_refs": {
                        "agent_contracts": [
                            {"agentId": "inventory_assistant"}
                        ]
                    },
                }
            ],
        )

        self.assertIn("Python 3.12", prompt)
        self.assertIn("DeepAgents", prompt)
        self.assertIn("AG-UI SSE", prompt)
        self.assertIn("inventory_assistant", prompt)
        self.assertIn("agent-runtime/", prompt)
        self.assertIn("must not modify frontend or Java backend", prompt)
        self.assertIn("agent-runtime-generate/SKILL.md", prompt)
        self.assertIn("create_agent(*, model, runtime_context, checkpointer)", prompt)
        self.assertIn("never initialize a second model", prompt)
        self.assertIn("Java gateway transport as deferred", prompt)
        self.assertIn("Never invent Java source", prompt)
        self.assertIn('"id": "inventory_api"', prompt)
        self.assertIn('"InventoryStatus"', prompt)
        self.assertNotIn('"id": "unrelated_api"', prompt)

    def test_agent_runtime_agent_does_not_expose_unrestricted_shell(self) -> None:
        """Agent Runtime 只能通过受权限中间件控制的文件工具修改 sidecar。"""

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
