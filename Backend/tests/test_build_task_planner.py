from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from app.services.build_task_planner import (
    _authorization_coverage_errors,
    _database_task_requires_approval,
    build_task_candidate_contract_errors,
    create_build_task_plan,
    frontend_endpoint_implementation_owners,
    frontend_endpoint_ownership_errors,
    merge_exact_duplicate_tasks,
    retained_frontend_endpoint_owner_conflict_errors,
    tasks_from_build_task_plan,
)
from app.services.build_unit_compiler import annotate_unit_inputs


def _test_deliverable(task_id: str, unit_id: str, owner: str, path: str) -> dict:
    """为任务规划测试构造符合当前 DAG 契约的最小交付物。"""

    if owner == "frontend":
        kind = "frontend.shared_capability"
    else:
        kind = "backend.bootstrap" if unit_id == "backend:bootstrap" else "backend.application_service"
    return {
        "id": f"deliverable:{task_id}",
        "kind": kind,
        "target_id": unit_id,
        "paths": [path],
        "provides": [f"{task_id}.implementation"],
    }


def _endpoint_design(
    *source_types: str,
    contract_id: str = "orders-api",
    endpoint_id: str = "orders.list",
    entity_id: str = "Order",
) -> dict:
    """构造供任务规划测试使用的自包含字段映射产物。"""

    snapshots: list[dict] = []
    field_mappings: list[dict] = []
    for index, source_type in enumerate(source_types):
        source_id = f"{source_type}-{index}"
        field_name = f"{source_type}{index}"
        if source_type == "database":
            details = {"table": "orders", "columns": ["order_id"]}
            source_field = {
                "sourceType": "database",
                "sourceId": source_id,
                "schema": "app",
                "table": "orders",
                "column": "order_id",
                "type": "string",
                "usage": "read",
            }
        else:
            details = {
                "connection": {
                    "baseUrl": "https://api.example.com",
                    "baseUrlConfigKey": "upstream.url",
                    "timeoutMs": 10000,
                    "headers": [],
                },
                "operation": {
                    "operationId": f"{endpoint_id}-operation",
                    "method": "GET",
                    "path": "/values",
                    "pathParameters": [],
                    "queryParameters": [],
                    "requestStructure": {},
                    "responseStructure": {},
                },
            }
            source_field = {
                "sourceType": "external_api",
                "sourceId": source_id,
                "directoryId": "upstream-directory",
                "operationId": f"{endpoint_id}-operation",
                "section": "response_body",
                "path": "value",
                "type": "string",
            }
        snapshots.append({
            "sourceType": source_type,
            "sourceId": source_id,
            "name": source_id,
            "details": details,
        })
        field_mappings.append({
            "endpointField": {
                "side": "response",
                "location": "response_body",
                "path": f"result.{field_name}",
                "type": "string",
                "required": True,
                "description": "",
            },
            "mappingType": "source_mapping",
            "processingType": "direct", "sourceFields": [source_field],
        })
    return {
        "schemaVersion": "endpoint-field-mapping.v3",
        "artifactType": "endpoint-field-mapping",
        "status": "confirmed",
        "confirmationStatus": "confirmed",
        "apiContractId": contract_id,
        "endpointId": endpoint_id,
        "endpointContract": {"id": endpoint_id, "method": "GET", "path": "/values"},
        "fieldMappings": field_mappings,
        "sourceSnapshots": snapshots,
        "basedOn": [{"artifactKey": "technical-plan", "sha256": "a" * 64}],
        "confirmedAt": "2026-09-04T00:00:00Z",
    }


