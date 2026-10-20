"""验证平台内置表目录、真实字段、空 Schema 绑定和 API 映射失效链路。"""

import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.config import Settings
from app.protocols.data_sources import build_data_sources_ag_ui_stream
from app.services.api_design import api_design_readiness, load_database_columns, load_database_tables
from app.services.binding_workspace import BindingDraftRequest, BindingTarget, save_binding_draft
from app.services.data_sources import change_selected_tables, public_catalog, selected_tables
from app.services.endpoint_design_detail import EndpointDesignPrepareRequest, EndpointDesignSaveRequest, prepare_endpoint_design, save_endpoint_design
from app.services.managed_table_client import ManagedTableClient
from app.workspace.endpoint_design_documents import technical_plan_sha256


class ManagedDatabaseMetadataTests(unittest.TestCase):
    """使用当前工作区文件和 HTTP 替身，不连接真实 SaaS 或数据库。"""

    def setUp(self) -> None:
        """准备已注册内置库及当前技术规划，替换 SaaS 传输。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        folder = self.root / ".devagentstudio"
        (folder / "plans").mkdir(parents=True)
        self.datasource_key = "12ab34cd56ef"
        self.table = self.datasource_key + "_orders"
        self.application = {
            "schemaVersion": 6, "configRevision": 3, "appName": "订单应用",
            "auth": {"enable": False}, "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database", "db": {"useBuiltin": True}},
            "managedDatabase": {"datasourceKey": self.datasource_key, "datasourceId": 2, "status": "REGISTERED"},
        }
        self.application_path = folder / "application.json"
        self.application_path.write_text(json.dumps(self.application))
        self.plan = {"api_contracts": [{"id": "orders", "endpoints": [{"id": "list", "method": "GET", "path": "/orders",
            "parameters": [{"name": "id", "in": "query", "schema": {"type": "integer"}}]}]}]}
        (folder / "plans" / "technical-plan.json").write_text(json.dumps(self.plan))
        self.columns = [
            {"name": "id", "type": "bigint", "nullable": False, "primaryKey": True, "autoIncrement": True, "defaultValue": None, "description": "主键"},
            {"name": "is_deleted", "type": "tinyint(1)", "nullable": False, "primaryKey": False, "autoIncrement": False, "defaultValue": "0", "description": "是否删除"},
        ]
        self.response_datasource_key = self.datasource_key
        self.busy = False
        self.calls: list[httpx.Request] = []
        settings = Settings(model_base_url="http://model.invalid", model_api_key="test", model_name="test", managed_database_service_key="test-service-key")
        client = ManagedTableClient(settings, transport=httpx.MockTransport(self.reply))
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch("app.services.managed_database_metadata.Settings.from_env", return_value=settings))
        stack.enter_context(patch("app.services.managed_database_metadata.ManagedTableClient", return_value=client))

    def reply(self, request: httpx.Request) -> httpx.Response:
        """只接受当前应用的元数据 GET，不允许测试意外提交表操作。"""

        self.calls.append(request)
        self.assertEqual(request.method, "GET")
        self.assertIn(f"/datasources/{self.datasource_key}/tables", request.url.path)
        if self.busy:
            return httpx.Response(409, json={"code": "RESOURCE_BUSY", "message": "not-for-public"})
        if request.url.path.endswith("/columns"):
            return httpx.Response(200, json={"datasourceKey": self.response_datasource_key, "tableName": self.table, "description": "订单表", "columns": self.columns})
        return httpx.Response(200, json={"datasourceKey": self.response_datasource_key, "tables": [{"tableName": self.table, "description": "订单表", "columnCount": len(self.columns)}]})

    def test_catalog_and_tables_are_automatic_and_do_not_persist_metadata(self) -> None:
        """来源统一命名，SaaS 表自动可选，真实表清单和字段不复制到本地目录。"""

        self.assertEqual(public_catalog(self.root).sources[0].name, "平台内置数据库")
        before = (self.root / ".devagentstudio/datasource/databases/application-database.json").read_text()
        tables = selected_tables(self.root)
        self.assertEqual(tables, [{"sourceId": "application-database", "schema": "", "table": self.table, "description": "订单表"}])
        metadata = load_database_tables(self.root, "application-database")
        self.assertEqual(metadata["tables"][0]["columnCount"], 2)
        self.assertEqual((self.root / ".devagentstudio/datasource/databases/application-database.json").read_text(), before)
        with self.assertRaisesRegex(ValueError, "无需手动添加或移除"):
            change_selected_tables(self.root, "application-database", [self.table], False)

    def test_columns_keep_real_defaults_and_reject_foreign_app(self) -> None:
        """字段来自实际回读并保留完整表名，错误应用回执不能进入映射。"""

        metadata = load_database_columns(self.root, "application-database", self.table)
        self.assertEqual(metadata["schema"], "")
        self.assertEqual(metadata["columns"][1]["defaultValue"], "0")
        self.assertTrue(metadata["columns"][0]["primaryKey"])
        self.assertTrue(metadata["columns"][1]["required"])
        self.response_datasource_key = "ab1234cd56ef"
        with self.assertRaisesRegex(ValueError, "应用身份"):
            load_database_columns(self.root, "application-database", self.table)

    def test_busy_and_missing_registration_are_errors_not_empty_tables(self) -> None:
        """SaaS 占用和注册缺失不能伪装成没有表或回退本地连接。"""

        public_catalog(self.root)
        self.busy = True
        with self.assertRaisesRegex(ValueError, "稍后刷新数据源"):
            selected_tables(self.root)
        self.calls.clear()
        self.application["managedDatabase"] = {"status": "REGISTERING"}
        self.application_path.write_text(json.dumps(self.application))
        with self.assertRaisesRegex(ValueError, "尚未确认注册成功"):
            selected_tables(self.root)
        self.assertEqual(self.calls, [])

    def test_api_binding_saves_empty_schema_and_detects_rebuilt_field(self) -> None:
        """真实空 Schema 查询映射可暂存、确认，引用字段改类型后门禁失效。"""

        preparation = prepare_endpoint_design(EndpointDesignPrepareRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="list"))
        draft = preparation["payload"]["draft"]
        field = draft["fieldMappings"][0]["endpointField"]
        selection = BindingTarget(sourceType="database", sourceId="application-database", schema="", table=self.table)
        draft["fieldMappings"] = []
        draft["sourceBinding"] = selection.model_dump(by_alias=True, exclude_none=True)
        draft["databaseOperation"] = "read"
        draft["databaseQuery"] = {"join": "and", "items": [{"kind": "condition", **draft["sourceBinding"],
            "column": "id", "type": "bigint", "operator": "eq", "right": {"kind": "endpoint", "endpointField": field}}]}
        digest = technical_plan_sha256(self.root)
        save_binding_draft(BindingDraftRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="list", draft=draft, selection=selection, technicalPlanHash=digest))
        result = save_endpoint_design(EndpointDesignSaveRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="list", draft=draft, bindingSelection=selection, technicalPlanHash=digest))
        self.assertEqual(result["design"]["sourceBinding"]["schema"], "")
        self.assertTrue(api_design_readiness(self.root, self.plan, target_type="endpoint", target_id="list", api_contract_id="orders")["ready"])
        self.columns[0]["type"] = "varchar(64)"
        gate = api_design_readiness(self.root, self.plan, target_type="endpoint", target_id="list", api_contract_id="orders")
        self.assertFalse(gate["ready"])
        self.assertIn("引用字段 id 已变化", gate["missing_api_designs"][0]["reason"])

    def test_builtin_snapshot_cannot_skip_resource_identity_check(self) -> None:
        """内置快照缺少当前数据源 Key 时必须阻断，不能当作外部来源跳过。"""

        from app.services.managed_database_metadata import validate_managed_snapshots

        with patch("app.services.data_sources.database_source_mode", return_value="builtin"):
            with self.assertRaisesRegex(ValueError, "缺少数据源 Key"):
                validate_managed_snapshots(self.root, {"sourceSnapshots": [{
                    "sourceType": "database", "sourceId": "application-database",
                    "details": {"schema": "", "table": self.table, "columns": []},
                }]})

    def test_selected_tables_uses_complete_ag_ui_lifecycle(self) -> None:
        """自动内置表目录仍通过标准数据源产品流返回，不新增 REST 读取链路。"""

        async def collect() -> str:
            """收集一次只读动作的完整输出。"""
            return "".join([part async for part in build_data_sources_ag_ui_stream(action="selected_tables", payload={
                "threadId": "metadata-test", "runId": "metadata-run", "forwardedProps": {"dataSources": {"workspaceRoot": str(self.root)}}})])

        with patch("app.services.preview_runtime_guard.record_product_interaction"):
            output = asyncio.run(collect())
        for event in ["RUN_STARTED", "STATE_SNAPSHOT", "TEXT_MESSAGE_END", "RUN_FINISHED"]:
            self.assertIn(event, output)
        self.assertIn(self.table, output)
        self.assertIn('"status":"completed"', output)
