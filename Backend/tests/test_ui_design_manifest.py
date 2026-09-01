from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.ui_design_manifest import (
    UI_MANIFEST_SCHEMA_VERSION,
    build_ui_page_manifest,
    inspect_ui_code_bindings,
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

    def test_business_display_nested_in_bound_container_not_flagged(self) -> None:
        """嵌套在已绑定 informationItemId 容器内的 Statistic 不应误报为未绑定。

        回归：校验器曾逐标签检查 data-information-item-id，不识别父子嵌套，
        导致 ProCard(data-information-item-id=X) > Statistic 的合法结构被误报
        "Statistic 未绑定 informationItemId"，重试 2 次仍失败。
        """

        code = """
import React from 'react';
import { Card, Input, Statistic } from 'antd';
import { ProCard } from '@ant-design/pro-components';
const Orders = () => <div>
  <Input data-action-id="search-orders" data-control-id="search-orders-control" />
  <ProCard data-information-item-id="orders-list" data-control-id="orders-list-display">
    <Card>
      <Statistic title="订单总数" value={10} />
      <Statistic title="今日订单" value={3} />
    </Card>
  </ProCard>
</div>;
export default Orders;
"""

        self.assertEqual(validate_ui_design_code(self.page, code), [])

    def test_expression_bound_ids_resolved_from_map_source(self) -> None:
        """map 回调里的表达式绑定 id 从数据源字面量静态解析，校验通过。

        回归：模型把 5 个指标卡做成 metricCards.map((m) => <div
        data-information-item-id={m.itemId}>)，旧 _STATIC_ATTRIBUTE_TEMPLATE 只认
        ="字面量"，5 项全部判"缺少"，重试 2 次仍用同种动态写法而失败。现在校验器
        从 .map() 数据源数组里提取 itemId 字面量，与 expected 精确匹配，动态写法
        （合理的 React 模式）不再被阻断。
        """

        page = {
            "pageId": "dashboard_page",
            "information_items": [
                {"itemId": "dashboard_page-project-total"},
                {"itemId": "dashboard_page-active-project-count"},
            ],
            "actions": [{"actionId": "dashboard_page-goto-project-list"}],
        }
        code = """
import React from 'react';
import { Statistic } from 'antd';
const metricCards = [
  { itemId: 'dashboard_page-project-total', value: 128 },
  { itemId: 'dashboard_page-active-project-count', value: 42 },
];
const DashboardPage = () => <div>
  {metricCards.map((m) => (
    <div data-information-item-id={m.itemId} data-control-id={`${m.itemId}-display`}>
      <Statistic value={m.value} />
    </div>
  ))}
  <button data-action-id="dashboard_page-goto-project-list" data-control-id="dashboard_page-goto-project-list-control">进入</button>
</div>;
export default DashboardPage;
"""

        # 动态绑定 + 数据源带字面量 → 校验通过。
        self.assertEqual(validate_ui_design_code(page, code), [])

        # 改成逐项静态字面量后，仍然通过。
        static_code = """
import React from 'react';
import { Statistic } from 'antd';
const DashboardPage = () => <div>
  <div data-information-item-id="dashboard_page-project-total" data-control-id="dashboard_page-project-total-display"><Statistic value={128} /></div>
  <div data-information-item-id="dashboard_page-active-project-count" data-control-id="dashboard_page-active-project-count-display"><Statistic value={42} /></div>
  <button data-action-id="dashboard_page-goto-project-list" data-control-id="dashboard_page-goto-project-list-control">进入</button>
</div>;
export default DashboardPage;
"""
        self.assertEqual(validate_ui_design_code(page, static_code), [])

    def test_expression_bound_without_source_still_reported(self) -> None:
        """动态绑定但数据源无对应字面量时，仍判缺失并给表达式绑定提示。

        兜底：{someVar} 这种无数据源的纯变量绑定，静态分析无法解析值，仍判缺失
        并回喂模型改写字面量。保证校验器不会被任意 {expr} 蒙混过关。
        """

        page = {
            "pageId": "dashboard_page",
            "information_items": [
                {"itemId": "dashboard_page-project-total"},
            ],
            "actions": [],
        }
        code = """
import React from 'react';
import { Statistic } from 'antd';
const DashboardPage = (props) => <div>
  <div data-information-item-id={props.someVar} data-control-id={`${props.someVar}-display`}>
    <Statistic value={1} />
  </div>
</div>;
export default DashboardPage;
"""

        errors = validate_ui_design_code(page, code)
        missing_error = next(e for e in errors if "缺少" in e and "完全一致" in e)
        self.assertIn("dashboard_page-project-total", missing_error)
        self.assertIn("表达式绑定", missing_error)

    def test_render_prop_buttons_all_registered(self) -> None:
        """submitter.render 等 render props 里的按钮必须逐个登记 action 与 control-id。

        回归：_jsx_opening_tags 曾把属性表达式里的 JSX 随父标签 attrs 整体消费，
        _attribute 只取第一个匹配——ModalForm submitter.render 里两个按钮只有第一个
        （取消）被登记，第二个（保存）被判"缺少 action + 缺少静态 data-control-id"，
        模型重试 2 次仍用同种 ProComponents 标准写法而失败。现在 attrs 内的 JSX
        会被递归扫描成独立标签。
        """

        page = {
            "pageId": "project_detail",
            "information_items": [],
            "actions": [
                {"actionId": "project_detail_cancel_edit"},
                {"actionId": "project_detail_submit_edit"},
            ],
        }
        code = """
import React from 'react';
import { Button } from 'antd';
import { ModalForm, ProFormText } from '@ant-design/pro-components';
const ProjectDetail = () => (
  <ModalForm
    title="编辑项目信息"
    open={editOpen}
    onOpenChange={setEditOpen}
    modalProps={{ confirmLoading: submitting, destroyOnClose: true }}
    submitter={{
      render: () => [
        <Button
          key="cancel"
          data-action-id="project_detail_cancel_edit"
          data-control-id="project_detail_cancel_edit-control"
          data-ui-effect="关闭编辑项目弹窗"
          onClick={() => setEditOpen(false)}
        >
          取消
        </Button>,
        <Button
          key="submit"
          type="primary"
          loading={submitting}
          data-action-id="project_detail_submit_edit"
          data-control-id="project_detail_submit_edit-control"
          onClick={() => editForm.submit()}
        >
          保存
        </Button>,
      ],
    }}
  >
    <ProFormText name="name" label="项目名称" />
  </ModalForm>
);
export default ProjectDetail;
"""

        self.assertEqual(validate_ui_design_code(page, code), [])

    def test_unbound_button_inside_render_prop_still_flagged(self) -> None:
        """递归扫描后，render props 里未绑定 actionId 的 Button 不应漏检。"""

        code = """
import React from 'react';
import { Button } from 'antd';
import { ModalForm, ProFormText } from '@ant-design/pro-components';
const Page = () => (
  <ModalForm
    title="示例"
    submitter={{ render: () => [<Button key="x" onClick={() => undefined}>未绑定</Button>] }}
  >
    <ProFormText name="name" />
  </ModalForm>
);
export default Page;
"""

        errors = validate_ui_design_code(self.page, code)
        self.assertTrue(
            any("Button" in error for error in errors),
            "render props 里未绑 actionId 的 Button 应报未归属交互控件",
        )

    def test_nested_render_prop_marker_not_paired_to_parent_control(self) -> None:
        """extra={Button} 的 action 归属按钮自身，父容器 control-id 不得错误配对。

        父标签 attrs 含嵌套 JSX 时，_attribute 的首个匹配曾把嵌套按钮的 action
        与父容器自己的 data-control-id 配对（如 open_edit_dialog 挂上
        basic_info-display），污染 manifest bindings。父标签 attrs 的花括号表达式
        主体置空后，嵌套标记只归属给递归出的子标签。
        """

        code = """
import React from 'react';
import { Button } from 'antd';
import { ProDescriptions } from '@ant-design/pro-components';
const Page = () => (
  <ProDescriptions
    data-information-item-id="orders-list"
    data-control-id="orders-list-display"
    extra={
      <Button
        data-action-id="search-orders"
        data-control-id="search-orders-control"
      >
        编辑
      </Button>
    }
  />
);
export default Page;
"""

        inspection = inspect_ui_code_bindings(code)
        self.assertEqual(
            inspection["actions"]["search-orders"],
            ["search-orders-control"],
        )
        self.assertEqual(
            inspection["information_items"]["orders-list"],
            ["orders-list-display"],
        )

    def test_business_display_nested_in_preview_only_container_not_flagged(self) -> None:
        """嵌套在 data-preview-only 容器内的 Table 不应误报为未绑定。"""

        page = {
            "pageId": "orders",
            "information_items": [],
            "actions": [],
        }
        code = """
import React from 'react';
import { Table } from 'antd';
const Orders = () => <div data-preview-only="true">
  <Table dataSource={[]} />
</div>;
export default Orders;
"""

        self.assertEqual(validate_ui_design_code(page, code), [])

    def test_business_display_sibling_of_bound_container_still_flagged(self) -> None:
        """与绑定容器平级（非嵌套）的 Statistic 仍应被报错。"""

        code = """
import React from 'react';
import { Card, Input, Statistic } from 'antd';
import { ProCard } from '@ant-design/pro-components';
const Orders = () => <div>
  <Input data-action-id="search-orders" data-control-id="search-orders-control" />
  <ProCard data-information-item-id="orders-list" data-control-id="orders-list-display">
    <Statistic title="订单总数" value={10} />
  </ProCard>
  <Statistic title="今日订单" value={3} />
</div>;
export default Orders;
"""

        errors = validate_ui_design_code(self.page, code)
        self.assertTrue(any("Statistic" in e for e in errors))

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

    def test_form_container_with_on_finish_not_misjudged_as_interaction(self) -> None:
        """ProForm/Form 等表单容器带 onFinish 不应被误判为未绑定 actionId 的交互控件。

        onFinish 是表单提交回调，真正的 action 是表单内的提交按钮。容器组件即使带
        交互属性也不该要求绑 actionId（回归：曾因 _INTERACTION_ATTRIBUTE_RE 匹配
        onFinish 把 ProForm 误判，导致设计稿校验失败）。真交互控件（Button）未绑
        actionId 仍要报错。
        """

        code = """
import React from 'react';
import { Button, Form } from 'antd';
import { ProForm, ProFormText } from '@ant-design/pro-components';
const Page = () => <div>
  <ProForm onFinish={() => undefined} submitter={false}>
    <ProFormText name="x" />
  </ProForm>
  <Form onFinish={() => undefined} />
  <Button data-action-id="search-orders" data-control-id="search-orders-control">提交</Button>
  <Button onClick={() => undefined}>未绑定</Button>
</div>;
export default Page;
"""
        errors = validate_ui_design_code(self.page, code)
        # ProForm/Form 不报错，只有未绑 actionId 的 Button 报错。
        self.assertTrue(
            any("Button" in e for e in errors),
            "未绑 actionId 的 Button 应报错",
        )
        self.assertFalse(
            any("ProForm" in e for e in errors),
            "ProForm 带 onFinish 不应误判为交互控件",
        )
        self.assertFalse(
            any("Form" in e for e in errors),
            "Form 带 onFinish 不应误判为交互控件",
        )

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