class BuildTaskPlannerTests(unittest.TestCase):




    def test_raw_candidate_reports_precise_deliverable_shape_errors(self) -> None:
        """原始候选必须在归一化前报告缺失字段和不受支持的单数 path。"""

        errors = build_task_candidate_contract_errors(
            {
                "tasks": [
                    {
                        "id": "task-page-api",
                        "unit_id": "page:test-page-1",
                        "owner": "frontend",
                        "deliverables": [
                            {
                                "kind": "frontend.api_module",
                                "path": "frontend/src/apis/testPage1.ts",
                            }
                        ],
                    }
                ]
            }
        )

        self.assertIn("Task task-page-api deliverables[0].id is required.", errors)
        self.assertIn(
            "Task task-page-api deliverables[0].target_id is required.", errors
        )
        self.assertIn(
            'Task task-page-api deliverables[0].paths must be a non-empty string array; singular field "path" is not supported.',
            errors,
        )
        self.assertIn(
            "Task task-page-api deliverables[0].provides must be a non-empty string array.",
            errors,
        )



















    def test_unit_inputs_filter_endpoint_designs_by_identity(self) -> None:
        """后端 Endpoint Unit 只携带自身复合标识对应的 API 设计。"""

        selected = _endpoint_design(
            "database",
            "external_api",
            contract_id="dashboard-api",
            endpoint_id="dashboard.get",
        )
        unrelated = _endpoint_design(
            "database",
            contract_id="notice-api",
            endpoint_id="notice.list",
        )
        build_context = {
            "target": {"type": "endpoint", "id": "dashboard.get"},
            "required_unit_ids": [
                "backend:endpoint:dashboard-api:dashboard.get",
                "frontend:data:static",
            ],
            "endpoint_ids": ["dashboard.get"],
            "entity_ids": ["Order"],
            "endpoint_designs": [selected, unrelated],
            "source_refs": {},
        }
        units = annotate_unit_inputs(
            {
                "backend:endpoint:dashboard-api:dashboard.get": {
                    "id": "backend:endpoint:dashboard-api:dashboard.get",
                    "kind": "backend",
                    "task_ids": [],
                },
                "frontend:data:static": {
                    "id": "frontend:data:static",
                    "kind": "frontend",
                    "task_ids": [],
                },
            },
            build_context,
            {},
        )

        backend_designs = units[
            "backend:endpoint:dashboard-api:dashboard.get"
        ]["source_refs"]["endpoint_designs"]
        self.assertEqual(
            [item["endpointId"] for item in backend_designs],
            ["dashboard.get"],
        )
        self.assertNotIn(
            "endpoint_designs",
            units["frontend:data:static"]["source_refs"],
        )
        self.assertNotEqual(
            units["backend:endpoint:dashboard-api:dashboard.get"]["input_fingerprint"],
            units["frontend:data:static"]["input_fingerprint"],
        )

    def test_page_backend_units_scope_external_operations_to_their_endpoint(self) -> None:
        """页面同时规划多个接口时，每个后端 Unit 只获得自身上游 operation。"""

        endpoint_designs = [
            _endpoint_design(
                "external_api",
                contract_id="product_api",
                endpoint_id=endpoint_id,
                entity_id="Product",
            )
            for endpoint_id in ("product_api.list", "product_api.detail")
        ]
        build_context = {
            "target": {"type": "page", "id": "products"},
            "required_unit_ids": [
                "backend:endpoint:product_api:product_api.list",
                "backend:endpoint:product_api:product_api.detail",
            ],
            "endpoint_ids": ["product_api.list", "product_api.detail"],
            "entity_ids": ["Product"],
            "endpoint_designs": endpoint_designs,
            "source_refs": {
                "technical_plan_endpoints": [
                    {"id": "product_api.list", "api_contract_id": "product_api"},
                    {"id": "product_api.detail", "api_contract_id": "product_api"},
                ]
            },
        }
        units = annotate_unit_inputs(
            {
                unit_id: {"id": unit_id, "kind": "backend", "task_ids": []}
                for unit_id in build_context["required_unit_ids"]
            },
            build_context,
            {},
        )

        for endpoint_id, operation_id in (
            ("product_api.list", "product-list"),
            ("product_api.detail", "product-detail"),
        ):
            refs = units[
                f"backend:endpoint:product_api:{endpoint_id}"
            ]["source_refs"]
            self.assertEqual(refs["endpoint_ids"], [endpoint_id])
            self.assertEqual(refs["target"]["id"], endpoint_id)
            design = refs["endpoint_designs"][0]
            self.assertEqual(
                design["sourceSnapshots"][0]["details"]["operation"]["operationId"],
                f"{endpoint_id}-operation",
            )
            self.assertEqual(len(refs["endpoint_designs"]), 1)

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
        self.assertEqual(set(tasks), {"pet-data-view"})
        self.assertNotIn("page:pet_list_page:route-menu-registration", tasks)
        self.assertNotIn(
            "frontend/src/pages/PetListPage/index.tsx",
            page_task["target_files"],
        )
        self.assertNotIn(
            "frontend/src/pages/PetListPage/index.tsx",
            [change["path"] for change in page_task["change_scope"]],
        )

    def test_scaffolded_menu_entry_excludes_model_menu_task(self) -> None:
        """脚手架已注册精确菜单项时，模型菜单任务直接不进入 Build DAG。"""

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
            "required_unit_ids": ["frontend:shell", "page:dashboard_page"],
        }
        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "frontend:shell": {"id": "frontend:shell", "kind": "frontend"},
                "page:dashboard_page": {"id": "page:dashboard_page", "kind": "page"},
            },
            "unit_graph": {
                "nodes": ["frontend:shell", "page:dashboard_page"],
                "edges": [
                    {
                        "from": "frontend:shell",
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
                            "unit_id": "frontend:shell",
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
        self.assertIn("task-menu-register-dashboard", tasks)
        self.assertIn("page-layout", tasks)
        self.assertFalse(plan["task_graph"]["validation"]["is_valid"])
        self.assertIn(
            "frontend/src/constants/menus.ts",
            str(plan["task_graph"]["validation"]["errors"]),
        )
        self.assertEqual(plan["summary"].get("already_satisfied", 0), 0)

    def test_mixed_page_task_template_boundary_violation_is_visible(self) -> None:
        """页面任务越界修改菜单时必须保留原候选并暴露 DAG 校验错误。"""

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
                "frontend/src/constants/menus.ts",
            ],
        )
        self.assertIn("frontend/src/constants/menus.ts", task["allowed_paths"])
        self.assertIn(
            "frontend/src/constants/menus.ts",
            str(plan["task_graph"]["validation"]["errors"]),
        )
        self.assertFalse(plan["task_graph"]["validation"]["is_valid"])
        self.assertNotIn("pre_satisfied_targets", task)

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

    def test_model_menu_task_is_rejected_by_dag_validation(self) -> None:
        """模型返回菜单任务时，DAG 必须拒绝候选而不是静默删除。"""

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

        self.assertEqual(
            [task["id"] for task in tasks_from_build_task_plan(plan)],
            ["task-menu-register-dashboard", "page-layout"],
        )
        self.assertFalse(plan["task_graph"]["validation"]["is_valid"])
        self.assertIn(
            "frontend/src/constants/menus.ts",
            str(plan["task_graph"]["validation"]["errors"]),
        )

    def test_missing_page_entry_is_not_injected_by_dag(self) -> None:
        """DAG 不因模型漏写入口而创建页面占位或菜单注册任务。"""

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
        self.assertEqual(list(tasks), ["task-api"])
        self.assertNotIn("frontend/src/constants/menus.ts", str(tasks))

    def test_v3_plan_contains_json_confirmation_fields(self) -> None:
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

        self.assertEqual(plan["schema_version"], "build-dag.v3")
        self.assertEqual(plan["confirmation_status"], "pending")
        self.assertIsNone(plan["confirmed_at"])
        self.assertEqual(plan["build_execution_scope"], {})
        self.assertIn("page-home", plan["task_registry"])

    def test_exact_duplicate_tasks_merge_dependencies_and_source_refs(self) -> None:
        tasks = merge_exact_duplicate_tasks(
            [
                {
                    "id": "task-a",
                    "owner": "frontend",
                    "unit_id": "page:home",
                    "task_type": "frontend.code",
                    "target_files": ["frontend/src/pages/Home/index.tsx"],
                    "change_scope": [{"operation": "modify", "path": "frontend/src/pages/Home/index.tsx"}],
                    "dependencies": ["shared"],
                    "source_refs": {"pages": [{"id": "home"}]},
                },
                {
                    "id": "task-b",
                    "owner": "frontend",
                    "unit_id": "page:home",
                    "task_type": "frontend.code",
                    "target_files": ["frontend/src/pages/Home/index.tsx"],
                    "change_scope": [{"operation": "modify", "path": "frontend/src/pages/Home/index.tsx"}],
                    "dependencies": ["task-a", "api"],
                    "source_refs": {"pages": [{"id": "home"}], "contracts": [{"id": "home-api"}]},
                },
            ]
        )

        self.assertEqual([task["id"] for task in tasks], ["task-a"])
        self.assertEqual(tasks[0]["dependencies"], ["shared", "api"])
        self.assertEqual(len(tasks[0]["source_refs"]["pages"]), 1)
        self.assertEqual(tasks[0]["source_refs"]["contracts"], [{"id": "home-api"}])

    def test_task_graph_rejects_duplicate_frontend_endpoint_owners(self) -> None:
        """不同 API 文件重复实现同一 Endpoint 时必须阻断候选 DAG。"""

        project_plan = {
            "api_contracts": [
                {
                    "id": "role_api",
                    "endpoints": [
                        {
                            "id": "role_api.list",
                            "method": "GET",
                            "path": "/api/roles",
                            "parameters": [],
                        }
                    ],
                    "schemas": {},
                }
            ]
        }
        tasks = [
            {
                "id": task_id,
                "unit_id": unit_id,
                "owner": "frontend",
                "description": f"实现 {task_id}",
                "change_scope": [{"operation": "add", "path": path}],
                "deliverables": [
                    {
                        "id": f"deliverable:{task_id}",
                        "kind": "frontend.api_module",
                        "target_id": task_id,
                        "paths": [path],
                        "provides": [f"{task_id}.api"],
                    }
                ],
            }
            for task_id, unit_id, path in (
                ("home-api", "frontend:api-client", "frontend/src/apis/homeApi.ts"),
                ("role-api", "frontend:shell", "frontend/src/apis/role.ts"),
            )
        ]

        plan = create_build_task_plan(
            project_plan,
            agent_plan={"tasks": tasks},
            build_context={
                "required_unit_ids": ["frontend:api-client", "frontend:shell"],
                "endpoint_ids": ["role_api.list"],
            },
        )

        self.assertEqual(plan["status"], "blocked")
        errors = str(plan["task_graph"]["validation"]["errors"])
        self.assertIn("role_api + role_api.list", errors)
        self.assertIn("home-api (frontend/src/apis/homeApi.ts)", errors)
        self.assertIn("role-api (frontend/src/apis/role.ts)", errors)

    def test_frontend_endpoint_owner_validation_ignores_repair_task(self) -> None:
        """同一路径的父任务与受限 Repair 不得被误判为两个实现 owner。"""

        check = {
            "kind": "frontend.api_contract",
            "target_paths": ["frontend/src/apis/roleApi.ts"],
            "expected": {
                "endpoints": [
                    {
                        "api_contract_id": "role_api",
                        "endpoint_id": "role_api.list",
                    }
                ]
            },
        }
        tasks = [
            {
                "id": "role-api",
                "owner": "frontend",
                "business_acceptance_checks": [check],
            },
            {
                "id": "repair:role-api:business",
                "kind": "repair",
                "owner": "frontend",
                "business_acceptance_checks": [check],
            },
        ]

        self.assertEqual(frontend_endpoint_ownership_errors(tasks), [])

    def test_frontend_page_endpoint_usage_does_not_claim_implementation_owner(self) -> None:
        """页面消费检查不得被误计为 API 模块实现 owner。"""

        page_usage = {
            "kind": "frontend.page_endpoint_usage",
            "expected": {
                "endpoints": [
                    {
                        "api_contract_id": "personal_info_api",
                        "endpoint_id": "personal_info_api.query",
                    }
                ]
            },
        }
        tasks = [
            {
                "id": "page:personal-info::page",
                "unit_id": "page:personal-info",
                "owner": "frontend",
                "business_acceptance_checks": [page_usage],
            }
        ]

        self.assertEqual(frontend_endpoint_implementation_owners(tasks), [])
        self.assertEqual(frontend_endpoint_ownership_errors(tasks), [])

    def test_candidate_compilation_rejects_retained_endpoint_owner_constraint(self) -> None:
        """候选编译后的业务检查必须与临时 retained owner 表交叉校验。"""

        plan = create_build_task_plan(
            {
                "api_contracts": [
                    {
                        "id": "personal_info_api",
                        "endpoints": [
                            {
                                "id": "personal_info_api.query",
                                "method": "GET",
                                "path": "/api/personal-info",
                                "parameters": [],
                            }
                        ],
                        "schemas": {},
                    }
                ]
            },
            agent_plan={
                "tasks": [
                    {
                        "id": "task_page_personal_info_query",
                        "unit_id": "page:page_personal_info_query",
                        "owner": "frontend",
                        "description": "重复实现个人信息 API。",
                        "change_scope": ["frontend/src/apis/personalInfoPage.ts"],
                        "deliverables": [
                            {
                                "id": "page-personal-info-api",
                                "kind": "frontend.api_module",
                                "target_id": "personal_info_api.query",
                                "paths": ["frontend/src/apis/personalInfoPage.ts"],
                                "provides": ["personal_info_api.query"],
                            }
                        ],
                    }
                ]
            },
            build_context={
                "required_unit_ids": ["page:page_personal_info_query"],
                "endpoint_ids": ["personal_info_api.query"],
                "frontend_endpoint_owner_constraints": [
                    {
                        "api_contract_id": "personal_info_api",
                        "endpoint_id": "personal_info_api.query",
                        "owner_task_id": "frontend:api-client::personalInfoApi",
                        "owner_unit_id": "frontend:api-client",
                        "policy": "reuse_only",
                    }
                ],
            },
        )

        errors = plan["task_graph"]["validation"]["errors"]
        self.assertTrue(
            any("retained_frontend_endpoint_owner_conflict" in error for error in errors)
        )

    def test_retained_endpoint_owner_constraint_has_no_path_and_blocks_candidate_owner(self) -> None:
        """规划约束只携带稳定 owner 身份，并确定性拒绝候选重复实现。"""

        check = {
            "kind": "frontend.api_contract",
            "target_paths": ["frontend/src/apis/personalInfo.ts"],
            "expected": {
                "endpoints": [
                    {
                        "api_contract_id": "personal_info_api",
                        "endpoint_id": "personal_info_api.query",
                    }
                ]
            },
        }
        retained_task = {
            "id": "frontend:api-client::personalInfoApi",
            "unit_id": "frontend:api-client",
            "owner": "frontend",
            "business_acceptance_checks": [check],
        }
        owners = frontend_endpoint_implementation_owners([retained_task])
        constraint = dict(owners[0])
        constraint["policy"] = "reuse_only"
        candidate_task = {
            "id": "task_page_personal_info_query",
            "unit_id": "page:page_personal_info_query",
            "owner": "frontend",
            "business_acceptance_checks": [check],
        }

        self.assertNotIn("planned_paths", constraint)
        errors = retained_frontend_endpoint_owner_conflict_errors(
            [candidate_task],
            [constraint],
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("retained_frontend_endpoint_owner_conflict", errors[0])
        self.assertIn("frontend:api-client::personalInfoApi", errors[0])
        self.assertNotIn("personalInfo.ts", errors[0])

    def test_ordinary_candidate_cannot_emit_repair_task(self) -> None:
        """普通 DAG 规划中的 repair 必须作为协议错误进入自动重生成。"""

        errors = build_task_candidate_contract_errors(
            {
                "tasks": [
                    {
                        "id": "repair:page",
                        "kind": "repair",
                        "unit_id": "page:dashboard",
                        "owner": "frontend",
                    }
                ]
            }
        )

        self.assertIn(
            "Task repair:page must not use kind=repair in ordinary Build DAG planning.",
            errors,
        )


    def test_compiles_unit_dependencies_and_source_refs(self) -> None:
        """页面任务只继承前端公共 Unit，后端 Unit 仅保留接口来源引用。"""

        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "frontend:api-client": {"id": "frontend:api-client", "kind": "frontend"},
                "backend:endpoint:orders-api:orders_api.list": {
                    "id": "backend:endpoint:orders-api:orders_api.list",
                    "kind": "backend",
                },
                "page:orders": {"id": "page:orders", "kind": "page"},
            },
            "unit_graph": {
                "schema_version": "build-unit-graph.v3",
                "nodes": [
                    "frontend:api-client",
                    "backend:endpoint:orders-api:orders_api.list",
                    "page:orders",
                ],
                "edges": [
                    {"from": "frontend:api-client", "to": "page:orders", "type": "depends_on"},
                    {
                        "from": "backend:endpoint:orders-api:orders_api.list",
                        "to": "page:orders",
                        "type": "depends_on",
                    },
                ],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        build_context = {
            "target": {"type": "page", "id": "orders"},
            "required_unit_ids": [
                "frontend:api-client",
                "backend:endpoint:orders-api:orders_api.list",
                "page:orders",
            ],
            "endpoint_ids": ["orders_api.list"],
            "source_refs": {
                "page_implementation_contract": {
                    "id": "orders",
                    "ui_design_path": ".xcodeagent/ui-design/pages/Orders/index.tsx",
                    "ui_design_sha256": "p1",
                },
                "technical_plan_endpoints": [
                    {"id": "orders_api.list", "api_contract_id": "orders-api"}
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
                        "unit_id": "backend:endpoint:orders-api:orders_api.list",
                        "owner": "backend",
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
        self.assertEqual(
            tasks["task:orders-page"]["source_refs"]["type"],
            "page_implementation_contract",
        )
        self.assertEqual(
            plan["build_units"]["backend:endpoint:orders-api:orders_api.list"]["source_refs"],
            {
                "type": "technical_plan_endpoint",
                "target": {
                    "type": "endpoint",
                    "id": "orders_api.list",
                    "api_contract_id": "orders-api",
                },
                "technical_plan_endpoint": {
                    "id": "orders_api.list",
                    "api_contract_id": "orders-api",
                },
                "technical_plan_endpoints": [
                    {"id": "orders_api.list", "api_contract_id": "orders-api"}
                ],
                "endpoint_ids": ["orders_api.list"],
                "entity_ids": [],
                "endpoint_designs": [],
                "mapping_flows": [],
            },
        )
        self.assertTrue(plan["build_units"]["page:orders"]["input_fingerprint"])

    def test_unit_graph_rewrites_reverse_dependencies_and_excludes_verification_tasks(self) -> None:
        """复现多任务计划，跨 Unit 反向边被改写且纯验证任务不进入注册表。"""

        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "backend:bootstrap": {"id": "backend:bootstrap", "kind": "backend"},
                "backend:core": {"id": "backend:core", "kind": "backend"},
                "backend:user": {"id": "backend:user", "kind": "backend"},
                "frontend:api-client": {"id": "frontend:api-client", "kind": "frontend"},
                "page:core": {"id": "page:core", "kind": "page"},
            },
            "unit_graph": {
                "nodes": [
                    "backend:bootstrap",
                    "backend:core",
                    "backend:user",
                    "frontend:api-client",
                    "page:core",
                ],
                "edges": [
                    {"from": "backend:core", "to": "backend:bootstrap", "type": "depends_on"},
                    {"from": "backend:user", "to": "backend:bootstrap", "type": "depends_on"},
                    {"from": "frontend:api-client", "to": "page:core", "type": "depends_on"},
                    {"from": "backend:core", "to": "page:core", "type": "depends_on"},
                ],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        code_tasks = [
            ("core", "backend:core", "backend", "Backend/Core.py"),
            ("user", "backend:user", "backend", "Backend/User.py"),
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
                "deliverables": [_test_deliverable(task_id, unit_id, owner, path)],
            }
            for task_id, unit_id, owner, path in code_tasks
        ]
        agent_tasks.extend(
            [
                {"id": "verify-shell", "unit_id": "frontend:shell", "owner": "frontend", "description": "验证壳", "change_scope": []},
                {"id": "verify-route", "unit_id": "frontend:shell", "owner": "frontend", "description": "验证路由", "change_scope": []},
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
        """数据库候选保留在 DAG 中并显式暴露代码职责越界。"""

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
            build_context={},
        )
        self.assertFalse(plan["task_graph"]["validation"]["is_valid"])
        self.assertIn("must not modify code files", str(plan["task_graph"]["validation"]["errors"]))

    def test_normal_build_scope_rejects_database_task(self) -> None:
        """实体确认已完成数据库操作后，正常 Build 显式拒绝 database Unit 候选。"""

        plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={
                "tasks": [
                    {
                        "id": "users-db",
                        "unit_id": "database:users",
                        "owner": "database",
                        "task_type": "database.change",
                        "description": "重复创建用户表。",
                        "database_scope": {
                            "data_source_id": "users",
                            "operations": ["create_table"],
                        },
                        "change_scope": [],
                    }
                ]
            },
            base_build_task_plan={
                "schema_version": "build-dag.v3",
                "build_units": {
                    "backend:endpoint:user_api:user.list": {
                        "id": "backend:endpoint:user_api:user.list",
                        "kind": "backend",
                    }
                },
                "unit_graph": {
                    "nodes": ["backend:endpoint:user_api:user.list"],
                    "edges": [],
                    "validation": {"is_valid": True, "errors": []},
                },
            },
            build_context={
                "required_unit_ids": ["backend:endpoint:user_api:user.list"]
            },
        )
        self.assertFalse(plan["task_graph"]["validation"]["is_valid"])
        self.assertIn("outside the current Build scope", str(plan["task_graph"]["validation"]["errors"]))

    def test_entity_backed_endpoint_and_page_allow_parallelism(self) -> None:
        """实体数据库操作完成后，endpoint 与 page 仍按代码 Unit 并行编译。"""

        plan = create_build_task_plan(
            {"version": "1.0.0"},
            agent_plan={
                "tasks": [
                    {
                        "id": "users-api",
                        "unit_id": "backend:endpoint:user_api:user.create",
                        "owner": "backend",
                        "description": "实现用户创建接口。",
                        "deliverables": [
                            {
                                "id": "controller:user-create",
                                "kind": "backend.endpoint_controller",
                                "target_id": "user.create",
                                "paths": ["Backend/UserApi.java"],
                                "provides": ["user.create.endpoint"],
                            }
                        ],
                        "change_scope": [
                            {"operation": "modify", "path": "Backend/UserApi.java"}
                        ],
                    },
                    {
                        "id": "users-page",
                        "unit_id": "page:users",
                        "owner": "frontend",
                        "description": "实现用户页面。",
                        "deliverables": [
                            {
                                "id": "capability:users-page",
                                "kind": "frontend.shared_capability",
                                "target_id": "users",
                                "paths": ["frontend/src/pages/Users.tsx"],
                                "provides": ["users.page"],
                            }
                        ],
                        "change_scope": [
                            {"operation": "modify", "path": "frontend/src/pages/Users.tsx"}
                        ],
                    }
                ]
            },
            base_build_task_plan={
                "schema_version": "build-dag.v3",
                "build_units": {
                    "backend:endpoint:user_api:user.create": {
                        "id": "backend:endpoint:user_api:user.create",
                        "kind": "backend",
                    },
                    "page:users": {"id": "page:users", "kind": "page"},
                },
                "unit_graph": {
                    "nodes": ["backend:endpoint:user_api:user.create", "page:users"],
                    "edges": [
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
                "required_unit_ids": [
                    "backend:endpoint:user_api:user.create",
                    "page:users",
                ],
            },
        )

        tasks = {task["id"]: task for task in tasks_from_build_task_plan(plan)}
        self.assertNotIn("users-api", tasks["users-page"]["dependencies"])
        self.assertEqual(tasks["users-page"]["dependencies"], [])
        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])

    def test_database_task_is_excluded_from_normal_build(self) -> None:
        """数据库候选不再被删除，缺少数据库范围时必须显式校验失败。"""

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
                    }
                ]
            },
            base_build_task_plan={
                "schema_version": "build-dag.v3",
                "build_units": {
                    "database:core": {"id": "database:core", "kind": "database"},
                },
                "unit_graph": {
                    "nodes": ["database:core"],
                    "edges": [],
                    "validation": {"is_valid": True, "errors": []},
                },
            },
            build_context={
                "required_unit_ids": ["database:core"],
            },
        )
        self.assertFalse(plan["task_graph"]["validation"]["is_valid"])
        self.assertIn("database_scope", str(plan["task_graph"]["validation"]["errors"]))

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

    def test_change_scope_explicit_add_or_modify_is_normalized_by_file_existence(self) -> None:
        """显式 add/modify 也必须由工作区文件存在性统一归一化。"""

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
                                # 显式 add，但文件存在，必须纠正为 modify。
                                {"operation": "add", "path": "frontend/src/apis/service.ts"},
                                # 显式 modify，但文件不存在，必须纠正为 add。
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
        self.assertEqual(scope["frontend/src/apis/service.ts"], "modify")
        self.assertEqual(scope["frontend/src/apis/missing.ts"], "add")

    def test_authorization_page_accepts_matching_page_unit(self) -> None:
        """页面权限约束存在匹配 Page Unit 时不应产生校验错误。"""

        self.assertEqual(
            _authorization_coverage_errors(
                [
                    {
                        "id": "page:page_personal_assets::page",
                        "unit_id": "page:page_personal_assets",
                        "owner": "frontend",
                    }
                ],
                {
                    "authorization_constraints": {
                        "pages": [{"pageId": "page_personal_assets"}]
                    }
                },
            ),
            [],
        )

    def test_authorization_page_reports_missing_page_unit(self) -> None:
        """页面权限约束缺少匹配 Page Unit 时应返回可诊断的校验错误。"""

        self.assertEqual(
            _authorization_coverage_errors(
                [],
                {
                    "authorization_constraints": {
                        "pages": [{"pageId": "page_personal_assets"}]
                    }
                },
            ),
            [
                "Authorization page page_personal_assets is missing its page Unit task."
            ],
        )

    def test_authorization_endpoint_requires_controller_only_for_required_backend_unit(self) -> None:
        """权限 endpoint 仅在当前后端 Unit 内要求 Controller 交付物。"""

        unit_id = "backend:endpoint:inbound_api:inbound_api.submit"
        constraints = {
            "endpoints": [
                {
                    "apiContractId": "inbound_api",
                    "endpointId": "inbound_api.submit",
                    "operationResourceKeys": ["inbound_submit"],
                }
            ]
        }
        service_only_task = {
            "id": "inbound-service",
            "unit_id": unit_id,
            "owner": "backend",
            "deliverables": [{"kind": "backend.application_service"}],
        }
        controller_task = {
            "id": "inbound-controller",
            "unit_id": unit_id,
            "owner": "backend",
            "deliverables": [{"kind": "backend.endpoint_controller"}],
        }

        self.assertEqual(
            _authorization_coverage_errors(
                [service_only_task],
                {
                    "required_unit_ids": [unit_id],
                    "authorization_constraints": constraints,
                },
            ),
            [
                "Authorization endpoint inbound_api:inbound_api.submit is missing its "
                "Controller implementation task."
            ],
        )
        self.assertEqual(
            _authorization_coverage_errors(
                [controller_task],
                {
                    "required_unit_ids": [unit_id],
                    "authorization_constraints": constraints,
                },
            ),
            [],
        )
        self.assertEqual(
            _authorization_coverage_errors(
                [],
                {
                    "required_unit_ids": ["frontend:data:static"],
                    "authorization_constraints": constraints,
                },
            ),
            [],
        )










    def test_prepared_bootstrap_is_not_required_in_incremental_candidate(self) -> None:
        """本轮 planning_unit_ids 不含已准备 bootstrap 时不产生遗漏错误。"""

        plan = create_build_task_plan(
            {"executable_details": {}},
            agent_plan={
                "tasks": [
                    {
                        "id": "orders-api",
                        "unit_id": "backend:endpoint:orders-api:orders.list",
                        "owner": "backend",
                        "description": "实现订单接口。",
                        "change_scope": [
                            {
                                "operation": "add",
                                "path": "backend/src/main/java/demo/OrdersController.java",
                            }
                        ],
                        "deliverables": [
                            {
                                "id": "controller:orders-list",
                                "kind": "backend.endpoint_controller",
                                "target_id": "orders.list",
                                "paths": [
                                    "backend/src/main/java/demo/OrdersController.java"
                                ],
                                "provides": ["orders.list.endpoint"],
                            }
                        ],
                    }
                ]
            },
            build_context={
                "planning_unit_ids": ["backend:endpoint:orders-api:orders.list"],
                "required_unit_ids": [
                    "backend:bootstrap",
                    "backend:endpoint:orders-api:orders.list",
                ],
            },
        )

        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])


    def test_platform_derives_execution_metadata_without_model_parallelism_fields(self) -> None:
        """任务候选不携带并行决策，执行元数据必须由平台按文件事实推导。"""

        project_plan = {"version": "1.0.0", "page_detail_plans": [], "data_sources": []}
        agent_plan = {
            "tasks": [
                {
                    "id": "page-login",
                    "unit_id": "page:login",
                    "owner": "frontend",
                    "title": "新增登录页",
                    "description": "实现登录表单与提交状态。",
                    "dependencies": [],
                    "change_scope": [
                        {"operation": "add", "path": "src/pages/Login/index.tsx", "description": "新增登录页面"},
                        {"operation": "modify", "path": "src/router/index.ts", "description": "注册登录路由"},
                    ],
                    "impact_scope": {
                        "summary": "影响登录入口和路由表。",
                        "affected_modules": ["pages", "router"],
                        "public_contracts": [],
                        "risks": ["未登录跳转可能形成循环"],
                    },
                    "deliverables": [
                        {
                            "id": "page:login",
                            "kind": "frontend.page",
                            "target_id": "login",
                            "paths": ["src/pages/Login/index.tsx"],
                            "provides": ["login.render"],
                        }
                    ],
                    "status": "completed",
                }
            ],
        }

        plan = create_build_task_plan(
            project_plan,
            agent_plan=agent_plan,
            workspace_snapshot={
                "entrypoints": [{"path": "src/router/index.ts"}],
                "project_roots": [{"path": "src"}],
                "tech_stack": ["React", "TypeScript"],
            },
        )
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
        self.assertEqual(task["target_files"], ["src/pages/Login/index.tsx", "src/router/index.ts"])
        self.assertEqual(task["change_scope"][0]["operation"], "add")
        self.assertEqual(task["impact_scope"]["affected_modules"], ["pages", "router"])
        self.assertEqual(task["executionMode"], "subagent-direct-write")
        self.assertIn("target_files 明确且互斥", task["directWriteReason"])
        self.assertNotIn("acceptance_criteria", task)
        self.assertEqual(
            [check["kind"] for check in task["acceptance_checks"]],
            [
                "file_operation",
                "file_operation",
                "scope_boundary",
                "page_entry",
                "page_default_export",
                "page_placeholder",
                "frontend_api_boundary",
            ],
        )
        self.assertEqual(task["unit_id"], "page:login")
        self.assertIn("page-login", [item["id"] for item in tasks_from_build_task_plan(plan)])

    def test_duplicate_task_ids_are_made_unique_and_parallel_batch_is_recorded(self) -> None:
        project_plan = {"version": "1.0.0", "page_detail_plans": [], "data_sources": []}
        agent_plan = {
            "tasks": [
                {
                    "id": "page-task",
                    "unit_id": "page:login",
                    "owner": "frontend",
                    "description": "新增登录页",
                    "change_scope": [{"operation": "add", "path": "src/pages/Login/index.tsx"}],
                    "deliverables": [{"id": "page:login", "kind": "frontend.page", "target_id": "login", "paths": ["src/pages/Login/index.tsx"], "provides": ["login.render"]}],
                },
                {
                    "id": "page-task",
                    "unit_id": "page:help",
                    "owner": "frontend",
                    "description": "新增帮助页",
                    "change_scope": [{"operation": "add", "path": "src/pages/Help/index.tsx"}],
                    "deliverables": [{"id": "page:help", "kind": "frontend.page", "target_id": "help", "paths": ["src/pages/Help/index.tsx"], "provides": ["help.render"]}],
                },
            ]
        }

        plan = create_build_task_plan(project_plan, agent_plan=agent_plan)

        tasks = tasks_from_build_task_plan(plan)
        self.assertEqual([task["id"] for task in tasks], ["page-task", "page-task-2"])
        self.assertEqual(plan["execution"]["batches"][0]["mode"], "parallel")
        self.assertEqual(tasks[0]["parallel_with"], ["page-task-2"])
        self.assertEqual(tasks[1]["parallel_with"], ["page-task"])

    def test_live_page_path_is_reconciled_without_menu_route_task(self) -> None:
        """实时唯一同义页面目录只用于路径校对，不补充菜单或路由登记任务。"""

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
            "required_unit_ids": ["frontend:shell", "page:dashboard_page"],
            "source_refs": {"type": "page_detail"},
        }
        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "frontend:shell": {"id": "frontend:shell", "kind": "frontend"},
                "page:dashboard_page": {"id": "page:dashboard_page", "kind": "page"},
            },
            "unit_graph": {
                "nodes": ["frontend:shell", "page:dashboard_page"],
                "edges": [
                    {
                        "from": "frontend:shell",
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
                            "deliverables": [
                                {
                                    "id": "page:dashboard_page",
                                    "kind": "frontend.page",
                                    "target_id": "dashboard_page",
                                    "paths": ["frontend/src/pages/DashboardPage/index.tsx"],
                                    "provides": ["dashboard_page.render"],
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
        self.assertNotIn("page:dashboard_page:route-menu-registration", tasks)
        self.assertEqual(page_task["target_files"], ["frontend/src/pages/Dashboard/index.tsx"])
        self.assertEqual(page_task["change_scope"][0]["operation"], "add")
        self.assertEqual(
            page_task["path_reconciliation"]["canonical_path"],
            "frontend/src/pages/Dashboard/index.tsx",
        )
        self.assertFalse(plan["task_graph"]["validation"]["is_valid"])
        self.assertIn("must include the dashboard_page page entry", str(plan["task_graph"]["validation"]["errors"]))
        self.assertNotIn("frontend/src/constants/menus.ts", page_task["allowed_paths"])

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
        self.assertEqual(set(tasks), {"pet-data-view"})
        self.assertNotIn("page:pet_list_page:route-menu-registration", tasks)


if __name__ == "__main__":
    unittest.main()
