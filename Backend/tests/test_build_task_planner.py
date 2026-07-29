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
from app.services.build_task_planner import create_build_task_plan, tasks_from_build_task_plan
from app.workspace.task_documents import render_build_task_dag_markdown


class BuildTaskPlannerTests(unittest.TestCase):
    def test_task_prompt_reserves_cross_unit_dependencies_for_unit_graph(self) -> None:
        """任务模型不得手写跨 Unit 或 reusable task 依赖。"""

        prompt = _task_preparation_prompt(
            {"version": "1.0.0"},
            {},
            {
                "target": {"type": "page", "id": "dashboard"},
                "reusable_tasks_by_unit": {
                    "app:api-client": ["shared-api-task"]
                },
            },
        )

        self.assertIn("same Unit only", prompt)
        self.assertIn("do not copy its task ids into dependencies", prompt)

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
            "schema_version": "build-dag.v2",
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
        self.assertEqual(page_task["targetFiles"], ["frontend/src/pages/Dashboard/index.tsx"])
        self.assertEqual(page_task["change_scope"][0]["operation"], "modify")
        self.assertEqual(
            page_task["path_reconciliation"]["canonical_path"],
            "frontend/src/pages/Dashboard/index.tsx",
        )
        self.assertEqual(route_task["targetFiles"], ["frontend/src/constants/menus.ts"])
        self.assertEqual(route_task["dependencies"], ["page-layout"])
        self.assertIn("key: 'Dashboard'", route_task["description"])
        self.assertIn("path: 'page'", route_task["description"])
        self.assertIn("BIZ_MENUS 顶层数组", route_task["description"])
        self.assertNotIn("firstLevel.children", route_task["description"])

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
            "schema_version": "build-dag.v2",
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
  children: [{ path: 'page', name: '概览页', key: 'DashboardPage' }]
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
                "[{ path: 'page', name: '概览页', key: 'DashboardPage' }] }];",
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
        self.assertEqual(menu_task["targetFiles"], ["frontend/src/constants/menus.ts"])
        self.assertEqual(menu_task["change_scope"][0]["description"], "仅向 BIZ_MENUS 顶层数组追加当前页面菜单项。")
        self.assertIn("BIZ_MENUS 顶层数组", menu_task["description"])
        self.assertIn("path: 'dashboard'", menu_task["description"])
        self.assertNotIn("/page/dashboard", menu_task["description"])
        self.assertNotIn("firstLevel.children", menu_task["description"])
        self.assertIn("新增菜单项 path 为 dashboard", menu_task["acceptance_criteria"][2])

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
            plan["build_units"]["data-source:orders"]["source_refs"],
            {
                "type": "endpoint_detail",
                "target": {"type": "page", "id": "orders"},
                "endpoint_details": [],
                "endpoint_ids": ["orders_api.list"],
            },
        )
        self.assertTrue(plan["build_units"]["page:orders"]["input_fingerprint"])

    def test_unit_graph_rewrites_reverse_dependencies_and_excludes_verification_tasks(self) -> None:
        """复现多任务计划，跨 Unit 反向边被改写且纯验证任务不进入注册表。"""

        base_plan = {
            "schema_version": "build-dag.v2",
            "build_units": {
                "app:backend-bootstrap": {"id": "app:backend-bootstrap", "kind": "application"},
                "data-source:core": {"id": "data-source:core", "kind": "data_source"},
                "data-source:user": {"id": "data-source:user", "kind": "data_source"},
                "app:api-client": {"id": "app:api-client", "kind": "application"},
                "page:core": {"id": "page:core", "kind": "page"},
            },
            "unit_graph": {
                "nodes": [
                    "app:backend-bootstrap",
                    "data-source:core",
                    "data-source:user",
                    "app:api-client",
                    "page:core",
                ],
                "edges": [
                    {"from": "app:backend-bootstrap", "to": "data-source:core", "type": "depends_on"},
                    {"from": "app:backend-bootstrap", "to": "data-source:user", "type": "depends_on"},
                    {"from": "app:api-client", "to": "page:core", "type": "depends_on"},
                    {"from": "data-source:core", "to": "page:core", "type": "depends_on"},
                ],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        code_tasks = [
            ("core", "data-source:core", "data_source", "Backend/core.py"),
            ("user", "data-source:user", "data_source", "Backend/user.py"),
            ("bootstrap", "app:backend-bootstrap", "data_source", "Backend/main.py"),
            ("client", "app:api-client", "frontend", "Frontend/api.ts"),
            ("page", "page:core", "frontend", "Frontend/Core.tsx"),
        ]
        agent_tasks = [
            {
                "id": task_id,
                "unit_id": unit_id,
                "owner": owner,
                "description": task_id,
                "dependencies": ["core", "user"] if task_id == "bootstrap" else [],
                "change_scope": [{"operation": "modify", "path": path}],
            }
            for task_id, unit_id, owner, path in code_tasks
        ]
        agent_tasks.extend(
            [
                {"id": "verify-shell", "unit_id": "app:frontend-shell", "owner": "frontend", "description": "验证壳", "change_scope": []},
                {"id": "verify-route", "unit_id": "app:route-registry", "owner": "frontend", "description": "验证路由", "change_scope": []},
            ]
        )

        plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={"tasks": agent_tasks},
            base_build_task_plan=base_plan,
        )
        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}

        self.assertEqual(set(tasks), {"core", "user", "bootstrap", "client", "page"})
        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])
        self.assertEqual(tasks["bootstrap"]["dependencies"], [])
        self.assertEqual(
            {item["dependency"] for item in tasks["bootstrap"]["dependency_rewrites"]},
            {"core", "user"},
        )
        self.assertEqual(tasks["core"]["dependencies"], ["bootstrap"])

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


if __name__ == "__main__":
    unittest.main()
