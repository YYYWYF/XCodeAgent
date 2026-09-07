from __future__ import annotations

import unittest

from app.agents.data_source.generator import _data_source_generation_prompt
from app.agents.data_source.prompt_context import (
    execution_task_packet,
    task_implementation_contract,
    task_required_instruction_paths,
    task_required_skill_paths,
)
from app.agents.data_source.workspace_context import backend_workspace_context
from app.services.build_unit_compiler import apply_unit_compilation


def _endpoint_design(source_specs: list[dict]) -> dict:
    """把测试所需来源规整为自包含字段映射产物。"""

    snapshots: list[dict] = []
    field_mappings: list[dict] = []
    scene_entities: dict[str, dict] = {}
    for index, source in enumerate(source_specs):
        source_type = str(source.get("data_source_type") or "database")
        entity_id = str(source.get("entity_id") or f"Entity{index}")
        source_id = str(source.get("source_id") or f"{source_type}-{index}")
        scene = scene_entities.setdefault(entity_id, {
            "id": f"scene:{entity_id}", "name": entity_id,
            "templateEntityId": entity_id, "fields": []
        })
        if source_type == "database":
            database = source.get("database_design") or {}
            table = str(database.get("matched_table") or entity_id.casefold())
            rows = database.get("bindings") or [{"entity_field": "name", "table_column": "name"}]
            snapshots.append({
                "sourceType": "database",
                "sourceId": source_id,
                "name": table,
                "details": {
                    "databaseMode": "direct_mysql",
                    "table": table,
                    "columns": [row.get("table_column") for row in rows],
                },
            })
            for row in rows:
                entity_field = str(row.get("entity_field") or "name")
                field_id = f"{entity_id}:{entity_field}"
                scene["fields"].append({"id": field_id, "name": entity_field, "type": "string"})
                field_mappings.append({
                    "endpointField": {
                        "side": "request", "location": "request_body",
                        "path": f"{entity_id}.{entity_field}", "type": "string", "required": True,
                        "description": "",
                    },
                    "mappingType": "through_entity",
                    "entityField": {
                        "entityId": scene["id"], "fieldId": field_id,
                        "path": entity_field, "type": "string",
                    },
                    "sourceField": {
                        "sourceType": "database", "sourceId": source_id, "schema": "app",
                        "table": table, "column": str(row.get("table_column") or entity_field),
                        "type": "string", "usage": "write",
                    },
                })
            continue

        external = source.get("external_api_design") or {}
        operation = (external.get("operations") or [{}])[0]
        api_info = operation.get("api_info") or {}
        connection = operation.get("effective_connection") or external.get("connection") or {}
        operation_id = str(operation.get("operation_id") or f"operation-{index}")
        operation_mappings = operation.get("field_mappings") or [{
            "entity_field": "value", "source_field": "value", "rule": "direct",
        }]
        snapshots.append({
            "sourceType": source_type,
            "sourceId": source_id,
            "name": str(source.get("entity_name") or entity_id),
            "details": {
                "connection": {
                    "baseUrl": connection.get("base_url") or "https://example.invalid",
                    "baseUrlConfigKey": connection.get("base_url_config_key") or "upstream.url",
                    "timeoutMs": connection.get("timeout_ms") or 10000,
                    "headers": connection.get("headers") or [],
                },
                "operation": {
                    "operationId": operation_id,
                    "name": operation.get("name") or operation_id,
                    "method": api_info.get("method") or "GET",
                    "path": api_info.get("path") or "/values",
                    "pathParameters": [],
                    "queryParameters": api_info.get("parameters") or [],
                    "requestStructure": api_info.get("request_shape") or {},
                    "responseStructure": api_info.get("response_shape") or {},
                },
            },
        })
        for mapping in operation_mappings:
            entity_field = str(mapping.get("entity_field") or "value")
            source_path = str(mapping.get("source_field") or "value")
            field_id = f"{entity_id}:{entity_field}"
            scene["fields"].append({"id": field_id, "name": entity_field, "type": "string"})
            field_mappings.append({
                "endpointField": {
                    "side": "response", "location": "response_body",
                    "path": f"result.{entity_field}", "type": "string", "required": True,
                    "description": "",
                },
                "mappingType": "through_entity",
                "entityField": {
                    "entityId": scene["id"], "fieldId": field_id,
                    "path": entity_field, "type": "string",
                },
                "sourceField": {
                    "sourceType": "external_api", "sourceId": source_id,
                    "directoryId": "products", "operationId": operation_id,
                    "section": "response_body", "path": source_path, "type": "string",
                },
            })
    return {
        "schemaVersion": "endpoint-field-mapping.v1",
        "artifactType": "endpoint-field-mapping",
        "status": "confirmed",
        "confirmationStatus": "confirmed",
        "apiContractId": "category_api",
        "endpointId": "category.create",
        "endpointContract": {
            "id": "category.create", "method": "POST", "path": "/api/categories",
        },
        "sceneEntities": list(scene_entities.values()),
        "fieldMappings": field_mappings,
        "sourceSnapshots": snapshots,
        "basedOn": [{"artifactKey": "technical-plan", "sha256": "a" * 64}],
        "confirmedAt": "2026-09-04T00:00:00Z",
    }


