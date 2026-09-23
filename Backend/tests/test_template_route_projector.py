from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.template_route_projector import (
    TemplateRouteProjectorError,
    build_route_projector_input,
    load_route_projector_contract,
    validate_route_projector_input,
    validate_route_projector_result,
)


class TemplateRouteProjectorTests(unittest.TestCase):
    """验证 v2 Projector 的最小 DTO、公开 Schema 和输出集合约束。"""

    def test_builds_minimal_v2_input(self) -> None:
        """技术页面字段不会进入输入，ProductPlan 顺序保持不变。"""

        product = {"pages": [{"pageId": "orders", "name": "订单", "path": "/ignored"}]}
        manifest = {"bindings": {"pages": [{"pageId": "orders", "resourceKey": "PAGE.ORDERS"}]}}
        route_input = build_route_projector_input(product, manifest)
        self.assertEqual(route_input, {"protocol": "route-projector.v2", "pages": [{"pageId": "orders", "name": "订单", "resourceKey": "PAGE.ORDERS"}]})

    def test_requires_valid_descriptor(self) -> None:
        """缺少或畸形 Descriptor 必须 fail closed，不能回退旧 renderer。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(TemplateRouteProjectorError):
                load_route_projector_contract(root)
            path = root / ".devagentstudio/template-contracts/route-projector.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"schemaVersion": "route-projector-contract.v1", "protocol": "route-projector.v1", "command": ["node", "script.mjs", "apply"]}), encoding="utf-8")
            with self.assertRaises(TemplateRouteProjectorError):
                load_route_projector_contract(root)
            path.write_text(json.dumps({"schemaVersion": "route-projector-contract.v2", "protocol": "route-projector.v2", "inputSchema": "route-projector-input.schema.json", "outputSchema": "route-projector-output.schema.json", "command": ["node", "script.mjs", "apply"]}), encoding="utf-8")
            input_schema = {"type": "object", "required": ["protocol", "pages"], "properties": {"protocol": {"const": "route-projector.v2"}, "pages": {"type": "array"}}}
            output_schema = {"type": "object", "required": ["status", "requestedPageIds", "appliedPageIds", "skippedPageIds"], "properties": {"status": {"const": "applied"}, "requestedPageIds": {"type": "array"}, "appliedPageIds": {"type": "array"}, "skippedPageIds": {"type": "array"}}}
            (path.parent / "route-projector-input.schema.json").write_text(json.dumps(input_schema), encoding="utf-8")
            (path.parent / "route-projector-output.schema.json").write_text(json.dumps(output_schema), encoding="utf-8")
            self.assertEqual(load_route_projector_contract(root)["protocol"], "route-projector.v2")

    def test_validates_v2_output_partition_and_order(self) -> None:
        """输出必须精确分割请求页面，并保持每个分区的请求顺序。"""

        schema = {"type": "object", "required": ["status", "requestedPageIds", "appliedPageIds", "skippedPageIds"], "properties": {"status": {"const": "applied"}, "requestedPageIds": {"type": "array"}, "appliedPageIds": {"type": "array"}, "skippedPageIds": {"type": "array"}}}
        route_input = {"protocol": "route-projector.v2", "pages": [{"pageId": "orders", "name": "订单"}, {"pageId": "users", "name": "用户"}]}
        validate_route_projector_input(route_input, {"type": "object", "required": ["protocol", "pages"], "properties": {"protocol": {"const": "route-projector.v2"}, "pages": {"type": "array"}}})
        validate_route_projector_result({"status": "applied", "requestedPageIds": ["orders", "users"], "appliedPageIds": ["orders"], "skippedPageIds": ["users"]}, route_input, schema)
        with self.assertRaises(TemplateRouteProjectorError):
            validate_route_projector_result({"status": "applied", "requestedPageIds": ["orders", "users"], "appliedPageIds": ["orders", "users"], "skippedPageIds": ["users"]}, route_input, schema)


if __name__ == "__main__":
    unittest.main()
