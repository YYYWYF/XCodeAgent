"""验证 mainline 正式输入只冻结当前 Endpoint API Design。"""

import unittest

from app.graph.nodes.task_planning_inputs import mainline_formal_contract_inputs
from app.services.frozen_contract_store import FrozenContractStore
from app.services.planning_frozen import plain_json


def _endpoint_design() -> dict:
    """构造包含当前 API 设计全部正式字段的最小夹具。"""

    return {
        "schemaVersion": "endpoint-field-mapping.v3",
        "artifactType": "endpoint-field-mapping",
        "status": "confirmed",
        "confirmationStatus": "confirmed",
        "artifactRevision": "0123456789abcdef0123456789abcdef",
        "apiContractId": "orders-api",
        "endpointId": "orders.list",
        "endpointContract": {"id": "orders.list", "method": "GET"},
        "fieldMappings": [{
            "endpointField": {
                "side": "response",
                "location": "response_body",
                "path": "items[].id",
                "type": "string",
                "required": True,
                "description": "订单 ID",
            },
            "mappingType": "source_mapping",
            "processingType": "direct",
            "sourceFields": [{
                "sourceType": "database",
                "sourceId": "orders-db",
                "schema": "app",
                "table": "orders",
                "column": "id",
                "type": "string",
                "usage": "read",
                "description": "数据库订单 ID",
            }],
        }],
        "sourceSnapshots": [{
            "sourceType": "database",
            "sourceId": "orders-db",
            "name": "订单数据库",
            "details": {"schema": "app", "table": "orders"},
        }],
        "implementationDescription": "读取订单列表",
        "basedOn": [{
            "artifactKey": "technical-plan",
            "sha256": "a" * 64,
        }],
        "confirmedAt": "2026-09-10T00:00:00Z",
    }


class MainlineFormalContractInputsTests(unittest.TestCase):
    """验证 BuildContext 到 Frozen Store 的正式边界。"""

    def test_current_endpoint_design_is_frozen_without_disk_read_or_entity_binding(self) -> None:
        """当前 Endpoint Design 正文完整进入 Store，旧 Entity binding 不再成为输入。"""

        design = _endpoint_design()
        inputs = mainline_formal_contract_inputs(
            {"product_plan": {"artifact_type": "product-plan", "version": "v1"}},
            {
                "page_implementation_contracts": [],
                "api_contracts": [],
            },
            {"endpoint_designs": [design]},
        )

        self.assertEqual(len(inputs.endpoint_api_designs), 1)
        self.assertEqual(
            plain_json(inputs.endpoint_api_designs[0].content),
            design,
        )
        self.assertEqual(
            plain_json(inputs.endpoint_api_designs[0].source),
            {
                "artifact": "confirmed-endpoint-api-design",
                "api_contract_id": "orders-api",
                "endpoint_id": "orders.list",
                "artifact_revision": "0123456789abcdef0123456789abcdef",
            },
        )
        self.assertNotIn("entity_bindings", inputs.model_dump(mode="json"))

        store = FrozenContractStore.create(
            planning_run_id="formal-boundary-run",
            formal_inputs=inputs,
        )
        contract = next(
            item
            for item in store.contracts.values()
            if item.kind == "endpoint_api_design"
        )
        self.assertEqual(contract.content["fieldMappings"][0]["mappingType"], "source_mapping")
        self.assertEqual(contract.content["sourceSnapshots"][0]["sourceType"], "database")


if __name__ == "__main__":
    unittest.main()