def _task(*, designs: list[dict]) -> dict:
    """构造携带当前 Endpoint API 设计和调度冗余字段的后端任务。"""

    endpoint_designs = (
        designs
        if designs and all(item.get("schemaVersion") for item in designs)
        else [_endpoint_design(designs)]
    )

    return {
        "id": "backend:endpoint:category_api:category.create::Category::objects",
        "unit_id": "backend:endpoint:category_api:category.create",
        "title": "创建分类对象",
        "description": (
            "1. 读取 backend/src/main/java/Category.java 并实现当前分类对象。\n"
            "2. 保留现有包结构和 Java 8 语法。"
        ),
        "allowed_paths": ["backend/src/main/java/Category.java"],
        "target_files": ["backend/src/main/java/Category.java"],
        "change_scope": [
            {
                "operation": "add",
                "path": "backend/src/main/java/Category.java",
                "description": "新增分类对象。",
            }
        ],
        "source_refs": {
            "type": "endpoint_detail",
            "target": {
                "type": "endpoint",
                "id": "category.create",
                "api_contract_id": "category_api",
            },
            "endpoint_ids": ["category.create"],
            "endpoint_designs": endpoint_designs,
        },
        "acceptance_checks": [{"description": "UNRELATED_ACCEPTANCE_SENTINEL"}],
        "impact_scope": {"summary": "UNRELATED_IMPACT_SENTINEL"},
        "status": "running",
    }


def _external_product_design() -> dict:
    """构造与当前商品外部 API JSON 一致的无样例值构建摘要。"""

    return {
        "entity_id": "Category",
        "entity_name": "Product",
        "data_source_type": "external_api",
        "fields": [
            {"name": "name", "type": "text", "required": True},
            {"name": "price", "type": "decimal", "required": True},
            {
                "name": "status",
                "type": "enum",
                "required": True,
                "enum_values": ["on", "off"],
            },
            {"name": "created_at", "type": "datetime", "required": True},
        ],
        "external_api_design": {
            "connection": {
                "base_url": "http://99.17.197.63:8090",
                "base_url_config_key": "product.url",
                "timeout_ms": 10000,
                "headers": [],
            },
            "operations": [
                {
                    "operation_id": "external-op-product-list",
                    "name": "查询商品列表",
                    "endpoint_refs": [
                        {
                            "api_contract_id": "category_api",
                            "endpoint_id": "category.create",
                        }
                    ],
                    "effective_connection": {
                        "base_url": "http://99.17.197.63:8090",
                        "base_url_config_key": "product.url",
                        "timeout_ms": 10000,
                        "headers": [],
                    },
                    "api_info": {
                        "method": "POST",
                        "path": "/v1/product/list",
                        "parameters": [],
                        "headers": [],
                        "request_shape": {
                            "root_type": "object",
                            "fields": [
                                {"path": "keyword", "type": "string"},
                                {"path": "pageSize", "type": "integer"},
                                {"path": "current", "type": "integer"},
                            ],
                        },
                        "response_shape": {
                            "root_type": "object",
                            "fields": [
                                {"path": "total", "type": "integer"},
                                {"path": "list[]", "type": "array"},
                                {"path": "list[].name", "type": "string"},
                                {"path": "list[].price", "type": "decimal"},
                                {"path": "list[].status", "type": "string"},
                                {"path": "list[].created_at", "type": "string"},
                            ],
                        },
                    },
                    "response_handling": {
                        "entity_payload": True,
                        "cardinality": "object",
                        "payload_path": "",
                        "success_status_codes": [200],
                    },
                    "mapped_entity_path": "list[]",
                    "field_mappings": [
                        {
                            "entity_field": name,
                            "source_field": f"list[].{name}",
                            "rule": "nested_match",
                        }
                        for name in ("name", "price", "status", "created_at")
                    ],
                }
            ],
        },
    }


