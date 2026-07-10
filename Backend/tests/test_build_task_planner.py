from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.main.task_preparer import prepare_build_tasks_with_main_agent
from app.services.build_task_planner import create_build_task_plan


class BuildTaskPlannerTests(unittest.TestCase):
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

        self.assertEqual(plan["tasks"][0]["id"], "task-home")
        self.assertEqual(plan["tasks"][0]["targetFiles"], ["src/pages/Home.tsx"])
        self.assertEqual(plan["workspace_analysis"]["inspection_status"], "completed")
        self.assertEqual(plan["prepared_by"]["model"], "test-model")

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
        task = plan["tasks"][0]

        self.assertEqual(plan["version"], "0.2.0")
        self.assertEqual(plan["workspace_analysis"]["entry_files"], ["src/router/index.ts"])
        self.assertEqual(task["id"], "page-login")
        self.assertEqual(task["task_id"], "page-login")
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["targetFiles"], ["src/pages/Login.tsx", "src/router/index.ts"])
        self.assertEqual(task["change_scope"][0]["operation"], "add")
        self.assertEqual(task["impact_scope"]["affected_modules"], ["pages", "router"])
        self.assertFalse(task["can_run_in_parallel"])
        self.assertEqual(task["acceptance_criteria"], ["访问 /login 可完成表单校验"])
        self.assertEqual(plan["task_statuses"], ["pending", "running", "completed", "failed"])

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

        self.assertEqual([task["id"] for task in plan["tasks"]], ["page-task", "page-task-2"])
        self.assertEqual(plan["coordination"]["execution_batches"][0]["mode"], "parallel")
        self.assertEqual(plan["tasks"][0]["parallel_with"], ["page-task-2"])
        self.assertEqual(plan["tasks"][1]["parallel_with"], ["page-task"])


if __name__ == "__main__":
    unittest.main()
