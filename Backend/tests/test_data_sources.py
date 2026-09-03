"""独立数据源目录的当前契约测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.persistence import data_sources as data_source_storage
from app.services.data_sources import (
    DataSourceError,
    mutate_catalog,
    public_catalog,
    validate_source,
)


class DataSourcesServiceTests(unittest.TestCase):
    """覆盖独立数据源目录的核心持久化和校验边界。"""

    def setUp(self) -> None:
        """为每个测试创建隔离工作区。"""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        """清理测试工作区。"""

        self.temporary_directory.cleanup()

    def write_application(self, payload: dict[str, object]) -> None:
        """把测试用 application.json 写入隔离工作区。"""

        application_directory = self.workspace / ".xcodeagent"
        application_directory.mkdir(parents=True, exist_ok=True)
        (application_directory / "application.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def data_sources_file(self) -> Path:
        """返回测试工作区的独立数据源索引路径。"""

        return self.workspace / ".xcodeagent" / "datasource" / "index.json"

    def test_first_read_imports_builtin_application_database(self) -> None:
        """首次读取时应把应用内置数据库导入独立目录。"""

        self.write_application(
            {"appName": "商品管理", "datasource": {"type": "database", "db": {"useBuiltin": True}}}
        )
        catalog = public_catalog(self.workspace)

        source = catalog.sources[0]
        self.assertEqual(source.id, "application-database")
        self.assertEqual(source.name, "商品管理数据库")
        self.assertEqual(source.mode, "builtin")
        self.assertNotIn("revision", json.loads(self.data_sources_file().read_text(encoding="utf-8")))
        index_entry = json.loads(self.data_sources_file().read_text(encoding="utf-8"))["sources"][0]
        self.assertEqual(set(index_entry), {"id", "type", "name", "mode", "hasPassword"})

    def test_first_read_imports_dbid_application_database_without_password(self) -> None:
        """首次读取 DBID 应映射连接字段且不保存密码。"""

        self.write_application(
            {
                "appName": "订单管理",
                "datasource": {
                    "type": "database",
                    "db": {
                        "useBuiltin": False,
                        "dbidMode": {
                            "dbid": "dbid-order",
                            "domain": "db.example.com",
                            "port": 3306,
                            "schema": "orders",
                            "userName": "app",
                        },
                    },
                },
            }
        )
        catalog = public_catalog(self.workspace)

        source = catalog.sources[0]
        self.assertEqual(source.mode, "dbid")
        # 列表只返回索引摘要，连接字段通过详情读取。
        self.assertIsNone(source.dbid)
        detailed_source = public_catalog(self.workspace, source_id="application-database").sources[0]
        self.assertEqual(detailed_source.dbid, "dbid-order")
        self.assertFalse(source.has_password)
        persisted = json.loads(
            (self.workspace / ".xcodeagent" / "datasource" / "databases" / "application-database.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("passwordCiphertext", persisted)

    def test_first_read_imports_direct_application_database_and_redacts_password(self) -> None:
        """首次读取直连数据库应保留密文但公开目录不得泄露密文。"""

        ciphertext = "xcodeagent-secret:v1:rsa-oaep-256:key:cipher"
        self.write_application(
            {
                "appName": "库存管理",
                "datasource": {
                    "type": "database",
                    "db": {
                        "useBuiltin": False,
                        "plantMode": {
                            "domain": "127.0.0.1",
                            "port": 3306,
                            "schema": "inventory",
                            "userName": "app",
                            "pwd": ciphertext,
                        },
                    },
                },
            }
        )
        catalog = public_catalog(self.workspace)

        self.assertTrue(catalog.sources[0].has_password)
        public_source = catalog.sources[0].model_dump(by_alias=True)
        self.assertNotIn("passwordCiphertext", public_source)
        persisted = json.loads(
            (self.workspace / ".xcodeagent" / "datasource" / "databases" / "application-database.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(persisted["passwordCiphertext"], ciphertext)

    def test_existing_independent_catalog_wins_and_deleted_import_is_not_recreated(self) -> None:
        """独立目录存在后不应被 application.json 覆盖，删除后也不应重导。"""

        self.write_application(
            {"appName": "原应用", "datasource": {"type": "database", "db": {"useBuiltin": True}}}
        )
        initial = public_catalog(self.workspace)
        self.assertEqual([source.name for source in initial.sources], ["原应用数据库"])
        mutate_catalog(
            self.workspace,
            action="create",
            source={"type": "external_api", "name": "独立 API", "baseUrl": "https://example.com"},
        )
        catalog = public_catalog(self.workspace)
        self.assertEqual([source.name for source in catalog.sources], ["原应用数据库", "独立 API"])

        deleted = mutate_catalog(
            self.workspace,
            action="delete",
            source_id="application-database",
        )
        self.assertEqual([source.name for source in deleted.sources], ["独立 API"])
        self.assertEqual([source.name for source in public_catalog(self.workspace).sources], ["独立 API"])

    def test_missing_application_database_creates_empty_catalog(self) -> None:
        """应用没有数据库配置时应建立空的独立目录。"""

        self.write_application({"appName": "静态应用", "datasource": {"type": "static"}})
        catalog = public_catalog(self.workspace)

        self.assertEqual(catalog.sources, [])
        self.assertTrue(self.data_sources_file().is_file())

    def test_legacy_single_file_is_not_read_or_migrated(self) -> None:
        """当前契约不读取旧单文件，首次访问直接建立新的空索引。"""

        agent_directory = self.workspace / ".xcodeagent"
        agent_directory.mkdir(parents=True, exist_ok=True)
        (agent_directory / "data-sources.json").write_text(
            json.dumps({"sources": [{"id": "legacy", "type": "external_api"}]}), encoding="utf-8"
        )

        catalog = public_catalog(self.workspace)

        self.assertEqual(catalog.sources, [])
        self.assertTrue(self.data_sources_file().is_file())

    def test_invalid_application_json_does_not_create_catalog(self) -> None:
        """非法 application.json 应返回数据源错误且不生成半成品目录。"""

        application_directory = self.workspace / ".xcodeagent"
        application_directory.mkdir(parents=True, exist_ok=True)
        (application_directory / "application.json").write_text("{invalid", encoding="utf-8")

        with self.assertRaisesRegex(DataSourceError, "格式无效"):
            public_catalog(self.workspace)
        self.assertFalse(self.data_sources_file().exists())

    def test_external_operation_id_is_generated_and_catalog_is_public(self) -> None:
        """创建外部 API 后应生成操作 ID并返回公开目录。"""

        catalog = mutate_catalog(
            self.workspace,
            action="create",
            source={
                "type": "external_api",
                "name": "商品 API",
                "baseUrl": "api.example.com",
                "directories": [{"name": "商品", "operations": [{"name": "列表", "method": "GET", "path": "/products"}]}],
            },
        )

        self.assertEqual(catalog.sources[0].type, "external_api")
        self.assertEqual(catalog.sources[0].directories[1].operations[0].id, "operation-1")
        self.assertEqual(catalog.sources[0].directories[0].name, "默认目录")
        persisted = json.loads(
            (self.workspace / ".xcodeagent" / "datasource" / "external-apis" / catalog.sources[0].id / "source.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("operations", persisted["directories"][1])
        operation_id = catalog.sources[0].directories[1].operations[0].id
        self.assertTrue(
            (self.workspace / ".xcodeagent" / "datasource" / "external-apis" / catalog.sources[0].id / "operations" / f"{operation_id}.json").is_file()
        )

    def test_new_external_domain_creates_plain_default_directory(self) -> None:
        """新建外部 API 域名应自动创建可普通编辑的默认目录。"""

        catalog = mutate_catalog(
            self.workspace,
            action="create",
            source={"type": "external_api", "name": "域名", "baseUrl": "api.example.com"},
        )

        source = catalog.sources[0]
        self.assertEqual(len(source.directories), 1)
        self.assertEqual(source.directories[0].name, "默认目录")
        self.assertTrue(source.directories[0].id)

        renamed = mutate_catalog(
            self.workspace,
            action="update",
            source={
                **source.model_dump(by_alias=True),
                "directories": [{**source.directories[0].model_dump(by_alias=True), "name": "公共接口"}],
            },
        )
        self.assertEqual(renamed.sources[0].directories[0].name, "公共接口")

        emptied = mutate_catalog(
            self.workspace,
            action="update",
            source={**renamed.sources[0].model_dump(by_alias=True), "directories": []},
        )
        self.assertEqual(emptied.sources[0].directories, [])
        self.assertEqual(public_catalog(self.workspace).sources[0].directories, [])

    def test_split_storage_layout_and_selective_operation_update(self) -> None:
        """外部域名元数据和接口应分文件保存，更新一个接口不改写另一个接口。"""

        created = mutate_catalog(
            self.workspace,
            action="create",
            source={
                "type": "external_api",
                "name": "商品 API",
                "baseUrl": "api.example.com",
                "directories": [
                    {
                        "name": "商品",
                        "operations": [
                            {"name": "列表", "method": "GET", "path": "/products"},
                            {"name": "详情", "method": "GET", "path": "/products/{id}", "pathParameters": [{"name": "id", "required": True}], "queryParameters": []},
                        ],
                    }
                ],
            },
        )
        source = created.sources[0]
        source_dir = self.workspace / ".xcodeagent" / "datasource" / "external-apis" / source.id
        operation_a, operation_b = source.directories[1].operations
        operation_a_path = source_dir / "operations" / f"{operation_a.id}.json"
        operation_b_path = source_dir / "operations" / f"{operation_b.id}.json"
        source_path = source_dir / "source.json"
        index_path = self.workspace / ".xcodeagent" / "datasource" / "index.json"
        operation_a_before = operation_a_path.read_bytes()
        source_before = source_path.read_bytes()
        index_before = index_path.read_bytes()
        index_entry = json.loads(index_before.decode("utf-8"))["sources"][0]
        self.assertEqual(set(index_entry), {"id", "type", "name", "baseUrl", "directories"})
        self.assertEqual(
            set(index_entry["directories"][1]),
            {"id", "name", "operations"},
        )
        self.assertEqual(
            set(index_entry["directories"][1]["operations"][1]),
            {"id", "name", "method", "path"},
        )
        self.assertTrue((self.workspace / ".xcodeagent" / "datasource" / "index.json").is_file())
        self.assertFalse((self.workspace / ".xcodeagent" / "data-sources.json").exists())
        summary = public_catalog(self.workspace)
        self.assertEqual(summary.sources[0].directories[1].operations[1].path_parameters, [])
        detailed = public_catalog(self.workspace, source_id=source.id, operation_id=operation_b.id)
        self.assertEqual(detailed.sources[0].directories[1].operations[1].path_parameters[0].name, "id")
        # 读取另一个接口时，含路径占位符的接口只剩摘要，不能将其当完整接口校验。
        other_detail = public_catalog(self.workspace, source_id=source.id, operation_id=operation_a.id)
        self.assertEqual(other_detail.sources[0].directories[1].operations[0].id, operation_a.id)

        updated_directories = [
            source.directories[0].model_dump(by_alias=True),
            {
                **source.directories[1].model_dump(by_alias=True),
                "operations": [
                    operation_a.model_dump(by_alias=True),
                    {**operation_b.model_dump(by_alias=True), "name": "详情接口"},
                ],
            },
        ]
        mutate_catalog(
            self.workspace,
            action="update",
            source={**source.model_dump(by_alias=True), "directories": updated_directories},
        )

        self.assertEqual(operation_a_path.read_bytes(), operation_a_before)
        self.assertEqual(source_path.read_bytes(), source_before)
        self.assertNotEqual(index_path.read_bytes(), index_before)
        self.assertEqual(json.loads(operation_b_path.read_text(encoding="utf-8"))["name"], "详情接口")
        self.assertEqual(public_catalog(self.workspace).sources[0].directories[1].operations[1].name, "详情接口")

    def test_missing_operation_file_is_reported_without_silent_data_loss(self) -> None:
        """目录引用的接口文件缺失时读取应返回明确存储错误。"""

        created = mutate_catalog(
            self.workspace,
            action="create",
            source={
                "type": "external_api",
                "name": "接口 API",
                "baseUrl": "api.example.com",
                "directories": [{"name": "目录", "operations": [{"name": "列表", "method": "GET", "path": "/items"}]}],
            },
        )
        operation_id = created.sources[0].directories[1].operations[0].id
        operation_path = (
            self.workspace
            / ".xcodeagent"
            / "datasource"
            / "external-apis"
            / created.sources[0].id
            / "operations"
            / f"{operation_id}.json"
        )
        operation_path.unlink()
        summary = public_catalog(self.workspace)
        self.assertEqual(summary.sources[0].directories[1].operations[0].name, "列表")
        with self.assertRaisesRegex(DataSourceError, "缺失"):
            public_catalog(
                self.workspace,
                source_id=created.sources[0].id,
                operation_id=operation_id,
            )

    def test_multi_file_write_failure_restores_previous_catalog(self) -> None:
        """多文件提交中途失败时应恢复已有文件和可读取目录。"""

        created = mutate_catalog(
            self.workspace,
            action="create",
            source={
                "type": "external_api",
                "name": "稳定 API",
                "baseUrl": "api.example.com",
                "directories": [{"name": "目录", "operations": [{"name": "列表", "method": "GET", "path": "/items"}]}],
            },
        )
        directory = self.workspace / ".xcodeagent" / "datasource"
        before = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}
        source = created.sources[0]
        updated = {
            **source.model_dump(by_alias=True),
            "name": "更新 API",
            "directories": [
                source.directories[0].model_dump(by_alias=True),
                {
                    **source.directories[1].model_dump(by_alias=True),
                    "operations": [
                        {
                            **source.directories[1].operations[0].model_dump(by_alias=True),
                            "name": "更新接口",
                        }
                    ],
                }
            ],
        }
        original_writer = data_source_storage._write_bytes_atomically
        call_count = 0

        def flaky_writer(path: Path, content: bytes) -> None:
            """仅让一次中间文件替换失败，以覆盖恢复分支。"""

            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("simulated write failure")
            original_writer(path, content)

        with patch.object(data_source_storage, "_write_bytes_atomically", side_effect=flaky_writer):
            with self.assertRaisesRegex(DataSourceError, "恢复"):
                mutate_catalog(self.workspace, action="update", source=updated)

        after = {path.relative_to(directory): path.read_bytes() for path in directory.rglob("*") if path.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(public_catalog(self.workspace).sources[0].name, "稳定 API")

    def test_external_domain_accepts_non_url_base_url_and_duplicate_values(self) -> None:
        """域名只做非空校验，同一个 Base URL 可以重复保存。"""

        first = mutate_catalog(
            self.workspace,
            action="create",
            source={"type": "external_api", "name": "域名一", "baseUrl": "内网服务"},
        )
        second = mutate_catalog(
            self.workspace,
            action="create",
            source={"type": "external_api", "name": "域名二", "baseUrl": " 内网服务 "},
        )

        self.assertEqual(len(first.sources), 1)
        self.assertEqual(len(second.sources), 2)

    def test_external_directory_names_must_be_unique(self) -> None:
        """同一个域名下目录名称不能重复。"""

        with self.assertRaisesRegex(DataSourceError, "目录名称不能重复"):
            mutate_catalog(
                self.workspace,
                action="create",
                source={
                    "type": "external_api",
                    "name": "域名",
                    "baseUrl": "api.example.com",
                    "directories": [
                        {"name": "商品", "operations": []},
                        {"name": "商品", "operations": []},
                    ],
                },
            )

    def test_external_operation_ids_are_unique_across_directories(self) -> None:
        """接口 ID 在同一个域名的不同目录之间也必须唯一。"""

        with self.assertRaisesRegex(DataSourceError, "操作 ID 不能重复"):
            mutate_catalog(
                self.workspace,
                action="create",
                source={
                    "type": "external_api",
                    "name": "域名",
                    "baseUrl": "api.example.com",
                    "directories": [
                        {"name": "一", "operations": [{"id": "same", "name": "一", "method": "GET", "path": "/one"}]},
                        {"name": "二", "operations": [{"id": "same", "name": "二", "method": "GET", "path": "/two"}]},
                    ],
                },
            )

    def test_json_field_descriptions_persist_and_are_cleaned_against_complete_samples(self) -> None:
        """请求和响应说明应分别保存，并在字段从完整样例移除后清理。"""

        created = mutate_catalog(
            self.workspace,
            action="create",
            source={
                "type": "external_api",
                "name": "说明 API",
                "baseUrl": "api.example.com",
                "directories": [
                    {
                        "name": "商品",
                        "operations": [
                            {
                                "name": "查询",
                                "method": "GET",
                                "path": "/products",
                                "requestSample": {"filter.value": "书", "items": [{"sku": "A1", "name": "商品"}]},
                                "responseSample": {"data": {"items": [{"sku": "A1"}], "total": 1}},
                                "requestStructure": {"type": "object", "properties": {
                                    "filter.value": {"type": "string", "description": "过滤文本"},
                                    "items": {"type": "array", "items": {"type": "object", "description": "请求商品数组元素", "properties": {"sku": {"type": "string", "description": "商品编码"}}}},
                                }},
                                "responseStructure": {"type": "object", "properties": {
                                    "data": {"type": "object", "description": "响应包装对象", "properties": {
                                        "items": {"type": "array", "items": {"type": "object", "description": "响应商品元素"}},
                                        "total": {"type": "integer", "description": "结果总数"},
                                    }},
                                }},
                            }
                        ],
                    }
                ],
            },
        )

        operation = created.sources[0].directories[1].operations[0]
        self.assertEqual(operation.request_structure.properties["filter.value"].description, "过滤文本")
        self.assertEqual(operation.response_structure.properties["data"].properties["total"].description, "结果总数")
        source_id = created.sources[0].id
        operation_id = created.sources[0].directories[1].operations[0].id
        persisted_operation = json.loads(
            (
                self.workspace
                / ".xcodeagent"
                / "datasource"
                / "external-apis"
                / source_id
                / "operations"
                / f"{operation_id}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(persisted_operation["requestStructure"]["properties"]["items"]["items"]["properties"]["sku"]["description"], "商品编码")

        updated = mutate_catalog(
            self.workspace,
            action="update",
            source={
                **created.sources[0].model_dump(by_alias=True),
                "directories": [
                    {
                        **created.sources[0].directories[1].model_dump(by_alias=True),
                        "operations": [
                            {
                                **operation.model_dump(by_alias=True),
                                "requestSample": {"filter.value": "书"},
                                "responseSample": {"data": {"total": 2}},
                            }
                        ],
                    }
                ],
            },
        )
        updated_operation = updated.sources[0].directories[0].operations[0]
        self.assertEqual(set(updated_operation.request_structure.properties), {"filter.value"})
        self.assertEqual(updated_operation.request_structure.properties["filter.value"].description, "过滤文本")
        self.assertEqual(set(updated_operation.response_structure.properties["data"].properties), {"total"})
        self.assertEqual(updated_operation.response_structure.properties["data"].description, "响应包装对象")

    def test_json_field_descriptions_reject_invalid_path_and_length(self) -> None:
        """字段说明保存时应清理无效路径，并拒绝超过单条长度限制的文本。"""

        cleaned = mutate_catalog(
            self.workspace,
            action="create",
            source={
                "type": "external_api",
                "name": "非法说明 API",
                "baseUrl": "api.example.com",
                "directories": [{"name": "默认", "operations": [{
                    "name": "接口", "method": "GET", "path": "/", "requestSample": {"id": 1},
                    "requestStructure": {"type": "object", "properties": {"missing": {"type": "string", "description": "不存在"}, "id": {"type": "integer", "description": "有效"}}},
                }]}],
            },
        )
        structure = cleaned.sources[0].directories[1].operations[0].request_structure
        self.assertEqual(set(structure.properties), {"id"})
        self.assertEqual(structure.properties["id"].description, "有效")
        with self.assertRaisesRegex(ValueError, "1024"):
            mutate_catalog(
                self.workspace,
                action="create",
                source={
                    "type": "external_api",
                    "name": "超长说明 API",
                    "baseUrl": "api.example.com",
                    "directories": [{"name": "默认", "operations": [{
                        "name": "接口", "method": "GET", "path": "/", "requestSample": {"id": 1},
                        "requestStructure": {"type": "object", "properties": {"id": {"type": "integer", "description": "x" * 1025}}},
                    }]}],
                },
            )

    def test_only_one_database_is_allowed(self) -> None:
        """目录最多允许一条数据库资源。"""

        source = {"type": "database", "mode": "builtin", "name": "内置库"}
        mutate_catalog(self.workspace, action="create", source=source)
        with self.assertRaisesRegex(DataSourceError, "最多配置一个"):
            mutate_catalog(
                self.workspace,
                action="create",
                source={"type": "database", "mode": "builtin", "name": "第二个库"},
            )

    def test_sensitive_header_and_invalid_placeholder_are_rejected(self) -> None:
        """外部 API 不得保存敏感 Header，路径占位符必须有对应参数。"""

        with self.assertRaisesRegex(DataSourceError, "敏感 Header"):
            validate_source(
                {
                    "type": "external_api",
                    "name": "API",
                    "baseUrl": "https://example.com",
                    "headers": [{"name": "Authorization", "value": "secret"}],
                }
            )
        with self.assertRaisesRegex(DataSourceError, "占位符"):
            validate_source(
                {
                    "type": "external_api",
                    "name": "API",
                    "baseUrl": "api.example.com",
                    "directories": [{"name": "接口", "operations": [{"name": "详情", "method": "GET", "path": "/products/{id}"}]}],
                }
            )

    def test_database_validation_decrypts_ciphertext_before_connecting(self) -> None:
        """直连数据库检测必须先解密密文，再把明文密码交给驱动。"""

        ciphertext = "xcodeagent-secret:v1:rsa-oaep-256:platform-key-v1:cipher"
        connection = Mock()
        connect = Mock(return_value=connection)
        source = {
            "type": "database",
            "mode": "direct",
            "name": "直连库",
            "domain": "db.example.com",
            "port": 3306,
            "schema": "inventory",
            "userName": "app",
            "passwordCiphertext": ciphertext,
        }
        with patch(
            "app.services.data_sources.decrypt_password", return_value="plain-password"
        ) as decrypt, patch.dict(sys.modules, {"pymysql": SimpleNamespace(connect=connect)}):
            result = validate_source(source)

        self.assertEqual(result, {"valid": True, "connection": "ok"})
        decrypt.assert_called_once_with(ciphertext)
        self.assertEqual(connect.call_args.kwargs["password"], "plain-password")
        connection.close.assert_called_once_with()

    def test_database_validation_reuses_stored_ciphertext_when_edit_password_is_blank(self) -> None:
        """编辑直连数据库留空密码时，应继续使用目录中已有的加密密码。"""

        ciphertext = "xcodeagent-secret:v1:rsa-oaep-256:platform-key-v1:cipher"
        created = mutate_catalog(
            self.workspace,
            action="create",
            source={
                "type": "database",
                "mode": "direct",
                "name": "直连库",
                "domain": "db.example.com",
                "port": 3306,
                "schema": "inventory",
                "userName": "app",
                "passwordCiphertext": ciphertext,
            },
        )
        connection = Mock()
        connect = Mock(return_value=connection)
        edited_source = {
            "id": created.sources[0].id,
            "type": "database",
            "mode": "direct",
            "name": "改名后的直连库",
            "domain": "db2.example.com",
            "port": 3307,
            "schema": "inventory2",
            "userName": "app2",
        }
        with patch(
            "app.services.data_sources.decrypt_password", return_value="plain-password"
        ) as decrypt, patch.dict(sys.modules, {"pymysql": SimpleNamespace(connect=connect)}):
            result = validate_source(edited_source, self.workspace)

        self.assertEqual(result, {"valid": True, "connection": "ok"})
        decrypt.assert_called_once_with(ciphertext)
        self.assertEqual(connect.call_args.kwargs["password"], "plain-password")

    def test_database_connection_error_is_clear_and_hides_driver_class_name(self) -> None:
        """数据库不可达时应返回可执行的中文提示，而不是暴露 OperationalError。"""

        connect = Mock(side_effect=Exception(2003, "connection refused"))
        source = {
            "type": "database",
            "mode": "direct",
            "name": "直连库",
            "domain": "db.example.com",
            "port": 3306,
            "schema": "inventory",
            "userName": "app",
            "passwordCiphertext": "xcodeagent-secret:v1:rsa-oaep-256:platform-key-v1:cipher",
        }
        with patch(
            "app.services.data_sources.decrypt_password", return_value="plain-password"
        ), patch.dict(sys.modules, {"pymysql": SimpleNamespace(connect=connect)}):
            with self.assertRaises(DataSourceError) as raised:
                validate_source(source)

        self.assertIn("无法连接到数据库服务器", str(raised.exception))
        self.assertNotIn("OperationalError", str(raised.exception))

    def test_public_catalog_never_contains_database_password(self) -> None:
        """公开目录只保留密码存在标记，不包含内部密文。"""

        mutate_catalog(
            self.workspace,
            action="create",
            source={
                "type": "database",
                "mode": "direct",
                "name": "直连库",
                "domain": "127.0.0.1",
                "port": 3306,
                "schema": "demo",
                "userName": "demo",
                "passwordCiphertext": "xcodeagent-secret:v1:rsa-oaep-256:key:cipher",
            },
        )
        catalog = public_catalog(self.workspace)
        public_source = catalog.sources[0].model_dump(by_alias=True)
        self.assertTrue(public_source["hasPassword"])
        self.assertNotIn("passwordCiphertext", public_source)

    def test_updating_database_mode_drops_inapplicable_fields(self) -> None:
        """切换数据库模式时不得残留上一模式的连接字段。"""

        created = mutate_catalog(
            self.workspace,
            action="create",
            source={
                "type": "database",
                "mode": "direct",
                "name": "数据库",
                "domain": "127.0.0.1",
                "port": 3306,
                "schema": "demo",
                "userName": "demo",
                "passwordCiphertext": "xcodeagent-secret:v1:rsa-oaep-256:key:cipher",
            },
        )
        updated = mutate_catalog(
            self.workspace,
            action="update",
            source={"id": created.sources[0].id, "type": "database", "mode": "builtin", "name": "数据库"},
        )
        self.assertEqual(updated.sources[0].mode, "builtin")
        persisted = json.loads(
            (self.workspace / ".xcodeagent" / "datasource" / "databases" / f"{created.sources[0].id}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("passwordCiphertext", persisted)
        self.assertNotIn("domain", persisted)


if __name__ == "__main__":
    unittest.main()