def _project_plan() -> dict:
    """构造同时包含目标和无关正式产物的 TechnicalPlan。"""

    return {
        "pages": [{"id": "unrelated", "name": "UNRELATED_PAGE_SENTINEL"}],
        "api_contracts": [
            {
                "id": "category_api",
                "entity_ids": ["Category"],
                "base_path": "/api/categories",
                "schemas": {
                    "CategoryInput": {
                        "type": "object",
                        "properties": {
                            "category": {"$ref": "#/schemas/CategoryValue"}
                        },
                    },
                    "CategoryValue": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                    "UnusedSchema": {"description": "UNRELATED_SCHEMA_SENTINEL"},
                },
                "endpoints": [
                    {
                        "id": "category.create",
                        "method": "POST",
                        "path": "/api/categories",
                        "request_schema_ref": "CategoryInput",
                        "response_schema_ref": "CategoryValue",
                    },
                    {
                        "id": "category.delete",
                        "summary": "UNRELATED_ENDPOINT_SENTINEL",
                    },
                ],
            },
            {
                "id": "weather_api",
                "endpoints": [
                    {"id": "weather.get", "summary": "UNRELATED_CONTRACT_SENTINEL"}
                ],
            },
        ],
        "endpoint_detail_plans": [
            {
                "api_contract_id": "category_api",
                "endpoint_id": "category.create",
                "status": "confirmed",
                "processing_logic": ["创建分类。"],
            },
            {
                "api_contract_id": "weather_api",
                "endpoint_id": "weather.get",
                "summary": "UNRELATED_DETAIL_SENTINEL",
            },
        ],
        "entity_detail_plans": [
            {
                "entity_id": "Category",
                "status": "confirmed",
                "data_source_type": "database",
                "database_design": {
                    "matched_table": "category",
                    "bindings": [
                        {"entity_field": "name", "table_column": "category_name"}
                    ],
                },
            },
            {
                "entity_id": "Weather",
                "status": "confirmed",
                "data_source_type": "external_api",
                "summary": "UNRELATED_ENTITY_SENTINEL",
            },
        ],
    }


def _workspace_snapshot() -> dict:
    """构造任务构建与执行阶段共同使用的完整 WorkspaceSnapshot。"""

    return {
        "workspace_revision": "UNRELATED_WORKSPACE_REVISION",
        "high_value_files": [
            {"path": "backend/pom.xml"},
            {"path": "frontend/package.json"},
        ],
        "entrypoints": [
            {"path": "backend/src/main/java/demo/Application.java"},
            {"path": "frontend/src/main.tsx"},
        ],
        "backend": {
            "dir_structure": (
                "└── backend/\n"
                "    ├── pom.xml\n"
                "    └── src/\n"
                "        └── main/\n"
                "            └── java/"
            ),
        },
        "frontend": {"dir_structure": "UNRELATED_FRONTEND_WORKSPACE"},
    }


