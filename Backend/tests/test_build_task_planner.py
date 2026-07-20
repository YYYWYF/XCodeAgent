from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.agents.main.task_preparer import _model_usage, prepare_build_tasks_with_main_agent
from app.services.build_task_planner import create_build_task_plan, tasks_from_build_task_plan
from app.workspace.task_documents import render_build_task_dag_markdown


class BuildTaskPlannerTests(unittest.TestCase):
    def test_model_usage_accepts_null_provider_token_usage(self) -> None:
        """Provider 将 token_usage 返回为 null 时，诊断日志不得中断任务规划。"""

        result = SimpleNamespace(usage_metadata=None, response_metadata={"token_usage": None})

        self.assertEqual(
            _model_usage(result),
            {"input_tokens": None, "output_tokens": None},
        )

    def test_main_agent_json_is_consumed_by_task_planner(self) -> None:
        response = """```json
        {
          "workspace_analysis": {"entry_files": ["src/main.tsx"]},
          "tasks": [{
            "id": "task-home",
            "owner": "frontend",
            "description": "新增首页",
            "change_scope": [{"operation": "add", "path": "src/pages/Home.tsx"}],
            "acceptance_criteria": ["首页可渲染"]
          }]
        }
        ```"""
        project_plan = {
            "version": "1.0.0",
            "page_detail_plans": [],
            "data_sources": [],
        }

        with (
            patch(
                "app.agents.main.task_preparer._invoke_live_main_agent",
                return_value=response,
            ),
            patch(
                "app.agents.main.task_preparer.Settings.from_env",
                return_value=SimpleNamespace(model_name="test-model"),
            ),
        ):
            plan = prepare_build_tasks_with_main_agent(project_plan, workspace="/tmp/demo")

        tasks = tasks_from_build_task_plan(plan)
        self.assertEqual(tasks[0]["id"], "task-home")
        self.assertEqual(tasks[0]["targetFiles"], ["src/pages/Home.tsx"])
        self.assertEqual(plan["workspace_analysis"]["inspection_status"], "completed")
        self.assertEqual(plan["prepared_by"]["model"], "test-model")

    def test_task_preparer_binds_configured_max_tokens(self) -> None:
        """任务规划调用必须显式传递 AGENT_MAX_TOKENS，避免采用 Provider 的短输出默认值。"""

        model = Mock()
        bound_model = Mock()
        model.bind.return_value = bound_model
        bound_model.invoke.return_value = SimpleNamespace(
            content='{"tasks": [{"id": "task", "owner": "frontend", "description": "任务", "change_scope": []}]}',
            usage_metadata=None,
            response_metadata={},
        )
        settings = SimpleNamespace(
            model_name="test-model",
            model_api_name="test-model",
            default_max_tokens=4096,
        )

        with (
            patch("app.agents.main.task_preparer.Settings.from_env", return_value=settings),
            patch("app.agents.main.task_preparer.create_chat_model", return_value=model),
        ):
            prepare_build_tasks_with_main_agent({"version": "1.0.0"})

        model.bind.assert_called_once_with(max_tokens=4096)

    def test_uses_workspace_aware_agent_tasks_with_detailed_contract(self) -> None:
        project_plan = {"version": "1.0.0", "page_detail_plans": [], "data_sources": []}
        agent_plan = {
            "workspace_analysis": {
                "stack": ["React", "TypeScript"],
                "inspected_directories": ["src/pages", "src/router"],
                "entry_files": ["src/router/index.ts"],
                "conventions": ["页面使用 PascalCase 文件名"],
            },
            "tasks": [
                {
                    "id": "page-login",
                    "owner": "frontend",
                    "title": "新增登录页",
                    "description": "实现登录表单与提交状态。",
                    "dependencies": [],
                    "change_scope": [
                        {"operation": "add", "path": "src/pages/Login.tsx", "description": "新增登录页面"},
                        {"operation": "modify", "path": "src/router/index.ts", "description": "注册登录路由"},
                    ],
                    "impact_scope": {
                        "summary": "影响登录入口和路由表。",
                        "affected_modules": ["pages", "router"],
                        "public_contracts": [],
                        "risks": ["未登录跳转可能形成循环"],
                    },
                    "can_run_in_parallel": False,
                    "parallel_reason": "修改共享路由表，需要串行。",
                    "acceptance_criteria": ["访问 /login 可完成表单校验"],
                    "verification_commands": ["pnpm test"],
                    "status": "completed",
                }
            ],
        }

        plan = create_build_task_plan(project_plan, agent_plan=agent_plan)
        task = tasks_from_build_task_plan(plan)[0]

        self.assertEqual(plan["version"], "2.0.0")
        self.assertEqual(plan["schema_version"], "build-dag.v2")
        self.assertEqual(plan["task_graph"]["nodes"], ["page-login"])
        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])
        self.assertEqual(plan["workspace_analysis"]["entry_files"], ["src/router/index.ts"])
        self.assertEqual(task["id"], "page-login")
        self.assertEqual(task["task_id"], "page-login")
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["targetFiles"], ["src/pages/Login.tsx", "src/router/index.ts"])
        self.assertEqual(task["change_scope"][0]["operation"], "add")
        self.assertEqual(task["impact_scope"]["affected_modules"], ["pages", "router"])
        self.assertFalse(task["can_run_in_parallel"])
        self.assertEqual(task["acceptance_criteria"], ["访问 /login 可完成表单校验"])
        self.assertEqual(task["unit_id"], "application:root")
        self.assertEqual(plan["build_units"]["application:root"]["task_ids"], ["page-login"])

    def test_duplicate_task_ids_are_made_unique_and_parallel_batch_is_recorded(self) -> None:
        project_plan = {"version": "1.0.0", "page_detail_plans": [], "data_sources": []}
        agent_plan = {
            "tasks": [
                {
                    "id": "page-task",
                    "owner": "frontend",
                    "description": "新增登录页",
                    "change_scope": [{"operation": "add", "path": "src/pages/Login.tsx"}],
                    "acceptance_criteria": ["登录页可渲染"],
                },
                {
                    "id": "page-task",
                    "owner": "frontend",
                    "description": "新增帮助页",
                    "change_scope": [{"operation": "add", "path": "src/pages/Help.tsx"}],
                    "acceptance_criteria": ["帮助页可渲染"],
                },
            ]
        }

        plan = create_build_task_plan(project_plan, agent_plan=agent_plan)

        tasks = tasks_from_build_task_plan(plan)
        self.assertEqual([task["id"] for task in tasks], ["page-task", "page-task-2"])
        self.assertEqual(plan["execution"]["batches"][0]["mode"], "parallel")
        self.assertEqual(tasks[0]["parallel_with"], ["page-task-2"])
        self.assertEqual(tasks[1]["parallel_with"], ["page-task"])

    def test_v2_markdown_renders_units_and_task_graph(self) -> None:
        plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={
                "tasks": [
                    {
                        "id": "page-home",
                        "owner": "frontend",
                        "description": "新增首页",
                        "change_scope": [{"operation": "add", "path": "src/Home.tsx"}],
                    }
                ]
            },
        )

        markdown = render_build_task_dag_markdown(plan)

        self.assertIn("## Units", markdown)
        self.assertIn("application:root", markdown)
        self.assertIn("page-home", markdown)

    def test_compiles_unit_dependencies_and_source_refs(self) -> None:
        """页面任务会继承直接数据源和公共 API Unit 的任务依赖。"""

        base_plan = {
            "schema_version": "build-dag.v2",
            "build_units": {
                "app:api-client": {"id": "app:api-client", "kind": "application"},
                "data-source:orders": {"id": "data-source:orders", "kind": "data_source"},
                "page:orders": {"id": "page:orders", "kind": "page"},
            },
            "unit_graph": {
                "schema_version": "build-unit-graph.v2",
                "nodes": ["app:api-client", "data-source:orders", "page:orders"],
                "edges": [
                    {"from": "app:api-client", "to": "page:orders", "type": "depends_on"},
                    {"from": "data-source:orders", "to": "page:orders", "type": "depends_on"},
                ],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        build_context = {
            "target": {"type": "page", "id": "orders"},
            "required_unit_ids": ["app:api-client", "data-source:orders", "page:orders"],
            "endpoint_ids": ["orders_api.list"],
            "data_source_ids": ["orders"],
            "source_refs": {
                "page_detail": {"id": "orders", "json_path": "plans/pages/page--orders.json", "sha256": "p1"},
                "data_source_details": [
                    {"id": "orders", "json_path": "plans/data-source/data-source--orders.json", "sha256": "d1"}
                ],
            },
        }

        plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={
                "tasks": [
                    {
                        "id": "task:api-client",
                        "unit_id": "app:api-client",
                        "owner": "frontend",
                        "description": "实现 API client",
                        "change_scope": [{"operation": "modify", "path": "src/api/orders.ts"}],
                    },
                    {
                        "id": "task:orders-api",
                        "unit_id": "data-source:orders",
                        "owner": "data_source",
                        "description": "实现订单 API",
                        "change_scope": [{"operation": "modify", "path": "Backend/app/orders.py"}],
                    },
                    {
                        "id": "task:orders-page",
                        "unit_id": "page:orders",
                        "owner": "frontend",
                        "description": "实现订单页面",
                        "change_scope": [{"operation": "modify", "path": "src/pages/Orders.tsx"}],
                    },
                ]
            },
            base_build_task_plan=base_plan,
            build_context=build_context,
        )

        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}
        self.assertEqual(
            tasks["task:orders-page"]["dependencies"],
            ["task:api-client", "task:orders-api"],
        )
        self.assertEqual(tasks["task:orders-page"]["source_refs"]["type"], "page_detail")
        self.assertEqual(
            plan["build_units"]["data-source:orders"]["source_refs"]["data_source_detail"]["sha256"],
            "d1",
        )
        self.assertTrue(plan["build_units"]["page:orders"]["input_fingerprint"])


if __name__ == "__main__":
    unittest.main()
