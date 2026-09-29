"""静态来源确认、草稿、来源边界与生成职责的回归测试。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain.api_design_static import static_fields, validate_static_design
from app.services.api_design import confirm_api_design, endpoint_field_nodes, initial_api_design_payload, api_design_readiness
from app.services.binding_workspace import BindingDraftRequest, save_binding_draft, read_binding_draft
from app.services.unit_generation_requirement_targets import endpoint_source_types
from app.services.unit_generation_requirements import _backend_requirements
from app.workspace.endpoint_design_documents import read_endpoint_design, technical_plan_sha256, render_endpoint_design_markdown


def fixture():
    """构造对象列表接口与包含未使用请求参数的静态来源配置。"""
    endpoint = {"id": "categories.list", "method": "GET", "path": "/categories", "response_schema_ref": "Result",
                "parameters": [{"name": "locale", "in": "query", "schema": {"type": "string"}}]}
    contract = {"id": "categories", "endpoints": [endpoint], "schemas": {"Result": {
        "type": "object", "required": ["list"], "properties": {"list": {"type": "array", "items": {
            "type": "object", "required": ["id", "name"], "properties": {"id": {"type": "integer"}, "name": {"type": "string"}}}}}}}}
    plan = {"api_contracts": [contract]}
    data = [{"id": 1, "name": "电子产品"}, {"id": 2, "name": "家居生活"}]
    fields = endpoint_field_nodes(contract, endpoint)
    mappings = []
    for field in fields:
        snapshot = {key: value for key, value in field.items() if key not in {"id", "nodeType"}}
        if field["side"] == "request":
            mappings.append({"endpointField": snapshot, "mappingType": "unconfigured"})
        else:
            mappings.append({"endpointField": snapshot, "mappingType": "source_mapping", "processingType": "direct",
                             "sourceFields": [next(source for source in static_fields(data) if source["path"] == field["path"].removeprefix("list"))]})
    return plan, {"apiContractId": "categories", "endpointId": "categories.list", "sourceBinding": {"sourceType": "static"},
                  "staticData": data, "fieldMappings": mappings, "databaseWrites": [], "externalApiBindings": []}


class StaticApiDesignTests(unittest.TestCase):
    """保证静态数据是可确认的正式配置而非仅前端展示。"""

    def test_round_trip_and_readiness_without_external_access(self):
        """通过正式确认与准备链路往返完整静态数据，且完全不读取外部元数据。"""
        plan, draft = fixture()
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace, ".devagentstudio/plans/technical-plan.json")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(plan), encoding="utf-8")
            with patch("app.services.api_design.load_database_columns", side_effect=AssertionError("数据库访问")), patch("app.services.api_design.load_external_operation", side_effect=AssertionError("外部调用")):
                result = confirm_api_design(workspace, plan, {"action": "confirm", **{key: draft[key] for key in ("apiContractId", "endpointId")}, "draft": draft})
                design = read_endpoint_design(workspace, "categories", "categories.list")
                self.assertEqual(design["staticData"], draft["staticData"])
                self.assertEqual(design["sourceSnapshots"], [])
                self.assertNotIn("databaseOperation", design)
                payload = initial_api_design_payload(workspace, plan, "categories", "categories.list")
                self.assertEqual(payload["draft"]["staticData"], draft["staticData"])
                ready = api_design_readiness(workspace, plan, target_type="endpoint", target_id="categories.list", api_contract_id="categories")
                self.assertTrue(ready["ready"])
                self.assertEqual(ready["missing_api_designs"], [])
            markdown = render_endpoint_design_markdown(result["design"])
            self.assertIn("电子产品", markdown)
            self.assertNotIn("接口映射说明", markdown)

    def test_draft_round_trip(self):
        """独立草稿保持静态内容和来源身份，未影响正式确认。"""
        plan, draft = fixture()
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace, ".devagentstudio/plans/technical-plan.json")
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(plan), encoding="utf-8")
            with patch("app.services.endpoint_design_detail._read_current_technical_plan", return_value=plan):
                save_binding_draft(BindingDraftRequest(workspaceRoot=workspace, apiContractId="categories", endpointId="categories.list", draft=draft,
                    selection={"sourceType": "static"}, technicalPlanHash=technical_plan_sha256(workspace)))
            saved = read_binding_draft(workspace, "categories", "categories.list")
            self.assertEqual(saved["draft"]["staticData"], draft["staticData"])
            self.assertEqual(saved["selection"], {"sourceType": "static"})
            self.assertIsNone(read_endpoint_design(workspace, "categories", "categories.list"))

    def test_invalid_data_and_stale_paths(self):
        """拒绝不明确类型、过大数据、混合结构和失效引用。"""
        for invalid in (None, [], {}, [1], {"id": None}, {"id": float("nan")}, [{"id": 1}, {"id": "1"}], {"name": "字" * 90000}, {"__proto__": 1}):
            with self.subTest(value=str(invalid)[:40]), self.assertRaises(ValueError):
                static_fields(invalid)
        _, draft = fixture()
        for update in ({"staticData": [{"code": 1}]}, {"implementationDescription": "说明"}, {"databaseOperation": "read"}, {"sourceBinding": {"sourceType": "database"}}):
            with self.assertRaises(ValueError):
                validate_static_design({**draft, **update})
        broken = copy.deepcopy(draft)
        broken["fieldMappings"][1]["endpointField"]["path"] = "id"
        with self.assertRaisesRegex(ValueError, "列表层级"):
            validate_static_design(broken)

    def test_generation_has_service_without_physical_access(self):
        """静态接口走后端对象、服务、控制器职责，不生成数据库或上游任务。"""
        _, draft = fixture()
        key = ("categories", "categories.list")
        types = endpoint_source_types([draft], {key: {}})
        self.assertEqual(types[key], frozenset({"static"}))
        kinds = [item.source_refs["kind"] for item in _backend_requirements(key, types[key])]
        self.assertEqual(kinds, ["backend.objects", "backend.application_service", "backend.endpoint_controller"])
