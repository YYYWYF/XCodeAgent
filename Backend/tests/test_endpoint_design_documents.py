"""Endpoint API 设计双文件产物测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from app.domain.api_design import EndpointApiDesign
import app.workspace.endpoint_design_documents as endpoint_documents
from app.workspace.endpoint_design_documents import (
    endpoint_design_paths,
    endpoint_design_status,
    read_endpoint_design,
    render_endpoint_design_markdown,
    technical_plan_sha256,
    write_endpoint_design,
)


class EndpointDesignDocumentsTests(unittest.TestCase):
    """验证当前版 JSON、Markdown 与 TechnicalPlan 指纹必须共同有效。"""

    def test_round_trip_requires_both_files_and_current_fingerprint(self) -> None:
        """双文件写入后可读，TechnicalPlan 改变后立即变为需重新设计。"""

        with tempfile.TemporaryDirectory() as workspace:
            _write_technical_plan(workspace, {"artifact_type": "technical-plan", "revision": 1})
            design = _design(workspace)
            paths = write_endpoint_design(workspace, design)
            self.assertTrue(Path(paths["json_path"]).is_file())
            self.assertTrue(Path(paths["markdown_path"]).read_text(encoding="utf-8").strip())
            self.assertIsNotNone(read_endpoint_design(workspace, "orders-api", "orders.list"))
            self.assertEqual(endpoint_design_status(workspace, "orders-api", "orders.list")["status"], "confirmed")

            _write_technical_plan(workspace, {"artifact_type": "technical-plan", "revision": 2})
            self.assertIsNone(read_endpoint_design(workspace, "orders-api", "orders.list"))
            self.assertEqual(endpoint_design_status(workspace, "orders-api", "orders.list")["status"], "stale")

    def test_incomplete_pair_is_stale(self) -> None:
        """单独存在 JSON 时应标记为残缺 stale，而不是初始 pending。"""

        with tempfile.TemporaryDirectory() as workspace:
            _write_technical_plan(workspace, {"artifact_type": "technical-plan"})
            json_path, _ = endpoint_design_paths(workspace, "orders-api", "orders.list")
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text("{}\n", encoding="utf-8")
            self.assertEqual(endpoint_design_status(workspace, "orders-api", "orders.list")["status"], "stale")

    def test_legacy_graph_artifact_is_stale_without_conversion(self) -> None:
        """旧 nodes/mappings 图结构只能标记 stale，不能被静默转换。"""

        with tempfile.TemporaryDirectory() as workspace:
            _write_technical_plan(workspace, {"artifact_type": "technical-plan"})
            design = _design(workspace)
            paths = write_endpoint_design(workspace, design)
            json_path = Path(paths["json_path"])
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            payload.pop("fieldMappings", None)
            payload["schemaVersion"] = "endpoint-dynamic-mapping.v1"
            payload["artifactType"] = "endpoint-dynamic-mapping"
            payload["nodes"] = []
            payload["mappings"] = []
            json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.assertIsNone(read_endpoint_design(workspace, "orders-api", "orders.list"))
            self.assertEqual(endpoint_design_status(workspace, "orders-api", "orders.list")["status"], "stale")

    def test_removed_scene_entity_shape_is_not_current_design(self) -> None:
        """已移除的场景实体字段不能被当前 Pydantic 产物模型读取。"""

        with tempfile.TemporaryDirectory() as workspace:
            _write_technical_plan(workspace, {"artifact_type": "technical-plan"})
            design = _design(workspace)
            paths = write_endpoint_design(workspace, design)
            json_path = Path(paths["json_path"])
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            payload["sceneEntities"] = [{"id": "scene", "name": "现场实体", "fields": []}]
            json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.assertIsNone(read_endpoint_design(workspace, "orders-api", "orders.list"))
            self.assertEqual(endpoint_design_status(workspace, "orders-api", "orders.list")["status"], "stale")

    def test_mismatched_markdown_revision_is_stale(self) -> None:
        """JSON 与 Markdown 修订号不一致时不能判定为已设计。"""

        with tempfile.TemporaryDirectory() as workspace:
            _write_technical_plan(workspace, {"artifact_type": "technical-plan"})
            design = _design(workspace)
            paths = write_endpoint_design(workspace, design)
            markdown_path = Path(paths["markdown_path"])
            markdown_path.write_text(
                markdown_path.read_text(encoding="utf-8").replace(
                    design.artifact_revision,
                    "ffffffffffffffffffffffffffffffff",
                ),
                encoding="utf-8",
            )
            self.assertEqual(endpoint_design_status(workspace, "orders-api", "orders.list")["status"], "stale")

    def test_second_file_write_failure_restores_previous_pair(self) -> None:
        """Markdown 已发布后第二个文件替换失败时恢复原有 JSON 与 Markdown。"""

        with tempfile.TemporaryDirectory() as workspace:
            _write_technical_plan(workspace, {"artifact_type": "technical-plan"})
            design = _design(workspace)
            paths = write_endpoint_design(workspace, design)
            old_json = Path(paths["json_path"]).read_text(encoding="utf-8")
            old_markdown = Path(paths["markdown_path"]).read_text(encoding="utf-8")
            updated = design.model_copy(update={"artifact_revision": "fedcba9876543210fedcba9876543210"})
            publish_count = [0]
            original_publish = endpoint_documents._publish_temporary_file

            def publish_with_failure(temporary: Path, target: Path) -> None:
                """让第一次替换真实生效，第二次替换模拟失败。"""

                publish_count[0] += 1
                if publish_count[0] == 2:
                    raise OSError("simulated publish failure")
                original_publish(temporary, target)

            with patch(
                "app.workspace.endpoint_design_documents._publish_temporary_file",
                side_effect=publish_with_failure,
            ):
                with self.assertRaises(OSError):
                    write_endpoint_design(workspace, updated)
            self.assertEqual(Path(paths["json_path"]).read_text(encoding="utf-8"), old_json)
            self.assertEqual(Path(paths["markdown_path"]).read_text(encoding="utf-8"), old_markdown)
            self.assertEqual(publish_count[0], 2)
            self.assertEqual(list(Path(paths["json_path"]).parent.glob(".*.tmp")), [])

    def test_markdown_contains_business_description_and_mapped_source_snapshot(self) -> None:
        """正式 Markdown 必须展示一句话业务说明和实际参与映射的来源字段。"""

        markdown = render_endpoint_design_markdown(
            {
                "apiContractId": "orders-api",
                "endpointId": "orders.list",
                "endpointContract": {"method": "GET", "path": "/orders"},
                "artifactRevision": "0123456789abcdef0123456789abcdef",
                "implementationDescription": "先校验查询条件。\n再执行分页查询并统一处理空结果。",
                "fieldMappings": [{
                    "endpointField": {"side": "response", "location": "response_body", "path": "status", "type": "string", "required": True, "description": ""},
                    "mappingType": "business_description",
                    "businessDescription": "展示上游返回的状态。",
                }],
                "sourceSnapshots": [{
                    "sourceType": "external_api",
                    "sourceId": "upstream",
                    "name": "订单上游",
                    "details": {
                        "operation": {"id": "list", "method": "GET", "path": "/orders"},
                        "fields": [{"section": "response_body", "path": "data.status"}],
                    },
                }],
            }
        )

        self.assertIn("## 业务说明", markdown)
        self.assertIn("## API 实现描述", markdown)
        self.assertIn("> 先校验查询条件。", markdown)
        self.assertIn("> 再执行分页查询并统一处理空结果。", markdown)
        self.assertIn("展示上游返回的状态", markdown)
        self.assertIn("response_body.data.status", markdown)


def _write_technical_plan(workspace: str, value: dict) -> None:
    """在隔离工作区写入规范 TechnicalPlan。"""

    path = Path(workspace) / ".xcodeagent" / "plans" / "technical-plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _design(workspace: str) -> EndpointApiDesign:
    """构造不依赖真实数据源的当前版已确认设计。"""

    return EndpointApiDesign.model_validate(
        {
            "apiContractId": "orders-api",
            "endpointId": "orders.list",
            "endpointContract": {"id": "orders.list", "method": "GET", "path": "/orders"},
            "artifactRevision": "0123456789abcdef0123456789abcdef",
            "fieldMappings": [],
            "sourceSnapshots": [],
            "basedOn": [{"artifactKey": "technical-plan", "sha256": technical_plan_sha256(workspace)}],
            "confirmedAt": datetime.now(UTC),
        }
    )


if __name__ == "__main__":
    unittest.main()
