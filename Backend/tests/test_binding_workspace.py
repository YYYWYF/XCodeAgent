"""绑定工作台中间状态、正式确认边界与 AG-UI 生命周期回归。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.persistence.data_sources import write_sources
from app.routes.data_sources import data_sources_router
from app.routes.endpoint_designs import endpoint_designs_router
from app.services.api_design import api_design_readiness
from app.services.binding_workspace import (
    BindingDraftRequest, BindingTarget, read_binding_draft,
    save_binding_draft, source_references, validate_binding_selection,
)
from app.services.data_sources import change_selected_tables, mutate_catalog, selected_tables
from app.services.endpoint_design_detail import EndpointDesignSaveRequest, prepare_endpoint_design, EndpointDesignPrepareRequest, save_endpoint_design
from app.workspace.endpoint_design_documents import technical_plan_sha256


class BindingWorkspaceTests(unittest.TestCase):
    """验证中间状态持久化及原有正式产物不被草稿污染。"""

    def setUp(self):
        """创建最小真实契约，测试不连接数据库或启动应用服务。"""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = self.root / ".devagentstudio/plans/technical-plan.json"
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text(json.dumps({"api_contracts": [{"id": "orders", "endpoints": [
            {"id": "list", "method": "GET", "path": "/orders", "parameters": [{"name": "id", "in": "query", "schema": {"type": "integer"}}]},
            {"id": "other", "method": "GET", "path": "/other"},
        ]}]}), encoding="utf-8")
        self.hash = technical_plan_sha256(self.root)

    def request(self, endpoint="list"):
        """为目标接口构造允许未完成的有效草稿。"""
        preparation = prepare_endpoint_design(EndpointDesignPrepareRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId=endpoint))
        return BindingDraftRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId=endpoint,
                                   draft=preparation["payload"]["draft"], technicalPlanHash=self.hash)

    def test_draft_roundtrip_isolated_and_not_formal(self):
        """保存未配置字段后可恢复，其他接口和正式产物不受影响。"""
        saved = save_binding_draft(self.request())
        self.assertEqual(read_binding_draft(self.root, "orders", "list"), saved)
        self.assertIsNone(read_binding_draft(self.root, "orders", "other"))
        self.assertFalse((self.root / ".devagentstudio/plans/endpoints").exists())
        restored = prepare_endpoint_design(EndpointDesignPrepareRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="list"))
        self.assertEqual(restored["bindingDraft"], saved)
        self.assertEqual(restored["payload"]["existingStatus"]["status"], "pending")

    def test_incomplete_query_draft_roundtrip_and_old_draft_ignored(self):
        """未完成的查询行可暂存，旧格式草稿不作为当前草稿恢复。"""
        request = self.request()
        request.draft["databaseOperation"] = "read"
        request.draft["databaseQuery"] = {"join": "and", "items": [{
            "kind": "condition", "sourceType": "database", "sourceId": "db", "schema": "app",
            "table": "orders", "column": "", "type": "", "operator": "eq",
        }]}
        saved = save_binding_draft(request)
        self.assertEqual(read_binding_draft(self.root, "orders", "list")["draft"]["databaseQuery"], request.draft["databaseQuery"])
        draft_path = next(self.root.rglob("draft-*.json"))
        draft_path.write_text(json.dumps({**saved, "draftFormat": "endpoint-field-mapping.v4"}), encoding="utf-8")
        self.assertIsNone(read_binding_draft(self.root, "orders", "list"))

    def test_draft_survives_catalog_rewrite(self):
        """独立目录不会被数据源存储的清理过程删除。"""
        saved = save_binding_draft(self.request())
        write_sources(self.root, [])
        self.assertEqual(read_binding_draft(self.root, "orders", "list"), saved)

    def test_changed_plan_and_formal_revision_reject_save_without_overwrite(self):
        """契约或正式修订变化后保留原草稿。"""
        request = self.request()
        saved = save_binding_draft(request)
        with patch("app.workspace.endpoint_design_documents.read_endpoint_design", return_value={"artifactRevision": "a" * 32}):
            with self.assertRaisesRegex(ValueError, "已变化"):
                save_binding_draft(request)
        self.plan.write_text('{"api_contracts": [{"id":"orders","endpoints":[{"id":"list"}]}]}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已变化"):
            save_binding_draft(request)
        self.assertEqual(read_binding_draft(self.root, "orders", "list"), saved)

    def test_table_add_remove_and_catalog_rewrite(self):
        """已管理清单写回数据库对象，去重和移除均不改变真实数据库。"""
        source = {"id": "db", "type": "database", "mode": "direct", "name": "DB", "schema": "app", "domain": "localhost", "port": 3306, "userName": "user", "passwordCiphertext": "devagentstudio-secret:v1:rsa-oaep-256:test:cipher"}
        write_sources(self.root, [source])
        with patch("app.services.api_design.load_database_tables", return_value={"schema": "app", "tables": [{"name": "orders"}, {"name": "users"}]}):
            change_selected_tables(self.root, "db", ["orders", "orders"], False)
            self.assertEqual(len(selected_tables(self.root)), 1)
            with self.assertRaisesRegex(ValueError, "不存在"):
                change_selected_tables(self.root, "db", ["missing"], False)
            self.assertEqual(len(selected_tables(self.root)), 1)
            path = self.root / ".devagentstudio/datasource/databases/db.json"
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["managedTables"], [{"table": "orders", "description": ""}])
            self.assertNotIn("columns", stored)
            self.assertEqual(change_selected_tables(self.root, "db", ["orders"], True), [])

    def test_single_source_confirmation_rejects_unadded_and_mixed_tables(self):
        """后端独立防止绕过添加清单或提交多来源映射。"""
        selection = BindingTarget(sourceType="database", sourceId="db", schema="app", table="orders")
        with patch("app.services.binding_workspace.selected_tables", return_value=[]):
            with self.assertRaisesRegex(ValueError, "已添加"):
                validate_binding_selection(self.root, selection, {"fieldMappings": [{"mappingType": "unconfigured"}]})
        with patch("app.services.binding_workspace.selected_tables", return_value=[{"sourceId": "db", "schema": "app", "table": "orders"}]), patch("app.services.api_design.load_database_columns", return_value={"schema": "app", "columns": [{"name": "id"}]}):
            with self.assertRaisesRegex(ValueError, "选定对象"):
                validate_binding_selection(self.root, selection, {"fieldMappings": [{"mappingType": "source_mapping", "processingType": "direct", "sourceFields": [{"sourceType": "database", "sourceId": "db", "schema": "app", "table": "users"}]}]})

    def test_selected_tables_reads_schema_from_real_detail_not_index(self):
        """连接名称更新保留清单，Schema 更新清空清单。"""
        source = {"id": "db", "type": "database", "mode": "direct", "name": "DB", "schema": "app", "domain": "localhost", "port": 3306, "userName": "user", "passwordCiphertext": "devagentstudio-secret:v1:rsa-oaep-256:test:cipher"}
        write_sources(self.root, [source])
        with patch("app.services.api_design.load_database_tables", return_value={"schema": "app", "tables": [{"name": "orders"}]}):
            change_selected_tables(self.root, "db", ["orders"], False)
        self.assertEqual(selected_tables(self.root)[0]["table"], "orders")
        mutate_catalog(self.root, action="update", source={**source, "name": "Renamed"})
        self.assertEqual(selected_tables(self.root)[0]["table"], "orders")
        mutate_catalog(self.root, action="update", source={**source, "schema": "another"})
        self.assertEqual(selected_tables(self.root), [])
        with patch("app.services.api_design.load_database_tables", return_value={"schema": "another", "tables": [{"name": "orders"}]}):
            change_selected_tables(self.root, "db", ["orders"], False)
        self.assertEqual([(item["schema"], item["table"]) for item in selected_tables(self.root)], [("another", "orders")])

    def test_empty_field_binding_rejected_without_losing_draft(self):
        """两种来源的空字段绑定均不能确认，草稿可恢复且不会生成空正式产物。"""
        selections = [
            BindingTarget(sourceType="database", sourceId="db", schema="app", table="orders"),
            BindingTarget(sourceType="external_api", sourceId="external", directoryId="directory", operationId="operation"),
        ]
        for selection in selections:
            with self.subTest(source_type=selection.source_type):
                request = self.request("other")
                request.selection = selection
                saved = save_binding_draft(request)
                with self.assertRaisesRegex(ValueError, "没有可映射字段"):
                    save_endpoint_design(EndpointDesignSaveRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="other",
                                         draft=request.draft, bindingSelection=selection, technicalPlanHash=self.hash))
                self.assertEqual(read_binding_draft(self.root, "orders", "other"), saved)
                self.assertFalse((self.root / ".devagentstudio/plans/endpoints").exists())

    def test_failed_confirmation_keeps_draft(self):
        """未完成草稿不能正式确认，失败后仍可恢复。"""
        request = self.request()
        saved = save_binding_draft(request)
        with self.assertRaises(ValueError):
            save_endpoint_design(EndpointDesignSaveRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="list", draft=request.draft))
        self.assertEqual(read_binding_draft(self.root, "orders", "list"), saved)

    def test_confirmed_shape_unchanged_and_draft_cleared(self):
        """无来源的空字段确认保留指纹校验与门禁检查，不自动继续开发。"""
        request = self.request("other")
        save_binding_draft(request)
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        self.assertFalse(api_design_readiness(self.root, plan, target_type="endpoint", target_id="other", api_contract_id="orders")["ready"])
        confirmation = EndpointDesignSaveRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="other",
                                                 draft=request.draft, technicalPlanHash="0" * 64)
        with self.assertRaisesRegex(ValueError, "TechnicalPlan"):
            save_endpoint_design(confirmation)
        self.assertIsNotNone(read_binding_draft(self.root, "orders", "other"))
        confirmation.technical_plan_hash = self.hash
        result = save_endpoint_design(confirmation)
        self.assertEqual(result["design"]["schemaVersion"], "endpoint-field-mapping.v7")
        self.assertEqual(result["design"]["confirmationStatus"], "confirmed")
        self.assertNotIn("selection", result["design"])
        self.assertEqual(result["design"]["fieldMappings"], [])
        self.assertEqual(result["design"]["sourceSnapshots"], [])
        self.assertIsNone(read_binding_draft(self.root, "orders", "other"))
        self.assertTrue(api_design_readiness(self.root, plan, target_type="endpoint", target_id="other", api_contract_id="orders")["ready"])
        self.assertFalse((self.root / ".devagentstudio/application-lifecycle.json").exists())

    def test_database_journey_confirm_then_remove_keeps_formal_mapping(self):
        """从添加表、保存草稿到正式确认，移除表仅影响候选清单。"""
        write_sources(self.root, [{"id": "db", "type": "database", "mode": "direct", "name": "DB", "schema": "app",
                                  "domain": "localhost", "port": 3306, "userName": "user",
                                  "passwordCiphertext": "devagentstudio-secret:v1:rsa-oaep-256:test:cipher"}])
        selection = BindingTarget(sourceType="database", sourceId="db", schema="app", table="orders")
        request = self.request()
        request.selection = selection
        field = request.draft["fieldMappings"][0]["endpointField"]
        request.draft["fieldMappings"] = []
        request.draft["databaseQuery"] = {"join": "and", "items": [{
            "kind": "condition", **selection.model_dump(by_alias=True, exclude_none=True),
            "column": "id", "type": "integer", "operator": "eq",
            "right": {"kind": "endpoint", "endpointField": field},
        }]}
        request.draft["databaseOperation"] = "read"
        metadata = {"schema": "app", "tables": [{"name": "orders"}], "columns": [{"name": "id", "type": "integer"}]}
        with patch("app.services.api_design.load_database_tables", return_value=metadata), patch("app.services.api_design.load_database_columns", return_value=metadata):
            change_selected_tables(self.root, "db", ["orders"], False)
            save_binding_draft(request)
            result = save_endpoint_design(EndpointDesignSaveRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="list",
                                          draft=request.draft, bindingSelection=selection, technicalPlanHash=self.hash))
        self.assertEqual(result["design"]["databaseQuery"]["items"][0]["right"]["kind"], "endpoint")
        self.assertIsNone(read_binding_draft(self.root, "orders", "list"))
        self.assertEqual(source_references(self.root, "db", table="orders"), ["orders/list"])
        change_selected_tables(self.root, "db", ["orders"], True)
        self.assertEqual(source_references(self.root, "db", table="orders"), ["orders/list"])
        self.assertEqual(selected_tables(self.root), [])

    def test_external_journey_checks_direction_and_missing_source(self):
        """真实目录接口可确认直接映射，方向错误或来源删除时保留草稿。"""
        write_sources(self.root, [{"id": "external", "type": "external_api", "name": "订单服务", "baseUrl": "https://example.com",
                                  "directories": [{"id": "directory", "name": "订单", "operations": [{"id": "operation", "name": "查询订单", "method": "GET", "path": "/orders",
                                  "queryParameters": [{"name": "id", "type": "integer", "required": False}]}]}]}])
        selection = BindingTarget(sourceType="external_api", sourceId="external", directoryId="directory", operationId="operation")
        request = self.request()
        request.selection = selection
        field = request.draft["fieldMappings"][0]["endpointField"]
        source_field = {**selection.model_dump(by_alias=True, exclude_none=True), "section": "response_body", "path": "id", "type": "integer"}
        request.draft["fieldMappings"] = [{"endpointField": field, "mappingType": "source_mapping", "processingType": "direct", "sourceFields": [source_field]}]
        saved = save_binding_draft(request)
        confirmation = EndpointDesignSaveRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="list",
                                               draft=request.draft, bindingSelection=selection, technicalPlanHash=self.hash)
        with self.assertRaises(ValueError):
            save_endpoint_design(confirmation)
        self.assertEqual(read_binding_draft(self.root, "orders", "list"), saved)

        source_field["section"] = "query"
        request.draft["fieldMappings"] = []
        request.draft["externalApiBindings"] = [{"externalField": source_field, "right": {"kind": "endpoint", "endpointField": field}}]
        confirmation.draft = request.draft
        result = save_endpoint_design(confirmation)
        self.assertEqual(result["design"]["externalApiBindings"][0]["externalField"]["section"], "query")
        self.assertIsNone(read_binding_draft(self.root, "orders", "list"))
        self.assertEqual(source_references(self.root, "external", directory_id="directory", operation_id="operation"), ["orders/list"])
        request.base_revision = result["artifactRevision"]
        saved = save_binding_draft(request)
        write_sources(self.root, [])
        confirmation.base_revision = result["artifactRevision"]
        with self.assertRaisesRegex(ValueError, "不存在"):
            save_endpoint_design(confirmation)
        self.assertEqual(read_binding_draft(self.root, "orders", "list"), saved)

    def test_external_required_fields_and_duplicate_sources_are_rejected(self):
        """外部接口必填请求字段必须有映射，且同一来源不能重复绑定。"""
        selection = BindingTarget(sourceType="external_api", sourceId="external", directoryId="directory", operationId="operation")
        endpoint = {"side": "request", "location": "query", "path": "id", "type": "integer", "required": True}
        source = {**selection.model_dump(by_alias=True, exclude_none=True), "section": "query", "path": "other", "type": "integer"}
        draft = {"fieldMappings": [], "externalApiBindings": [{"externalField": source, "right": {"kind": "endpoint", "endpointField": endpoint}}]}
        with patch("app.services.api_design.load_external_operation", return_value={"fields": [{"section": "query", "path": "id", "type": "integer", "required": True}, {"section": "query", "path": "other", "type": "integer", "required": False}]}):
            with self.assertRaisesRegex(ValueError, "必填字段"):
                validate_binding_selection(self.root, selection, draft)
            source["path"] = "id"
            draft["externalApiBindings"].append({"externalField": source, "right": {"kind": "endpoint", "endpointField": endpoint}})
            with self.assertRaisesRegex(ValueError, "不能重复配置"):
                validate_binding_selection(self.root, selection, draft)

    def test_new_actions_emit_complete_lifecycles(self):
        """新增动作的成功和业务失败均保留完整标准事件。"""
        app = FastAPI()
        app.include_router(data_sources_router)
        app.include_router(endpoint_designs_router)
        client = TestClient(app)
        draft = self.request().model_dump(by_alias=True)
        requests = [
            ("/data-sources/selected-tables", "dataSources", {"workspaceRoot": str(self.root)}, "completed"),
            ("/data-sources/add-tables", "dataSources", {"workspaceRoot": str(self.root)}, "failed"),
            ("/endpoint-designs/run", "endpointDesigns", {"action": "save_draft", **draft}, "completed"),
            ("/endpoint-designs/run", "endpointDesigns", {"action": "discard_draft", "workspaceRoot": str(self.root), "apiContractId": "orders", "endpointId": "list"}, "completed"),
        ]
        for url, key, value, status in requests:
            response = client.post(url, json={"threadId": "binding-test", "runId": "binding-run", "forwardedProps": {key: value}})
            for event in ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT", "RUN_FINISHED"]:
                self.assertIn(event, response.text)
            self.assertIn(f'"status":"{status}"', response.text)

    def test_business_rule_confirm_reload_and_reconfirm(self):
        """规则草稿不推进门禁，正式确认后可完整恢复并再次修订确认。"""
        from app.services.api_design import initial_api_design_payload
        write_sources(self.root, [{"id": "external", "type": "external_api", "name": "订单服务", "baseUrl": "https://example.com",
            "directories": [{"id": "directory", "name": "订单", "operations": [{"id": "operation", "name": "查询订单", "method": "GET", "path": "/orders",
            "queryParameters": [{"name": "offset", "type": "integer", "required": True}, {"name": "cursor", "type": "integer", "required": False}]}]}]}])
        request = self.request()
        request.selection = BindingTarget(sourceType="external_api", sourceId="external", directoryId="directory", operationId="operation")
        field = request.draft["fieldMappings"][0]["endpointField"]
        right = {"kind": "business", "origin": "endpoint", "endpointFields": [field], "builtinFields": [], "businessDescription": "将 id 减一作为偏移量。", "missingBehavior": "default", "defaultValue": 0}
        request.draft["externalApiBindings"] = [{"externalField": {**request.selection.model_dump(by_alias=True, exclude_none=True), "section": "query", "path": path, "type": "integer"}, "right": right} for path in ("offset", "cursor")]
        saved = save_binding_draft(request)
        self.assertEqual(saved["draft"]["externalApiBindings"][0]["right"]["defaultValue"], 0)
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        self.assertFalse(api_design_readiness(self.root, plan, target_type="endpoint", target_id="list", api_contract_id="orders")["ready"])
        confirmation = EndpointDesignSaveRequest(workspaceRoot=str(self.root), apiContractId="orders", endpointId="list", draft=request.draft, bindingSelection=request.selection, technicalPlanHash=self.hash)
        first = save_endpoint_design(confirmation)
        restored = initial_api_design_payload(self.root, plan, "orders", "list")["draft"]
        self.assertEqual(restored["sourceBinding"], request.selection.model_dump(by_alias=True, exclude_none=True))
        self.assertEqual(restored["externalApiBindings"][0]["right"]["businessDescription"], right["businessDescription"])
        self.assertTrue(api_design_readiness(self.root, plan, target_type="endpoint", target_id="list", api_contract_id="orders")["ready"])
        restored["externalApiBindings"][0]["right"]["businessDescription"] = "将 id 乘以 20 作为偏移量。"
        confirmation.draft = restored
        confirmation.base_revision = first["artifactRevision"]
        second = save_endpoint_design(confirmation)
        self.assertNotEqual(first["artifactRevision"], second["artifactRevision"])
        self.assertIn("乘以 20", second["detail"]["markdown"])


if __name__ == "__main__":
    unittest.main()
