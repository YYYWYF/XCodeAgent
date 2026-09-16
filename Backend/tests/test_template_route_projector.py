from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.template_route_projector import (
    TemplateRouteProjectorError,
    build_route_projector_input,
    extract_route_facts,
    load_route_projector_contract,
    requires_route_projection,
)
from app.workspace.task_documents import (
    load_latest_successful_build_route_facts,
    persist_build_run_success_evidence,
)


class TemplateRouteProjectorTests(unittest.TestCase):
    """验证平台只处理最小 DTO、Descriptor 和成功 Build 执行证据。"""

    def test_builds_minimal_input_and_compares_facts(self) -> None:
        """技术页面字段不会进入输入，页面顺序变化不会影响 Route Facts。"""

        product = {"pages": [{"pageId": "orders", "name": "订单", "path": "/ignored"}]}
        manifest = {"bindings": {"pages": [{"pageId": "orders", "resourceKey": "PAGE.ORDERS"}]}}
        route_input = build_route_projector_input(product, manifest)
        self.assertEqual(route_input, {"protocol": "route-projector.v1", "pages": [{"pageId": "orders", "name": "订单", "resourceKey": "PAGE.ORDERS"}]})
        facts = extract_route_facts(route_input)
        self.assertFalse(requires_route_projection(facts, route_input))
        self.assertTrue(requires_route_projection(None, route_input))

    def test_requires_valid_descriptor(self) -> None:
        """缺少或畸形 Descriptor 必须 fail closed，不能回退旧 renderer。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(TemplateRouteProjectorError):
                load_route_projector_contract(root)
            path = root / ".xcodeagent/template-contracts/route-projector.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"schemaVersion": "route-projector-contract.v1", "protocol": "route-projector.v1", "command": ["node", "script.mjs", "apply"]}), encoding="utf-8")
            self.assertEqual(load_route_projector_contract(root)["protocol"], "route-projector.v1")

    def test_success_evidence_is_the_next_build_baseline(self) -> None:
        """只成功 Run 写入的 routeFacts 能被下一次 Planning 读取。"""

        with tempfile.TemporaryDirectory() as directory:
            state = {"workspace": directory}
            run_id = "build-" + "a" * 32
            persist_build_run_success_evidence(state, build_run_id=run_id, route_facts={"orders": {"name": "订单", "resourceKey": None}})
            self.assertEqual(load_latest_successful_build_route_facts(state), {"orders": {"name": "订单", "resourceKey": None}})


if __name__ == "__main__":
    unittest.main()