class DataSourceGenerationPromptTests(unittest.TestCase):
    """验证 DataSource 执行提示词的任务级 Skill 路由与最小上下文。"""

    def test_task_skill_paths_follow_exact_api_source_types(self) -> None:
        """每个任务按 Endpoint API 设计来源加载生成与模板边界 Skill。"""

        database = _task(
            designs=[{"entity_id": "Category", "data_source_type": "database"}],
        )
        external = _task(
            designs=[{"entity_id": "Weather", "data_source_type": "external_api"}],
        )
        mixed = _task(
            designs=[
                {"entity_id": "Category", "data_source_type": "database"},
                {"entity_id": "Weather", "data_source_type": "external_api"},
            ],
        )

        expected_skills = [
            "/.xcodeagent/builtin-skills/springboot-backend-generate/SKILL.md",
            "/.xcodeagent/builtin-skills/"
            "springboot-template-modification-boundary/SKILL.md",
        ]
        self.assertEqual(task_required_skill_paths(database), expected_skills)
        self.assertEqual(task_required_skill_paths(external), expected_skills)
        self.assertEqual(task_required_skill_paths(mixed), expected_skills)
        database_paths = task_required_instruction_paths(database)
        external_paths = task_required_instruction_paths(external)
        mixed_paths = task_required_instruction_paths(mixed)
        self.assertEqual(database_paths[:2], expected_skills)
        self.assertEqual(external_paths[:2], expected_skills)
        self.assertEqual(mixed_paths[:2], expected_skills)
        self.assertIn(
            "/.xcodeagent/builtin-skills/springboot-backend-generate/"
            "references/database/layer-implementation.md",
            database_paths,
        )
        self.assertIn(
            "/.xcodeagent/builtin-skills/springboot-backend-generate/"
            "references/external-api/layer-implementation.md",
            external_paths,
        )
        self.assertIn(
            "/.xcodeagent/builtin-skills/springboot-backend-generate/"
            "references/database/layer-implementation.md",
            mixed_paths,
        )
        self.assertIn(
            "/.xcodeagent/builtin-skills/springboot-backend-generate/"
            "references/external-api/layer-implementation.md",
            mixed_paths,
        )
        mixed_bootstrap = dict(mixed)
        mixed_bootstrap["id"] = "backend:bootstrap::bootstrap"
        mixed_bootstrap["unit_id"] = "backend:bootstrap"
        bootstrap_paths = task_required_instruction_paths(mixed_bootstrap)
        self.assertIn(
            "/.xcodeagent/builtin-skills/springboot-backend-generate/"
            "references/database/bootstrap.md",
            bootstrap_paths,
        )
        self.assertIn(
            "/.xcodeagent/builtin-skills/springboot-backend-generate/"
            "references/external-api/bootstrap.md",
            bootstrap_paths,
        )

    def test_static_backend_task_is_rejected(self) -> None:
        """非法 static 来源若误入 API 设计，应在调用模型前失败。"""

        task = _task(
            designs=[{"entity_id": "Notice", "data_source_type": "static"}],
        )
        with self.assertRaisesRegex(ValueError, "非法数据源类型：static"):
            task_required_skill_paths(task)

    def test_prompt_contains_only_execution_fields_and_targeted_artifacts(self) -> None:
        """提示词排除全局计划与调度字段，同时保留目标正式设计。"""

        task = _task(
            designs=[{
                "entity_id": "Category",
                "data_source_type": "database",
                "database_design": {
                    "matched_table": "category",
                    "bindings": [
                        {"entity_field": "name", "table_column": "category_name"}
                    ],
                },
            }],
        )
        prompt = _data_source_generation_prompt(
            project_plan=_project_plan(),
            workspace_snapshot=_workspace_snapshot(),
            tasks=[task],
        )

        self.assertIn("springboot-backend-generate/SKILL.md", prompt)
        self.assertIn("CategoryInput", prompt)
        self.assertIn("CategoryValue", prompt)
        self.assertIn('"table": "category"', prompt)
        for sentinel in (
            "UNRELATED_PAGE_SENTINEL",
            "UNRELATED_SCHEMA_SENTINEL",
            "UNRELATED_ENDPOINT_SENTINEL",
            "UNRELATED_CONTRACT_SENTINEL",
            "UNRELATED_DETAIL_SENTINEL",
            "UNRELATED_ENTITY_SENTINEL",
            "UNRELATED_ACCEPTANCE_SENTINEL",
            "UNRELATED_IMPACT_SENTINEL",
            "UNRELATED_BUILD_SUMMARY",
        ):
            self.assertNotIn(sentinel, prompt)
        self.assertNotIn("ProjectPlan context:", prompt)
        self.assertNotIn("BuildTaskPlan summary:", prompt)
        self.assertNotIn("Code graph navigation contract", prompt)
        self.assertIn("outer_integration_test_only", prompt)
        self.assertIn('"sceneEntities"', prompt)
        self.assertIn('"mappingType": "through_entity"', prompt)
        self.assertIn("Backend Workspace Context:", prompt)
        self.assertIn('"backend_working_directory": "/backend"', prompt)
        self.assertIn('"backend_directory_structure"', prompt)
        self.assertIn("└── backend/\\n", prompt)
        self.assertIn("└── java/", prompt)
        self.assertIn("authoritative and trustworthy navigation evidence", prompt)
        self.assertIn("You may read any relevant listed file", prompt)
        self.assertIn("path metadata, not file contents or write authorization", prompt)
        self.assertIn("prefer the current filesystem result", prompt)
        self.assertIn("execution_steps as the ordered task sequence", prompt)
        self.assertIn("process those items in array order", prompt)
        self.assertIn("planner's snapshot-time existence classification", prompt)
        self.assertIn("only to detect WorkspaceSnapshot drift", prompt)
        self.assertIn("If an add target now exists", prompt)
        self.assertIn("If a modify target is now missing", prompt)
        self.assertIn("follow the already-classified add or modify action", prompt)
        self.assertIn("Leave a fully satisfying target unchanged", prompt)
        self.assertIn("add or correct only the missing behavior", prompt)
        self.assertIn("only when every target in the task already satisfies", prompt)
        self.assertIn("another target requires a permitted creation", prompt)
        self.assertNotIn("UNRELATED_WORKSPACE_REVISION", prompt)
        self.assertNotIn("UNRELATED_FRONTEND_WORKSPACE", prompt)

    def test_endpoint_contract_keeps_only_current_api_behavior_and_bindings(self) -> None:
        """Endpoint 实现契约只保留当前 API、行为与已确认来源绑定。"""

        task = _task(
            designs=[{
                "entity_id": "Category",
                "data_source_type": "database",
                "database_design": {
                    "matched_table": "category",
                    "bindings": [
                        {"entity_field": "name", "table_column": "category_name"}
                    ],
                },
            }],
        )
        context = task_implementation_contract(_project_plan(), task)

        self.assertEqual(context["kind"], "endpoint")
        self.assertEqual(context["api_contract"]["id"], "category_api")
        self.assertEqual(
            [item["id"] for item in context["api_contract"]["endpoints"]],
            ["category.create"],
        )
        self.assertEqual(
            set(context["api_contract"]["schemas"]),
            {"CategoryInput", "CategoryValue"},
        )
        design = context["api_design"]
        self.assertEqual(design["schemaVersion"], "endpoint-field-mapping.v1")
        self.assertEqual(design["sceneEntities"][0]["templateEntityId"], "Category")
        self.assertEqual(design["fieldMappings"][0]["mappingType"], "through_entity")
        self.assertEqual(
            next(
                mapping["sourceField"]
                for mapping in design["fieldMappings"]
                if mapping.get("sourceField", {}).get("sourceType") == "database"
            )["table"],
            "category",
        )
        self.assertNotIn("entity_designs", context)

    def test_endpoint_contract_projects_platform_authorization_constraints(self) -> None:
        """后端执行任务包只消费平台注入的 Endpoint 权限切片。"""

        task = _task(designs=[{"entity_id": "Category", "data_source_type": "database"}])
        task["source_refs"]["authorization"] = {
            "endpoints": [
                {
                    "apiContractId": "category_api",
                    "endpointId": "category.create",
                    "httpMethod": "POST",
                    "path": "/categories",
                    "operationResourceKeys": ["categories_create"],
                    "semantics": "ANY_OF",
                }
            ],
            "authConstants": [
                {"name": "CATEGORIES_CREATE_RESOURCE", "resourceKey": "categories_create"}
            ],
        }

        context = task_implementation_contract(_project_plan(), task)

        self.assertEqual(
            context["authorization_constraints"],
            {
                "endpointIdentity": {
                    "apiContractId": "category_api",
                    "endpointId": "category.create",
                    "httpMethod": "POST",
                    "path": "/categories",
                },
                "operationResourceKeys": ["categories_create"],
                "semantics": "ANY_OF",
                "authConstants": [
                    {"name": "CATEGORIES_CREATE_RESOURCE", "resourceKey": "categories_create"}
                ],
            },
        )

    def test_external_api_contract_keeps_only_endpoint_scoped_operations(self) -> None:
        """Java 实现契约不得重新读取实体的其他上游操作。"""

        design = _external_product_design()
        design["external_api_design"]["operations"].append({
            "operation_id": "unrelated-operation",
            "endpoint_refs": [{
                "api_contract_id": "category_api",
                "endpoint_id": "category.delete",
            }],
        })
        task = _task(designs=[design])
        task["id"] = "backend:endpoint:category_api:category.create::Category::upstream"
        task["description"] = (
            "1. 创建 product.url 配置读取和商品上游请求 DTO。\n"
            "2. 使用 POST /v1/product/list 实现 OpenFeign Client。"
        )

        context = task_implementation_contract(_project_plan(), task)

        api_design = context["api_design"]
        operation = api_design["sourceSnapshots"][0]["details"]["operation"]
        self.assertEqual(operation["operationId"], "external-op-product-list")
        self.assertNotIn("unrelated-operation", str(api_design))
        self.assertNotIn("request_body", str(operation))
        self.assertNotIn("response_body", str(operation))

    def test_endpoint_contract_rejects_missing_or_duplicate_api_design(self) -> None:
        """Endpoint 缺失或重复 API 设计时必须在 Agent 写入前失败。"""

        missing = _task(designs=[_external_product_design()])
        missing["source_refs"]["endpoint_designs"] = []
        with self.assertRaisesRegex(ValueError, "必须且只能携带一个已确认 Endpoint API 设计"):
            task_implementation_contract(_project_plan(), missing)

        duplicate = _task(designs=[_external_product_design()])
        duplicate["source_refs"]["endpoint_designs"].append(
            dict(duplicate["source_refs"]["endpoint_designs"][0])
        )
        with self.assertRaisesRegex(ValueError, "必须且只能携带一个已确认 Endpoint API 设计"):
            task_implementation_contract(_project_plan(), duplicate)

    def test_external_api_prompt_carries_shapes_stage_and_mapping_without_examples(self) -> None:
        """DatasourceAgent Prompt 携带当前商品操作结构、阶段和映射规则。"""

        task = _task(designs=[_external_product_design()])
        task["id"] = "backend:endpoint:category_api:category.create::Category::mapping"
        task["description"] = (
            "1. 读取商品上游响应 DTO 并遍历 list[]。\n"
            "2. 按 list[].price 到 price 等字段映射转换商品。"
        )

        prompt = _data_source_generation_prompt(
            project_plan=_project_plan(),
            workspace_snapshot=_workspace_snapshot(),
            tasks=[task],
        )

        self.assertIn('"stage": "mapping"', prompt)
        self.assertIn('"fieldMappings"', prompt)
        self.assertIn('"baseUrlConfigKey": "product.url"', prompt)
        self.assertIn('"path": "pageSize"', prompt)
        self.assertIn('"path": "list[].price"', prompt)
        self.assertIn("request_shape and requestStructure and responseStructure", prompt)
        self.assertIn("Persist the confirmed baseUrl directly", prompt)
        self.assertIn("plain YAML or properties value", prompt)
        self.assertIn("never wrap it in a `${ENV_NAME:default}`", prompt)
        self.assertIn("never place the base URL in Java constants", prompt)
        self.assertIn("Prefer Spring Cloud OpenFeign", prompt)
        self.assertIn("existing RestTemplate, WebClient", prompt)
        self.assertIn("do not reject or rewrite it solely", prompt)
        self.assertIn("backend:bootstrap task owns the Maven OpenFeign dependency", prompt)
        self.assertIn(
            "springboot-backend-generate/"
            "references/external-api/layer-implementation.md",
            prompt,
        )
        self.assertNotIn(
            "springboot-backend-generate/references/external-api/bootstrap.md",
            prompt,
        )
        self.assertIn("For status=already_satisfied, satisfaction_evidence is mandatory", prompt)
        self.assertIn("Never return already_satisfied with omitted", prompt)
        self.assertIn('"target_files":["<inspected-relative-path>"]', prompt)
        self.assertIn("If the task performs any write, return status=completed", prompt)
        self.assertNotIn("PROMPT_SAMPLE_KEYWORD", prompt)
        self.assertNotIn("PROMPT_SAMPLE_PRODUCT", prompt)

    def test_execution_packet_drops_scheduler_only_fields(self) -> None:
        """最小任务包不携带验收、状态和影响分析等调度字段。"""

        task = _task(
            designs=[{"entity_id": "Category", "data_source_type": "database"}],
        )
        packet = execution_task_packet(_project_plan(), task)

        self.assertNotIn("acceptance_checks", packet)
        self.assertNotIn("impact_scope", packet)
        self.assertNotIn("status", packet)
        self.assertNotIn("target_files", packet)
        self.assertNotIn("source_refs", packet)
        self.assertNotIn("title", packet)
        self.assertNotIn("description", packet)
        self.assertEqual(packet["kind"], "endpoint")
        self.assertEqual(packet["stage"], "objects")
        self.assertEqual(
            packet["execution_steps"],
            [
                "读取 backend/src/main/java/Category.java 并实现当前分类对象。",
                "保留现有包结构和 Java 8 语法。",
            ],
        )
        self.assertIn("implementation_contract", packet)
        self.assertNotIn("backend_working_directory", str(packet))

    def test_bootstrap_packet_expands_reference_and_omits_endpoint_context(self) -> None:
        """bootstrap 只携带基础设施契约并展开专属参考文档。"""

        task = _task(
            designs=[{"entity_id": "Category", "data_source_type": "database"}],
        )
        task["id"] = "backend:bootstrap::bootstrap"
        task["unit_id"] = "backend:bootstrap"
        packet = execution_task_packet(_project_plan(), task)

        self.assertEqual(packet["kind"], "bootstrap")
        self.assertEqual(packet["stage"], "bootstrap")
        self.assertIn(
            "/.xcodeagent/builtin-skills/springboot-backend-generate/"
            "references/database/bootstrap.md",
            packet["instruction_paths"],
        )
        self.assertNotIn("references/layer-implementation.md", str(packet))
        self.assertEqual(packet["implementation_contract"]["kind"], "bootstrap")
        self.assertEqual(
            packet["implementation_contract"]["capabilities"],
            ["mybatis_plus_mysql"],
        )
        self.assertNotIn("http_client", packet["implementation_contract"])
        self.assertNotIn("api_contract", packet["implementation_contract"])
        self.assertNotIn("entities", packet["implementation_contract"])

    def test_external_api_bootstrap_packet_requires_openfeign_capability(self) -> None:
        """外部 API bootstrap 必须加载 Feign 参考并携带模板依赖基线。"""

        task = _task(
            designs=[{"entity_id": "Weather", "data_source_type": "external_api"}],
        )
        task["id"] = "backend:bootstrap::bootstrap"
        task["unit_id"] = "backend:bootstrap"
        packet = execution_task_packet(_project_plan(), task)

        self.assertIn(
            "/.xcodeagent/builtin-skills/springboot-backend-generate/"
            "references/external-api/bootstrap.md",
            packet["instruction_paths"],
        )
        self.assertNotIn("references/layer-implementation.md", str(packet))
        contract = packet["implementation_contract"]
        self.assertEqual(contract["capabilities"], ["spring_cloud_openfeign"])
        self.assertEqual(contract["http_client"], "openfeign")
        self.assertEqual(
            contract["template_dependencies"]["spring_cloud_version"],
            "2021.0.3",
        )
        self.assertNotIn("persistence", contract)


