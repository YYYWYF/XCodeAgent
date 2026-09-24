"""Direct 实体 SQL 与 API 契约的独立确认及 Build 投影测试。"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.direct_api_contract import (
    DirectEndpointRequest,
    DirectEndpointSaveRequest,
    direct_build_prerequisite_errors,
    project_confirmed_direct_contracts,
    read_direct_endpoint_contract,
    save_direct_endpoint_contract,
)
from app.services.direct_entity_design import (
    DirectEntityConfirmRequest,
    DirectEntityRequest,
    confirm_direct_entity_design,
    read_direct_entity_design,
)


class DirectDevelopmentTests(unittest.TestCase):
    """验证实体和 API 的确认互不依赖，Build 只消费当前确认版本。"""

    def setUp(self) -> None:
        """创建只包含当前 Direct TechnicalPlan 的隔离工作区。"""

        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        plan_path = self.root / ".xcodeagent/plans/technical-plan.json"
        plan_path.parent.mkdir(parents=True)
        self.plan = {
            "artifact_type": "technical-plan",
            "confirmation_status": "confirmed",
            "topology": {"type": "agent_runtime_direct", "publicEdgeServiceId": "agent-runtime"},
            "entities": [{
                "id": "Message", "name": "Message",
                "fields": [{"name": "content", "type": "long_text", "required": True}],
            }],
            "api_contracts": [{
                "id": "chat_api", "entity_ids": ["Message"],
                "schemas": {"MessageOutput": {"type": "object", "properties": {"content": {"type": "string"}}}},
                "endpoints": [{
                    "id": "chat_api.list", "method": "GET", "path": "/messages",
                    "response_schema_ref": "MessageOutput",
                }],
            }],
        }
        plan_path.write_text(json.dumps(self.plan), encoding="utf-8")

    def test_api_contract_can_be_confirmed_before_entity_sql(self) -> None:
        """API 契约只引用正式 Entity 定义，不读取实体完成状态。"""

        request = DirectEndpointRequest(
            workspaceRoot=str(self.root), apiContractId="chat_api", endpointId="chat_api.list",
        )
        draft = read_direct_endpoint_contract(request)
        saved = save_direct_endpoint_contract(DirectEndpointSaveRequest(
            workspaceRoot=str(self.root), apiContractId="chat_api", endpointId="chat_api.list",
            technicalPlanSha256=draft["technicalPlanSha256"], requestSchema=None,
            responseSchema={"type": "object", "properties": {"messages": {"type": "array", "items": {"type": "string"}}}},
        ))
        self.assertEqual(saved["status"], "confirmed")
        saved_path = self.root / ".xcodeagent/plans/direct/endpoints/chat_api/chat_api.list.json"
        self.assertEqual(
            json.loads(saved_path.read_text(encoding="utf-8"))["basedOn"],
            {"technicalPlanSha256": draft["technicalPlanSha256"]},
        )
        projected = project_confirmed_direct_contracts(self.root, self.plan)
        self.assertIn("chat_api/chat_api.list", projected["directConfirmedApiContracts"])
        self.assertEqual(
            projected["api_contracts"][0]["endpoints"][0]["response_schema_ref"],
            "Direct_chat_api_chat_api_list_Response",
        )
        errors = direct_build_prerequisite_errors(
            self.root, projected, {"type": "endpoint", "targetId": "chat_api.list", "apiContractId": "chat_api"},
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("实体 Message", errors[0])

    def test_api_contract_rejects_invalid_fields_and_becomes_stale(self) -> None:
        """错误字段类型和跨契约引用不能确认，上游变化必须重新确认。"""

        request = DirectEndpointRequest(
            workspaceRoot=str(self.root), apiContractId="chat_api", endpointId="chat_api.list",
        )
        draft = read_direct_endpoint_contract(request)
        base = {
            "workspaceRoot": str(self.root), "apiContractId": "chat_api",
            "endpointId": "chat_api.list", "technicalPlanSha256": draft["technicalPlanSha256"],
            "requestSchema": None,
        }
        for schema in (
            {"type": "object", "properties": {"bad": {"type": "uuid"}}},
            {"type": "object", "properties": {"bad": {"$ref": "other#/schemas/Missing"}}},
        ):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                save_direct_endpoint_contract(DirectEndpointSaveRequest(
                    **base, responseSchema=schema,
                ))
        save_direct_endpoint_contract(DirectEndpointSaveRequest(
            **base, responseSchema={"type": "object", "properties": {"content": {"type": "string"}}},
        ))
        self.plan["entities"][0]["fields"].append({"name": "title", "type": "text"})
        (self.root / ".xcodeagent/plans/technical-plan.json").write_text(
            json.dumps(self.plan), encoding="utf-8",
        )
        self.assertEqual(read_direct_endpoint_contract(request)["status"], "stale")
        projected = project_confirmed_direct_contracts(self.root, self.plan)
        self.assertEqual(projected["directConfirmedApiContracts"], {})

    def test_entity_confirmation_writes_dry_run_checked_sql(self) -> None:
        """用户确认后保存 SQL，状态可由磁盘重读且不连接目标数据库。"""

        request = DirectEntityRequest(workspaceRoot=str(self.root), entityId="Message")
        draft = read_direct_entity_design(request)
        self.assertEqual(draft["status"], "pending")
        self.assertIn('"content" TEXT NOT NULL', draft["sql"])
        saved = confirm_direct_entity_design(DirectEntityConfirmRequest(
            workspaceRoot=str(self.root), entityId="Message", sqlSha256=draft["sqlSha256"],
        ))
        self.assertEqual(saved["status"], "confirmed")
        self.assertEqual((self.root / saved["sqlPath"]).read_text(encoding="utf-8"), draft["sql"])


if __name__ == "__main__":
    unittest.main()
