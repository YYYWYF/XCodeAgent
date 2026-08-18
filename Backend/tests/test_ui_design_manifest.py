from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.ui_design_manifest import (
    UI_MANIFEST_SCHEMA_VERSION,
    build_ui_page_manifest,
    persisted_ui_manifest,
    validate_ui_design_code,
)
from app.workspace.spec_documents import load_ui_designs_json, write_ui_designs_json


class UiDesignManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        """准备包含一个业务信息项和一个业务操作的 ProductPlan 页面。"""

        self.page = {
            "pageId": "orders",
            "name": "订单页",
            "path": "/orders",
            "description": "展示订单并允许搜索。",
            "allowed_roles": ["admin"],
            "information_items": [
                {"itemId": "orders-list", "label": "订单列表"},
            ],
            "actions": [
                {"actionId": "search-orders", "name": "搜索订单"},
            ],
        }

    def test_manifest_only_keeps_ui_references_bindings_and_evidence(self) -> None:
        """v3 页面清单不得重复保存 ProductPlan 文案、正式路由或角色。"""

        code = """
import React from 'react';
import { Input } from 'antd';
import { ProTable } from '@ant-design/pro-components';
const Orders = () => <div>
  <Input data-action-id="search-orders" data-control-id="search-orders-control" />
  <ProTable data-information-item-id="orders-list" data-control-id="orders-list-display" />
</div>;
export default Orders;
"""

        manifest = build_ui_page_manifest(
            self.page,
            page_key="Orders",
            code_path="/.xcodeagent/ui-design/pages/Orders/index.tsx",
            code=code,
            status="confirmed",
        )

        self.assertEqual(manifest["verification"]["status"], "passed")
        self.assertEqual(
            manifest["bindings"]["actions"],
            [{"actionId": "search-orders", "controlIds": ["search-orders-control"]}],
        )
        for forbidden in ("name", "path", "description", "allowed_roles"):
            self.assertNotIn(forbidden, manifest)

        persisted = persisted_ui_manifest(
            {
                "confirmation_status": "confirmed",
                "product_plan_sha256": "a" * 64,
                "pages": [manifest],
            }
        )
        self.assertEqual(persisted["schema_version"], UI_MANIFEST_SCHEMA_VERSION)
        self.assertNotIn("code", persisted["pages"][0])

    def test_validation_rejects_unknown_and_unowned_business_ui(self) -> None:
        """新增业务指标、未知 action 和无归属交互控件必须被确定性拒绝。"""

        code = """
import React from 'react';
import { Button, Statistic } from 'antd';
const Orders = () => <div>
  <Button data-action-id="export-orders" data-control-id="export-orders-control">导出</Button>
  <Statistic title="今日订单" value={10} />
</div>;
export default Orders;
"""

        errors = validate_ui_design_code(self.page, code)

        self.assertTrue(any("越界" in error for error in errors))
        self.assertTrue(any("Statistic" in error for error in errors))

    def test_preview_controls_require_explicit_preview_only_marker(self) -> None:
        """状态评审控件显式标记后不应被误判为产品 action。"""

        code = """
import React from 'react';
import { Input, Radio } from 'antd';
import { ProTable } from '@ant-design/pro-components';
const Orders = () => <div>
  <Radio.Group data-preview-only="true" />
  <Input data-action-id="search-orders" data-control-id="search-orders-control" />
  <ProTable data-information-item-id="orders-list" data-control-id="orders-list-display" />
</div>;
export default Orders;
"""

        self.assertEqual(validate_ui_design_code(self.page, code), [])

    def test_bindings_survive_arrow_functions_in_jsx_attributes(self) -> None:
        """JSX 回调中的箭头符号不得截断后续产品绑定属性。"""

        code = """
import React from 'react';
import { Button } from 'antd';
import { ProTable } from '@ant-design/pro-components';
const Orders = () => <div>
  <Button
    onClick={() => Promise.resolve(true)}
    data-action-id="search-orders"
    data-control-id="search-orders-control"
  >搜索</Button>
  <ProTable
    onRow={() => ({ onClick: () => undefined })}
    data-information-item-id="orders-list"
    data-control-id="orders-list-display"
  />
</div>;
export default Orders;
"""

        self.assertEqual(validate_ui_design_code(self.page, code), [])

    def test_interface_action_requires_ui_owned_effect(self) -> None:
        """界面行为必须由真实 TSX 的 data-ui-effect 固化，不能留给 TechnicalPlan。"""

        interface_page = {
            **self.page,
            "actions": [
                {
                    "actionId": "search-orders",
                    "name": "打开筛选",
                    "behavior": {
                        "type": "interface",
                        "expectedResult": "展示筛选条件。",
                    },
                }
            ],
        }
        missing_effect = """
import React from 'react';
import { Button } from 'antd';
import { ProTable } from '@ant-design/pro-components';
const Orders = () => <div>
  <Button data-action-id="search-orders" data-control-id="search-orders-control">筛选</Button>
  <ProTable data-information-item-id="orders-list" data-control-id="orders-list-display" />
</div>;
export default Orders;
"""
        with_effect = missing_effect.replace(
            'data-control-id="search-orders-control"',
            'data-control-id="search-orders-control" data-ui-effect="打开订单筛选抽屉"',
        )

        self.assertTrue(any("data-ui-effect" in error for error in validate_ui_design_code(interface_page, missing_effect)))
        manifest = build_ui_page_manifest(interface_page, page_key="Orders", code=with_effect)
        self.assertEqual(manifest["verification"]["status"], "passed")
        self.assertEqual(
            manifest["bindings"]["actions"][0]["uiEffect"],
            "打开订单筛选抽屉",
        )

    def test_sequence_interface_steps_keep_independent_ui_effects(self) -> None:
        """组合行为中的本地步骤必须按 stepId 保存独立 UI 效果。"""

        page = {
            **self.page,
            "actions": [
                {
                    "actionId": "save-and-refresh",
                    "behavior": {
                        "type": "sequence",
                        "expectedResult": "保存并刷新。",
                        "steps": [
                            {"stepId": "save", "type": "business", "expectedResult": "保存。"},
                            {"stepId": "close", "type": "interface", "expectedResult": "关闭弹窗。"},
                            {"stepId": "refresh", "type": "interface", "expectedResult": "刷新列表。"},
                        ],
                    },
                }
            ],
        }
        code = """
import React from 'react';
import { Button } from 'antd';
import { ProTable } from '@ant-design/pro-components';
const Orders = () => <div>
  <Button data-action-id="save-and-refresh" data-action-step-id="close" data-control-id="save-control" data-ui-effect="关闭编辑弹窗">保存</Button>
  <Button data-action-id="save-and-refresh" data-action-step-id="refresh" data-control-id="refresh-control" data-ui-effect="刷新订单列表">刷新</Button>
  <ProTable data-information-item-id="orders-list" data-control-id="orders-list-display" />
</div>;
export default Orders;
"""

        manifest = build_ui_page_manifest(page, page_key="Orders", code=code)

        self.assertEqual(manifest["verification"]["status"], "passed")
        self.assertEqual(
            manifest["bindings"]["actions"][0]["stepEffects"],
            [
                {"stepId": "close", "uiEffect": "关闭编辑弹窗"},
                {"stepId": "refresh", "uiEffect": "刷新订单列表"},
            ],
        )

    def test_legacy_manifest_is_migrated_without_product_fact_copies(self) -> None:
        """旧 controls/display_items 必须迁移成 v3 bindings，不能被静默丢失。"""

        persisted = persisted_ui_manifest(
            {
                "confirmation_status": "confirmed",
                "product_plan_sha256": "b" * 64,
                "pages": [
                    {
                        "pageId": "orders",
                        "name": "订单页",
                        "path": "/orders",
                        "controls": [
                            {"actionId": "search-orders", "controlId": "search-orders-control"}
                        ],
                        "display_items": [
                            {
                                "informationItemId": "orders-list",
                                "controlId": "orders-list-display",
                            }
                        ],
                    }
                ],
            }
        )

        page = persisted["pages"][0]
        self.assertNotIn("name", page)
        self.assertNotIn("path", page)
        self.assertEqual(page["bindings"]["actions"][0]["actionId"], "search-orders")
        self.assertEqual(page["verification"]["status"], "legacy_unverified")

    def test_workspace_persists_manifest_without_code_and_hydrates_runtime_copy(self) -> None:
        """正式 JSON 不保存源码，恢复运行态时只从受控 UI 目录读取。"""

        with TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            code_path = workspace / ".xcodeagent" / "ui-design" / "pages" / "Orders" / "index.tsx"
            code_path.parent.mkdir(parents=True)
            code = "export default function Orders() { return null }"
            code_path.write_text(code, encoding="utf-8")
            state = {"workspace": str(workspace)}
            manifest = {
                "confirmation_status": "confirmed",
                "product_plan_sha256": "c" * 64,
                "pages": [
                    {
                        "pageId": "orders",
                        "page_key": "Orders",
                        "code_path": str(code_path),
                        "code": code,
                        "status": "confirmed",
                        "bindings": {"actions": [], "information_items": []},
                    }
                ],
            }

            json_path = Path(write_ui_designs_json(state, manifest))
            persisted_text = json_path.read_text(encoding="utf-8")
            hydrated = load_ui_designs_json(json_path)

            self.assertNotIn('"code":', persisted_text)
            self.assertEqual(hydrated["pages"][0]["code"], code)


if __name__ == "__main__":
    unittest.main()
