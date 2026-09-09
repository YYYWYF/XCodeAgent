"""Endpoint API 设计独立配置服务的当前契约测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.endpoint_design_detail import (
    EndpointDesignDetailRequest,
    EndpointDesignPrepareRequest,
    EndpointDesignSaveRequest,
    prepare_endpoint_design,
    read_endpoint_design_detail,
    save_endpoint_design,
)
from app.services.api_design import ApiDesignError


class EndpointDesignDetailTests(unittest.TestCase):
    """验证查询目标隔离、编辑准备、revision 冲突以及 pending/stale 投影。"""

    def test_missing_pair_is_pending_without_writing(self) -> None:
        """缺少双文件时返回 pending，查询不会创建目录或产物。"""

        with tempfile.TemporaryDirectory() as workspace:
            detail = read_endpoint_design_detail(
                EndpointDesignDetailRequest(
                    workspaceRoot=workspace,
                    apiContractId="orders-api",
                    endpointId="orders.list",
                )
            )
            self.assertEqual(detail["status"], "pending")
            self.assertIsNone(detail["design"])
            self.assertFalse((Path(workspace) / ".xcodeagent").exists())

    def test_stale_design_remains_readable(self) -> None:
        """上游指纹变化时保留旧设计供只读查看，并返回 stale 原因。"""

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.services.endpoint_design_detail.endpoint_design_status",
                return_value={"status": "stale", "designed": False, "reason": "TechnicalPlan 已变化。"},
            ), patch(
                "app.services.endpoint_design_detail.read_endpoint_design",
                return_value={"apiContractId": "orders-api", "endpointId": "orders.list", "fieldMappings": []},
            ), patch(
                "app.services.endpoint_design_detail.endpoint_design_paths",
                return_value=(Path(workspace) / "design.json", Path(workspace) / "design.md"),
            ):
                Path(workspace, "design.md").write_text("# old design", encoding="utf-8")
                detail = read_endpoint_design_detail(
                    EndpointDesignPrepareRequest(
                        workspaceRoot=workspace,
                        apiContractId="orders-api",
                        endpointId="orders.list",
                    )
                )
            self.assertEqual(detail["status"], "stale")
            self.assertEqual(detail["design"]["endpointId"], "orders.list")
            self.assertEqual(detail["markdown"], "# old design")

    def test_save_rejects_revision_conflict_before_writing(self) -> None:
        """编辑器基于旧 revision 保存时应拒绝覆盖其他操作的新版本。"""

        with tempfile.TemporaryDirectory() as workspace:
            request = EndpointDesignSaveRequest(
                workspaceRoot=workspace,
                apiContractId="orders-api",
                endpointId="orders.list",
                baseRevision="a" * 32,
                draft={"apiContractId": "orders-api", "endpointId": "orders.list"},
            )
            with patch(
                "app.services.endpoint_design_detail._read_current_technical_plan",
                return_value={"api_contracts": []},
            ), patch(
                "app.services.endpoint_design_detail.read_endpoint_design",
                return_value={"artifactRevision": "b" * 32},
            ), patch(
                "app.services.endpoint_design_detail.confirm_api_design"
            ) as confirm:
                with self.assertRaises(ApiDesignError):
                    save_endpoint_design(request)
            confirm.assert_not_called()

    def test_prepare_returns_existing_revision_and_full_editor_payload(self) -> None:
        """独立编辑器准备阶段同时返回完整字段草稿和当前 artifact revision。"""

        with tempfile.TemporaryDirectory() as workspace:
            with patch(
                "app.services.endpoint_design_detail._read_current_technical_plan",
                return_value={"api_contracts": []},
            ), patch(
                "app.services.endpoint_design_detail.initial_api_design_payload",
                return_value={"endpointFields": [], "draft": {"fieldMappings": []}},
            ), patch(
                "app.services.endpoint_design_detail.read_endpoint_design",
                return_value={"artifactRevision": "revision-1", "fieldMappings": []},
            ):
                result = prepare_endpoint_design(
                    EndpointDesignDetailRequest(
                        workspaceRoot=workspace,
                        apiContractId="orders-api",
                        endpointId="orders.list",
                    )
                )
            self.assertEqual(result["artifactRevision"], "revision-1")
            self.assertEqual(result["payload"]["draft"]["fieldMappings"], [])
