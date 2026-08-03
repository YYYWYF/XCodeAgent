from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.application_development_planning import (
    ApplicationDevelopmentPlan,
    ApplicationDevelopmentTask,
    ConfirmDevelopmentPlanRequest,
    MenuDevelopmentPlan,
    SharedDevelopmentModule,
    _datasource_type_context,
    confirm_application_development_plan,
)


def _task(task_id: str, *, kind: str = "feature", depends_on: list[str] | None = None, covers_features: list[str] | None = None) -> ApplicationDevelopmentTask:
    """构造包含最小验收信息的开发任务测试数据。"""

    return ApplicationDevelopmentTask(
        id=task_id,
        title=f"任务 {task_id}",
        description="完成对应功能并接入页面流程。",
        kind=kind,
        depends_on=depends_on or [],
        covers_features=covers_features or [],
        acceptance_criteria=["核心功能可按设计完成操作", "页面结果与预期业务状态一致"],
    )


def _application_payload() -> dict[str, object]:
    """构造同时包含目录菜单和页面菜单的 application.json。"""

    return {
        "appName": "客户中心",
        "menus": {
            "homeMenuKey": "customers",
            "items": [
                {
                    "key": "customers",
                    "label": "客户管理",
                    "path": "/customers",
                    "type": "menu",
                    "purpose": "管理客户",
                    "keyFeatures": ["客户管理"],
                    "children": [
                        {
                            "key": "customer-list",
                            "label": "客户列表",
                            "path": "/customers/list",
                            "type": "page",
                            "purpose": "查询客户",
                            "keyFeatures": ["筛选客户"],
                        }
                    ],
                }
            ],
        },
        "apis": [],
        "preserved": True,
    }


def _planning_only_application_payload() -> dict[str, object]:
    """构造只包含两阶段规划 JSON、尚未生成派生结构的应用配置。"""

    return {
        "appName": "任务中心",
        "planning": {
            "status": "confirmed",
            "requirementSpec": {"confirmation_status": "confirmed"},
            "projectPlan": {
                "confirmation_status": "confirmed",
                "frontend_pages": [{
                    "id": "tasks",
                    "name": "任务列表",
                    "path": "/tasks",
                    "description": "查看并完成任务",
                    "permissions": ["user"],
                }],
                "api_contracts": [],
                "data_sources": [],
            },
        },
        "preserved": True,
    }


