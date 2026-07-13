---
name: code-block-template
description: >-
  XcodeAgent 前端代码区块与页面模板技能。用于通过 LLM 驱动生成基于 React + Ant Design ProComponents的前端代码。提供详细的设计规范、
  使用决策树、代码示例、页面模板、分步代码生成策略和 Mock 数据生成规范。当用户需要生成前端页面、表格、表单、列表、卡片、弹窗表单、抽屉表单、分步表单、行、列等组件时使用此技能。
agent_created: true
---

# XcodeAgent Frontend Code Block Template

面向 XcodeAgent 大模型前端代码生成的区块/页面设计规范。提供 ProComponents 体系下各区块的详细设计、决策逻辑与分步生成策略。

## When to Use

- 生成带查询、分页、批量操作的 ProTable 表格页
- 生成新增/编辑/详情的 ModalForm / DrawerForm / StepsForm
- 生成卡片式 ProList 列表页
- 生成多标签页 ProCard tabs 切换页面
- 生成统计/概览仪表盘卡片
- 需要 Excel 批量导入导出功能
- 需要 Mock 数据支持独立开发

## Architecture Overview

XcodeAgent 前端页面由**区块（Blocks）**拼装而成。区块是可复用的最小单元，页面是多个区块的组合。

```
ProCard / PageContainer          ← 页面容器与卡片
├── ProForm（筛选表单）            ← 横排筛选条件，Row + Col 栅格
├── ProTable                      ← 表格（search / request / toolBarRender）
├── ModalForm（弹窗表单）          ← 统一弹窗管理
├── DrawerForm（抽屉表单）
└── StepsForm（分步表单）
```

## How to Use

### Decision Flow

1. 确定页面类型 → 参考 `references/page-templates.md` 选模板
2. 确定需要的区块类型 → 参考 `references/blocks.md` 查详细用法
3. 按分步策略生成代码 → 参考 `references/codegen-strategy.md`
4. 补充 Mock 数据 → 参考 `references/mock-data.md`

### Component Import Rules (CRITICAL)

ProComponents 系列从 `@ant-design/pro-components` 导入；基础组件从 `antd` 导入。两者绝不能混在同一个 `from 'antd'`：

```ts
// ✅ Pro 系列
import { ProTable, ProForm, ProFormText, ProFormSelect, ProList, ProCard, ModalForm, DrawerForm, StepsForm } from '@ant-design/pro-components';

// ✅ 基础组件
import { Button, Col, Row, Space, Spin, FormInstance, message, Upload, Checkbox, Tag, Popconfirm } from 'antd';
```

判断依据：名称以 `Pro` 开头或为 `ModalForm`/`DrawerForm`/`StepsForm` → `@ant-design/pro-components`；其余 → `antd`。

### xlsx Import

涉及 Excel 导入导出的页面，统一：
```ts
import * as XLSX from 'xlsx';
```

## Reference Files

- **`references/blocks.md`** — 所有区块详解：ProTable / ProForm 体系 / ProList / Row/Col 栅格 / ProCard
- **`references/page-templates.md`** — 页面模板：查询表格+批量导入导出 / 多标签页查询表格
- **`references/codegen-strategy.md`** — 代码生成分步策略
- **`references/mock-data.md`** — Mock 数据生成规范（Random 方法速查）

## Quick Reference: Key Decision Trees

### 筛选表单：ProTable search or custom ProForm?
- 布局要求不高 → ProTable 内置 `search={{ labelLayout: 'default', defaultCollapsed: false }}`
- 需要自定义横排布局 → ProForm + Row/Col + `search={false}` + 手动查询/重置

### 表单类型选择
- 多个阶段 → StepsForm
- 字段非常多（15+）→ DrawerForm
- 表格行操作 → ModalForm（首选）
- 页面主体配置 → ProForm

### 数据展示：ProTable or ProList?
- 结构化表格、列多、需排序/筛选 → ProTable
- 每条信息量大、图文混排、卡片式 → ProList

### 何时用 ProCard
- 表单/信息按模块分组（最常用）
- 多标签页容器（`tabs`）
- 统计/概览卡片（Dashboard KPI）
- **禁止**用 antd `<Card>` 或手写 `<div className="xxx-card">` 替代
