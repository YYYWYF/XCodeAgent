"""内置表数据 AG-UI 适配及身份隔离测试，不读取实际数据库。"""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
from ag_ui.core import RunStartedEvent, RunFinishedEvent, CustomEvent, StateSnapshotEvent
from ag_ui.encoder import EventEncoder

from app.config import Settings
from app.protocols.data_sources import DataSourceRequest, data_sources_capabilities
from app.services.managed_database_agui import ManagedDatabaseAgUiClient
from app.services.managed_database_table_data import read_table_page, read_table_operations


class ManagedDatabaseTableDataTests(unittest.TestCase):
    """使用当前配置、官方事件模型和模拟 HTTP 验证完整查询边界。"""

    def setUp(self):
        """构造当前应用与真实形状响应，所有远端请求均由替身接管。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.datasource_key, self.table = "12ab34cd56ef", "12ab34cd56ef_order"
        config = self.root / ".devagentstudio/application.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"schemaVersion": 6, "configRevision": 3, "appName": "订单",
            "auth": {"enable": False}, "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database", "db": {"useBuiltin": True}},
            "managedDatabase": {"status": "REGISTERED", "datasourceKey": self.datasource_key, "datasourceId": 2}}))
        self.page = {"status": "SUCCEEDED", "datasourceKey": self.datasource_key, "tableName": self.table,
            "columns": [{"name": "id", "type": "bigint", "nullable": False, "primaryKey": True, "autoIncrement": True, "defaultValue": None, "description": "主键"},
                        {"name": "reason", "type": "text", "nullable": True, "primaryKey": False, "autoIncrement": False, "defaultValue": None, "description": "原因"}],
            "rows": [{"id": "18446744073709551615", "reason": None}], "pageNumber": 1, "pageSize": 20,
            "hasMore": False, "truncated": False, "columnFormats": {"id": "TEXT", "reason": "TEXT"}, "filteringSupported": False}
        self.finish, self.same_snapshot, self.same_identity = True, True, True
        self.failure_code = None
        self.calls = []
        settings = Settings(model_base_url="http://model.invalid", model_api_key="test", model_name="test", managed_database_service_key="test-only-key")
        self.client = ManagedDatabaseAgUiClient(settings, transport=httpx.MockTransport(self.reply))
        self.enterContext(patch("app.services.managed_database_table_data.Settings.from_env", return_value=settings))
        self.enterContext(patch("app.services.managed_database_table_data.ManagedDatabaseAgUiClient", return_value=self.client))
        self.enterContext(patch("app.services.data_sources.database_source_mode", return_value="builtin"))
        self.listing = self.enterContext(patch("app.services.managed_database_table_data.read_managed_tables", return_value={"tables": [{"name": self.table}]}))

    def reply(self, request):
        """用官方编码器模拟 SaaS 事件，检查凭据只在后端请求头中。"""

        self.calls.append(request)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer test-only-key")
        body = json.loads(request.content)
        self.assertEqual(body["forwardedProps"]["query"]["filter"]["conditions"], [])
        thread, run = body["threadId"], body["runId"]
        value = {"status": "FAILED", "code": self.failure_code} if self.failure_code else self.page
        state = value if self.same_snapshot else {"status": "SUCCEEDED"}
        events = [RunStartedEvent(thread_id=thread if self.same_identity else "foreign", run_id=run),
                  CustomEvent(name="TABLE_DATA_ERROR" if self.failure_code else "TABLE_DATA_RESULT", value=value),
                  StateSnapshotEvent(snapshot={"tableData": state})]
        if self.finish:
            events.append(RunFinishedEvent(thread_id=thread, run_id=run, result=value))
        text = "".join(EventEncoder().encode(event) for event in events)
        return httpx.Response(200, text=text, headers={"Content-Type": "text/event-stream"})

    def read(self):
        """读取当前工作区应用的一页，测试不允许客户端指定 datasourceKey。"""

        return read_table_page(str(self.root), "database", self.table, 1, 20)

    def test_preserves_exact_numbers_null_and_one_request(self):
        """正常页只发一次查询，精度及 NULL 原样返回。"""

        result = self.read()
        self.assertEqual(result["rows"][0]["id"], "18446744073709551615")
        self.assertIsNone(result["rows"][0]["reason"])
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("test-only-key", json.dumps(result))

    def test_rejects_incomplete_or_inconsistent_event_streams(self):
        """缺结束、串运行身份及终态不一致均不能发布已有数据。"""

        for attr in ["finish", "same_snapshot", "same_identity"]:
            with self.subTest(attr=attr):
                setattr(self, attr, False)
                with self.assertRaises(ValueError):
                    self.read()
                setattr(self, attr, True)

    def test_rejects_foreign_table_or_app_and_invalid_rows(self):
        """SaaS 响应应用、表、分页和字段形状必须与本次请求精确对应。"""

        original = deepcopy(self.page)
        for key, value in [("datasourceKey", "ab1234cd56ef"), ("tableName", "other"), ("pageNumber", 2),
                           ("rows", [{"id": 123, "reason": None}]), ("rows", [{"id": "1", "foreign": "hidden"}])]:
            self.page = deepcopy(original)
            self.page[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.read()

    def test_unlisted_table_never_queries_rows(self):
        """目录外表不能触发行查询，空表数据与未授权表明确区分。"""

        self.listing.return_value = {"tables": []}
        with self.assertRaises(ValueError):
            self.read()
        self.assertEqual(self.calls, [])

    def test_business_errors_are_safe_and_not_retried(self):
        """返回稳定操作建议而非原始响应，失败不重发查询。"""

        self.failure_code = "RESOURCE_BUSY"
        with self.assertRaisesRegex(ValueError, "正在变更"):
            self.read()
        self.assertEqual(len(self.calls), 1)

    def test_recent_operations_filter_exact_table_and_drop_internal_fields(self):
        """最近记录限定当前表，整应用范围也匹配，内部 SQL 字段被剔除。"""

        entry = {"operationId": "101", "mode": "CREATE", "tableName": self.table, "tableNames": [self.table],
                 "status": "SUCCEEDED", "createdAt": "2026-10-15T02:00:00Z",
                 "executionResult": {"errorCode": None, "errorSummary": None, "recoveryState": "NOT_REQUESTED", "sql": "private-sql"}}
        unrelated = {**entry, "operationId": "102", "mode": "DROP", "tableName": "other", "tableNames": ["other"]}
        bulk = {**entry, "operationId": "103", "mode": "RELEASE", "tableName": ""}
        with patch("app.services.managed_database_table_data.ManagedTableClient") as remote:
            remote.return_value.request.return_value = [entry, unrelated, bulk]
            result = read_table_operations(str(self.root), "database", self.table)
        self.assertEqual([item["operationId"] for item in result["operations"]], ["101", "103"])
        self.assertEqual(result["historyLimit"], 100)
        self.assertNotIn("private-sql", json.dumps(result))

    def test_new_actions_publish_capabilities_and_strict_page_input(self):
        """health 元数据列出新动作，分页不能接受字符串或布尔值。"""

        from pydantic import ValidationError

        self.assertIn("database_rows", data_sources_capabilities()["actions"])
        for value in ["20", True, 101]:
            with self.assertRaises(ValidationError):
                DataSourceRequest.model_validate({"workspaceRoot": str(self.root), "pageSize": value})