class DataSourceWorkspaceContextTests(unittest.TestCase):
    """验证 Java Agent 直接复用任务构建阶段的 WorkspaceSnapshot Context。"""

    def test_resolves_supported_backend_directories(self) -> None:
        """目录投影必须保留工作目录和完整的后端目录树。"""

        directory_structure = (
            "└── backend/\n"
            "    ├── pom.xml\n"
            "    └── src/\n"
            "        └── main/"
        )
        self.assertEqual(
            backend_workspace_context(
                {"backend": {"dir_structure": directory_structure}}
            ),
            {
                "backend_working_directory": "/backend",
                "backend_directory_structure": directory_structure,
            },
        )

    def test_preserves_uppercase_backend_directory(self) -> None:
        """大小写不同的后端根目录必须按快照原样传递。"""

        directory_structure = "└── Backend/\n    └── pom.xml"
        self.assertEqual(
            backend_workspace_context(
                {"backend": {"dir_structure": directory_structure}}
            ),
            {
                "backend_working_directory": "/Backend",
                "backend_directory_structure": directory_structure,
            },
        )

    def test_rejects_missing_or_unsafe_workspace_context(self) -> None:
        """不得从宿主机绝对路径、目录穿越或缺失信息猜测后端目录。"""

        invalid_values = (
            {},
            {"backend": {}},
            {"backend": {"dir_structure": "└── C:\\workspace\\backend/"}},
            {"backend": {"dir_structure": "└── ../backend/"}},
            {"backend": {"dir_structure": "└── frontend/"}},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "dir_structure"):
                    backend_workspace_context(value)


