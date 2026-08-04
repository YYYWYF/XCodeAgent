---
name: antd-ui-design
description: >-
  为应用生成流程的 UI 确认节点生成单个页面的真实视觉设计稿。输出一个自包含的
  React + antd5 + @ant-design/pro-components 的 .tsx 页面文件，使用内联静态
  Mock 数据，不接入任何 API/路由/权限/真实交互，仅呈现视觉 UI 效果。当需要
  生成"设计稿"、"UI 设计稿"、"页面视觉稿"、"antd 页面原型"、"可视化页面"
  时使用。
---

# antd-ui-design Skill

为单个页面生成一个**自包含 .tsx 文件**，作为可运行 Vite 工程里的一个页面。
目标是**真实视觉**——用真实的 antd / pro-components 组件渲染，配静态 Mock
数据，让用户在浏览器里看到与最终产品一致的 antd 界面。**不是线框图、不是
灰盒、不是 HTML 仿写**。

页面会渲染在工程 `ProLayout` 的 `<Outlet />` 里，所以**不要包任何布局外壳**
（不要 ProLayout / PageContainer / 自带 header / sider / footer）——只输出
页面主体内容。

## 技术栈（固定，不可替换）

- React 18 + TypeScript
- antd 5（基础组件）
- @ant-design/pro-components 2.8（Pro 系列组件）
- @ant-design/icons（图标）
- 工程未安装 mockjs / xlsx / axios，**禁止 import 这些**

## 导入规则（CRITICAL）

Pro 系列从 `@ant-design/pro-components` 导入；基础组件从 `antd` 导入；图标从
`@ant-design/icons` 导入。三者绝不能混在同一个 import 语句里：

```ts
// ✅ Pro 系列
import { ProTable, ProColumns, ProForm, ProFormText, ProFormSelect, ProFormDatePicker, ProList, ProCard, ProDescriptions, ModalForm, DrawerForm } from '@ant-design/pro-components';

// ✅ 基础组件
import { Button, Col, Row, Space, Tag, Typography, Card, Statistic, Divider, Avatar, Progress, Tooltip, Popconfirm, Badge } from 'antd';

// ✅ 图标
import { PlusOutlined, ReloadOutlined, SearchOutlined, EyeOutlined, EditOutlined, DeleteOutlined, DownloadOutlined } from '@ant-design/icons';
```

判断依据：名称以 `Pro` 开头或为 `ModalForm`/`DrawerForm`/`StepsForm`/
`ProDescriptions` → `@ant-design/pro-components`；其余 → `antd`。

## 页面类型决策树

需求里通常只给页面 name 和 description（没有 components 字段），按以下规则
**从 name/description 推断**页面类型并选组件：

| name/description 关键词 | 页面类型 | 主组件 |
|---|---|---|
| 列表 / 查询 / 搜索 / 筛选 / 管理 | 查询表格页 | `ProTable`（`dataSource` 静态数据 + `search` 开启） |
| 详情 / 单条记录 / 查看 | 详情页 | `ProDescriptions` + `ProForm`（只读） |
| 概览 / 首页 / 仪表盘 / 统计 / dashboard | 概览页 | `ProCard` 栅格 + `Statistic` / `Progress` |
| 登录 / login | 登录页 | 居中 `Card` + `ProForm`（账号密码） |
| 多视图 / 多标签 / 切换 | 标签页 | `ProCard` 的 `tabs` |
| 卡片列表 / 图文混排 | 卡片列表 | `ProList` |
| 表单 / 新增 / 编辑 / 配置 | 表单页 | `ProForm` + `ProFormText` 等字段 |

拿不准时默认用 `ProTable` 查询表格页（最通用）。

## 硬约束（禁止项）

1. **禁 API / 数据请求**：不用 `request` prop、不用 `useEffect` 发请求、
   不 `import` `@/apis/*`、不 `fetch`、不 `axios`。ProTable 用 `dataSource`
   传静态数组，**不要**用 `request`。
2. **禁 mockjs / xlsx**：工程未安装。Mock 数据用**内联静态数组**。
3. **禁布局外壳**：不用 `ProLayout`、`PageContainer`、自带 `<header>`/
   `<Layout>`/sider。页面外层最多一个 `<div style={{ padding: 24 }}>`。
4. **禁真实交互逻辑**：按钮、链接可以渲染（视觉需要），但 `onClick`/
   `onFinish` 给 no-op（`() => {}`）或省略。不要写状态机、不要 `useState`
   管业务数据（纯展示不需要）。`rowSelection` 可省略或静态。
5. **禁路由 / 权限**：不 `import` react-router、不写 `useNavigate`、
   不做权限判断。
6. **禁外部依赖**：只用 antd / pro-components / icons 三个库，不引入其它。

## Mock 数据规范

