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
        self.assertFalse(
            any("employee" in criterion or "manager" in criterion for criterion in compiled["acceptance_criteria"])
        )
        self.assertEqual(
            [check["kind"] for check in compiled["acceptance_checks"]],
            ["file_operation", "scope_boundary"],
        )

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
                        {"path": str(api_file.relative_to(root)), "changeType": "added"},
                        {"path": str(page_file.relative_to(root)), "changeType": "added"},
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
                        {"path": path, "changeType": "added"}
                        for path in (
                            "frontend/src/apis/leaveApi.ts",
                            "frontend/src/pages/LeaveListPage/index.tsx",
                        )
                    ]
                },
                workspace_root=workspace,
            )

        self.assertTrue(any("service.get" in error for error in errors))
        self.assertTrue(any("Schema 字段" in error for error in errors))

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
            ["file_operation"],
        )
        self.assertEqual(recovered["acceptance_criteria"], ["pom.xml 必须被修改。"])

    def test_confirmed_endpoint_detail_overrides_project_plan_source_type(self) -> None:
        """已确认 EndpointDetail 的 third_party 来源必须覆盖 ProjectPlan 的旧 database 声明。"""

        task = {
            "id": "backend-leave-list",
            "owner": "backend",
            "source_refs": {"endpoint_ids": ["leave.list"]},
            "change_scope": [
                {"operation": "add", "path": "backend/LeaveController.java"}
            ],
        }
        context = self._contract_context()
        context["direct_endpoint_details"] = [
            {
                "api_contract_id": "leave-api",
                "endpoint_id": "leave.list",
                "status": "confirmed",
                "endpoint_decision": {
                    "data_origin": {
                        "source_type": "third_party",
                        "effective_source": {"kind": "third_party"},
                    }
                },
            }
        ]

        compiled = compile_engineering_acceptance([task], context)[0]
        contract_check = next(
            check
            for check in compiled["acceptance_checks"]
            if check["kind"] == "backend_contract_binding"
        )

        self.assertEqual(
            contract_check["expected"]["endpoints"][0]["source_type"],
            "third_party",
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

        self.assertEqual(
            sum(error.count("employee_name") for error in missing_errors),
            1,
        )
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

        contract_check = next(
            check
            for check in compiled["acceptance_checks"]
            if check["kind"] == "backend_contract_binding"
        )
        self.assertNotIn(
            "backend/dto/LeaveRecordDTO.java",
            contract_check["target_paths"],
        )
        self.assertFalse(errors)
        self.assertTrue(any("employee_name" in error for error in missing_mapping_errors))

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

        self.assertNotIn("管理员可以查看全部业务记录。", task["acceptance_criteria"])
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

        self.assertTrue(any("rerun prepare_build_tasks" in error for error in errors))
        self.assertTrue(any("no deterministic contract binding" in error for error in errors))

    def test_task_plan_compiles_contract_checks_from_build_context(self) -> None:
        """真实任务规划入口必须把 executable_details 交给工程验收编译器。"""

        task, context = self._frontend_contract_fixture()
        plan = create_build_task_plan(
            {
                "version": "1.0.0",
                "executable_details": context["executable_details"],
            },
            agent_plan={"tasks": [task]},
            build_context={"required_unit_ids": ["page:leave-list"]},
        )
        compiled = plan["task_registry"][task["id"]]

        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])
        self.assertIn(
            "frontend_contract_binding",
            [check["kind"] for check in compiled["acceptance_checks"]],
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

    def test_menu_registration_is_verified_from_existing_source(self) -> None:
        """菜单工程验收必须复用确定性解析器核对完整对象字段。"""

        task = {
            "id": "menu-leave-list",
            "owner": "frontend",
            "change_scope": [
                {
                    "operation": "modify",
                    "path": "frontend/src/constants/menus.ts",
                }
            ],
            "engineering_context": {
                "menu_registration": {
                    "file": "frontend/src/constants/menus.ts",
                    "path": "leave-list",
                    "name": "请假列表",
                    "key": "LeaveListPage",
                    "hide_in_menu": False,
                }
            },
        }
        compiled = compile_engineering_acceptance([task], {})[0]
        with tempfile.TemporaryDirectory() as workspace:
            menu = Path(workspace) / "frontend/src/constants/menus.ts"
            menu.parent.mkdir(parents=True)
            menu.write_text(
                "export const BIZ_MENUS = [{ path: 'leave-list', name: '请假列表', key: 'LeaveListPage' }];",
                encoding="utf-8",
            )
            _, errors = verify_engineering_acceptance(
                task=compiled,
                status="already_satisfied",
                code_change_set=None,
                workspace_root=workspace,
            )

        self.assertFalse(errors)

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
        self.assertFalse(any("管理员" in item for item in compiled["acceptance_criteria"]))

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
                    "operation": "add",
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