class DataSourceTaskCompilationTests(unittest.TestCase):
    """验证 Unit 编译阶段按 Endpoint 身份隔离 API 设计。"""

    def test_endpoint_task_filters_designs_to_endpoint_identity(self) -> None:
        """Endpoint Unit 只能继承复合标识完全匹配的 API 设计。"""

        unit_id = "backend:endpoint:dashboard_api:dashboard.get"
        selected = _endpoint_design([
            {"entity_id": "Order", "data_source_type": "database"},
            {"entity_id": "Weather", "data_source_type": "external_api"},
        ])
        selected["apiContractId"] = "dashboard_api"
        selected["endpointId"] = "dashboard.get"
        unrelated = _endpoint_design([
            {"entity_id": "Notice", "data_source_type": "database"}
        ])
        unrelated["apiContractId"] = "notice_api"
        unrelated["endpointId"] = "notice.list"
        tasks = apply_unit_compilation(
            {"build_units": {unit_id: {"id": unit_id}}},
            [
                {
                    "id": f"{unit_id}::Order::repository",
                    "unit_id": unit_id,
                    "source_refs": {},
                }
            ],
            {
                "target": {"type": "endpoint", "id": "dashboard.get"},
                "endpoint_ids": ["dashboard.get"],
                "entity_ids": ["Order", "Weather"],
                "endpoint_designs": [selected, unrelated],
                "source_refs": {},
            },
        )

        self.assertNotIn("entity_ids", tasks[0]["source_refs"])
        self.assertEqual(
            [item["endpointId"] for item in tasks[0]["source_refs"]["endpoint_designs"]],
            ["dashboard.get"],
        )

    def test_endpoint_task_rejects_model_source_override(self) -> None:
        """模型任务中的来源设计不能覆盖平台编译的正式 API 设计。"""

        unit_id = "backend:endpoint:dashboard_api:dashboard.get"
        selected = _endpoint_design([
            {"entity_id": "Order", "data_source_type": "database"}
        ])
        selected["apiContractId"] = "dashboard_api"
        selected["endpointId"] = "dashboard.get"
        tasks = apply_unit_compilation(
            {"build_units": {unit_id: {"id": unit_id}}},
            [{
                "id": "ambiguous",
                "unit_id": unit_id,
                "source_refs": {"endpoint_designs": [{"endpointId": "forged"}]},
            }],
            {
                "target": {"type": "endpoint", "id": "dashboard.get"},
                "endpoint_ids": ["dashboard.get"],
                "entity_ids": ["Order"],
                "endpoint_designs": [selected],
                "source_refs": {},
            },
        )

        self.assertEqual(
            [item["endpointId"] for item in tasks[0]["source_refs"]["endpoint_designs"]],
            ["dashboard.get"],
        )

    def test_bootstrap_inherits_current_endpoint_sources(self) -> None:
        """bootstrap 从当前 Endpoint API 设计继承数据库和外部 API 来源。"""

        design = _endpoint_design([
            {"entity_id": "Order", "data_source_type": "database"},
            {"entity_id": "Weather", "data_source_type": "external_api"},
        ])
        design["apiContractId"] = "dashboard_api"
        design["endpointId"] = "dashboard.get"
        tasks = apply_unit_compilation(
            {"build_units": {"backend:bootstrap": {"id": "backend:bootstrap"}}},
            [{"id": "bootstrap", "unit_id": "backend:bootstrap", "source_refs": {}}],
            {
                "target": {"type": "endpoint", "id": "dashboard.get"},
                "endpoint_ids": ["dashboard.get"],
                "entity_ids": ["Order", "Weather"],
                "endpoint_designs": [design],
                "source_refs": {},
            },
        )

        self.assertNotIn("entity_ids", tasks[0]["source_refs"])
        self.assertEqual(
            {
                snapshot["sourceType"]
                for snapshot in tasks[0]["source_refs"]["endpoint_designs"][0]["sourceSnapshots"]
            },
            {"database", "external_api"},
        )
        self.assertEqual(len(task_required_skill_paths(tasks[0])), 2)


if __name__ == "__main__":
    unittest.main()