- 在组件文件内**内联**一个 `const mockData = [ ... ]` 数组，8–15 条。
- 字段贴合页面语义（列表页给表格列对应的字段，详情页给单条记录字段）。
- 用真实感的中文值（姓名、状态、金额、日期），不要 `xxx`/`lorem`。
- 枚举字段值要与页面里的 Tag/Select 选项一致。
- 日期用 `'2026-07-15'` 这类字符串，金额用数字。

## 输出契约

- **只返回 .tsx 代码**，不包 markdown 围栏（不要 ```tsx），不加前后说明文字。
- 文件以 `import React from 'react';` 开头，以 `export default 组件名;` 结尾。
- 组件名用 PascalCase，与页面语义相关（如 `OrderList`、`UserDetail`）。
- 单文件、自包含，所有数据/类型/组件都在这一个文件里。

## 最小示例（查询表格页）

下面是一个完整可用的参考实现，展示 ProTable + 静态 dataSource + search +
no-op 工具栏的写法。生成时按具体页面调整列、字段、Mock 数据，**不要照抄**
这个订单示例的数据。

```tsx
import React from 'react';
import { ProTable, ProColumns } from '@ant-design/pro-components';
import { Button, Tag, Space } from 'antd';
import { PlusOutlined, ReloadOutlined, EyeOutlined } from '@ant-design/icons';

type OrderItem = {
  id: string;
  orderNo: string;
  customer: string;
  amount: number;
  status: 'pending' | 'paid' | 'shipped' | 'done';
  createTime: string;
};

const mockData: OrderItem[] = [
  { id: '1', orderNo: 'DD20260715001', customer: '张伟', amount: 1280.5, status: 'paid', createTime: '2026-07-15 09:30' },
  { id: '2', orderNo: 'DD20260715002', customer: '李娜', amount: 860, status: 'shipped', createTime: '2026-07-15 10:12' },
  { id: '3', orderNo: 'DD20260715003', customer: '王强', amount: 2399, status: 'pending', createTime: '2026-07-15 11:45' },
  { id: '4', orderNo: 'DD20260715004', customer: '刘洋', amount: 78.9, status: 'done', createTime: '2026-07-14 16:20' },
  { id: '5', orderNo: 'DD20260715005', customer: '陈静', amount: 560, status: 'paid', createTime: '2026-07-14 14:05' },
  { id: '6', orderNo: 'DD20260715006', customer: '赵敏', amount: 1899, status: 'shipped', createTime: '2026-07-13 09:18' },
  { id: '7', orderNo: 'DD20260715007', customer: '孙磊', amount: 420, status: 'pending', createTime: '2026-07-13 13:50' },
  { id: '8', orderNo: 'DD20260715008', customer: '周婷', amount: 3100, status: 'done', createTime: '2026-07-12 17:33' },
];

const STATUS_MAP: Record<OrderItem['status'], { text: string; color: string }> = {
  pending: { text: '待付款', color: 'orange' },
  paid: { text: '已付款', color: 'blue' },
  shipped: { text: '已发货', color: 'cyan' },
  done: { text: '已完成', color: 'green' },
};

const OrderList: React.FC = () => {
  const columns: ProColumns<OrderItem>[] = [
    { title: '订单编号', dataIndex: 'orderNo', key: 'orderNo' },
    { title: '客户', dataIndex: 'customer', key: 'customer' },
    { title: '金额', dataIndex: 'amount', key: 'amount', valueType: 'money' },
    {
      title: '状态', dataIndex: 'status', key: 'status',
      render: (_, record) => {
        const s = STATUS_MAP[record.status];
        return <Tag color={s.color}>{s.text}</Tag>;
      },
    },
    { title: '创建时间', dataIndex: 'createTime', key: 'createTime' },
    {
      title: '操作', key: 'operation', width: 120,
      render: () => (
        <Space>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => {}}>详情</Button>
          <Button type="link" size="small" onClick={() => {}}>编辑</Button>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <ProTable<OrderItem>
        columns={columns}
        dataSource={mockData}
        rowKey="id"
        search={{ labelLayout: 'default', defaultCollapsed: false }}
        pagination={{ pageSize: 10 }}
        options={{ setting: true, reload: false, density: false, fullScreen: false }}
        toolBarRender={() => [
          <Button key="add" type="primary" icon={<PlusOutlined />} onClick={() => {}}>新建</Button>,
          <Button key="refresh" icon={<ReloadOutlined />} onClick={() => {}}>刷新</Button>,
        ]}
      />
    </div>
  );
};

export default OrderList;
```

## 自检（生成后必做）

- 所有 `import` 只来自 `react` / `antd` / `@ant-design/pro-components` /
  `@ant-design/icons`，无其它依赖。
- ProTable 用的是 `dataSource` 而非 `request`。
- 没有任何 `useEffect` / `fetch` / `axios` / `@/apis` 引用。
- 没有包 `ProLayout` / `PageContainer` 等布局外壳。
- 组件 `export default`，文件自包含。
- Mock 数据 8–15 条，字段与列/表单对应。
