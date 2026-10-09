"""验证 DBID 取密留白覆盖目录、工作区工具、数据库执行和启动环境。"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.protocols.data_sources import build_data_sources_ag_ui_stream
from app.services.api_design import load_database_columns, load_database_tables
from app.services.backend_launch_support import _backend_runtime_environment
from app.services.database_credentials import (
    DatabaseCredentialError,
    resolve_application_mysql_config,
    resolve_dbid_password,
)
from app.services.database_execution import _mysql_connection_config
from app.services.data_sources import (
    DataSourceError,
    mutate_catalog,
    resolve_database_config,
    validate_source,
)
from app.tools.mysql_info import get_mysql_table_info_for_workspace


class DbidConnectionTests(unittest.TestCase):
    """覆盖同一取密入口在各数据库连接场景中的调用及不落盘约束。"""

    def setUp(self) -> None:
        """准备完整 DBID 连接配置，无文件依赖的测试不创建工作区。"""

        self.fields = {
            "dbid": "dbid-orders",
            "domain": "db.example.com",
            "port": 3307,
            "schema": "orders",
            "userName": "reader",
        }
        self.source = {
            "id": "orders-db",
            "type": "database",
            "mode": "dbid",
            "name": "订单库",
            **self.fields,
        }

    def prepare_workspace(self) -> None:
        """仅为持久化和工作区集成测试创建隔离应用配置。"""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.workspace = Path(self.temporary_directory.name)
        self.application_file = self.workspace / ".devagentstudio" / "application.json"
        self.application_file.parent.mkdir(parents=True)
        self.application_file.write_text(json.dumps({
            "datasource": {"type": "database", "db": {"useBuiltin": False, "dbidMode": self.fields}},
        }), encoding="utf-8")

    def test_placeholder_returns_dbid_and_rejects_blank_identifier(self) -> None:
        """默认取密返回 DBID，缺失标识立即失败而不建立连接。"""

        self.assertEqual(resolve_dbid_password("dbid-orders"), "dbid-orders")
        for value in (None, "", "  "):
            with self.subTest(value=value), self.assertRaises(DatabaseCredentialError):
                resolve_dbid_password(value)

    def test_validation_uses_adapter_password_without_decryption(self) -> None:
        """新建或编辑连接检测使用取密结果，不调用直连密码解密器。"""

        connection = Mock()
        connect = Mock(return_value=connection)
        with patch(
            "app.services.database_credentials.resolve_dbid_password", return_value="resolved-secret"
        ) as resolver, patch(
            "app.services.database_credentials.decrypt_password"
        ) as decrypt, patch.dict(sys.modules, {"pymysql": SimpleNamespace(connect=connect)}):
            result = validate_source(self.source)

        self.assertEqual(result, {"valid": True, "connection": "ok"})
        resolver.assert_called_once_with("dbid-orders")
        decrypt.assert_not_called()
        self.assertEqual(connect.call_args.kwargs["password"], "resolved-secret")
        self.assertEqual(connect.call_args.kwargs["database"], "orders")
        connection.close.assert_called_once_with()
        self.assertNotIn("resolved-secret", json.dumps(result))

    def test_runtime_paths_share_adapter_without_file_access(self) -> None:
        """隔离文件边界，验证应用、目录、元数据、SQL 和启动环境共用取密入口。"""

        application = {"datasource": {"type": "database", "db": {"dbidMode": self.fields}}}
        metadata = '{"status":"ok","database":"orders","tables":[],"schemas":{}}'
        with patch("app.services.database_credentials.load_application_json", return_value=application), patch(
            "app.services.data_sources._read_catalog", return_value=[self.source]
        ), patch("app.services.database_credentials.resolve_dbid_password", return_value="resolved-secret") as resolver, patch(
            "app.tools.mysql_info.mysql_table_info", return_value=metadata
        ) as workspace_reader, patch("app.services.api_design.mysql_table_info", return_value=metadata) as source_reader, patch(
            "app.services.backend_launch_support.Path.is_file", return_value=True
        ):
            config = resolve_application_mysql_config("unused-db-workspace")
            sql_config = _mysql_connection_config("unused-db-workspace")
            get_mysql_table_info_for_workspace("unused-db-workspace")
            load_database_tables("unused-db-workspace", "orders-db")
            load_database_columns("unused-db-workspace", "orders-db", "orders")
            environment, error = _backend_runtime_environment(Path("unused-db-workspace"))

        self.assertEqual(resolver.call_count, 6)
        self.assertEqual(config.password, "resolved-secret")
        self.assertEqual(sql_config["password"], "resolved-secret")
        self.assertEqual(workspace_reader.call_args.kwargs["password"], "resolved-secret")
        for invocation in source_reader.call_args_list:
            self.assertEqual(invocation.kwargs["password"], "resolved-secret")
        self.assertIsNone(error)
        self.assertEqual(environment["SPRING_DATASOURCE_PASSWORD"], "resolved-secret")

    def test_saved_source_resolves_fresh_password_without_persisting_it(self) -> None:
        """保存只写 DBID；后续每次连接重新取密，不缓存或回写密码。"""

        self.prepare_workspace()
        catalog_workspace = self.workspace / "new-database"
        mutate_catalog(catalog_workspace, action="create", source=self.source)
        source_file = catalog_workspace / ".devagentstudio" / "datasource" / "databases" / "orders-db.json"
        before = source_file.read_bytes()
        with patch(
            "app.services.database_credentials.resolve_dbid_password", side_effect=["first-secret", "second-secret"]
        ) as resolver:
            first = resolve_database_config(catalog_workspace, "orders-db")
            second = resolve_database_config(catalog_workspace, "orders-db")

        self.assertEqual(first["password"], "first-secret")
        self.assertEqual(second["password"], "second-secret")
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(source_file.read_bytes(), before)
        stored = json.loads(before)
        self.assertNotIn("password", stored)
        self.assertNotIn("passwordCiphertext", stored)

    def test_tables_and_columns_use_adapter_and_return_only_metadata(self) -> None:
        """后续表和列读取使用统一取密方法，响应与目录不含取密结果。"""

        self.prepare_workspace()
        metadata = json.dumps({
            "status": "ok", "database": "orders", "database_exists": True,
            "tables": [{"table_name": "orders", "comment": "订单"}],
            "schemas": {"orders": [{"column_name": "id", "column_type": "bigint", "is_nullable": "NO"}]},
        })
        with patch(
            "app.services.database_credentials.resolve_dbid_password", return_value="resolved-secret"
        ) as resolver, patch("app.services.api_design.mysql_table_info", return_value=metadata) as reader:
            tables = load_database_tables(self.workspace, "application-database")
            columns = load_database_columns(self.workspace, "application-database", "orders")

        self.assertEqual(tables["tables"][0]["name"], "orders")
        self.assertEqual(columns["columns"][0]["name"], "id")
        self.assertEqual(resolver.call_count, 2)
        for invocation in reader.call_args_list:
            self.assertEqual(invocation.kwargs["password"], "resolved-secret")
        self.assertNotIn("resolved-secret", json.dumps([tables, columns]))

    def test_workspace_tools_sql_execution_and_launcher_share_adapter(self) -> None:
        """工作区元数据、SQL 执行及 Java 启动环境都消费同一 DBID 取密入口。"""

        self.prepare_workspace()
        before = self.application_file.read_bytes()
        with patch.dict("os.environ", {"MYSQL_HOST": "global-host", "MYSQL_PWD": "global-secret"}), patch(
            "app.services.database_credentials.resolve_dbid_password", return_value="resolved-secret"
        ) as resolver, patch("app.tools.mysql_info.mysql_table_info", return_value='{"status":"ok"}') as reader:
            get_mysql_table_info_for_workspace(str(self.workspace))
            sql_config = _mysql_connection_config(self.workspace)
            environment, error = _backend_runtime_environment(self.workspace)

        self.assertEqual(resolver.call_count, 3)
        self.assertEqual(reader.call_args.kwargs["password"], "resolved-secret")
        self.assertEqual(sql_config["password"], "resolved-secret")
        self.assertEqual(sql_config["host"], "db.example.com")
        self.assertIsNone(error)
        self.assertEqual(environment["MYSQL_PWD"], "resolved-secret")
        self.assertEqual(environment["SPRING_DATASOURCE_PASSWORD"], "resolved-secret")
        self.assertIn("db.example.com:3307/orders", environment["SPRING_DATASOURCE_URL"])
        self.assertEqual(self.application_file.read_bytes(), before)

    def test_ag_ui_connection_validation_preserves_lifecycle_and_redaction(self) -> None:
        """DBID 的检测成功及失败都经现有 AG-UI 流返回且不泄露密码。"""

        payload = {
            "threadId": "dbid-test-thread", "runId": "dbid-test-run",
            "forwardedProps": {"dataSources": {"workspaceRoot": "unused-db-workspace", "source": self.source}},
        }
        async def collect_stream() -> str:
            """直接消费正式协议流，避免为无文件依赖的协议测试启动 HTTP 线程。"""

            return "".join([event async for event in build_data_sources_ag_ui_stream(action="validate", payload=payload)])

        # 此测试只检查协议生命周期，工作区运行登记和交互记账使用内存替身。
        with patch("app.protocols.ag_ui_action_stream.workflow_run_registry"), patch(
            "app.services.preview_runtime_guard.record_product_interaction"
        ), patch("app.services.database_credentials.resolve_dbid_password", return_value="resolved-secret"):
            with patch.dict(sys.modules, {"pymysql": SimpleNamespace(connect=Mock(return_value=Mock()))}):
                success = asyncio.run(collect_stream())
            with patch.dict(sys.modules, {"pymysql": SimpleNamespace(connect=Mock(side_effect=Exception(1045, "resolved-secret")))}):
                failure = asyncio.run(collect_stream())

        self.assertIn('"connection":"ok"', success)
        self.assertIn('"status":"failed"', failure)
        for response in (success, failure):
            for event in ("RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT", "RUN_FINISHED"):
                self.assertIn(event, response)
            self.assertNotIn("resolved-secret", response)

    def test_dbid_rejects_direct_password_and_ambiguous_application_mode(self) -> None:
        """DBID 配置不接受直连密码，同时配置两种模式时不能静默择一。"""

        self.prepare_workspace()
        with self.assertRaises(DataSourceError):
            validate_source({**self.source, "passwordCiphertext": "unexpected-secret"})
        application = json.loads(self.application_file.read_text(encoding="utf-8"))
        application["datasource"]["db"]["dbidMode"]["pwd"] = "unexpected-secret"
        self.application_file.write_text(json.dumps(application), encoding="utf-8")
        with self.assertRaisesRegex(DatabaseCredentialError, "不应保存直连密码"):
            resolve_application_mysql_config(self.workspace)
        application["datasource"]["db"]["plantMode"] = {}
        self.application_file.write_text(json.dumps(application), encoding="utf-8")
        with self.assertRaisesRegex(DatabaseCredentialError, "同时配置"):
            resolve_application_mysql_config(self.workspace)

    def test_empty_adapter_result_prevents_database_connection(self) -> None:
        """特殊取密方法返回空值时立即失败，不退回 DBID 或空密码连接。"""

        connect = Mock()
        with patch(
            "app.services.database_credentials.resolve_dbid_password", return_value=""
        ), patch.dict(sys.modules, {"pymysql": SimpleNamespace(connect=connect)}):
            with self.assertRaisesRegex(DataSourceError, "取密未返回有效"):
                validate_source(self.source)
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
