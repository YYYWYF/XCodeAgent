# 代码生成分步策略

大模型在生成页面代码时，应按以下步骤逐步构建，避免一次性输出大段代码导致结构混乱。

## 通用分步流程

```
步骤 1: 确定页面类型 → 选择模板
步骤 2: 定义类型 (typings) → 数据模型先行
步骤 3: 编写 API 接口 (api) → 数据层隔离
步骤 4: 搭建骨架布局 → UI 框架
步骤 5: 填充列定义 / 筛选表单 → 表格/表单内容
步骤 6: 实现交互逻辑 → 弹窗/提交/删除
步骤 7: 补充 Mock 数据 → 可运行验证
```

## 模板一生成步骤（查询表格 + 批量导入导出）

### 步骤 2：定义类型 (typings)

```ts
// typings/XXX.ts
export interface XXXItem {
  id: string;
  name: string;
  // ...根据需求定义所有字段
}

export interface XXXQuery {
  page: number;
  pageSize: number;
  name?: string;
  // ...筛选字段
}
```

### 步骤 3：编写 API 接口

```ts
// api/xxxApi.ts
export async function fetchList(params: XXXQuery): Promise<{ data: XXXItem[]; total: number; success: boolean }>
export async function importData(rows: Record<string, any>[]): Promise<{ imported: number }>
```

### 步骤 4：搭建页面骨架（先做 UI，不做数据）

```tsx
// 1. ProForm 筛选表单骨架（Row + Col + placeholder 字段）
// 2. ProTable 骨架（columns 先写空的 title）
// 3. ModalForm 骨架（新增/编辑/删除弹窗占位）
// 4. Row/Col 排版确认（每行 4 个筛选条件，弹窗内 2 列或全宽）
```

### 步骤 5：定义 FIELD_DEFS 并填充列

```ts
const FIELD_DEFS: { title: string; dataIndex: keyof XXXItem }[] = [
  { title: '字段中文名', dataIndex: 'fieldKey' },
  // ...
];
```

然后由 FIELD_DEFS 驱动生成 columns、筛选表单项。

### 步骤 6：填充数据交互

```tsx
// request 函数合并 formValues + 分页
// toolBarRender 按钮
// 行内操作列（查看详情 / 编辑 / 删除）
```

### 步骤 7：实现弹窗逻辑

```tsx
// ModalForm 绑定 modal.type + modal.record
// ImportModal 子组件（上传 + 解析 + 提交）
// ExportModal 子组件（勾选列 + 下载）
// DetailModal（只读表单，所有字段 disabled）
```

### 步骤 8：补充 Mock 数据

参考 `references/mock-data.md`。

---

## 模板二生成步骤（多标签页查询表格）

### 步骤 2：定义类型

```ts
export type TabKey = 'tab1' | 'tab2' | 'tab3';
export interface Tab1Item { /* 第一个 Tab 的数据字段 */ }
export interface Tab2Item { /* 第二个 Tab 的数据字段 */ }
```

### 步骤 3：编写 API 接口

```ts
export async function fetchManagementList(params: {
  page: number; pageSize: number; tabType: TabKey;
  ...filterFields
}): Promise<{ data: any[]; total: number; success: boolean }>;
```

### 步骤 4：搭建 ProCard tabs 骨架

```tsx
// ProCard tabs with items[{ key, label, children: <></> }]
// 先用占位文字填充每个 Tab
```

### 步骤 5：定义每个 Tab 的列

```tsx
const tab1Columns: ProColumns[] = [];
const tab2Columns: ProColumns[] = [];
```

### 步骤 6：实现筛选表单

```tsx
// renderFilterForm() 根据 activeTab 动态返回表单项
// 先用 1-2 个字段验证逻辑，再扩展
```

### 步骤 7：实现 toolBarRender 动态切换

```tsx
// getToolBarRender() 根据 activeTab 返回不同按钮组
```

### 步骤 8：实现弹窗

```tsx
// 统一 modal state: { type, record }
// ModalForm × N，每个用 modal.type === 'xxx' 控制 visible
```

### 步骤 9：实现标签页切换钩子

```tsx
// handleTabChange: 重置筛选 + 清空选择 + 刷新表格
```

### 步骤 10：补充 Mock 数据

每个标签页都要有对应的 Mock 数据，可独立验证。
