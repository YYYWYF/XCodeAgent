from __future__ import annotations

import json
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.agents.main.task_preparer import (
    _model_usage,
    prepare_build_tasks_with_main_agent,
)
from app.agents.main.task_preparer_prompt import (
    _deliverable_kind_contract_prompt,
    build_task_preparation_prompt,
    compact_workspace_snapshot,
    endpoint_source_types,
    planning_context_mode,
    scoped_prompt_build_context,
)
from app.graph.nodes.tasks import _task_preparation_project_plan
from app.services.build_task_planner import (
    _database_task_requires_approval,
    build_task_candidate_contract_errors,
    create_build_task_plan,
    frontend_endpoint_ownership_errors,
    merge_exact_duplicate_tasks,
    tasks_from_build_task_plan,
)
from app.services.business_acceptance import DELIVERABLE_KINDS
from app.services.build_unit_compiler import annotate_unit_inputs


def _test_deliverable(task_id: str, unit_id: str, owner: str, path: str) -> dict:
    """为规划器旧测试构造符合当前 DAG 契约的最小交付物。"""

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


class BuildTaskPlannerTests(unittest.TestCase):
    def test_task_prompt_declares_exact_deliverable_kind_allowlist(self) -> None:
        """任务规划提示必须声明唯一完整的交付物结构和类型白名单。"""

        prompt = _deliverable_kind_contract_prompt()

        self.assertIn("complete allowlist", prompt)
        self.assertIn("Any value outside this list will be rejected", prompt)
        self.assertIn('"id": "stable unique id"', prompt)
        self.assertIn('"target_id": "formal page, endpoint, entity, or capability id"', prompt)
        self.assertIn('"paths": ["workspace-relative/path"]', prompt)
        self.assertIn('"provides": ["semantic.capability"]', prompt)
        self.assertIn('Singular `path`', prompt)
        for kind in DELIVERABLE_KINDS:
            self.assertIn(f"`{kind}`", prompt)

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

    def test_planning_context_mode_uses_pending_units(self) -> None:
        """上下文模式由本轮实际待生成 Unit 决定，而不是只看请求类型。"""

        self.assertEqual(
            planning_context_mode(
                {
                    "target": {"type": "page", "id": "orders"},
                    "planning_unit_ids": ["page:orders"],
                }
            ),
            "page",
        )
        self.assertEqual(
            planning_context_mode(
                {
                    "target": {"type": "page", "id": "orders"},
                    "planning_unit_ids": [
                        "backend:endpoint:orders-api:orders.list",
                        "page:orders",
                    ],
                }
            ),
            "combined",
        )
        self.assertEqual(
            planning_context_mode(
                {
                    "target": {"type": "endpoint", "id": "orders.list"},
                    "planning_unit_ids": ["backend:endpoint:orders-api:orders.list"],
                }
            ),
            "endpoint",
        )

    def test_prompt_uses_eight_ordered_sections_and_exact_top_level_contract(self) -> None:
        """所有规划模式必须共享八段顺序和唯一顶层 JSON 契约。"""

        prompt = build_task_preparation_prompt(
            {
                "executable_details": {
                    "entity_designs": [
                        {"entity_id": "Order", "data_source_type": "database"}
                    ]
                }
            },
            {},
            {
                "planning_context_mode": "endpoint",
                "required_unit_ids": [
                    "backend:endpoint:orders-api:orders.list"
                ],
            },
        )

        headings = [
            "## 1. Role & Boundary",
            "## 2. Output Contract",
            "## 3. Planning Algorithm",
            "## 4. Task Rules",
            "## 5. Dependency Rules",
            "## 6. Skill Injection",
            "## 7. Forbidden Output",
            "## 8. Workspace Context",
        ]
        positions = [prompt.index(heading) for heading in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(all(prompt.count(heading) == 1 for heading in headings))
        self.assertIn("exactly two top-level keys", prompt)
        self.assertIn("`workspace_analysis` and `tasks`", prompt)
        self.assertIn("do not return `dag`", prompt)
        self.assertNotIn("source_refs.entity_ids", prompt)

    def test_database_algorithm_fixes_ids_dependencies_and_module_resolution(self) -> None:
        """数据库 Prompt 固定结构任务、命名优先级和同 Unit 依赖链。"""

        prompt = build_task_preparation_prompt(
            {
                "executable_details": {
                    "entity_designs": [
                        {"entity_id": "ProductCategory", "data_source_type": "database"}
                    ]
                }
            },
            {
                "entrypoints": [
                    {"path": "backend/src/main/java/com/example/Application.java"}
                ],
                "backend": {"dir_structure": ["backend/src/main/java/com/example/"]},
            },
            {
                "planning_context_mode": "endpoint",
                "required_unit_ids": [
                    "backend:bootstrap",
                    "backend:endpoint:catalog-api:catalog.list",
                ],
            },
        )

        self.assertIn("`backend:bootstrap::bootstrap`", prompt)
        self.assertIn("`<endpointUnitId>::<entityId>::<stage>`", prompt)
        self.assertIn("`objects`, `repository`, `service`, `controller`", prompt)
        self.assertIn("objects → repository → service → controller", prompt)
        self.assertIn("ProductCategory becomes productCategory", prompt)
        self.assertIn("Never invent semantic names", prompt)
        self.assertIn("Existing files do not remove a stage", prompt)
        self.assertIn("For every owner=backend task", prompt)
        self.assertIn("`1. ...\\n2. ...`", prompt)
        self.assertIn("classify every exact change_scope path during planning", prompt)
        self.assertIn("operation=modify when the path is listed in existing_files", prompt)
        self.assertIn("operation=add when it is absent", prompt)
        self.assertIn("Do not defer this first existence decision", prompt)
        self.assertIn("leave a fully satisfying file unchanged", prompt)
        self.assertIn("minimum additions or corrections", prompt)

    def test_forbidden_output_follows_injected_skill_and_preserves_scope_semantics(self) -> None:
        """Skill 后的平台禁止项必须覆盖冲突描述并保留 change_scope 契约。"""

        with patch(
            "app.agents.main.task_preparer_prompt._static_data_skill_document",
            return_value="SKILL ALLOWS MENU APPEND",
        ):
            prompt = build_task_preparation_prompt(
                {
                    "executable_details": {
                        "entity_designs": [
                            {"entity_id": "Notice", "data_source_type": "static"}
                        ]
                    }
                },
                {},
                {"required_unit_ids": ["frontend:data:static"]},
            )

        self.assertLess(prompt.index("## 5. Dependency Rules"), prompt.index("## 6. Skill Injection"))
        self.assertLess(prompt.index("## 6. Skill Injection"), prompt.index("SKILL ALLOWS MENU APPEND"))
        self.assertLess(prompt.index("SKILL ALLOWS MENU APPEND"), prompt.index("## 7. Forbidden Output"))
        self.assertIn("Never create a menu or route registration task", prompt)
        self.assertIn("planned file-operation intent, not a pure permission list", prompt)
        self.assertIn("`allowed_paths` remains the execution authorization boundary", prompt)

    def test_scoped_workspace_snapshot_excludes_other_side(self) -> None:
        """endpoint/page 提示词只接收对应工作区导航事实。"""

        snapshot = {
            "project_roots": [{"path": "frontend/src"}],
            "tech_stack": ["React", "Vite"],
            "entrypoints": [
                {"path": "frontend/src/main.tsx"},
                {"path": "backend/src/main/java/demo/Application.java"},
            ],
            "build_commands": [{"cwd": "Frontend", "command": "pnpm build"}],
            "test_commands": [{"cwd": "Frontend", "command": "pnpm test"}],
            "file_manifest": [
                "frontend/src/pages/Orders.tsx",
                "backend/src/main/java/OrdersController.java",
                "package.json",
            ],
            "shared_contracts": [{"path": "frontend/src/typings/index.ts"}],
            "high_value_files": [
                {"path": "frontend/package.json"},
                {"path": "backend/pom.xml"},
            ],
            "code_graph": {"sampleSymbols": [{"path": "frontend/src/main.tsx"}]},
            "backend": {
                "dir_structure": ["backend/src/main/java/"],
                "api_routes": [],
                "models": [],
                "workflow_nodes": ["irrelevant"],
            },
            "frontend": {"pages": ["frontend/src/pages/"]},
        }

        page_snapshot = compact_workspace_snapshot(snapshot, scope="page")
        endpoint_snapshot = compact_workspace_snapshot(snapshot, scope="endpoint")

        self.assertNotIn("backend", page_snapshot)
        self.assertIn("frontend", page_snapshot)
        self.assertIn("frontend/src/pages/Orders.tsx", page_snapshot["file_manifest"])
        self.assertNotIn("backend/src/main/java/OrdersController.java", page_snapshot["file_manifest"])
        self.assertNotIn("frontend", endpoint_snapshot)
        self.assertIn("backend", endpoint_snapshot)
        self.assertEqual(
            endpoint_snapshot["entrypoints"],
            [{"path": "backend/src/main/java/demo/Application.java"}],
        )
        self.assertEqual(
            endpoint_snapshot["high_value_files"],
            [{"path": "backend/pom.xml"}],
        )
        for omitted_key in (
            "project_roots",
            "tech_stack",
            "build_commands",
            "test_commands",
            "file_manifest",
            "shared_contracts",
            "code_graph",
        ):
            self.assertNotIn(omitted_key, endpoint_snapshot)
        self.assertNotIn("workflow_nodes", endpoint_snapshot["backend"])

    def test_page_prompt_does_not_inject_backend_skill(self) -> None:
        """只生成页面时不读取 Spring/MyBatis 技能或后端快照。"""

        project_plan = {
            "version": "1.0.0",
            "application_skeleton": {"pages": [{"pageId": "orders"}]},
            "executable_details": {
                "page_implementation_contracts": [{"pageId": "orders"}],
                "api_contracts": [{"id": "orders-api"}],
            },
        }
        with patch(
            "app.agents.main.task_preparer_prompt._springboot_mybatis_skill_document"
        ) as backend_skill:
            prompt = build_task_preparation_prompt(
                project_plan,
                {
                    "backend": {"dir_structure": ["backend/src/main/java/"]},
                    "frontend": {"pages": ["frontend/src/pages/"]},
                },
                {
                    "planning_context_mode": "page",
                    "planning_unit_ids": ["page:orders"],
                    "required_unit_ids": ["page:orders"],
                },
                ["Task menu-task must not modify frontend/src/constants/menus.ts."],
            )

        backend_skill.assert_not_called()
        self.assertIn("Plan frontend page tasks only", prompt)
        self.assertIn("PageImplementationContract", prompt)
        self.assertNotIn("INJECTED springboot-mybatis-generate", prompt)
        self.assertIn(
            "No source-specific Skill is required for the current planning scope.",
            prompt,
        )
        self.assertNotIn('"backend"', prompt)
        self.assertNotIn("direct_endpoint_contracts", prompt)
        self.assertNotIn('"entity_designs"', prompt)
        self.assertNotIn("append the current menu item", prompt)
        self.assertIn("Never create a menu or route registration task", prompt)
        self.assertIn("Task menu-task must not modify", prompt)
        self.assertNotIn("For every owner=backend task", prompt)

    def test_backend_snapshot_projects_exact_existing_files_for_planning(self) -> None:
        """后端目录树必须在 Prompt 内投影为可直接判断 add/modify 的文件列表。"""

        snapshot = {
            "entrypoints": [
                {
                    "path": "backend/src/main/java/com/cmbchina/backend/Application.java",
                    "kind": "spring_application",
                }
            ],
            "high_value_files": [{"path": "backend/pom.xml"}],
            "backend": {
                "api_routes": [],
                "models": [],
                "dir_structure": (
                    "└── backend/\n"
                    "    ├── pom.xml\n"
                    "    └── src/\n"
                    "        └── main/\n"
                    "            ├── java/\n"
                    "            │   └── com/\n"
                    "            │       └── cmbchina/\n"
                    "            │           └── backend/\n"
                    "            │               └── Application.java\n"
                    "            └── resources/\n"
                    "                └── application.yml"
                ),
            },
        }

        projected = compact_workspace_snapshot(snapshot, scope="endpoint")

        self.assertEqual(
            projected["backend"]["existing_files"],
            [
                "backend/pom.xml",
                "backend/src/main/java/com/cmbchina/backend/Application.java",
                "backend/src/main/resources/application.yml",
            ],
        )
        self.assertNotIn(
            "backend/src/main/java/com/cmbchina/backend/OrderController.java",
            projected["backend"]["existing_files"],
        )

    def test_endpoint_prompt_injects_backend_skill_only_for_endpoint_scope(self) -> None:
        """只生成 endpoint 时注入后端技能和 endpoint 规则。"""

        project_plan = {
            "version": "1.0.0",
            "application_skeleton": {"data_sources": [{"id": "orders", "type": "database"}]},
            "executable_details": {
                "endpoint_detail_plans": [{"endpoint_id": "orders.list"}],
                "entity_designs": [{"entity_id": "Order", "data_source_type": "database"}],
                "api_contracts": [{"id": "orders-api"}],
            },
        }
        with patch(
            "app.agents.main.task_preparer_prompt._springboot_mybatis_skill_document",
            return_value="SKILL BODY",
        ) as backend_skill:
            prompt = build_task_preparation_prompt(
                project_plan,
                {"backend": {"dir_structure": ["backend/src/main/java/"]}},
                {
                    "planning_context_mode": "endpoint",
                    "planning_unit_ids": ["backend:endpoint:orders-api:orders.list"],
                    "required_unit_ids": ["backend:endpoint:orders-api:orders.list"],
                },
            )

        backend_skill.assert_called_once()
        self.assertIn("Plan endpoint/data tasks only", prompt)
        self.assertIn("SKILL BODY", prompt)
        self.assertIn("endpoint_detail_plans", prompt)
        self.assertIn("Use `[]` for a root", prompt)
        self.assertIn("JSON array of prerequisite task IDs", prompt)
        self.assertIn("same Unit", prompt)
        self.assertIn('"paths": ["workspace-relative/path"]', prompt)
        self.assertIn("Singular `path`", prompt)
        self.assertNotIn("page implementation contract", prompt.lower())

    def test_database_endpoint_prompt_requires_bootstrap_task(self) -> None:
        """数据库 endpoint 待准备 bootstrap 时必须生成幂等依赖校验任务。"""

        project_plan = {
            "executable_details": {
                "endpoint_detail_plans": [{"endpoint_id": "orders.list"}],
                "entity_designs": [
                    {"entity_id": "Order", "data_source_type": "database"}
                ],
                "api_contracts": [{"id": "orders-api"}],
            }
        }
        with patch(
            "app.agents.main.task_preparer_prompt._springboot_mybatis_skill_document",
            return_value="DATABASE SKILL BODY",
        ):
            prompt = build_task_preparation_prompt(
                project_plan,
                {
                    "high_value_files": [{"path": "backend/pom.xml"}],
                    "backend": {"dir_structure": "backend/pom.xml"},
                },
                {
                    "planning_context_mode": "endpoint",
                    "planning_unit_ids": [
                        "backend:bootstrap",
                        "backend:endpoint:orders-api:orders.list",
                    ],
                    "required_unit_ids": [
                        "backend:bootstrap",
                        "backend:endpoint:orders-api:orders.list",
                    ],
                },
            )

        self.assertIn("exactly one backend:bootstrap root task", prompt)
        self.assertIn("`backend:bootstrap::bootstrap`", prompt)
        self.assertIn("backend/pom.xml", prompt)
        self.assertIn("Only backend:bootstrap", prompt)
        self.assertIn("already_satisfied", prompt)

    def test_database_combined_prompt_requires_bootstrap_task(self) -> None:
        """前后端混合规划同样注入数据库 bootstrap 任务规则。"""

        project_plan = {
            "application_skeleton": {"pages": [{"pageId": "orders"}]},
            "executable_details": {
                "page_implementation_contracts": [{"pageId": "orders"}],
                "endpoint_detail_plans": [{"endpoint_id": "orders.list"}],
                "entity_designs": [
                    {"entity_id": "Order", "data_source_type": "database"}
                ],
                "api_contracts": [{"id": "orders-api"}],
            },
        }
        with patch(
            "app.agents.main.task_preparer_prompt._springboot_mybatis_skill_document",
            return_value="DATABASE SKILL BODY",
        ):
            prompt = build_task_preparation_prompt(
                project_plan,
                {"backend": {"dir_structure": "backend/pom.xml"}},
                {
                    "planning_context_mode": "combined",
                    "planning_unit_ids": [
                        "backend:bootstrap",
                        "backend:endpoint:orders-api:orders.list",
                        "page:orders",
                    ],
                    "required_unit_ids": [
                        "backend:bootstrap",
                        "backend:endpoint:orders-api:orders.list",
                        "page:orders",
                    ],
                },
            )

        self.assertIn("exactly one backend:bootstrap root task", prompt)
        self.assertIn("backend/pom.xml", prompt)
        self.assertIn("Only backend:bootstrap", prompt)

    def test_endpoint_prompt_keeps_executable_facts_once_and_target_routing_only(self) -> None:
        """endpoint 正文只出现在 executable_details，TargetBuildContext 仅保留路由字段。"""

        project_plan = {
            "architecture": {"backend_tech_stack": {"framework": "Spring Boot"}},
            "executable_details": {
                "endpoint_detail_plans": [
                    {
                        "endpoint_id": "orders.create",
                        "processing_logic": ["UNIQUE_ENDPOINT_LOGIC"],
                    }
                ],
                "entity_designs": [
                    {
                        "entity_id": "Order",
                        "data_source_type": "database",
                        "fields": [{"name": "UNIQUE_ENTITY_FIELD"}],
                    }
                ],
                "api_contracts": [{"id": "orders-api"}],
            },
        }
        build_context = {
            "target": {
                "type": "endpoint",
                "id": "orders.create",
                "api_contract_id": "orders-api",
            },
            "endpoint_detail": {"processing_logic": ["UNIQUE_ENDPOINT_LOGIC"]},
            "direct_endpoint_details": [
                {"processing_logic": ["UNIQUE_ENDPOINT_LOGIC"]}
            ],
            "endpoint_ids": ["orders.create"],
            "required_endpoint_ids": ["orders.create"],
            "entity_ids": ["Order"],
            "entity_designs": [
                {
                    "entity_id": "Order",
                    "data_source_type": "database",
                    "fields": [{"name": "UNIQUE_ENTITY_FIELD"}],
                }
            ],
            "planning_unit_ids": ["backend:endpoint:orders-api:orders.create"],
            "required_unit_ids": [
                "backend:bootstrap",
                "backend:endpoint:orders-api:orders.create",
            ],
            "source_refs": {"endpoint_detail": {"sha256": "sha-endpoint"}},
            "planning_context_mode": "endpoint",
        }

        with patch(
            "app.agents.main.task_preparer_prompt._springboot_mybatis_skill_document",
            return_value="DATABASE SKILL BODY",
        ):
            prompt = build_task_preparation_prompt(
                project_plan,
                {"backend": {"dir_structure": "backend/src/main/java"}},
                build_context,
            )

        prompt_context = scoped_prompt_build_context(build_context, "endpoint")
        self.assertEqual(
            prompt_context["required_unit_ids"],
            ["backend:endpoint:orders-api:orders.create"],
        )
        for omitted_key in (
            "endpoint_detail",
            "direct_endpoint_details",
            "entity_designs",
            "required_endpoint_ids",
            "planning_unit_ids",
            "planning_context_mode",
        ):
            self.assertNotIn(omitted_key, prompt_context)
        self.assertEqual(prompt.count("UNIQUE_ENDPOINT_LOGIC"), 1)
        self.assertEqual(prompt.count("UNIQUE_ENTITY_FIELD"), 1)
        self.assertEqual(prompt.count("owner=database"), 1)
        self.assertNotIn('"application_skeleton"', prompt)

    def test_endpoint_projection_includes_confirmed_entity_design(self) -> None:
        """endpoint 投影保留有序实体摘要、字段和数据库绑定。"""

        project_plan = {
            "architecture": {"backend_tech_stack": {"framework": "Spring Boot"}},
            "entity_detail_plans": [
                {
                    "status": "confirmed",
                    "entity_id": "Category",
                    "entity_name": "商品分类",
                    "data_source_type": "database",
                    "fields": [
                        {
                            "name": "category_name",
                            "label": "分类名称",
                            "type": "text",
                            "required": True,
                        }
                    ],
                    "database_design": {
                        "database_name": "xcode",
                        "matched_table": "category",
                        "bindings": [
                            {
                                "entity_field": "category_name",
                                "table_column": "category_name",
                            }
                        ],
                    },
                }
            ],
            "api_contracts": [
                {
                    "id": "category_api",
                    "entity_ids": ["Category"],
                    "endpoints": [
                        {"id": "category_api.create", "method": "POST"}
                    ],
                }
            ],
        }
        build_context = {
            "target": {
                "type": "endpoint",
                "id": "category_api.create",
                "api_contract_id": "category_api",
            },
            "direct_endpoint_details": [
                {
                    "api_contract_id": "category_api",
                    "endpoint_id": "category_api.create",
                }
            ],
            "endpoint_ids": ["category_api.create"],
            "entity_ids": ["Category"],
            "required_unit_ids": [
                "backend:endpoint:category_api:category_api.create"
            ],
            "planning_context_mode": "endpoint",
        }

        projected = _task_preparation_project_plan(project_plan, build_context)

        designs = projected["executable_details"]["entity_designs"]
        self.assertEqual([item["entity_id"] for item in designs], ["Category"])
        self.assertEqual(designs[0]["fields"][0]["name"], "category_name")
        self.assertEqual(
            designs[0]["database_design"]["matched_table"],
            "category",
        )
        self.assertNotIn("application_skeleton", projected)

    def test_external_api_endpoint_prompt_uses_external_skill_only(self) -> None:
        """外部 API endpoint 只注入外部集成规则，不误用 MyBatis。"""

        project_plan = {
            "application_skeleton": {
                "data_sources": [{"id": "weather", "type": "external_api"}]
            },
            "executable_details": {
                "endpoint_detail_plans": [{"endpoint_id": "weather.get"}],
                "entity_designs": [
                    {"entity_id": "Weather", "data_source_type": "external_api"}
                ],
                "api_contracts": [{"id": "weather-api"}],
            },
        }
        with (
            patch(
                "app.agents.main.task_preparer_prompt._springboot_mybatis_skill_document"
            ) as database_skill,
            patch(
                "app.agents.main.task_preparer_prompt._external_api_skill_document",
                return_value="EXTERNAL SKILL BODY",
            ) as external_skill,
        ):
            prompt = build_task_preparation_prompt(
                project_plan,
                {
                    "backend": {"dir_structure": ["backend/src/main/java/"]},
                    "frontend": {"pages": ["frontend/src/pages/"]},
                },
                {
                    "planning_context_mode": "endpoint",
                    "planning_unit_ids": ["backend:endpoint:weather-api:weather.get"],
                    "required_unit_ids": ["backend:endpoint:weather-api:weather.get"],
                },
            )

        self.assertEqual(endpoint_source_types(project_plan), {"external_api"})
        database_skill.assert_not_called()
        external_skill.assert_called_once()
        self.assertIn("EXTERNAL SKILL BODY", prompt)
        self.assertIn("`upstream`, `mapping`, `service`, and `controller`", prompt)
        self.assertIn("external_api", prompt)
        self.assertIn("For every owner=backend task", prompt)
        self.assertIn("database, and external_api tasks", prompt)
        self.assertNotIn("INJECTED springboot-mybatis-generate", prompt)
        self.assertNotIn("DATABASE BOOTSTRAP TASK IS REQUIRED", prompt)

    def test_mixed_endpoint_prompt_injects_exact_source_skill_union(self) -> None:
        """混合 endpoint 按实体来源同时注入三类规则，但保留前后端边界。"""

        project_plan = {
            "application_skeleton": {
                "data_sources": [
                    {"id": "database", "type": "database"},
                    {"id": "external_api", "type": "external_api"},
                    {"id": "static", "type": "static"},
                ]
            },
            "executable_details": {
                "endpoint_detail_plans": [{"endpoint_id": "dashboard.get"}],
                "entity_designs": [
                    {"entity_id": "Order", "data_source_type": "database"},
                    {"entity_id": "Weather", "data_source_type": "external_api"},
                    {"entity_id": "Notice", "data_source_type": "static"},
                ],
                "api_contracts": [{"id": "dashboard-api"}],
            },
        }
        with (
            patch(
                "app.agents.main.task_preparer_prompt._springboot_mybatis_skill_document",
                return_value="DATABASE SKILL BODY",
            ) as database_skill,
            patch(
                "app.agents.main.task_preparer_prompt._external_api_skill_document",
                return_value="EXTERNAL SKILL BODY",
            ) as external_skill,
            patch(
                "app.agents.main.task_preparer_prompt._static_data_skill_document",
                return_value="STATIC SKILL BODY",
            ) as static_skill,
        ):
            prompt = build_task_preparation_prompt(
                project_plan,
                {
                    "backend": {"dir_structure": ["backend/src/main/java/"]},
                    "frontend": {"api_clients": ["frontend/src/apis/"]},
                },
                {
                    "planning_context_mode": "endpoint",
                    "planning_unit_ids": [
                        "backend:endpoint:dashboard-api:dashboard.get",
                        "frontend:data:static",
                    ],
                    "required_unit_ids": [
                        "backend:endpoint:dashboard-api:dashboard.get",
                        "frontend:data:static",
                    ],
                },
            )

        self.assertEqual(
            endpoint_source_types(project_plan),
            {"database", "external_api", "static"},
        )
        database_skill.assert_called_once()
        external_skill.assert_called_once()
        static_skill.assert_called_once()
        for marker in ("DATABASE SKILL BODY", "EXTERNAL SKILL BODY", "STATIC SKILL BODY"):
            self.assertIn(marker, prompt)
        self.assertLess(prompt.index("DATABASE SKILL BODY"), prompt.index("EXTERNAL SKILL BODY"))
        self.assertLess(prompt.index("EXTERNAL SKILL BODY"), prompt.index("STATIC SKILL BODY"))
        self.assertLess(prompt.index("STATIC SKILL BODY"), prompt.index("## 7. Forbidden Output"))
        self.assertIn("`objects`, `repository`, `service`, `controller`", prompt)
        self.assertIn("`upstream`, `mapping`, `service`, and `controller`", prompt)
        self.assertIn("`<frontendDataUnitId>::data-module`", prompt)

    def test_static_endpoint_prompt_uses_frontend_snapshot(self) -> None:
        """纯 static endpoint 只注入前端快照和静态 Skill。"""

        project_plan = {
            "executable_details": {
                "entity_designs": [
                    {"entity_id": "Notice", "data_source_type": "static"}
                ],
                "api_contracts": [{"id": "notice-api"}],
            }
        }
        prompt = build_task_preparation_prompt(
            project_plan,
            {
                "backend": {"dir_structure": ["backend/src/main/java/"]},
                "frontend": {
                    "api_clients": ["frontend/src/apis/"],
                    "dir_structure": "frontend/src/apis/noticeApi.ts",
                },
                "file_manifest": [
                    "backend/src/main/java/NoticeController.java",
                    "frontend/src/apis/noticeApi.ts",
                ],
            },
            {
                "planning_context_mode": "endpoint",
                "planning_unit_ids": ["frontend:data:static"],
                "required_unit_ids": ["frontend:data:static"],
            },
        )

        self.assertIn("All frontend paths are under `/frontend/`", prompt)
        self.assertIn("frontend/src/apis/noticeApi.ts", prompt)
        self.assertNotIn("backend/src/main/java/NoticeController.java", prompt)
        self.assertIn("frontend-static-data-generate", prompt)
        self.assertNotIn("springboot-mybatis-generate SKILL.md ---", prompt)

    def test_unit_inputs_filter_entity_designs_by_owner_source(self) -> None:
        """后端 endpoint 与前端 static Unit 只携带各自来源实体。"""

        build_context = {
            "target": {"type": "endpoint", "id": "dashboard.get"},
            "required_unit_ids": [
                "backend:endpoint:dashboard-api:dashboard.get",
                "frontend:data:static",
            ],
            "endpoint_ids": ["dashboard.get"],
            "entity_ids": ["Order", "Weather", "Notice"],
            "entity_designs": [
                {"entity_id": "Order", "data_source_type": "database"},
                {"entity_id": "Weather", "data_source_type": "external_api"},
                {"entity_id": "Notice", "data_source_type": "static"},
            ],
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

        backend_sources = units[
            "backend:endpoint:dashboard-api:dashboard.get"
        ]["source_refs"]["entity_designs"]
        static_sources = units["frontend:data:static"]["source_refs"]["entity_designs"]
        self.assertEqual(
            {item["data_source_type"] for item in backend_sources},
            {"database", "external_api"},
        )
        self.assertEqual(
            {item["data_source_type"] for item in static_sources},
            {"static"},
        )
        self.assertNotEqual(
            units["backend:endpoint:dashboard-api:dashboard.get"]["input_fingerprint"],
            units["frontend:data:static"]["input_fingerprint"],
        )

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

        prompt = build_task_preparation_prompt(
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
            "app.agents.main.task_preparer_prompt._springboot_mybatis_skill_document"
        ) as backend_skill:
            prompt = build_task_preparation_prompt(
                project_plan,
                {},
                {"required_unit_ids": ["frontend:data:orders", "page:orders"]},
            )

        backend_skill.assert_not_called()
        self.assertIn("frontend:data:*", prompt)
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
            "unit_id": "page:home",
            "owner": "frontend",
            "description": "新增首页",
            "change_scope": [{"operation": "add", "path": "src/pages/Home/index.tsx"}],
            "deliverables": [{"id": "page:home", "kind": "frontend.page", "target_id": "home", "paths": ["src/pages/Home/index.tsx"], "provides": ["home.render"]}]
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
        self.assertEqual(tasks[0]["target_files"], ["src/pages/Home/index.tsx"])
        self.assertEqual(plan["workspace_analysis"]["inspection_status"], "completed")
        self.assertEqual(plan["prepared_by"]["model"], "test-model")

    def test_malformed_deliverable_is_regenerated_with_precise_feedback(self) -> None:
        """错误的 path 结构必须以精确字段错误回灌并在下一轮修正。"""

        invalid_response = json.dumps(
            {
                "tasks": [
                    {
                        "id": "task-page-api",
                        "unit_id": "page:test-page-1",
                        "owner": "frontend",
                        "description": "创建页面 API 模块。",
                        "change_scope": ["frontend/src/apis/testPage1.ts"],
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
        valid_response = json.dumps(
            {
                "tasks": [
                    {
                        "id": "task-page-api",
                        "unit_id": "page:test-page-1",
                        "owner": "frontend",
                        "description": "创建页面 API 模块。",
                        "change_scope": ["frontend/src/apis/testPage1.ts"],
                        "deliverables": [
                            {
                                "id": "api:test-page-1",
                                "kind": "frontend.api_module",
                                "target_id": "test-page-1",
                                "paths": ["frontend/src/apis/testPage1.ts"],
                                "provides": ["test-page-1.api"],
                            }
                        ],
                    }
                ]
            }
        )
        chat_model = Mock()
        bound_model = Mock()
        chat_model.bind.return_value = bound_model
        bound_model.invoke.side_effect = [
            SimpleNamespace(
                content=invalid_response, usage_metadata=None, response_metadata={}
            ),
            SimpleNamespace(
                content=valid_response, usage_metadata=None, response_metadata={}
            ),
        ]
        settings = SimpleNamespace(
            model_name="test-model",
            model_api_name="test-model",
            default_max_tokens=4096,
            build_task_plan_max_retries=2,
        )

        with (
            patch(
                "app.agents.main.task_preparer.Settings.from_env",
                return_value=settings,
            ),
            patch(
                "app.agents.main.task_preparer.create_chat_model",
                return_value=chat_model,
            ),
        ):
            plan = prepare_build_tasks_with_main_agent(
                {"version": "1.0.0"},
                build_context={
                    "planning_context_mode": "page",
                    "required_unit_ids": ["page:test-page-1"],
                },
            )

        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])
        self.assertEqual(plan["prepared_by"]["generation_attempt"], 2)
        retry_prompt = bound_model.invoke.call_args_list[1].args[0]
        self.assertIn("deliverables[0].id is required", retry_prompt)
        self.assertIn('singular field "path" is not supported', retry_prompt)
        self.assertNotIn("must declare at least one deliverable", retry_prompt)

    def test_invalid_candidate_is_automatically_regenerated(self) -> None:
        """平台边界错误回喂模型自动重生成，不要求用户修正任务拆分。"""

        invalid_response = json.dumps(
            {
                "tasks": [
                    {
                        "id": "menu-task",
                        "unit_id": "page:dashboard",
                        "owner": "frontend",
                        "description": "修改模板菜单",
                        "change_scope": [
                            {
                                "operation": "modify",
                                "path": "frontend/src/constants/menus.ts",
                            }
                        ],
                    }
                ]
            }
        )
        valid_response = json.dumps(
            {
                "tasks": [
                    {
                        "id": "page-task",
                        "unit_id": "page:dashboard",
                        "owner": "frontend",
                        "description": "实现页面内容",
                        "change_scope": [
                            {
                                "operation": "modify",
                                "path": "frontend/src/pages/Dashboard/index.tsx",
                            }
                        ],
                        "deliverables": [
                            {
                                "id": "page:dashboard",
                                "kind": "frontend.page",
                                "target_id": "dashboard",
                                "paths": ["frontend/src/pages/Dashboard/index.tsx"],
                                "provides": ["dashboard.render"],
                            }
                        ],
                    }
                ]
            }
        )
        chat_model = Mock()
        bound_model = Mock()
        chat_model.bind.return_value = bound_model
        bound_model.invoke.side_effect = [
            SimpleNamespace(
                content=invalid_response,
                usage_metadata=None,
                response_metadata={},
            ),
            SimpleNamespace(
                content=valid_response,
                usage_metadata=None,
                response_metadata={},
            ),
        ]
        settings = SimpleNamespace(
            model_name="test-model",
            model_api_name="test-model",
            default_max_tokens=4096,
            build_task_plan_max_retries=2,
        )
        with (
            patch(
                "app.agents.main.task_preparer.Settings.from_env",
                return_value=settings,
            ),
            patch(
                "app.agents.main.task_preparer.create_chat_model",
                return_value=chat_model,
            ) as create_model,
        ):
            plan = prepare_build_tasks_with_main_agent(
                {
                    "version": "1.0.0",
                    "application_skeleton": {
                        "data_sources": [{"id": "main", "type": "static"}]
                    },
                },
                build_context={"required_unit_ids": ["page:dashboard"]},
            )

        self.assertEqual([task["id"] for task in tasks_from_build_task_plan(plan)], ["page-task"])
        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])
        self.assertEqual(plan["prepared_by"]["generation_attempt"], 2)
        self.assertEqual(create_model.call_count, 2)
        self.assertEqual(bound_model.invoke.call_count, 2)
        first_prompt = bound_model.invoke.call_args_list[0].args[0]
        second_prompt = bound_model.invoke.call_args_list[1].args[0]
        self.assertIn("Never create a menu or route registration task", first_prompt)
        self.assertNotIn("AUTOMATIC REGENERATION FEEDBACK", first_prompt)
        self.assertIn("AUTOMATIC REGENERATION FEEDBACK", second_prompt)
        self.assertIn("Task menu-task", second_prompt)
        self.assertIn("frontend/src/constants/menus.ts", second_prompt)

    def test_missing_database_bootstrap_is_automatically_regenerated(self) -> None:
        """数据库候选遗漏 bootstrap 时必须通过确定性错误触发自动重生成。"""

        endpoint_task = {
            "id": "orders-objects",
            "unit_id": "backend:endpoint:orders-api:orders.list",
            "owner": "backend",
            "description": "实现订单对象层。",
            "change_scope": [
                {
                    "operation": "add",
                    "path": "backend/src/main/java/demo/Order.java",
                }
            ],
            "deliverables": [
                {
                    "id": "domain:order",
                    "kind": "backend.domain_mapping",
                    "target_id": "Order",
                    "paths": ["backend/src/main/java/demo/Order.java"],
                    "provides": ["order.domain"],
                }
            ],
        }
        invalid_response = json.dumps({"tasks": [endpoint_task]})
        valid_response = json.dumps(
            {
                "tasks": [
                    {
                        "id": "backend-bootstrap",
                        "unit_id": "backend:bootstrap",
                        "owner": "backend",
                        "description": "幂等校验数据库后端依赖和基础配置。",
                        "dependencies": [],
                        "change_scope": [
                            {
                                "operation": "modify",
                                "path": "backend/pom.xml",
                            }
                        ],
                        "deliverables": [
                            {
                                "id": "bootstrap:backend",
                                "kind": "backend.bootstrap",
                                "target_id": "backend:bootstrap",
                                "paths": ["backend/pom.xml"],
                                "provides": ["backend.bootstrap"],
                            }
                        ],
                    },
                    endpoint_task,
                ]
            }
        )
        chat_model = Mock()
        bound_model = Mock()
        chat_model.bind.return_value = bound_model
        bound_model.invoke.side_effect = [
            SimpleNamespace(content=invalid_response, usage_metadata=None, response_metadata={}),
            SimpleNamespace(content=valid_response, usage_metadata=None, response_metadata={}),
        ]
        settings = SimpleNamespace(
            model_name="test-model",
            model_api_name="test-model",
            default_max_tokens=4096,
            build_task_plan_max_retries=2,
        )
        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "backend:bootstrap": {"id": "backend:bootstrap", "kind": "backend"},
                "backend:endpoint:orders-api:orders.list": {
                    "id": "backend:endpoint:orders-api:orders.list",
                    "kind": "backend",
                },
            },
            "unit_graph": {
                "schema_version": "build-unit-graph.v3",
                "nodes": [
                    "backend:bootstrap",
                    "backend:endpoint:orders-api:orders.list",
                ],
                "edges": [
                    {
                        "from": "backend:bootstrap",
                        "to": "backend:endpoint:orders-api:orders.list",
                        "type": "depends_on",
                    }
                ],
                "validation": {"is_valid": True, "errors": []},
            },
        }
        build_context = {
            "target": {"type": "endpoint", "id": "orders.list"},
            "endpoint_ids": ["orders.list"],
            "entity_ids": ["Order"],
            "entity_designs": [
                {"entity_id": "Order", "data_source_type": "database"}
            ],
            "planning_unit_ids": [
                "backend:bootstrap",
                "backend:endpoint:orders-api:orders.list",
            ],
            "required_unit_ids": [
                "backend:bootstrap",
                "backend:endpoint:orders-api:orders.list",
            ],
        }
        project_plan = {
            "executable_details": {
                "entity_designs": build_context["entity_designs"],
                "api_contracts": [],
            }
        }
        with (
            patch("app.agents.main.task_preparer.Settings.from_env", return_value=settings),
            patch("app.agents.main.task_preparer.create_chat_model", return_value=chat_model),
        ):
            plan = prepare_build_tasks_with_main_agent(
                project_plan,
                build_context=build_context,
                build_task_plan=base_plan,
            )

        self.assertTrue(plan["task_graph"]["validation"]["is_valid"])
        self.assertEqual(plan["prepared_by"]["generation_attempt"], 2)
        self.assertEqual(
            {task["unit_id"] for task in tasks_from_build_task_plan(plan)},
            {
                "backend:bootstrap",
                "backend:endpoint:orders-api:orders.list",
            },
        )
        retry_prompt = bound_model.invoke.call_args_list[1].args[0]
        self.assertIn("AUTOMATIC REGENERATION FEEDBACK", retry_prompt)
        self.assertIn("requires a backend:bootstrap task", retry_prompt)

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

    def test_task_preparer_binds_configured_max_tokens(self) -> None:
        """任务规划调用必须显式传递 AGENT_MAX_TOKENS，避免采用 Provider 的短输出默认值。"""

        model = Mock()
        bound_model = Mock()
        model.bind.return_value = bound_model
        bound_model.invoke.return_value = SimpleNamespace(
            content='{"tasks": [{"id": "task", "unit_id": "frontend:api-client", "owner": "frontend", "description": "任务", "change_scope": [{"operation": "modify", "path": "src/task.ts"}], "deliverables": [{"id": "api:task", "kind": "frontend.shared_capability", "target_id": "frontend:api-client", "paths": ["src/task.ts"], "provides": ["task.capability"]}]}]}',
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
                    "can_run_in_parallel": False,
                    "parallel_reason": "修改共享路由表，需要串行。",
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
        self.assertEqual(task["target_files"], ["src/pages/Login/index.tsx", "src/router/index.ts"])
        self.assertEqual(task["change_scope"][0]["operation"], "add")
        self.assertEqual(task["impact_scope"]["affected_modules"], ["pages", "router"])
        self.assertFalse(task["can_run_in_parallel"])
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
        self.assertIn("must not add template page entry", str(plan["task_graph"]["validation"]["errors"]))
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

    def test_compiles_unit_dependencies_and_source_refs(self) -> None:
        """页面任务只继承前端公共 Unit，后端 Unit 仅保留接口来源引用。"""

        base_plan = {
            "schema_version": "build-dag.v3",
            "build_units": {
                "frontend:api-client": {"id": "frontend:api-client", "kind": "frontend"},
                "backend:endpoint:orders": {
                    "id": "backend:endpoint:orders",
                    "kind": "backend",
                },
                "page:orders": {"id": "page:orders", "kind": "page"},
            },
            "unit_graph": {
                "schema_version": "build-unit-graph.v3",
                "nodes": [
                    "frontend:api-client",
                    "backend:endpoint:orders",
                    "page:orders",
                ],
                "edges": [
                    {"from": "frontend:api-client", "to": "page:orders", "type": "depends_on"},
                    {
                        "from": "backend:endpoint:orders",
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
                "backend:endpoint:orders",
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
                        "unit_id": "backend:endpoint:orders",
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
            plan["build_units"]["backend:endpoint:orders"]["source_refs"],
            {
                "type": "technical_plan_endpoint",
                "target": {"type": "page", "id": "orders"},
                "technical_plan_endpoint": {},
                "technical_plan_endpoints": [
                    {"id": "orders_api.list", "api_contract_id": "orders-api"}
                ],
                "endpoint_ids": ["orders_api.list"],
                "entity_designs": [],
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
