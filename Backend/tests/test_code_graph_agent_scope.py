from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch

from deepagents.backends import FilesystemBackend

from app.agents.data_source.agent import create_data_source_agent
from app.agents.data_source.generator import _data_source_generation_prompt
from app.agents.database.agent import create_database_agent
from app.agents.frontend.agent import create_frontend_agent
from app.agents.frontend.generator import _frontend_generation_prompt
from app.agents.repair_planner.agent import create_repair_planner_agent
from app.tools.code_graph_context import create_code_graph_context_tool


class CodeGraphAgentScopeTests(unittest.TestCase):
    """验证代码图只由仍需要符号导航的前端执行 Agent 消费。"""

    def test_only_frontend_agent_registers_code_graph(self) -> None:
        """Frontend 保留代码图工具，DataSource 与只读规划 Agent 均不注册。"""

        with tempfile.TemporaryDirectory() as workspace:
            user_skills_backend = FilesystemBackend(root_dir=workspace, virtual_mode=True)
            agent_memory_backend = FilesystemBackend(root_dir=workspace, virtual_mode=True)
            factories = (
                "app.agents.frontend.agent.create_deep_agent",
                "app.agents.data_source.agent.create_deep_agent",
                "app.agents.database.agent.create_deep_agent",
                "app.agents.repair_planner.agent.create_deep_agent",
            )
            with (
                patch(factories[0], side_effect=lambda **kwargs: kwargs),
                patch(factories[1], side_effect=lambda **kwargs: kwargs),
                patch(factories[2], side_effect=lambda **kwargs: kwargs),
                patch(factories[3], side_effect=lambda **kwargs: kwargs),
            ):
                common = {
                    "workspace_root": workspace,
                    "user_skills_backend": user_skills_backend,
                    "agent_memory_backend": agent_memory_backend,
                }
                frontend = create_frontend_agent("model", **common)
                data_source = create_data_source_agent("model", **common)
                database = create_database_agent("model", **common)
                repair = create_repair_planner_agent("model", **common)

        self.assertIn("code_graph_context", _tool_names(frontend))
        self.assertNotIn("code_graph_context", _tool_names(data_source))
        self.assertNotIn("code_graph_context", _tool_names(database))
        self.assertNotIn("code_graph_context", _tool_names(repair))
        self.assertEqual(data_source.get("name"), "data-source-generation-agent")
        data_source_system_prompt = str(data_source.get("system_prompt") or "")
        self.assertIn("Java Backend Coding Agent", data_source_system_prompt)
        self.assertNotIn("Data Source Coding Agent", data_source_system_prompt)
        self.assertNotIn("unique filename", data_source_system_prompt)
        self.assertNotIn("changed files", data_source_system_prompt)
        prompt = " ".join(str(frontend.get("system_prompt") or "").split())
        self.assertIn("Process dispatched tasks one by one by `task_id`", prompt)
        self.assertIn("do not repeat the same graph query", prompt)
        self.assertIn("always read the current source file", prompt)
        for agent in (data_source, database, repair):
            self.assertNotIn("code_graph_context", str(agent.get("system_prompt") or ""))

    def test_frontend_execution_prompt_keeps_graph_fallback_only(self) -> None:
        """Frontend 保留图降级契约，DataSource 执行提示词不再注入该模块。"""

        frontend_prompt = _frontend_generation_prompt(
            project_plan={"app": {"name": "demo"}},
            build_task_plan={"summary": {}},
            tasks=[{"id": "frontend-task", "allowed_paths": ["frontend/src/**"]}],
        )
        data_source_prompt = _data_source_generation_prompt(
            project_plan={},
            workspace_snapshot={
                "backend": {"dir_structure": "└── backend/\n    └── pom.xml"}
            },
            tasks=[{"id": "backend-task", "allowed_paths": ["backend/src/**"]}],
        )
        compact_prompt = " ".join(frontend_prompt.split())
        self.assertIn("Process dispatched tasks one by one by `task_id`", compact_prompt)
        self.assertIn("`status` is `ready`", compact_prompt)
        self.assertIn("all four result collections empty", compact_prompt)
        self.assertIn("do not repeat the same graph query", compact_prompt)
        self.assertIn("always read the current source file", compact_prompt)
        self.assertIn("never expands the task's authorized paths", compact_prompt)
        self.assertNotIn("Code graph navigation contract", data_source_prompt)
        self.assertNotIn("code_graph_context", data_source_prompt)

        self.assertIn("Do not run project-level dependency installation", frontend_prompt)
        self.assertIn("Do not call `pnpm install`, `npm install`, or `npx tsc`", frontend_prompt)
        self.assertIn("outer integration-test phase performs the repository checks", frontend_prompt)
        self.assertIn("Do not install dependencies", data_source_prompt)
        self.assertIn("provided array order", data_source_prompt)
        self.assertNotIn("regular backend verification", data_source_prompt)

    def test_frontend_execution_prompt_requires_exact_resources_import(self) -> None:
        """受控页面任务必须收到唯一 RESOURCES 目录的精确导入约束。"""

        prompt = _frontend_generation_prompt(
            project_plan={"app": {"name": "demo"}},
            build_task_plan={"summary": {}},
            tasks=[
                {
                    "id": "page:assets",
                    "allowed_paths": ["frontend/src/pages/Assets/index.tsx"],
                    "source_refs": {
                        "authorization": {
                            "actions": [
                                {
                                    "actionId": "create_asset",
                                    "resourceKey": "assets_create_asset",
                                    "resourceConstant": {
                                        "group": "OPERATION",
                                        "name": "ASSETS_CREATE_ASSET",
                                    },
                                }
                            ]
                        }
                    },
                }
            ],
        )

        self.assertIn(
            "import { RESOURCES } from '@/authorization/resources';",
            prompt,
        )
        self.assertIn("Do not use a relative path, barrel export, default import, or alias.", prompt)

    def test_external_api_execution_prompt_does_not_load_database_rules(self) -> None:
        """外部 API 后端执行批次只要求外部集成 Skill。"""

        prompt = _data_source_generation_prompt(
            project_plan={
                "data_sources": [{"id": "database", "type": "database"}],
                "entity_detail_plans": [
                    {
                        "entity_id": "Weather",
                        "status": "confirmed",
                        "data_source_type": "external_api",
                        "external_api_design": {
                            "connection": {
                                "base_url": "https://weather.example.com",
                                "base_url_config_key": "integrations.weather.base-url",
                                "timeout_ms": 10000,
                                "headers": [],
                            },
                            "operations": [],
                        },
                    }
                ],
            },
            workspace_snapshot={
                "backend": {"dir_structure": "└── backend/\n    └── pom.xml"}
            },
            tasks=[
                {
                    "id": "backend:endpoint:weather_api:weather.get::Weather::upstream",
                    "unit_id": "backend:endpoint:weather_api:weather.get",
                    "description": "1. 实现天气上游 Client。",
                    "allowed_paths": ["backend/src/main/java/**"],
                    "source_refs": {
                        "target": {
                            "type": "endpoint",
                            "id": "weather.get",
                            "api_contract_id": "weather_api",
                        },
                        "endpoint_ids": ["weather.get"],
                        "entity_designs": [
                            {
                                "entity_id": "Weather",
                                "data_source_type": "external_api",
                                "external_api_design": {
                                    "connection": {
                                        "base_url_config_key": "integrations.weather.base-url"
                                    },
                                    "operations": [
                                        {
                                            "operation_id": "weather-get",
                                            "endpoint_refs": [
                                                {
                                                    "api_contract_id": "weather_api",
                                                    "endpoint_id": "weather.get",
                                                }
                                            ],
                                            "api_info": {
                                                "method": "GET",
                                                "path": "/weather",
                                                "request_shape": {
                                                    "root_type": "null",
                                                    "fields": [],
                                                },
                                                "response_shape": {
                                                    "root_type": "object",
                                                    "fields": [],
                                                },
                                            },
                                            "response_handling": {
                                                "entity_payload": True,
                                                "cardinality": "object",
                                                "payload_path": "",
                                                "success_status_codes": [200],
                                            },
                                            "mapped_entity_path": "",
                                            "field_mappings": [],
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                }
            ],
        )

        self.assertIn("springboot-external-api-generate", prompt)
        self.assertNotIn("springboot-mybatis-generate/SKILL.md", prompt)
        self.assertIn("external_api", prompt)

    def test_frontend_static_skill_is_scoped_to_current_tasks(self) -> None:
        """项目级存在 static 来源时，普通页面批次不误注入 static Skill。"""

        page_prompt = _frontend_generation_prompt(
            project_plan={
                "data_sources": [{"id": "static", "type": "static"}],
                "app": {"name": "demo"},
            },
            build_task_plan={"summary": {}},
            tasks=[
                {
                    "id": "orders-page",
                    "allowed_paths": ["frontend/src/pages/**"],
                    "source_refs": {
                        "entity_designs": [
                            {"entity_id": "Order", "data_source_type": "database"}
                        ]
                    },
                }
            ],
        )
        static_prompt = _frontend_generation_prompt(
            project_plan={"app": {"name": "demo"}},
            build_task_plan={"summary": {}},
            tasks=[
                {
                    "id": "notice-static",
                    "allowed_paths": ["frontend/src/apis/**"],
                    "source_refs": {
                        "entity_designs": [
                            {"entity_id": "Notice", "data_source_type": "static"}
                        ]
                    },
                }
            ],
        )

        self.assertNotIn("frontend-static-data-generate", page_prompt)
        self.assertNotIn("Data source is STATIC", page_prompt)
        self.assertIn("frontend-static-data-generate", static_prompt)
        self.assertIn("Data source is STATIC", static_prompt)

    def test_empty_or_unavailable_tool_result_keeps_workspace_search_fallback(self) -> None:
        """空图结果和查询异常必须显式返回文件搜索降级信息。"""

        with tempfile.TemporaryDirectory() as workspace:
            tool = create_code_graph_context_tool(workspace)
            empty = {
                "schemaVersion": "xcodeagent.code_graph_context.v1",
                "status": "ready",
                "matches": [],
                "relations": [],
                "relatedTests": [],
                "impactedFiles": [],
                "fallback": "workspace_search",
            }
            with patch(
                "app.tools.code_graph_context.CodeGraphContextResolver.resolve",
                return_value=empty,
            ):
                empty_payload = json.loads(
                    tool.invoke({"operation": "search_symbols", "query": "login"})
                )
            with patch(
                "app.tools.code_graph_context.CodeGraphContextResolver.resolve",
                side_effect=RuntimeError("host path must not be exposed"),
            ):
                unavailable_payload = json.loads(
                    tool.invoke({"operation": "search_symbols", "query": "login"})
                )

        self.assertEqual(empty_payload["status"], "ready")
        self.assertEqual(empty_payload["fallback"], "workspace_search")
        self.assertEqual(unavailable_payload["status"], "unavailable")
        self.assertEqual(unavailable_payload["fallback"], "workspace_search")
        self.assertNotIn("host path must not be exposed", str(unavailable_payload))


def _tool_names(agent: dict[str, object]) -> list[str]:
    """读取测试替身收到的 Deep Agent 工具名称。"""

    tools = agent.get("tools")
    if not isinstance(tools, list):
        return []
    return [str(getattr(item, "name", "")) for item in tools]


if __name__ == "__main__":
    unittest.main()
