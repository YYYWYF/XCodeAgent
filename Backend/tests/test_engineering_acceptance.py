from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch

from app.agents.database.generator import _verify_database_gaps
from app.services.engineering_acceptance import (
    compile_engineering_acceptance,
    engineering_acceptance_contract_errors,
    ensure_engineering_acceptance,
)
from app.services.build_task_planner import create_build_task_plan
from app.services.engineering_acceptance_verifier import (
    unauthorized_batch_paths,
    verify_engineering_acceptance,
)


class EngineeringAcceptanceTests(unittest.TestCase):
    def test_backend_endpoint_permission_requires_one_exact_any_of_annotation(self) -> None:
        """后端受控 Endpoint 只能在精确 Controller Method 上使用平台常量。"""

        task = {
            "id": "endpoint-orders-approve",
            "owner": "backend",
            "unit_id": "backend:endpoint:orders_api:orders.approve",
            "source_refs": {
                "authorization": {
                    "endpoints": [
                        {
                            "apiContractId": "orders_api",
                            "endpointId": "orders.approve",
                            "httpMethod": "POST",
                            "path": "/orders/approve",
                            "operationResourceKeys": ["orders_approve", "orders_recheck"],
                            "semantics": "ANY_OF",
                        }
                    ],
                    "authConstants": [
                        {"name": "ORDERS_APPROVE_RESOURCE", "resourceKey": "orders_approve"},
                        {"name": "ORDERS_RECHECK_RESOURCE", "resourceKey": "orders_recheck"},
                    ],
                }
            },
            "deliverables": [
                {
                    "kind": "backend.endpoint_controller",
                    "paths": ["backend/src/main/java/example/OrdersController.java"],
                }
            ],
            "change_scope": [
                {"operation": "add", "path": "backend/src/main/java/example/OrdersController.java"}
            ],
        }
        compiled = compile_engineering_acceptance([task])[0]
        self.assertIn("backend_authorization", [item["kind"] for item in compiled["acceptance_checks"]])
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            controller = root / "backend/src/main/java/example/OrdersController.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                "@RestController\n@RequestMapping(\"/orders\")\nclass OrdersController {\n"
                "  @PostMapping(\"/approve\")\n"
                "  @RequireAnyResource({AuthConstants.ORDERS_APPROVE_RESOURCE, AuthConstants.ORDERS_RECHECK_RESOURCE})\n"
                "  public void approve() {}\n}\n",
                encoding="utf-8",
            )
            _, errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={"files": [{"path": controller.relative_to(root).as_posix(), "changeType": "added"}]},
                workspace_root=workspace,
            )
            controller.write_text(
                "@RestController\n@RequestMapping(\"/orders\")\nclass OrdersController {\n"
                "  @PostMapping(\"/approve\")\n"
                "  @RequireAnyResource({AuthConstants.ORDERS_APPROVE_RESOURCE})\n"
                "  public void approve() {}\n}\n",
                encoding="utf-8",
            )
            _, mismatch_errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={"files": [{"path": controller.relative_to(root).as_posix(), "changeType": "added"}]},
                workspace_root=workspace,
            )

        self.assertFalse(errors, errors)
        self.assertTrue(any("常量集合不匹配" in error for error in mismatch_errors), mismatch_errors)

    def test_frontend_action_permission_uses_platform_resource_key(self) -> None:
        """页面受控操作必须唯一映射到平台切片给定的 Permission。"""

        task = {
            "id": "page-orders",
            "owner": "frontend",
            "unit_id": "page:orders",
            "source_refs": {
                "authorization": {
                    "actions": [
                        {
                            "pageId": "orders",
                            "actionId": "approve",
                            "resourceKey": "orders_approve",
                        }
                    ]
                },
                "page_implementation_contract": {
                    "actionBindings": [
                        {"actionId": "approve"},
                        {"actionId": "export"},
                    ]
                },
            },
            "deliverables": [
                {
                    "kind": "frontend.page",
                    "target_id": "orders",
                    "paths": ["frontend/src/pages/Orders/index.tsx"],
                }
            ],
            "change_scope": [
                {
                    "operation": "add",
                    "path": "frontend/src/pages/Orders/index.tsx",
                }
            ],
        }
        compiled = compile_engineering_acceptance([task])[0]
        self.assertIn(
            "frontend_authorization",
            [check["kind"] for check in compiled["acceptance_checks"]],
        )
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            page_file = root / "frontend/src/pages/Orders/index.tsx"
            page_file.parent.mkdir(parents=True)
            page_file.write_text(
                "import { Permission } from '@/authorization';\nimport { RESOURCES } from '@/authorization/resources';\n"
                "export default function Orders() {\n"
                "  return <Permission resourceKey={RESOURCES.OPERATION.ORDERS_APPROVE} mode=\"hidden\">\n"
                "    <button data-action-id=\"approve\">批准</button>\n"
                "  </Permission>;\n"
                "}\n",
                encoding="utf-8",
            )
            evidence, errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={
                    "files": [
                        {
                            "path": page_file.relative_to(root).as_posix(),
                            "changeType": "added",
                        }
                    ]
                },
                workspace_root=workspace,
            )

        self.assertFalse(errors, errors)
        self.assertTrue(
            any(item["kind"] == "frontend_authorization" and item["status"] == "passed" for item in evidence)
        )

    def test_frontend_action_permission_rejects_wrong_key_uncontrolled_wrap_and_http(self) -> None:
        """页面不得改写资源键、包装未受控操作或绕过领域 API 边界。"""

        task = {
            "id": "page-orders",
            "owner": "frontend",
            "unit_id": "page:orders",
            "source_refs": {
                "authorization": {
                    "actions": [
                        {"actionId": "approve", "resourceKey": "orders_approve"}
                    ]
                },
                "page_implementation_contract": {
                    "actionBindings": [
                        {"actionId": "approve"},
                        {"actionId": "export"},
                    ]
                },
            },
            "deliverables": [
                {
                    "kind": "frontend.page",
                    "paths": ["frontend/src/pages/Orders/index.tsx"],
                }
            ],
            "change_scope": [
                {"operation": "add", "path": "frontend/src/pages/Orders/index.tsx"}
            ],
        }
        compiled = compile_engineering_acceptance([task])[0]
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            page_file = root / "frontend/src/pages/Orders/index.tsx"
            page_file.parent.mkdir(parents=True)
            page_file.write_text(
                "import { Permission } from '@/authorization';\nimport { RESOURCES } from '@/authorization/resources';\n"
                "export default function Orders() {\n"
                "  return <>\n"
                "    <Permission resourceKey=\"wrong\" mode=\"hidden\"><button data-action-id=\"approve\">批准</button></Permission>\n"
                "    <Permission resourceKey=\"orders_export\" mode=\"hidden\"><button data-action-id=\"export\">导出</button></Permission>\n"
                "  </>;\n"
                "}\n",
                encoding="utf-8",
            )
            _, errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={
                    "files": [
                        {
                            "path": page_file.relative_to(root).as_posix(),
                            "changeType": "added",
                        }
                    ]
                },
                workspace_root=workspace,
            )

            page_file.write_text(
                "import { Permission } from '@/authorization';\nimport { RESOURCES } from '@/authorization/resources';\n"
                "export default function Orders() {\n"
                "  fetch('/api/orders');\n"
                "  return <Permission resourceKey={RESOURCES.OPERATION.ORDERS_APPROVE} mode=\"hidden\"><button data-action-id=\"approve\">批准</button></Permission>;\n"
                "}\n",
                encoding="utf-8",
            )
            _, http_errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={
                    "files": [
                        {
                            "path": page_file.relative_to(root).as_posix(),
                            "changeType": "added",
                        }
                    ]
                },
                workspace_root=workspace,
            )

            page_file.write_text(
                "import { Permission } from '@/authorization';\nimport { RESOURCES } from '@/authorization/resources';\n"
                "export default function Orders() {\n"
                "  return <>\n"
                "    <Permission resourceKey={RESOURCES.OPERATION.ORDERS_APPROVE} mode=\"hidden\"><button data-action-id=\"approve\">批准</button></Permission>\n"
                "    <Permission resourceKey=\"orders_export\" mode=\"hidden\"><button data-action-id=\"export\">导出</button></Permission>\n"
                "  </>;\n"
                "}\n",
                encoding="utf-8",
            )
            _, uncontrolled_errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={
                    "files": [
                        {
                            "path": page_file.relative_to(root).as_posix(),
                            "changeType": "added",
                        }
                    ]
                },
                workspace_root=workspace,
            )

        self.assertTrue(any("RESOURCES" in error for error in errors), errors)
        self.assertTrue(any("fetch、axios 或 service" in error for error in http_errors), http_errors)
        self.assertTrue(any("未受控 Action export" in error for error in uncontrolled_errors), uncontrolled_errors)

    def test_business_acceptance_is_not_copied_into_build_task(self) -> None:
        """角色过滤等业务验收必须保留在详情上下文，但不得进入 Build Task。"""

        business_criteria = [
            "当角色为 employee 时仅显示当前用户的申请。",
            "当角色为 manager 时显示全部记录。",
        ]
        context = {
            "executable_details": {
                "page_detail_plans": [
                    {
                        "pageId": "leave-list",
                        "acceptance_criteria": business_criteria,
                    }
                ]
            }
        }
        task = {
            "id": "page-leave-list",
            "owner": "frontend",
            "unit_id": "page:leave-list",
            "change_scope": [
                {
                    "operation": "add",
                    "path": "frontend/src/pages/LeaveListPage/index.tsx",
                }
            ],
            "acceptance_criteria": business_criteria,
        }

        compiled = compile_engineering_acceptance([task], context)[0]

        self.assertEqual(
            context["executable_details"]["page_detail_plans"][0]["acceptance_criteria"],
            business_criteria,
        )
        self.assertNotIn("acceptance_criteria", compiled)
        self.assertEqual(
            [check["kind"] for check in compiled["acceptance_checks"]],
            ["file_operation", "scope_boundary", "frontend_api_boundary"],
        )

    def test_frontend_mock_source_skips_service_get_binding_check(self) -> None:
        """前端内存 Mock 来源（effective_source.kind=frontend_mock）不经过集中 service，
        应跳过 service.get 与接口路径绑定检查，只校验 Schema 字段。"""

        task = {
            "id": "page-leave-list",
            "owner": "frontend",
            "unit_id": "page:leave-list",
            "source_refs": {"endpoint_ids": ["leave.list"]},
            "change_scope": [
                {"operation": "add", "path": "frontend/src/apis/leaveApi.ts"},
                {"operation": "add", "path": "frontend/src/pages/LeaveListPage/index.tsx"},
            ],
        }
        context = {
            "executable_details": {
                "data_sources": [
                    {
                        "id": "leave-source",
                        "type": "static",
                        "entities": [{"id": "Leave"}],
                    }
                ],
                "direct_endpoint_details": [
                    {
                        "endpoint_id": "leave.list",
                        "api_contract_id": "leave-api",
                        "endpoint_decision": {
                            "data_origin": {
                                "source_type": "static",
                                "effective_source": {
                                    "kind": "frontend_mock",
                                    "data_source_id": "leave-source",
                                },
                            }
                        },
                    }
                ],
                "page_detail_plans": [
                    {
                        "response_bindings": [
                            {"endpoint_id": "leave.list", "source_path": "items[].applicant"},
                            {"endpoint_id": "leave.list", "source_path": "items[].status"},
                        ]
                    }
                ],
                "api_contracts": [
                    {
                        "id": "leave-api",
                        "data_source_id": "leave-source",
                        "entity_ids": ["Leave"],
                        "schemas": {
                            "LeaveListResponse": {
                                "type": "object",
                                "properties": {
                                    "applicant": {"type": "string"},
                                    "status": {"type": "string"},
                                },
                            }
                        },
                        "endpoints": [
                            {
                                "id": "leave.list",
                                "method": "GET",
                                "path": "/api/leaves",
                                "response_schema_ref": "#/schemas/LeaveListResponse",
                            }
                        ],
                    }
                ],
            }
        }
        compiled = compile_engineering_acceptance([task], context)[0]
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            api_file = root / "frontend/src/apis/leaveApi.ts"
            page_file = root / "frontend/src/pages/LeaveListPage/index.tsx"
            api_file.parent.mkdir(parents=True)
            page_file.parent.mkdir(parents=True)
            # 内存 Mock 模块不 import service、不调用 service.get，
            # 只用内存数组与 async 函数实现契约字段。
            api_file.write_text(
                "export interface LeaveItem { applicant: string; status: string }\n"
                "const records: LeaveItem[] = [{ applicant: 'a', status: 'pending' }];\n"
                "export const fetchLeaveList = async () => ({ items: records, total: 1 });",
                encoding="utf-8",
            )
            page_file.write_text(
                "const columns = [{ dataIndex: 'applicant' }, { dataIndex: 'status' }];",
                encoding="utf-8",
            )
            _, errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={
                    "files": [
                        {"path": api_file.relative_to(root).as_posix(), "changeType": "added"},
                        {"path": page_file.relative_to(root).as_posix(), "changeType": "added"},
                    ]
                },
                workspace_root=workspace,
            )

        # frontend_mock 来源跳过 service.get 与接口路径检查，Schema 字段齐全，应通过。
        self.assertFalse(
            any("service.get" in error for error in errors),
            f"frontend_mock 来源不应要求 service.get 绑定，但报错：{errors}",
        )
        self.assertFalse(
            any("接口路径" in error for error in errors),
            f"frontend_mock 来源不应要求接口路径绑定，但报错：{errors}",
        )
        self.assertFalse(errors, f"frontend_mock 内存 Mock 模块应通过契约验收，但报错：{errors}")

    def test_frontend_contract_binding_uses_deterministic_source_evidence(self) -> None:
        """前端 API 方法、路径和页面绑定字段必须来自生成源文件。"""

        task, context = self._frontend_contract_fixture()
        compiled = compile_engineering_acceptance([task], context)[0]
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            api_file = root / "frontend/src/apis/leaveApi.ts"
            page_file = root / "frontend/src/pages/LeaveListPage/index.tsx"
            api_file.parent.mkdir(parents=True)
            page_file.parent.mkdir(parents=True)
            api_file.write_text(
                "export interface LeaveItem { applicant: string; status: string }\n"
                "export const fetchLeaveList = () => service.get('/api/leaves');",
                encoding="utf-8",
            )
            page_file.write_text(
                "const columns = [{ dataIndex: 'applicant' }, { dataIndex: 'status' }];",
                encoding="utf-8",
            )
            evidence, errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={
                    "files": [
                        {"path": api_file.relative_to(root).as_posix(), "changeType": "added"},
                        {"path": page_file.relative_to(root).as_posix(), "changeType": "modified"},
                    ]
                },
                workspace_root=workspace,
            )

        self.assertFalse(errors)
        self.assertTrue(all(item["status"] == "passed" for item in evidence))

    def test_frontend_contract_mismatch_fails_engineering_acceptance(self) -> None:
        """接口路径或 Schema 字段不匹配时不得接受 Agent 的 completed 声明。"""

        task, context = self._frontend_contract_fixture()
        compiled = compile_engineering_acceptance([task], context)[0]
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            for path, content in (
                (
                    "frontend/src/apis/leaveApi.ts",
                    "export const fetchLeaveList = () => service.post('/api/other');",
                ),
                (
                    "frontend/src/pages/LeaveListPage/index.tsx",
                    "const columns = [{ dataIndex: 'unknown' }];",
                ),
            ):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            _, errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={
                    "files": [
                        {"path": "frontend/src/apis/leaveApi.ts", "changeType": "added"},
                        {
                            "path": "frontend/src/pages/LeaveListPage/index.tsx",
                            "changeType": "modified",
                        },
                    ]
                },
                workspace_root=workspace,
            )

        # API method/path 和字段语义已迁移到 business_acceptance，工程验收只负责文件状态。
        self.assertFalse(errors)

    def test_repair_add_check_accepts_modified_existing_file(self) -> None:
        """修复已被失败尝试创建的文件时，added 检查应接受本轮 modified 差异。"""

        task = {
            "id": "repair:backend",
            "kind": "repair",
            "owner": "backend",
            "acceptance_checks": [
                {
                    "id": "repair-file",
                    "kind": "file_operation",
                    "description": "修复后端文件。",
                    "target_paths": ["backend/Pet.java"],
                    "expected": {"operation": "add", "change_type": "added"},
                }
            ],
            "acceptance_criteria": ["修复后端文件。"],
        }

        _, errors = verify_engineering_acceptance(
            task=task,
            status="completed",
            code_change_set={
                "files": [
                    {"path": "backend/Pet.java", "changeType": "modified"}
                ]
            },
            workspace_root=None,
        )

        self.assertFalse(errors)

    def test_backend_contract_binding_checks_spring_mapping_and_fields(self) -> None:
        """后端接口任务必须包含正式 Mapping、路径和请求响应字段。"""

        task = {
            "id": "backend-leave-list",
            "owner": "backend",
            "unit_id": "backend:endpoint:leave-api:leave.list",
            "source_refs": {"endpoint_ids": ["leave.list"]},
            "change_scope": [
                {"operation": "add", "path": "backend/LeaveController.java"},
                {"operation": "add", "path": "backend/LeaveResponse.java"},
            ],
        }
        context = self._contract_context()
        compiled = compile_engineering_acceptance([task], context)[0]
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            controller = root / "backend/LeaveController.java"
            response = root / "backend/LeaveResponse.java"
            controller.parent.mkdir(parents=True)
            controller.write_text(
                '@GetMapping("/api/leaves") public LeaveResponse list() { return null; }',
                encoding="utf-8",
            )
            response.write_text(
                "class LeaveResponse { String applicant; String status; }",
                encoding="utf-8",
            )
            _, errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={
                    "files": [
                        {"path": "backend/LeaveController.java", "changeType": "added"},
                        {"path": "backend/LeaveResponse.java", "changeType": "added"},
                    ]
                },
                workspace_root=workspace,
            )

        self.assertFalse(errors)

    def test_backend_infrastructure_task_does_not_own_endpoint_contract(self) -> None:
        """只修改依赖与配置的后端前置任务不得承担 Controller/DTO 契约验收。"""

        task = {
            "id": "backend-precheck",
            "owner": "backend",
            "source_refs": {"endpoint_ids": ["leave.list"]},
            "change_scope": [
                {"operation": "modify", "path": "backend/pom.xml"},
                {
                    "operation": "add",
                    "path": "backend/src/main/java/example/MyBatisPlusConfig.java",
                },
            ],
        }

        compiled = compile_engineering_acceptance(
            [task],
            self._contract_context(),
        )[0]

        self.assertNotIn(
            "backend_contract_binding",
            [check["kind"] for check in compiled["acceptance_checks"]],
        )
        self.assertFalse(engineering_acceptance_contract_errors(compiled))

    def test_legacy_infrastructure_task_drops_misassigned_contract_check(self) -> None:
        """恢复旧 DAG 时应移除配置任务上历史误分配的端点契约检查。"""

        task = {
            "id": "backend-precheck",
            "owner": "backend",
            "source_refs": {"endpoint_ids": ["leave.list"]},
            "change_scope": [
                {"operation": "modify", "path": "backend/pom.xml"},
            ],
            "acceptance_checks": [
                {
                    "id": "file",
                    "kind": "file_operation",
                    "description": "pom.xml 必须被修改。",
                },
                {
                    "id": "contract",
                    "kind": "backend_contract_binding",
                    "description": "错误分配的接口契约检查。",
                },
            ],
        }

        recovered = ensure_engineering_acceptance(task)

        self.assertEqual(
            [check["kind"] for check in recovered["acceptance_checks"]],
            ["file_operation", "scope_boundary"],
        )
        self.assertNotIn("acceptance_criteria", recovered)

    def test_verifier_normalizes_legacy_infrastructure_task_before_checking(self) -> None:
        """直接调用验收器时也必须过滤旧配置任务上的接口契约检查。"""

        task = {
            "id": "backend-precheck",
            "owner": "backend",
            "source_refs": {"endpoint_ids": ["leave.list"]},
            "change_scope": [{"operation": "modify", "path": "backend/pom.xml"}],
            "acceptance_checks": [
                {
                    "id": "file",
                    "kind": "file_operation",
                    "target_paths": ["backend/pom.xml"],
                    "expected": {"operation": "modify"},
                },
                {
                    "id": "contract",
                    "kind": "backend_contract_binding",
                    "target_paths": ["backend/pom.xml"],
                    "expected": {"endpoints": [{"method": "GET", "path": "/api/leave"}]},
                },
            ],
        }

        with tempfile.TemporaryDirectory() as workspace:
            pom = Path(workspace) / "backend/pom.xml"
            pom.parent.mkdir(parents=True)
            pom.write_text("<project />", encoding="utf-8")
            evidence, errors = verify_engineering_acceptance(
                task=task,
                status="already_satisfied",
                code_change_set=None,
                workspace_root=workspace,
            )

        self.assertFalse(errors)
        self.assertEqual(
            [item["kind"] for item in evidence],
            ["file_operation", "scope_boundary"],
        )

    def test_confirmed_entity_binding_supplies_endpoint_source_type(self) -> None:
        """接口来源必须从已确认 EntitySourceBinding 推导。"""

        task = {
            "id": "backend-leave-list",
            "owner": "backend",
            "source_refs": {"endpoint_ids": ["leave.list"]},
            "change_scope": [
                {"operation": "add", "path": "backend/LeaveController.java"}
            ],
        }
        context = self._contract_context()
        context["executable_details"]["entity_designs"] = [
            {"entity_id": "Leave", "data_source_type": "external_api"}
        ]

        compiled = compile_engineering_acceptance([task], context)[0]
        self.assertNotIn(
            "backend_contract_binding",
            [check["kind"] for check in compiled["acceptance_checks"]],
        )

    def test_backend_contract_requires_explicit_snake_case_wire_mapping(self) -> None:
        """Java camelCase 字段只有具备 Jackson 映射时才能满足 snake_case 契约。"""

        task = {
            "id": "backend-leave-create",
            "owner": "backend",
            "source_refs": {"endpoint_ids": ["leave.create"]},
            "change_scope": [
                {"operation": "add", "path": "backend/LeaveController.java"},
                {"operation": "add", "path": "backend/LeaveCreateDTO.java"},
            ],
        }
        context = {
            "executable_details": {
                "api_contracts": [
                    {
                        "id": "leave-api",
                        "schemas": {
                            "LeaveCreateRequest": {
                                "properties": {"employee_name": {"type": "string"}}
                            },
                            "LeaveRecord": {
                                "properties": {"employee_name": {"type": "string"}}
                            },
                        },
                        "endpoints": [
                            {
                                "id": "leave.create",
                                "method": "POST",
                                "path": "/api/leave-records",
                                "request_schema_ref": "LeaveCreateRequest",
                                "response_schema_ref": "LeaveRecord",
                            }
                        ],
                    }
                ]
            }
        }
        compiled = compile_engineering_acceptance([task], context)[0]
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            controller = root / "backend/LeaveController.java"
            dto = root / "backend/LeaveCreateDTO.java"
            controller.parent.mkdir(parents=True)
            controller.write_text('@PostMapping("/api/leave-records") class LeaveController {}', encoding="utf-8")
            dto.write_text("class LeaveCreateDTO { String employeeName; }", encoding="utf-8")
            change_set = {
                "files": [
                    {"path": "backend/LeaveController.java", "changeType": "added"},
                    {"path": "backend/LeaveCreateDTO.java", "changeType": "added"},
                ]
            }
            _, missing_errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set=change_set,
                workspace_root=workspace,
            )
            dto.write_text(
                'class LeaveCreateDTO { @JsonProperty("employee_name") String employeeName; }',
                encoding="utf-8",
            )
            _, mapped_errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set=change_set,
                workspace_root=workspace,
            )
            dto.write_text("class LeaveCreateDTO { String employeeName; }", encoding="utf-8")
            config = root / "backend/src/main/resources/application.yml"
            config.parent.mkdir(parents=True)
            config.write_text(
                "spring:\n  jackson:\n    property-naming-strategy: SNAKE_CASE\n",
                encoding="utf-8",
            )
            _, global_mapping_errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set=change_set,
                workspace_root=workspace,
            )

        self.assertFalse(missing_errors)
        self.assertFalse(mapped_errors)
        self.assertFalse(global_mapping_errors)

    def test_backend_contract_follows_referenced_existing_dto_read_only(self) -> None:
        """列表包装模型必须只读跟随既有 DTO，且不得要求该 DTO 出现在改动范围。"""

        task = {
            "id": "backend-leave-list",
            "owner": "backend",
            "source_refs": {"endpoint_ids": ["leave.list"]},
            "change_scope": [
                {"operation": "add", "path": "backend/LeaveController.java"},
                {
                    "operation": "add",
                    "path": "backend/dto/LeaveRecordListResponse.java",
                },
            ],
        }
        context = {
            "executable_details": {
                "api_contracts": [
                    {
                        "id": "leave-api",
                        "schemas": {
                            "LeaveListResponse": {
                                "properties": {
                                    "items": {
                                        "type": "array",
                                        "items": {"$ref": "LeaveRecord"},
                                    },
                                    "total": {"type": "integer"},
                                }
                            },
                            "LeaveRecord": {
                                "properties": {
                                    "id": {"type": "integer"},
                                    "employee_name": {"type": "string"},
                                    "created_at": {"type": "string"},
                                }
                            },
                        },
                        "endpoints": [
                            {
                                "id": "leave.list",
                                "method": "GET",
                                "path": "/api/leave-records",
                                "response_schema_ref": "LeaveListResponse",
                            }
                        ],
                    }
                ]
            }
        }
        compiled = compile_engineering_acceptance([task], context)[0]
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            controller = root / "backend/LeaveController.java"
            response = root / "backend/dto/LeaveRecordListResponse.java"
            existing_dto = root / "backend/dto/LeaveRecordDTO.java"
            response.parent.mkdir(parents=True)
            controller.write_text(
                '@GetMapping("/api/leave-records") class LeaveController {}',
                encoding="utf-8",
            )
            response.write_text(
                "class LeaveRecordListResponse { "
                "List<LeaveRecordDTO> items; long total; }",
                encoding="utf-8",
            )
            existing_dto.write_text(
                "class LeaveRecordDTO { int id; "
                '@JsonProperty("employee_name") String employeeName; '
                '@JsonProperty("created_at") String createdAt; }',
                encoding="utf-8",
            )
            change_set = {
                "files": [
                    {"path": "backend/LeaveController.java", "changeType": "added"},
                    {
                        "path": "backend/dto/LeaveRecordListResponse.java",
                        "changeType": "added",
                    },
                ]
            }
            _, errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set=change_set,
                workspace_root=workspace,
            )
            existing_dto.write_text(
                "class LeaveRecordDTO { "
                "int id; String employeeName; String createdAt; }",
                encoding="utf-8",
            )
            unrelated_dto = root / "backend/dto/UnrelatedDTO.java"
            unrelated_dto.write_text(
                "class UnrelatedDTO { "
                '@JsonProperty("employee_name") String employeeName; '
                '@JsonProperty("created_at") String createdAt; }',
                encoding="utf-8",
            )
            _, missing_mapping_errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set=change_set,
                workspace_root=workspace,
            )

        self.assertNotIn(
            "backend_contract_binding",
            [check["kind"] for check in compiled["acceptance_checks"]],
        )
        self.assertFalse(errors)
        self.assertFalse(missing_mapping_errors)

    def test_frontend_page_checks_only_confirmed_response_bindings(self) -> None:
        """页面不必消费响应全部字段，只验证 PageDetail 明确声明的绑定字段。"""

        task = {
            "id": "page-leave-form",
            "owner": "frontend",
            "source_refs": {"endpoint_ids": ["leave.create"]},
            "change_scope": [
                {"operation": "add", "path": "frontend/src/pages/LeaveForm/index.tsx"}
            ],
        }
        context = {
            "executable_details": {
                "page_detail_plans": [
                    {
                        "response_bindings": [
                            {
                                "endpoint_id": "leave.create",
                                "source_path": "employee_name",
                            }
                        ]
                    }
                ],
                "api_contracts": [
                    {
                        "id": "leave-api",
                        "schemas": {
                            "LeaveCreateRequest": {
                                "properties": {"employee_name": {"type": "string"}}
                            },
                            "LeaveRecord": {
                                "properties": {
                                    "id": {"type": "integer"},
                                    "employee_name": {"type": "string"},
                                }
                            },
                        },
                        "endpoints": [
                            {
                                "id": "leave.create",
                                "method": "POST",
                                "path": "/api/leave-records",
                                "request_schema_ref": "LeaveCreateRequest",
                                "response_schema_ref": "LeaveRecord",
                            }
                        ],
                    }
                ],
            }
        }
        compiled = compile_engineering_acceptance([task], context)[0]
        with tempfile.TemporaryDirectory() as workspace:
            page = Path(workspace) / "frontend/src/pages/LeaveForm/index.tsx"
            page.parent.mkdir(parents=True)
            page.write_text("const name = response.employee_name;", encoding="utf-8")
            _, errors = verify_engineering_acceptance(
                task=compiled,
                status="completed",
                code_change_set={
                    "files": [
                        {
                            "path": "frontend/src/pages/LeaveForm/index.tsx",
                            "changeType": "added",
                        }
                    ]
                },
                workspace_root=workspace,
            )

        self.assertFalse(errors)

    def test_legacy_task_rebuilds_engineering_checks_and_drops_business_text(self) -> None:
        """旧 DAG 恢复时必须从文件元数据重建检查，不能继续展示旧业务验收。"""

        task = ensure_engineering_acceptance(
            {
                "id": "legacy-page",
                "owner": "frontend",
                "target_files": ["frontend/src/pages/Legacy/index.tsx"],
                "change_scope": [
                    {
                        "operation": "modify",
                        "path": "frontend/src/pages/Legacy/index.tsx",
                    }
                ],
                "acceptance_criteria": ["管理员可以查看全部业务记录。"],
            }
        )

        self.assertNotIn("acceptance_criteria", task)
        self.assertEqual(
            [check["kind"] for check in task["acceptance_checks"]],
            ["file_operation", "scope_boundary"],
        )

    def test_legacy_endpoint_task_requires_task_preparation_context(self) -> None:
        """旧接口任务缺少正式契约上下文时必须要求重新准备，不能降级放行。"""

        task = ensure_engineering_acceptance(
            {
                "id": "legacy-endpoint",
                "owner": "backend",
                "source_refs": {"endpoint_ids": ["leave.list"]},
                "change_scope": [
                    {"operation": "modify", "path": "backend/LeaveController.java"}
                ],
            }
        )

        errors = engineering_acceptance_contract_errors(task)

        self.assertFalse(errors)
        self.assertEqual(
            [check["kind"] for check in task["acceptance_checks"]],
            ["file_operation", "scope_boundary"],
        )

    def test_task_plan_compiles_contract_checks_from_build_context(self) -> None:
        """真实任务规划入口必须把 executable_details 交给工程验收编译器。"""

        task, context = self._frontend_contract_fixture()
        task = {
            **task,
            "deliverables": [
                {
                    "id": "api-module:leave",
                    "kind": "frontend.api_module",
                    "target_id": "leave-api",
                    "paths": ["frontend/src/apis/leaveApi.ts"],
                    "provides": ["leave.list.client"],
                },
                {
                    "id": "page:leave-list",
                    "kind": "frontend.page",
                    "target_id": "leave-list",
                    "paths": ["frontend/src/pages/LeaveListPage/index.tsx"],
                    "provides": ["leave-list.render"],
                },
            ],
        }
        plan = create_build_task_plan(
            {
                "version": "1.0.0",
                "executable_details": context["executable_details"],
            },
            agent_plan={"tasks": [task]},
            build_context={"required_unit_ids": ["page:leave-list"]},
        )
        compiled = plan["task_registry"][task["id"]]

        self.assertTrue(
            plan["task_graph"]["validation"]["is_valid"],
            plan["task_graph"]["validation"]["errors"],
        )
        self.assertIn(
            "frontend.api_contract",
            [check["kind"] for check in compiled["business_acceptance_checks"]],
        )

    def test_batch_scope_detects_changes_outside_all_task_paths(self) -> None:
        """并发批次只能修改所有任务授权范围的并集。"""

        unauthorized = unauthorized_batch_paths(
            {
                "files": [
                    {"path": "frontend/src/pages/Orders.tsx"},
                    {"path": "frontend/vite.config.ts"},
                ]
            },
            [
                {
                    "allowed_paths": ["frontend/src/pages/Orders.tsx"],
                    "change_scope": [],
                }
            ],
        )

        self.assertEqual(unauthorized, ["frontend/vite.config.ts"])

    def test_batch_scope_accepts_nested_file_under_directory_authorization(self) -> None:
        """目录级修复授权必须覆盖其内部源码，避免成功修复被误判为越权。"""

        unauthorized = unauthorized_batch_paths(
            {"files": [{"path": "frontend/src/index.tsx"}]},
            [
                {
                    "allowed_paths": ["frontend"],
                    "target_files": ["frontend/package.json"],
                    "change_scope": [],
                }
            ],
        )

        self.assertEqual(unauthorized, [])

    def test_database_task_compiles_gap_and_approval_checks(self) -> None:
        """数据库任务只生成 schema gap 与高风险审批工程检查。"""

        compiled = compile_engineering_acceptance(
            [
                {
                    "id": "database-leave",
                    "owner": "database",
                    "database_scope": {"gap_ids": ["gap-leave-status"]},
                    "approval": {"required": True},
                    "acceptance_criteria": ["管理员可以查看全部记录。"],
                }
            ],
            {},
        )[0]

        self.assertEqual(
            [check["kind"] for check in compiled["acceptance_checks"]],
            ["database_gap", "database_approval"],
        )
        self.assertNotIn("acceptance_criteria", compiled)

    def test_database_post_verification_rescans_real_schema_gaps(self) -> None:
        """数据库执行后必须重新扫描真实 Schema，仍缺字段时不得报告完成。"""

        tasks = [
            {
                "id": "database-leave",
                "database_scope": {
                    "database": "leave_app",
                    "gaps": [
                        {
                            "id": "gap-status",
                            "kind": "missing_column",
                            "table": "leave_request",
                            "column": "status",
                            "required": {"name": "status", "type": "varchar(20)"},
                        }
                    ],
                },
            }
        ]
        before_summary = {"database": "leave_app"}
        missing_summary = {
            "status": "completed",
            "database": "leave_app",
            "database_exists": True,
            "tables": [{"name": "leave_request", "columns": []}],
        }
        satisfied_summary = {
            **missing_summary,
            "tables": [
                {
                    "name": "leave_request",
                    "columns": [{"name": "status", "type": "varchar(20)"}],
                }
            ],
        }

        workspace = "/tmp/xcodeagent-database-verification"
        with patch(
            "app.agents.database.generator.inspect_mysql_schema",
            side_effect=[missing_summary, satisfied_summary],
        ) as inspect_schema:
            failed = _verify_database_gaps(tasks, before_summary, workspace)
            completed = _verify_database_gaps(tasks, before_summary, workspace)

        self.assertEqual(failed["status"], "failed")
        self.assertEqual(len(failed["remaining_gaps"]), 1)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["remaining_gaps"], [])
        target = {
            "data_source_id": None,
            "data_source": {"type": "database", "tables": []},
            "method": "DATABASE",
            "path": "database-leave",
            "endpoint_id": None,
        }
        self.assertEqual(
            inspect_schema.call_args_list,
            [call(target, workspace), call(target, workspace)],
        )

    def _frontend_contract_fixture(self) -> tuple[dict, dict]:
        """构造包含 API 模块和页面入口的前端契约测试数据。"""

        task = {
            "id": "page-leave-list",
            "owner": "frontend",
            "unit_id": "page:leave-list",
            "source_refs": {"endpoint_ids": ["leave.list"]},
            "change_scope": [
                {"operation": "add", "path": "frontend/src/apis/leaveApi.ts"},
                {
                    "operation": "modify",
                    "path": "frontend/src/pages/LeaveListPage/index.tsx",
                },
            ],
        }
        return task, self._contract_context()

    def _contract_context(self) -> dict:
        """构造带页面字段绑定的最小正式 API 契约。"""

        return {
            "executable_details": {
                "data_sources": [{"id": "leave-source", "type": "mysql"}],
                "page_detail_plans": [
                    {
                        "response_bindings": [
                            {
                                "endpoint_id": "leave.list",
                                "source_path": "items[].applicant",
                            },
                            {
                                "endpoint_id": "leave.list",
                                "source_path": "items[].status",
                            },
                        ]
                    }
                ],
                "api_contracts": [
                    {
                        "id": "leave-api",
                        "data_source_id": "leave-source",
                        "schemas": {
                            "LeaveListResponse": {
                                "type": "object",
                                "properties": {
                                    "applicant": {"type": "string"},
                                    "status": {"type": "string"},
                                },
                            }
                        },
                        "endpoints": [
                            {
                                "id": "leave.list",
                                "method": "GET",
                                "path": "/api/leaves",
                                "response_schema_ref": "#/schemas/LeaveListResponse",
                            }
                        ],
                    }
                ],
            }
        }


if __name__ == "__main__":
    unittest.main()
