from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.agents.main.task_preparer import (
    _model_usage,
    _task_preparation_prompt,
    prepare_build_tasks_with_main_agent,
)
from app.services.build_task_planner import (
    _database_task_requires_approval,
    create_build_task_plan,
    tasks_from_build_task_plan,
)
from app.workspace.task_documents import render_build_task_dag_markdown


class BuildTaskPlannerTests(unittest.TestCase):
    def test_delete_endpoint_name_does_not_make_create_table_high_risk(self) -> None:
        """来源 endpoint 名称中的 delete 不得被误判为高危数据库删除操作。"""

        task = {
            "database_scope": {
                "operations": ["create_table"],
                "gaps": [
                    {
                        "kind": "missing_table",
                        "source_evidence": {
                            "endpoint_id": "core_management.delete",
                            "operation": "create_table",
                        },
                    }
                ],
            }
        }

        self.assertFalse(_database_task_requires_approval(task))

    def test_drop_column_operation_remains_high_risk(self) -> None:
        """结构化 drop_column 仍必须触发高风险数据库审批。"""

        task = {"database_scope": {"operations": ["drop_column"]}}

        self.assertTrue(_database_task_requires_approval(task))

    def test_task_prompt_reserves_cross_unit_dependencies_for_unit_graph(self) -> None:
        """任务模型不得手写跨 Unit 或 reusable task 依赖。"""

        prompt = _task_preparation_prompt(
            {
                "version": "1.0.0",
                "application_skeleton": {"data_sources": [{"id": "main", "type": "database"}]},
            },
            {},
            {
                "target": {"type": "page", "id": "dashboard"},
                "reusable_tasks_by_unit": {
                    "frontend:api-client": ["shared-api-task"]
                },
            },
        )

        self.assertIn("same Unit only", prompt)
        self.assertIn("do not copy its task ids into dependencies", prompt)

    def test_static_task_prompt_excludes_backend_generation_requirements(self) -> None:
        """Static 任务准备不读取或注入后端数据库技能。"""

        project_plan = {
            "version": "1.0.0",
            "application_skeleton": {
                "data_sources": [{"id": "orders", "type": "static"}]
            },
            "executable_details": {"data_sources": [], "api_contracts": []},
        }
        with patch(
            "app.agents.main.task_preparer._springboot_mybatis_skill_document"
        ) as backend_skill:
            prompt = _task_preparation_prompt(
                project_plan,
                {},
                {"required_unit_ids": ["frontend:data:orders", "page:orders"]},
            )

        backend_skill.assert_not_called()
        self.assertIn("frontend:data:<sourceId>", prompt)
        self.assertIn("Never create database, backend", prompt)
        self.assertNotIn("INJECTED springboot-mybatis-generate", prompt)
        self.assertNotIn("WorkspaceNavigationContext", prompt)

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
        self.assertEqual(tasks[0]["target_files"], ["src/pages/Home.tsx"])
        self.assertEqual(plan["workspace_analysis"]["inspection_status"], "completed")
        self.assertEqual(plan["prepared_by"]["model"], "test-model")

    def test_task_preparer_binds_configured_max_tokens(self) -> None:
        """任务规划调用必须显式传递 AGENT_MAX_TOKENS，避免采用 Provider 的短输出默认值。"""

        model = Mock()
        bound_model = Mock()
        model.bind.return_value = bound_model
        bound_model.invoke.return_value = SimpleNamespace(
            content='{"tasks": [{"id": "task", "owner": "frontend", "description": "任务", "change_scope": [{"operation": "modify", "path": "src/task.ts"}]}]}',
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
            prepare_build_tasks_with_main_agent(
                {
                    "version": "1.0.0",
                    "application_skeleton": {"data_sources": [{"id": "main", "type": "database"}]},
                }
            )

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

        self.assertEqual(plan["version"], "3.0.0")
        self.assertEqual(plan["schema_version"], "build-dag.v3")
        self.assertEqual(plan["task_graph"]["nodes"], ["page-login"])
        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])
        self.assertEqual(plan["workspace_analysis"]["entry_files"], ["src/router/index.ts"])
        self.assertEqual(task["id"], "page-login")
        self.assertEqual(task["status"], "pending")
        self.assertNotIn("task_id", task)
        self.assertNotIn("dependsOn", task)
        self.assertNotIn("targetFiles", task)
        self.assertNotIn("acceptanceCriteria", task)
        self.assertNotIn("canRunInParallel", task)
        self.assertEqual(task["target_files"], ["src/pages/Login.tsx", "src/router/index.ts"])
        self.assertEqual(task["change_scope"][0]["operation"], "add")
        self.assertEqual(task["impact_scope"]["affected_modules"], ["pages", "router"])
        self.assertFalse(task["can_run_in_parallel"])
        self.assertNotIn("访问 /login 可完成表单校验", task["acceptance_criteria"])
        self.assertEqual(
            [check["kind"] for check in task["acceptance_checks"]],
            ["file_operation", "file_operation", "scope_boundary"],
        )
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

    def test_live_page_path_is_reconciled_and_menu_route_task_is_added(self) -> None:
        """实时唯一同义页面目录应成为规范目标，并补齐菜单自动路由登记任务。"""

        project_plan = {
            "version": "1.0.0",
            "application_skeleton": {
                "pages": [
                    {
                        "pageId": "dashboard_page",
                        "name": "概览页",
                        "path": "/page/",
                        "module_id": "dashboard",
                    }
                ]
            },
        }
        build_context = {
            "target": {"type": "page", "id": "dashboard_page"},
            "page_detail": {"page_name": "概览页", "path": "/page/"},
            "required_unit_ids": ["app:route-registry", "page:dashboard_page"],
            "source_refs": {"type": "page_detail"},
        }
        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "app:route-registry": {"id": "app:route-registry", "kind": "application"},
                "page:dashboard_page": {"id": "page:dashboard_page", "kind": "page"},
            },
            "unit_graph": {
                "nodes": ["app:route-registry", "page:dashboard_page"],
                "edges": [
                    {
                        "from": "app:route-registry",
                        "to": "page:dashboard_page",
                        "type": "depends_on",
                    }
                ],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        with tempfile.TemporaryDirectory() as workspace:
            dashboard = Path(workspace) / "frontend/src/pages/Dashboard/index.tsx"
            dashboard.parent.mkdir(parents=True)
            dashboard.write_text("export default function Dashboard() {}", encoding="utf-8")
            menus = Path(workspace) / "frontend/src/constants/menus.ts"
            menus.parent.mkdir(parents=True)
            menus.write_text("export const BIZ_MENUS = [];", encoding="utf-8")
            plan = create_build_task_plan(
                project_plan,
                agent_plan={
                    "tasks": [
                        {
                            "id": "page-layout",
                            "unit_id": "page:dashboard_page",
                            "owner": "frontend",
                            "description": "创建概览页",
                            "change_scope": [
                                {
                                    "operation": "add",
                                    "path": "frontend/src/pages/DashboardPage/index.tsx",
                                }
                            ],
                        }
                    ]
                },
                base_build_task_plan=base_plan,
                build_context=build_context,
                workspace_root=workspace,
            )

        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}
        page_task = tasks["page-layout"]
        route_task = tasks["page:dashboard_page:route-menu-registration"]
        self.assertEqual(page_task["target_files"], ["frontend/src/pages/Dashboard/index.tsx"])
        self.assertEqual(page_task["change_scope"][0]["operation"], "modify")
        self.assertEqual(
            page_task["path_reconciliation"]["canonical_path"],
            "frontend/src/pages/Dashboard/index.tsx",
        )
        self.assertEqual(route_task["target_files"], ["frontend/src/constants/menus.ts"])
        self.assertEqual(route_task["dependencies"], ["page-layout"])
        self.assertIn("key: 'Dashboard'", route_task["description"])
        self.assertIn("path: '/page/'", route_task["description"])
        self.assertIn("BIZ_MENUS 顶层数组", route_task["description"])
        self.assertNotIn("firstLevel.children", route_task["description"])

    def test_existing_page_entry_is_used_when_model_omits_page_path(self) -> None:
        """模板已有唯一页面入口时，模型漏写入口路径不应阻断任务拆分。"""

        project_plan = {
            "version": "1.0.0",
            "application_skeleton": {
                "pages": [
                    {
                        "pageId": "pet_list_page",
                        "name": "宠物照片列表页",
                        "path": "/page/home",
                    }
                ]
            },
        }
        build_context = {
            "target": {"type": "page", "id": "pet_list_page"},
            "page_detail": {"page_name": "宠物照片列表页", "path": "/page/home"},
        }

        with tempfile.TemporaryDirectory() as workspace:
            page_file = Path(workspace) / "frontend/src/pages/PetListPage/index.tsx"
            page_file.parent.mkdir(parents=True)
            page_file.write_text("export default function PetListPage() {}", encoding="utf-8")
            menus = Path(workspace) / "frontend/src/constants/menus.ts"
            menus.parent.mkdir(parents=True)
            menus.write_text("export const BIZ_MENUS = [];", encoding="utf-8")

            plan = create_build_task_plan(
                project_plan,
                agent_plan={
                    "tasks": [
                        {
                            "id": "pet-data-view",
                            "unit_id": "page:pet_list_page",
                            "owner": "frontend",
                            "description": "实现宠物列表内容",
                            "change_scope": [
                                {
                                    "operation": "modify",
                                    "path": "frontend/src/components/PetCard.tsx",
                                }
                            ],
                        }
                    ]
                },
                build_context=build_context,
                workspace_root=workspace,
            )

        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}
        route_task = tasks["page:pet_list_page:route-menu-registration"]
        self.assertIn("key: 'PetListPage'", route_task["description"])
        self.assertEqual(route_task["dependencies"], ["pet-data-view"])

    def test_missing_page_entry_is_injected_from_page_target(self) -> None:
        """模板入口尚未落盘且模型漏写路径时，按 pageId 补回标准页面入口。"""

        project_plan = {
            "version": "1.0.0",
            "application_skeleton": {
                "pages": [
                    {
                        "pageId": "pet_list_page",
                        "name": "宠物照片列表页",
                        "path": "/page/home",
                    }
                ]
            },
        }
        build_context = {
            "target": {"type": "page", "id": "pet_list_page"},
            "page_detail": {"page_name": "宠物照片列表页", "path": "/page/home"},
        }

        with tempfile.TemporaryDirectory() as workspace:
            menus = Path(workspace) / "frontend/src/constants/menus.ts"
            menus.parent.mkdir(parents=True)
            menus.write_text("export const BIZ_MENUS = [];", encoding="utf-8")

            plan = create_build_task_plan(
                project_plan,
                agent_plan={
                    "tasks": [
                        {
                            "id": "pet-data-view",
                            "unit_id": "page:pet_list_page",
                            "owner": "frontend",
                            "description": "实现宠物列表内容",
                            "change_scope": [
                                {
                                    "operation": "modify",
                                    "path": "frontend/src/components/PetCard.tsx",
                                }
                            ],
                        }
                    ]
                },
                build_context=build_context,
                workspace_root=workspace,
            )

        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}
        page_task = tasks["pet-data-view"]
        route_task = tasks["page:pet_list_page:route-menu-registration"]
        self.assertIn(
            "frontend/src/pages/PetListPage/index.tsx",
            page_task["target_files"],
        )
        self.assertIn(
            {
                "operation": "add",
                "path": "frontend/src/pages/PetListPage/index.tsx",
                "description": "创建当前页面的标准入口文件。",
            },
            page_task["change_scope"],
        )
        self.assertEqual(route_task["dependencies"], ["pet-data-view"])

    def test_scaffolded_menu_entry_marks_model_task_already_satisfied(self) -> None:
        """脚手架已注册精确菜单项时，模型菜单任务不得再次进入写执行器。"""

        project_plan = {
            "version": "1.0.0",
            "application_skeleton": {
                "pages": [
                    {
                        "pageId": "dashboard_page",
                        "name": "概览页",
                        "path": "/page/",
                        "module_id": "dashboard",
                    }
                ]
            },
        }
        build_context = {
            "target": {"type": "page", "id": "dashboard_page"},
            "page_detail": {"page_name": "概览页", "path": "/page/"},
            "required_unit_ids": ["app:route-registry", "page:dashboard_page"],
        }
        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "app:route-registry": {"id": "app:route-registry", "kind": "application"},
                "page:dashboard_page": {"id": "page:dashboard_page", "kind": "page"},
            },
            "unit_graph": {
                "nodes": ["app:route-registry", "page:dashboard_page"],
                "edges": [
                    {
                        "from": "app:route-registry",
                        "to": "page:dashboard_page",
                        "type": "depends_on",
                    }
                ],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        with tempfile.TemporaryDirectory() as workspace:
            page_file = Path(workspace) / "frontend/src/pages/DashboardPage/index.tsx"
            page_file.parent.mkdir(parents=True)
            page_file.write_text("export default function DashboardPage() {}", encoding="utf-8")
            menus = Path(workspace) / "frontend/src/constants/menus.ts"
            menus.parent.mkdir(parents=True)
            menus.write_text(
                """export const BIZ_MENUS = [{
  path: 'firstLevel',
  children: [{ path: '/page/', name: '概览页', key: 'DashboardPage' }]
}];""",
                encoding="utf-8",
            )
            plan = create_build_task_plan(
                project_plan,
                agent_plan={
                    "tasks": [
                        {
                            "id": "task-menu-register-dashboard",
                            "unit_id": "app:route-registry",
                            "owner": "frontend",
                            "description": "追加 DashboardPage 概览页菜单项",
                            "change_scope": [
                                {
                                    "operation": "modify",
                                    "path": "frontend/src/constants/menus.ts",
                                }
                            ],
                            "acceptance_criteria": ["DashboardPage 菜单项存在"],
                        },
                        {
                            "id": "page-layout",
                            "unit_id": "page:dashboard_page",
                            "owner": "frontend",
                            "description": "实现概览页",
                            "dependencies": ["task-menu-register-dashboard"],
                            "change_scope": [
                                {
                                    "operation": "modify",
                                    "path": "frontend/src/pages/DashboardPage/index.tsx",
                                }
                            ],
                        },
                    ]
                },
                base_build_task_plan=base_plan,
                build_context=build_context,
                workspace_root=workspace,
            )

        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}
        menu_task = tasks["task-menu-register-dashboard"]
        self.assertEqual(menu_task["status"], "already_satisfied")
        self.assertEqual(menu_task["satisfied_by"], "frontend-template-page-scaffold")
        self.assertEqual(
            menu_task["satisfaction_evidence"]["target_files"],
            ["frontend/src/constants/menus.ts"],
        )
        self.assertEqual(tasks["page-layout"]["dependencies"], ["task-menu-register-dashboard"])
        self.assertEqual(plan["summary"]["already_satisfied"], 1)
        self.assertEqual(plan["summary"]["completed"], 1)

    def test_existing_menu_is_removed_from_mixed_page_task(self) -> None:
        """页面、API 与菜单混合任务遇到既有菜单时只移除菜单写入，其他工作仍需执行。"""

        project_plan = {
            "version": "1.0.0",
            "application_skeleton": {
                "pages": [
                    {
                        "pageId": "dashboard_page",
                        "name": "概览页",
                        "path": "/page/home",
                        "module_id": "dashboard",
                    }
                ]
            },
        }
        build_context = {
            "target": {"type": "page", "id": "dashboard_page"},
            "page_detail": {"page_name": "概览页", "path": "/page/home"},
            "required_unit_ids": ["page:dashboard_page"],
        }
        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "page:dashboard_page": {
                    "id": "page:dashboard_page",
                    "kind": "page",
                }
            },
            "unit_graph": {
                "nodes": ["page:dashboard_page"],
                "edges": [],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        with tempfile.TemporaryDirectory() as workspace:
            page = Path(workspace) / "frontend/src/pages/DashboardPage/index.tsx"
            page.parent.mkdir(parents=True)
            page.write_text("export default function DashboardPage() {}", encoding="utf-8")
            menus = Path(workspace) / "frontend/src/constants/menus.ts"
            menus.parent.mkdir(parents=True)
            menus.write_text(
                "export const BIZ_MENUS = [{ path: '/page/home', name: '概览页', key: 'DashboardPage' }];",
                encoding="utf-8",
            )
            plan = create_build_task_plan(
                project_plan,
                agent_plan={
                    "tasks": [
                        {
                            "id": "task-dashboard",
                            "unit_id": "page:dashboard_page",
                            "owner": "frontend",
                            "description": "实现概览页并确认菜单注册",
                            "change_scope": [
                                {
                                    "operation": "modify",
                                    "path": "frontend/src/pages/DashboardPage/index.tsx",
                                },
                                {
                                    "operation": "add",
                                    "path": "frontend/src/apis/leaveApi.ts",
                                },
                                {
                                    "operation": "modify",
                                    "path": "frontend/src/constants/menus.ts",
                                },
                            ],
                        }
                    ]
                },
                base_build_task_plan=base_plan,
                build_context=build_context,
                workspace_root=workspace,
            )

        task = plan["task_registry"]["task-dashboard"]
        self.assertEqual(task["status"], "pending")
        self.assertEqual(
            task["target_files"],
            [
                "frontend/src/pages/DashboardPage/index.tsx",
                "frontend/src/apis/leaveApi.ts",
            ],
        )
        self.assertNotIn("frontend/src/constants/menus.ts", task["allowed_paths"])
        self.assertEqual(
            task["pre_satisfied_targets"][0]["path"],
            "frontend/src/constants/menus.ts",
        )

    def test_scaffolded_menu_entry_prevents_deterministic_duplicate_task(self) -> None:
        """模型未生成菜单任务时，已存在的脚手架菜单也不得被确定性重复补齐。"""

        project_plan = {
            "version": "1.0.0",
            "application_skeleton": {
                "pages": [
                    {
                        "pageId": "dashboard_page",
                        "name": "概览页",
                        "path": "/page/",
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as workspace:
            page_file = Path(workspace) / "frontend/src/pages/DashboardPage/index.tsx"
            page_file.parent.mkdir(parents=True)
            page_file.write_text("export default function DashboardPage() {}", encoding="utf-8")
            menus = Path(workspace) / "frontend/src/constants/menus.ts"
            menus.parent.mkdir(parents=True)
            menus.write_text(
                "export const BIZ_MENUS = [{ children: "
                "[{ path: '/page/', name: '概览页', key: 'DashboardPage' }] }];",
                encoding="utf-8",
            )
            plan = create_build_task_plan(
                project_plan,
                agent_plan={
                    "tasks": [
                        {
                            "id": "page-layout",
                            "unit_id": "page:dashboard_page",
                            "owner": "frontend",
                            "description": "实现概览页",
                            "change_scope": [
                                {
                                    "operation": "modify",
                                    "path": "frontend/src/pages/DashboardPage/index.tsx",
                                }
                            ],
                        }
                    ]
                },
                build_context={
                    "target": {"type": "page", "id": "dashboard_page"},
                    "page_detail": {"page_name": "概览页", "path": "/page/"},
                    "required_unit_ids": ["page:dashboard_page"],
                },
                workspace_root=workspace,
            )

        self.assertEqual(
            [task["id"] for task in tasks_from_build_task_plan(plan)],
            ["page-layout"],
        )

    def test_existing_model_menu_task_is_normalized_to_top_level_biz_menus(self) -> None:
        """模型已有菜单任务时，任务编译器仍要统一追加位置和菜单 path。"""

        project_plan = {
            "version": "1.0.0",
            "application_skeleton": {
                "pages": [
                    {
                        "pageId": "dashboard_page",
                        "name": "概览页",
                        "path": "/page/dashboard",
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as workspace:
            page_file = Path(workspace) / "frontend/src/pages/DashboardPage/index.tsx"
            page_file.parent.mkdir(parents=True)
            page_file.write_text("export default function DashboardPage() {}", encoding="utf-8")
            menus = Path(workspace) / "frontend/src/constants/menus.ts"
            menus.parent.mkdir(parents=True)
            menus.write_text("export const BIZ_MENUS = [];", encoding="utf-8")
            plan = create_build_task_plan(
                project_plan,
                agent_plan={
                    "tasks": [
                        {
                            "id": "task-menu-register-dashboard",
                            "unit_id": "page:dashboard_page",
                            "owner": "frontend",
                            "description": "追加 { path: '/page/dashboard', name: '概览页', key: 'DashboardPage' }",
                            "change_scope": [
                                {
                                    "operation": "modify",
                                    "path": "frontend/src/constants/menus.ts",
                                    "description": "追加到 BIZ_MENUS.firstLevel.children",
                                }
                            ],
                            "acceptance_criteria": ["path 为 /page/dashboard"],
                        },
                        {
                            "id": "page-layout",
                            "unit_id": "page:dashboard_page",
                            "owner": "frontend",
                            "description": "实现概览页",
                            "change_scope": [
                                {
                                    "operation": "modify",
                                    "path": "frontend/src/pages/DashboardPage/index.tsx",
                                }
                            ],
                        },
                    ]
                },
                build_context={
                    "target": {"type": "page", "id": "dashboard_page"},
                    "page_detail": {"page_name": "概览页", "path": "/page/dashboard"},
                    "required_unit_ids": ["page:dashboard_page"],
                },
                workspace_root=workspace,
            )

        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}
        menu_task = tasks["task-menu-register-dashboard"]
        self.assertEqual(menu_task["target_files"], ["frontend/src/constants/menus.ts"])
        self.assertEqual(menu_task["change_scope"][0]["description"], "仅向 BIZ_MENUS 顶层数组追加当前页面菜单项。")
        self.assertIn("BIZ_MENUS 顶层数组", menu_task["description"])
        self.assertIn("path: '/page/dashboard'", menu_task["description"])
        self.assertNotIn("firstLevel.children", menu_task["description"])
        self.assertIn("path=/page/dashboard", menu_task["acceptance_criteria"][2])

    def test_missing_page_entry_falls_back_to_target_page_key(self) -> None:
        """模型漏在 change_scope 声明页面入口时，用 target.page_key 兜底补齐菜单登记。"""

        project_plan = {
            "version": "1.0.0",
            "application_skeleton": {
                "pages": [
                    {
                        "pageId": "project_list_page",
                        "name": "项目列表页",
                        "path": "/page/project-list",
                        "module_id": "project_management",
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as workspace:
            page_file = Path(workspace) / "frontend/src/pages/ProjectListPage/index.tsx"
            page_file.parent.mkdir(parents=True)
            page_file.write_text("export default function ProjectListPage() {}", encoding="utf-8")
            menus = Path(workspace) / "frontend/src/constants/menus.ts"
            menus.parent.mkdir(parents=True)
            menus.write_text("export const BIZ_MENUS = [];", encoding="utf-8")
            plan = create_build_task_plan(
                project_plan,
                agent_plan={
                    "tasks": [
                        {
                            "id": "task-api",
                            "unit_id": "page:project_list_page",
                            "owner": "frontend",
                            "description": "实现项目列表 API",
                            "change_scope": [
                                {
                                    "operation": "add",
                                    "path": "frontend/src/apis/projectApi.ts",
                                }
                            ],
                        }
                    ]
                },
                build_context={
                    "target": {
                        "type": "page",
                        "id": "project_list_page",
                        "page_key": "ProjectListPage",
                    },
                    "page_detail": {"page_name": "项目列表页", "path": "/page/project-list"},
                    "required_unit_ids": ["page:project_list_page"],
                },
                workspace_root=workspace,
            )

        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}
        route_task = tasks["page:project_list_page:route-menu-registration"]
        self.assertIn("key: 'ProjectListPage'", route_task["description"])
        self.assertIn("path: '/page/project-list'", route_task["description"])

    def test_v3_markdown_renders_units_and_task_graph(self) -> None:
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
        """页面任务只继承前端公共 Unit，后端和数据库仅保留来源引用。"""

        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "frontend:api-client": {"id": "frontend:api-client", "kind": "frontend"},
                "database:orders": {"id": "database:orders", "kind": "database"},
                "page:orders": {"id": "page:orders", "kind": "page"},
            },
            "unit_graph": {
                "schema_version": "build-unit-graph.v3",
                "nodes": ["frontend:api-client", "database:orders", "page:orders"],
                "edges": [
                    {"from": "frontend:api-client", "to": "page:orders", "type": "depends_on"},
                    {"from": "database:orders", "to": "page:orders", "type": "depends_on"},
                ],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        build_context = {
            "target": {"type": "page", "id": "orders"},
            "required_unit_ids": ["frontend:api-client", "database:orders", "page:orders"],
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
                        "unit_id": "frontend:api-client",
                        "owner": "frontend",
                        "description": "实现 API client",
                        "change_scope": [{"operation": "modify", "path": "src/api/orders.ts"}],
                    },
                    {
                        "id": "task:orders-api",
                        "unit_id": "database:orders",
                        "owner": "database",
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
            ["task:api-client"],
        )
        self.assertEqual(tasks["task:orders-page"]["source_refs"]["type"], "page_detail")
        self.assertEqual(
            plan["build_units"]["database:orders"]["source_refs"],
            {
                "type": "database_context",
                "target": {"type": "page", "id": "orders"},
                "database_context_status": None,
                "database_context_hashes": [],
                "endpoint_details": [],
                "data_source_ids": ["orders"],
                "endpoint_ids": ["orders_api.list"],
            },
        )
        self.assertTrue(plan["build_units"]["page:orders"]["input_fingerprint"])

    def test_unit_graph_rewrites_reverse_dependencies_and_excludes_verification_tasks(self) -> None:
        """复现多任务计划，跨 Unit 反向边被改写且纯验证任务不进入注册表。"""

        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "backend:bootstrap": {"id": "backend:bootstrap", "kind": "backend"},
                "database:core": {"id": "database:core", "kind": "database"},
                "database:user": {"id": "database:user", "kind": "database"},
                "frontend:api-client": {"id": "frontend:api-client", "kind": "frontend"},
                "page:core": {"id": "page:core", "kind": "page"},
            },
            "unit_graph": {
                "nodes": [
                    "backend:bootstrap",
                    "database:core",
                    "database:user",
                    "frontend:api-client",
                    "page:core",
                ],
                "edges": [
                    {"from": "database:core", "to": "backend:bootstrap", "type": "depends_on"},
                    {"from": "database:user", "to": "backend:bootstrap", "type": "depends_on"},
                    {"from": "frontend:api-client", "to": "page:core", "type": "depends_on"},
                    {"from": "database:core", "to": "page:core", "type": "depends_on"},
                ],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        code_tasks = [
            ("core", "database:core", "database", "database/migrations/core.sql"),
            ("user", "database:user", "database", "database/migrations/user.sql"),
            ("bootstrap", "backend:bootstrap", "backend", "Backend/main.py"),
            ("client", "frontend:api-client", "frontend", "Frontend/api.ts"),
            ("page", "page:core", "frontend", "Frontend/Core.tsx"),
        ]
        agent_tasks = [
            {
                "id": task_id,
                "unit_id": unit_id,
                "owner": owner,
                "description": task_id,
                "dependencies": ["core", "user"] if task_id == "bootstrap" else [],
                **(
                    {
                        "database_scope": {
                            "data_source_id": unit_id.split(":", 1)[1],
                            "operations": ["create_table"],
                        }
                    }
                    if owner == "database"
                    else {}
                ),
                "change_scope": [{"operation": "modify", "path": path}],
            }
            for task_id, unit_id, owner, path in code_tasks
        ]
        agent_tasks.extend(
            [
                {"id": "verify-shell", "unit_id": "frontend:shell", "owner": "frontend", "description": "验证壳", "change_scope": []},
                {"id": "verify-route", "unit_id": "app:route-registry", "owner": "frontend", "description": "验证路由", "change_scope": []},
            ]
        )

        plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={"tasks": agent_tasks},
            base_build_task_plan=base_plan,
            build_context={
                "direct_endpoint_details": [
                    {
                        "endpoint_id": "core.create",
                        "method": "POST",
                        "data_origin": {
                            "source_type": "database",
                            "effective_source": {"kind": "mysql_new_table"},
                            "differences": ["需要建表。"],
                        },
                    }
                ],
                "database_planning_context": {
                    "schema_version": "database-context.v1",
                    "status": "completed",
                    "actual_schema": {"schema_hash": "actual-hash", "tables": []},
                    "required_schema": {"schema_hash": "required-hash", "tables": []},
                    "gaps": [
                        {"id": "gap-core", "kind": "missing_table", "table": "core"},
                        {"id": "gap-user", "kind": "missing_table", "table": "user"},
                    ],
                    "task_intents": [],
                }
            },
        )
        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}

        self.assertEqual(set(tasks), {"core", "user", "bootstrap", "client", "page"})
        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])
        self.assertEqual(tasks["bootstrap"]["dependencies"], ["core", "user"])
        self.assertEqual(
            {item["dependency"] for item in tasks["bootstrap"]["dependency_rewrites"]},
            {"core", "user"},
        )
        self.assertEqual(tasks["core"]["dependencies"], [])

    def test_database_task_cannot_modify_backend_code_files(self) -> None:
        """database owner 任务混入 Java/后端代码文件时必须在 DAG 校验中失败。"""

        plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={
                "tasks": [
                    {
                        "id": "bad-db-task",
                        "unit_id": "database:users",
                        "owner": "database",
                        "task_type": "database.change",
                        "description": "错误地生成 Entity 代码。",
                        "database_scope": {
                            "data_source_id": "users",
                            "operations": ["create_table"],
                        },
                        "change_scope": [
                            {"operation": "add", "path": "Backend/src/main/java/User.java"}
                        ],
                    }
                ]
            },
            base_build_task_plan={
                "schema_version": "build-dag.v3",
                "build_units": {
                    "database:users": {"id": "database:users", "kind": "database"}
                },
                "unit_graph": {
                    "nodes": ["database:users"],
                    "edges": [],
                    "validation": {"is_valid": True, "errors": []},
                },
            },
            build_context={
                "database_planning_context": {
                    "schema_version": "database-context.v1",
                    "status": "completed",
                    "actual_schema": {"schema_hash": "actual-hash", "tables": []},
                    "required_schema": {"schema_hash": "required-hash", "tables": []},
                    "gaps": [],
                    "task_intents": [],
                }
            },
        )

        self.assertFalse(plan["task_graph"]["validation"]["is_valid"])
        self.assertIn(
            "must not modify code files",
            " ".join(plan["task_graph"]["validation"]["errors"]),
        )

    def test_readonly_existing_database_endpoint_drops_database_change_task(self) -> None:
        """只读 mysql_existing 接口已有真实摘要时，不保留多余 database.change 任务。"""

        plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={
                "tasks": [
                    {
                        "id": "users-db",
                        "unit_id": "database:users",
                        "owner": "database",
                        "task_type": "database.change",
                        "description": "为用户列表准备数据库。",
                        "database_scope": {
                            "data_source_id": "users",
                            "operations": ["create_table"],
                        },
                        "change_scope": [
                            {"operation": "add", "path": "database/migrations/users.sql"}
                        ],
                    },
                    {
                        "id": "users-api",
                        "unit_id": "backend:endpoint:user_api:user.list",
                        "owner": "backend",
                        "description": "实现用户列表接口。",
                        "change_scope": [{"operation": "modify", "path": "Backend/UserApi.java"}],
                    },
                ]
            },
            base_build_task_plan={
                "schema_version": "build-dag.v3",
                "build_units": {
                    "database:users": {"id": "database:users", "kind": "database"},
                    "backend:endpoint:user_api:user.list": {
                        "id": "backend:endpoint:user_api:user.list",
                        "kind": "backend",
                    },
                },
                "unit_graph": {
                    "nodes": [
                        "database:users",
                        "backend:endpoint:user_api:user.list",
                    ],
                    "edges": [
                        {
                            "from": "database:users",
                            "to": "backend:endpoint:user_api:user.list",
                            "type": "depends_on",
                        }
                    ],
                    "validation": {"is_valid": True, "errors": []},
                },
            },
            build_context={
                "direct_endpoint_details": [
                    {
                        "endpoint_id": "user.list",
                        "method": "GET",
                        "data_origin": {
                            "source_type": "database",
                            "effective_source": {"kind": "mysql_existing"},
                            "differences": [],
                            "notes": [],
                        },
                    }
                ],
                "database_planning_context": {
                    "schema_version": "database-context.v1",
                    "status": "completed",
                    "actual_schema": {"schema_hash": "actual-hash", "tables": []},
                    "required_schema": {"schema_hash": "required-hash", "tables": []},
                    "gaps": [],
                    "task_intents": [],
                },
            },
        )

        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}
        self.assertNotIn("users-db", tasks)
        self.assertIn("users-api", tasks)

    def test_database_endpoint_and_page_units_allow_frontend_backend_parallelism(self) -> None:
        """数据库 gap 约束 endpoint，但 page 不等待 endpoint 实现。"""

        plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={
                "tasks": [
                    {
                        "id": "users-api",
                        "unit_id": "backend:endpoint:user_api:user.create",
                        "owner": "backend",
                        "description": "实现用户创建接口。",
                        "change_scope": [
                            {"operation": "modify", "path": "Backend/UserApi.java"}
                        ],
                    },
                    {
                        "id": "users-page",
                        "unit_id": "page:users",
                        "owner": "frontend",
                        "description": "实现用户页面。",
                        "change_scope": [
                            {"operation": "modify", "path": "frontend/src/pages/Users.tsx"}
                        ],
                    }
                ]
            },
            base_build_task_plan={
                "schema_version": "build-dag.v3",
                "build_units": {
                    "database:users": {"id": "database:users", "kind": "database"},
                    "backend:endpoint:user_api:user.create": {
                        "id": "backend:endpoint:user_api:user.create",
                        "kind": "backend",
                    },
                    "page:users": {"id": "page:users", "kind": "page"},
                },
                "unit_graph": {
                    "nodes": [
                        "database:users",
                        "backend:endpoint:user_api:user.create",
                        "page:users",
                    ],
                    "edges": [
                        {
                            "from": "database:users",
                            "to": "backend:endpoint:user_api:user.create",
                            "type": "depends_on",
                        },
                        {
                            "from": "backend:endpoint:user_api:user.create",
                            "to": "page:users",
                            "type": "depends_on",
                        }
                    ],
                    "validation": {"is_valid": True, "errors": []},
                },
            },
            build_context={
                "data_source_ids": ["users"],
                "database_planning_context": {
                    "schema_version": "database-context.v1",
                    "status": "completed",
                    "actual_schema": {"schema_hash": "actual-hash", "tables": []},
                    "required_schema": {
                        "schema_hash": "required-hash",
                        "tables": [{"name": "users", "columns": []}],
                    },
                    "gaps": [
                        {
                            "id": "gap-users-table",
                            "kind": "missing_table",
                            "database": "xcode",
                            "table": "users",
                            "message": "表 users 不存在，需要创建。",
                        }
                    ],
                    "task_intents": [
                        {
                            "id": "db-intent-gap-users-table",
                            "operation": "create_table",
                            "task_type": "database.change",
                            "risk": "low",
                            "gap_ids": ["gap-users-table"],
                            "database_scope": {
                                "database": "xcode",
                                "tables": ["users"],
                                "operations": ["create_table"],
                                "gaps": [
                                    {
                                        "id": "gap-users-table",
                                        "kind": "missing_table",
                                        "database": "xcode",
                                        "table": "users",
                                        "message": "表 users 不存在，需要创建。",
                                    }
                                ],
                            },
                            "description": "表 users 不存在，需要创建。",
                        }
                    ],
                },
            },
        )

        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}
        self.assertIn("db-intent-gap-users-table", tasks)
        self.assertEqual(tasks["db-intent-gap-users-table"]["owner"], "database")
        self.assertIn("db-intent-gap-users-table", tasks["users-api"]["dependencies"])
        self.assertNotIn("users-api", tasks["users-page"]["dependencies"])
        self.assertEqual(tasks["users-page"]["dependencies"], [])
        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])

    def test_database_task_missing_scope_uses_unique_gap_intent_scope(self) -> None:
        """模型生成数据库任务但漏 scope 时，可由唯一 gap 意图补齐范围。"""

        plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={
                "tasks": [
                    {
                        "id": "db-add-summary-columns",
                        "unit_id": "database:core",
                        "owner": "database",
                        "task_type": "database.change",
                        "description": "补充 user 表的 entryDate 字段。",
                        "change_scope": [],
                    },
                    {
                        "id": "summary-api",
                        "unit_id": "backend:endpoint:core_api:summary",
                        "owner": "backend",
                        "description": "实现概览接口。",
                        "change_scope": [
                            {"operation": "modify", "path": "Backend/SummaryApi.java"}
                        ],
                    },
                ]
            },
            base_build_task_plan={
                "schema_version": "build-dag.v3",
                "build_units": {
                    "database:core": {"id": "database:core", "kind": "database"},
                    "backend:endpoint:core_api:summary": {
                        "id": "backend:endpoint:core_api:summary",
                        "kind": "backend",
                    },
                },
                "unit_graph": {
                    "nodes": ["database:core", "backend:endpoint:core_api:summary"],
                    "edges": [
                        {
                            "from": "database:core",
                            "to": "backend:endpoint:core_api:summary",
                            "type": "depends_on",
                        }
                    ],
                    "validation": {"is_valid": True, "errors": []},
                },
            },
            build_context={
                "data_source_ids": ["core"],
                "database_planning_context": {
                    "schema_version": "database-context.v1",
                    "status": "completed",
                    "actual_schema": {"schema_hash": "actual-hash", "tables": []},
                    "required_schema": {
                        "schema_hash": "required-hash",
                        "tables": [{"name": "user", "columns": [{"name": "entryDate"}]}],
                    },
                    "gaps": [
                        {
                            "id": "gap-entry-date",
                            "kind": "missing_column",
                            "resolution_kind": "database_change",
                            "database": "xcode",
                            "table": "user",
                            "column": "entryDate",
                            "message": "表 user 缺少字段 entryDate。",
                        }
                    ],
                    "task_intents": [
                        {
                            "id": "db-intent-gap-entry-date",
                            "operation": "add_column",
                            "task_type": "database.change",
                            "risk": "low",
                            "gap_ids": ["gap-entry-date"],
                            "database_scope": {
                                "database": "xcode",
                                "tables": ["user"],
                                "columns": ["entryDate"],
                                "operations": ["add_column"],
                                "gap_ids": ["gap-entry-date"],
                            },
                            "description": "表 user 缺少字段 entryDate。",
                        }
                    ],
                },
            },
        )

        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}
        self.assertEqual(
            tasks["db-add-summary-columns"]["database_scope"]["columns"],
            ["entryDate"],
        )
        self.assertIn("db-add-summary-columns", tasks["summary-api"]["dependencies"])
        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])

    def test_invalid_graph_reader_preserves_every_registry_task(self) -> None:
        """无效 DAG 使用完整 nodes 读取，不能退化为不完整拓扑序。"""

        plan = {
            "task_registry": {
                "a": {"id": "a"},
                "b": {"id": "b"},
                "c": {"id": "c"},
            },
            "task_graph": {
                "nodes": ["a", "b", "c"],
                "topological_order": ["a"],
                "validation": {"is_valid": False, "errors": ["cycle"]},
            },
        }

        self.assertEqual(
            [task["id"] for task in tasks_from_build_task_plan(plan)],
            ["a", "b", "c"],
        )

    def test_change_scope_defaults_to_add_for_missing_file_and_modify_for_existing(self) -> None:
        """未显式声明 operation 时，按磁盘存在性决定 add/modify，避免验收 modified/added 错配。"""

        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "frontend:api-client": {"id": "frontend:api-client", "kind": "application"},
            },
            "unit_graph": {
                "nodes": ["frontend:api-client"],
                "edges": [],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        with tempfile.TemporaryDirectory() as workspace:
            # 模板工程已有的 API 文件（应判 modify）
            existing = Path(workspace) / "frontend/src/apis/service.ts"
            existing.parent.mkdir(parents=True)
            existing.write_text("// service", encoding="utf-8")
            # 业务 API 文件尚未生成（应判 add）
            plan = create_build_task_plan(
                {"version": "1.0.0"},
                agent_plan={
                    "tasks": [
                        {
                            "id": "task-api",
                            "unit_id": "frontend:api-client",
                            "owner": "frontend",
                            "description": "实现 API 模块",
                            # 故意不写 operation，触发磁盘存在性兜底
                            "change_scope": [
                                {"path": "frontend/src/apis/service.ts"},
                                {"path": "frontend/src/apis/userApi.ts"},
                            ],
                        }
                    ]
                },
                base_build_task_plan=base_plan,
                workspace_root=workspace,
            )

        task = plan["task_registry"]["task-api"]
        scope = {item["path"]: item["operation"] for item in task["change_scope"]}
        self.assertEqual(scope["frontend/src/apis/service.ts"], "modify")
        self.assertEqual(scope["frontend/src/apis/userApi.ts"], "add")

    def test_change_scope_explicit_operation_is_preserved(self) -> None:
        """模型显式声明的 add/modify/delete 一律保留，不被磁盘存在性覆盖。"""

        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "frontend:api-client": {"id": "frontend:api-client", "kind": "application"},
            },
            "unit_graph": {
                "nodes": ["frontend:api-client"],
                "edges": [],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        with tempfile.TemporaryDirectory() as workspace:
            existing = Path(workspace) / "frontend/src/apis/service.ts"
            existing.parent.mkdir(parents=True)
            existing.write_text("// service", encoding="utf-8")
            plan = create_build_task_plan(
                {"version": "1.0.0"},
                agent_plan={
                    "tasks": [
                        {
                            "id": "task-api",
                            "unit_id": "frontend:api-client",
                            "owner": "frontend",
                            "description": "实现 API 模块",
                            "change_scope": [
                                # 显式 add，即使文件已存在也保留 add
                                {"operation": "add", "path": "frontend/src/apis/service.ts"},
                                # 显式 modify，即使文件不存在也保留 modify
                                {"operation": "modify", "path": "frontend/src/apis/missing.ts"},
                            ],
                        }
                    ]
                },
                base_build_task_plan=base_plan,
                workspace_root=workspace,
            )

        task = plan["task_registry"]["task-api"]
        scope = {item["path"]: item["operation"] for item in task["change_scope"]}
        self.assertEqual(scope["frontend/src/apis/service.ts"], "add")
        self.assertEqual(scope["frontend/src/apis/missing.ts"], "modify")


if __name__ == "__main__":
    unittest.main()