class ApplicationDevelopmentPlanningTests(unittest.TestCase):
    def test_datasource_context_excludes_connection_details(self) -> None:
        """规划模型上下文只保留数据库类型，不暴露连接方案与凭据。"""

        context = _datasource_type_context({
            "type": "DataBase",
            "db": {
                "useBuiltin": False,
                "plantMode": {
                    "domain": "database.example",
                    "port": 3306,
                    "userName": "example-user",
                    "pwd": "example-password",
                    "schema": "example-schema",
                },
            },
        })

        self.assertEqual(context, {"type": "DataBase"})

    def test_confirm_derives_application_json_from_confirmed_project_plan(self) -> None:
        """开发计划确认时应补齐创建阶段延后的菜单与其他派生 JSON。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / ".xcodeagent" / "application.json"
            target.parent.mkdir()
            target.write_text(json.dumps(_planning_only_application_payload()), encoding="utf-8")
            plan = ApplicationDevelopmentPlan(
                summary="实现任务列表业务能力。",
                execution_order=["tasks-page"],
                menu_plans=[MenuDevelopmentPlan(
                    menu_key="tasks",
                    menu_label="任务列表",
                    tasks=[_task("tasks-page", covers_features=["查看并完成任务"])],
                )],
            )

            confirm_application_development_plan(
                ConfirmDevelopmentPlanRequest(workspace_root=str(workspace), selected_page_key="tasks", plan=plan)
            )
            saved = json.loads(target.read_text(encoding="utf-8"))

            self.assertTrue(saved["preserved"])
            self.assertEqual(saved["menus"]["homeMenuKey"], "tasks")
            self.assertEqual(saved["menus"]["items"][0]["developmentTasks"][0]["id"], "tasks-page")
            self.assertEqual(saved["apis"], [])
            self.assertEqual(saved["schemas"], {})
            self.assertEqual(saved["dataSources"], [])

    def test_confirm_writes_only_selected_page_tasks(self) -> None:
        """确认后应只写入用户选中页面的编号任务及其验收项。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / ".xcodeagent" / "application.json"
            target.parent.mkdir()
            target.write_text(json.dumps(_application_payload()), encoding="utf-8")
            plan = ApplicationDevelopmentPlan(
                summary="复用现有基础能力，开发客户列表。",
                execution_order=["customer-list-page"],
                menu_plans=[
                    MenuDevelopmentPlan(
                        menu_key="customer-list",
                        menu_label="客户列表",
                        tasks=[_task("customer-list-page", covers_features=["筛选客户"])],
                    ),
                ],
            )

            response = confirm_application_development_plan(
                ConfirmDevelopmentPlanRequest(workspace_root=str(workspace), selected_page_key="customer-list", plan=plan)
            )
            saved = json.loads(target.read_text(encoding="utf-8"))

            self.assertTrue(saved["preserved"])
            self.assertNotIn("developmentTasks", saved["menus"]["items"][0])
            self.assertEqual(saved["menus"]["items"][0]["children"][0]["developmentTasks"][0]["id"], "customer-list-page")
            self.assertEqual(saved["menus"]["items"][0]["children"][0]["developmentTasks"][0]["acceptanceCriteria"], ["核心功能可按设计完成操作", "页面结果与预期业务状态一致"])
            self.assertEqual(saved["menus"]["sharedModules"], [])
            self.assertEqual(response.menus["developmentPlan"]["executionOrder"][0], "customer-list-page")

    def test_confirm_rejects_new_shared_modules(self) -> None:
        """工程基础能力已具备时应拒绝模型再次规划公共模块。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / ".xcodeagent" / "application.json"
            target.parent.mkdir()
            target.write_text(json.dumps(_application_payload()), encoding="utf-8")
            plan = ApplicationDevelopmentPlan(
                summary="错误地重复建设公共请求层。",
                execution_order=["shared-api", "customers-shell", "customer-list-page"],
                shared_modules=[SharedDevelopmentModule(
                    id="api-client",
                    name="公共请求层",
                    responsibility="统一请求与错误处理。",
                    used_by_menu_keys=["customers", "customer-list"],
                    tasks=[_task("shared-api", kind="shared")],
                )],
                menu_plans=[
                    MenuDevelopmentPlan(menu_key="customers", menu_label="客户管理", tasks=[_task("customers-shell", depends_on=["shared-api"], covers_features=["客户管理"])]),
                    MenuDevelopmentPlan(menu_key="customer-list", menu_label="客户列表", tasks=[_task("customer-list-page", depends_on=["customers-shell"], covers_features=["筛选客户"])]),
                ],
            )

            with self.assertRaisesRegex(ValueError, "不得新增 sharedModules"):
                confirm_application_development_plan(
                    ConfirmDevelopmentPlanRequest(workspace_root=str(workspace), selected_page_key="customer-list", plan=plan)
                )

    def test_confirm_rejects_dependency_cycle(self) -> None:
        """确认阶段必须拒绝互相依赖的循环任务。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            target = workspace / ".xcodeagent" / "application.json"
            target.parent.mkdir()
            target.write_text(json.dumps(_application_payload()), encoding="utf-8")
            plan = ApplicationDevelopmentPlan(
                summary="无效循环计划。",
                execution_order=["customer-list-filter", "customer-list-page"],
                menu_plans=[
                    MenuDevelopmentPlan(menu_key="customer-list", menu_label="客户列表", tasks=[
                        _task("customer-list-filter", depends_on=["customer-list-page"], covers_features=["筛选客户"]),
                        _task("customer-list-page", depends_on=["customer-list-filter"]),
                    ]),
                ],
            )

            with self.assertRaisesRegex(ValueError, "循环依赖"):
                confirm_application_development_plan(
                    ConfirmDevelopmentPlanRequest(workspace_root=str(workspace), selected_page_key="customer-list", plan=plan)
                )


if __name__ == "__main__":
    unittest.main()
