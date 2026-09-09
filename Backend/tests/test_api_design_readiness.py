"""API 设计开发前置检查测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.domain.api_design import EndpointApiDesign
from app.services.api_design import api_design_readiness
from app.workspace.endpoint_design_documents import technical_plan_sha256, write_endpoint_design


class ApiDesignReadinessTests(unittest.TestCase):
    """验证页面一次返回全部缺失 Endpoint，且来源目录变化不使快照失效。"""

    def test_page_reports_all_missing_designs(self) -> None:
        """页面开发不会自动进入单个设计，而是一次列出全部缺失项。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            readiness = api_design_readiness(workspace, plan, target_type="page", target_id="orders")
            self.assertFalse(readiness["ready"])
            self.assertEqual(
                [item["endpoint_id"] for item in readiness["missing_api_designs"]],
                ["orders.list", "orders.create"],
            )

    def test_endpoint_reports_only_its_own_mapping(self) -> None:
        """接口开发门禁不得把同一 Contract 的兄弟 Endpoint 纳入检测。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            readiness = api_design_readiness(
                workspace,
                plan,
                target_type="endpoint",
                target_id="orders.list",
                api_contract_id="orders-api",
            )
            self.assertEqual(readiness["endpoint_ids"], ["orders.list"])
            self.assertEqual(
                [item["endpoint_id"] for item in readiness["missing_api_designs"]],
                ["orders.list"],
            )

    def test_page_without_endpoint_dependencies_is_ready(self) -> None:
        """不依赖 Endpoint 的页面应直接通过字段映射检测。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            plan["pages"].append({"pageId": "empty", "name": "空页面"})
            plan["page_implementation_contracts"].append(
                {"pageId": "empty", "requiredEndpointIds": []}
            )
            _write_plan(workspace, plan)
            readiness = api_design_readiness(
                workspace,
                plan,
                target_type="page",
                target_id="empty",
            )
            self.assertTrue(readiness["ready"])
            self.assertEqual(readiness["endpoint_ids"], [])

    def test_confirmed_snapshot_survives_source_catalog_change(self) -> None:
        """确认后仅 TechnicalPlan 指纹决定有效性，数据源目录变化不主动失效。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            for endpoint_id in ("orders.list", "orders.create"):
                write_endpoint_design(workspace, _empty_design(workspace, endpoint_id))
            source_path = Path(workspace) / ".xcodeagent" / "data-sources.json"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text('{"sources":[]}\n', encoding="utf-8")
            readiness = api_design_readiness(workspace, plan, target_type="page", target_id="orders")
            self.assertTrue(readiness["ready"])

    def test_invalid_confirmed_mapping_is_stale(self) -> None:
        """正式产物含有草稿态未配置字段时必须回到 stale。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            paths = write_endpoint_design(workspace, _empty_design(workspace, "orders.list"))
            payload = json.loads(Path(paths["json_path"]).read_text(encoding="utf-8"))
            payload["fieldMappings"] = [{
                "endpointField": {
                    "side": "response",
                    "location": "response_body",
                    "path": "status",
                    "type": "string",
                    "required": False,
                    "description": "",
                },
                "mappingType": "unconfigured",
            }]
            Path(paths["json_path"]).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            readiness = api_design_readiness(
                workspace,
                plan,
                target_type="endpoint",
                target_id="orders.list",
                api_contract_id="orders-api",
            )
            self.assertFalse(readiness["ready"])
            self.assertEqual(readiness["missing_api_designs"][0]["status"], "stale")


def _plan() -> dict:
    """返回一个页面依赖两个 Endpoint 的最小 TechnicalPlan。"""

    return {
        "artifact_type": "technical-plan",
        "entities": [{"id": "Order", "name": "订单", "fields": [{"name": "id", "type": "string"}]}],
        "pages": [{"pageId": "orders", "name": "订单"}],
        "page_implementation_contracts": [
            {"pageId": "orders", "requiredEndpointIds": ["orders.list", "orders.create"]}
        ],
        "api_contracts": [
            {
                "id": "orders-api",
                "entity_ids": ["Order"],
                "schemas": {},
                "endpoints": [
                    {"id": "orders.list", "method": "GET", "path": "/orders"},
                    {"id": "orders.create", "method": "POST", "path": "/orders"},
                ],
            }
        ],
    }


def _write_plan(workspace: str, plan: dict) -> None:
    """写入用于计算 API 设计上游指纹的规范文件。"""

    path = Path(workspace) / ".xcodeagent" / "plans" / "technical-plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


def _empty_design(workspace: str, endpoint_id: str) -> EndpointApiDesign:
    """构造无业务字段 Endpoint 的合法确认产物。"""

    method = "POST" if endpoint_id.endswith(".create") else "GET"
    return EndpointApiDesign.model_validate(
        {
            "apiContractId": "orders-api",
            "endpointId": endpoint_id,
            "endpointContract": {"id": endpoint_id, "method": method, "path": "/orders"},
            "artifactRevision": "0123456789abcdef0123456789abcdef",
            "fieldMappings": [],
            "basedOn": [{"artifactKey": "technical-plan", "sha256": technical_plan_sha256(workspace)}],
            "confirmedAt": datetime.now(UTC),
        }
    )


if __name__ == "__main__":
    unittest.main()
