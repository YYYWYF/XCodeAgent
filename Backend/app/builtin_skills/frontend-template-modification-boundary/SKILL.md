---
name: frontend-template-modification-boundary
description: 前端模板工程文件修改边界规范（前端 skill）。当大模型在从远程拉取的前端模板工程中生成或修改前端页面代码、新增业务页面、新增业务 API、登记菜单、新增类型/常量/hooks/工具函数/可复用组件时使用此技能，明确哪些前端文件禁止修改、哪些只能增量追加、哪些可以自由编写，避免破坏前端模板工程的框架骨架与自动路由机制。涉及前端 src/pages、src/typings、src/constants、src/hooks、src/utils、src/components、src/apis、src/constants/menus.ts、路由自动生成、layout/providers/入口文件、package.json/tailwind.config.js 等配置文件时使用。
---

# 前端模板工程文件修改边界规范

本技能规定大模型在**从远程拉取的前端模板工程**中生成前端页面代码时，各文件的**修改边界**与**放置位置**。模板工程是带自动路由、自动菜单、统一请求封装、统一布局的前端脚手架，框架骨架不能被破坏，业务代码只能在指定区域、按指定方式生成。

## 虚拟路径前缀（重要）

本技能里所有 `src/...` 路径都是**相对于前端工程根**的相对路径。但在代码生成工作流中，文件系统工具（read_file / write_file / list_files 等）使用的是**相对于工作区根的虚拟绝对路径**，虚拟根是 `/`。

前端工程根在工作区中的实际位置是：

```
/frontend/
```

直接平铺在工作区根目录下，与 `.xcodeagent` 同级。

因此本技能里写的每一条 `src/...` 路径，在调用文件系统工具时都要加上前缀 `/frontend/`：

| 本技能里的相对路径 | 实际虚拟绝对路径 |
| --- | --- |
| `src/pages/<PageKey>/index.tsx` | `/frontend/src/pages/<PageKey>/index.tsx` |
| `src/typings/<page>.ts` | `/frontend/src/typings/<page>.ts` |
| `src/constants/<page>.ts` | `/frontend/src/constants/<page>.ts` |
| `src/hooks/use<Page>.ts` | `/frontend/src/hooks/use<Page>.ts` |
| `src/utils/<page>.ts` | `/frontend/src/utils/<page>.ts` |
| `src/components/<Module>/index.tsx` | `/frontend/src/components/<Module>/index.tsx` |
| `src/apis/<biz>Api.ts` | `/frontend/src/apis/<biz>Api.ts` |
| `src/constants/menus.ts` | `/frontend/src/constants/menus.ts` |

**生成代码前，务必先用 `list_files` 确认 `/frontend/src/pages/` 下已存在的页面目录与脚手架占位文件，再按上表前缀写入。** 不要把文件写到工作区根下的裸 `src/` 或 `Frontend/src/`，那会写到错误位置。

## 🔴 前端工程根目录禁止创建文件

`/frontend/` 根目录下**禁止创建任何新文件**，包括但不限于：

- ❌ 脚本文件：`.py`、`.sh`、`.bash`、`.ps1`、`.bat`
- ❌ 配置文件：任何 `.json`、`.yaml`、`.yml`、`.toml`、`.env`、`.ini` 文件
- ❌ 文档文件：`.md`、`.txt`、`.log` 文件
- ❌ 临时文件：`.tmp`、`.bak`、`.swp` 文件
- ❌ 任何其他非框架骨架的文件

前端工程根目录下已有的文件（`package.json`、`vite.config.ts`、`tsconfig.json` 等）由模板工程管理，**禁止修改**。新增文件**只能**放在下述允许的子目录中。

**唯一允许创建新文件的位置**：

| 允许的目录 | 可创建的文件类型 |
| --- | --- |
| `src/pages/<PageKey>/` | 页面主组件 `index.tsx` |
| `src/typings/` | 类型定义 `<page>.ts` |
| `src/constants/` | 常量文件 `<page>.ts` |
| `src/hooks/` | Hooks 文件 `use<Page>.ts` |
| `src/utils/` | 工具函数 `<page>.ts` |
| `src/components/<Module>/` | 可复用组件 `index.tsx` |
| `src/apis/` | 业务接口 `<biz>Api.ts` |

> 在 XCodeAgent 的 Frontend task 中，类型检查、构建、安装、lint 和测试由外层 integration-test 阶段统一执行，Frontend Agent 不调用这些项目级命令。独立人工流程且用户明确要求验证时，仍然禁止创建临时脚本。

## 🔴 验证边界：由外层质量门禁统一执行

在 XCodeAgent 的 Frontend task 中，写完代码后不要运行依赖安装、TypeScript 类型检查、lint、build、unit test 或 dev-server 命令。外层 integration-test 阶段会在所有 owner task 完成后统一执行仓库级检查；如果发现依赖或命令缺失，应在最终 JSON 中报告，不能通过安装依赖或临时脚本绕过边界。

### 外层质量门禁负责的检查

| 目的 | 执行方 |
| --- | --- |
| TypeScript 类型检查 | 由外层 integration-test 阶段按工程配置执行 |
| 生产构建 | 由外层 integration-test 阶段按工程配置执行 |
| lint、单元测试和依赖检查 | 由外层 integration-test 阶段按工程配置执行 |

Frontend Agent 只负责实现 task 声明的代码变更和读取真实源码，不负责启动开发服务器或修改依赖清单。

### ❌ Frontend Agent 禁止行为

- ❌ 创建 `run_build.sh`、`run_tsc.sh`、`run_check.sh`、`run_check.py` 等任何脚本文件，再想办法执行它。
- ❌ 在 Frontend task 中调用 `pnpm install`、`npm install`、`pnpm add`、`npx tsc` 或项目级 build/lint/test 命令。
- ❌ 把脚本写到 `/tmp/`、工作区根目录或 `/frontend/` 根目录来"绕过"限制——任何位置都不允许生成临时脚本。

### 独立人工流程读取命令输出

只有在脱离 XCodeAgent task 的独立人工流程中，用户明确要求执行命令时，才读取 `execute` 返回的 `{ exit_code, stdout, stderr }`；不要用 `echo "EXIT_CODE=$?"` 包装命令，也不要为此创建脚本。

## 核心原则

模板工程的路由是**自动生成**的：`src/utils/route.tsx` 用 `import.meta.glob('@/pages/**/index.tsx')` 静态扫描页面，再根据 `src/constants/menus.ts` 里每个菜单项的 `key` 解析到 `src/pages/<key>/index.tsx`。因此：

- 页面文件路径**必须**是 `src/pages/<PageKey>/index.tsx`，`<PageKey>` 即菜单项的 `key`，二者必须完全一致。
- 页面要能被访问，`menus.ts` 的 `BIZ_MENUS` 任意层级中必须存在合法的 `{ path, name, key }` 菜单项；如果当前页面尚未注册，把新菜单项追加到 `BIZ_MENUS` 顶层数组末尾。
- 框架骨架文件（入口、路由生成器、布局、Provider、守卫、配置）**禁止修改**。
- 页面的类型、常量、hooks、工具函数**统一放公共目录**（`src/typings`、`src/constants`、`src/hooks`、`src/utils`），不放在页面目录内；可复用组件放 `src/components`。

## 文件修改边界总览

| 分类 | 含义 | 涉及文件 |
| --- | --- | --- |
| 🔴 禁止修改 | 前端框架骨架与配置，改了会破坏整个工程 | 入口、路由、布局、Provider、守卫、常量、类型、配置、请求封装、全局样式 |
| 🟡 只能增量 | 只能追加新文件/新内容，不能删改现有项 | `menus.ts` 的BIZ_MENUS数组顶层、`src/apis/`、`src/typings/`、`src/constants/`、`src/hooks/`、`src/utils/`、`src/components/`、`src/pages/` |
| 🟢 自由编写 | 业务代码生成目标，可任意编写 | `src/pages/<PageKey>/index.tsx`（页面主组件） |

## 🔴 禁止修改的文件（前端框架骨架）

以下文件**任何情况下都不得修改**，包括内容、结构、导入关系、配置项：

### 入口与路由生成
- `src/index.tsx` — React 挂载入口
- `src/App.tsx` — 应用根组件（BrowserRouter / ConfigProvider / Provider 装配）
- `src/routes/index.tsx` — 路由注册器，从菜单自动生成路由
- `src/utils/route.tsx` — 菜单转路由工具，依赖 `import.meta.glob` 静态扫描页面

### 布局与全局上下文
- `src/layout/index.tsx` 及 `src/layout/components/**` — ProLayout 布局壳、Header/Sider 渲染
- `src/providers/index.tsx` — GlobalContext 定义与 Provider 装配
- `src/components/ErrorBoundary/**` — 全局错误边界
- `src/hooks/useGuard.ts` — 路由守卫（鉴权跳转）

### 全局常量与类型（框架自带文件）
- `src/constants/index.ts`、`src/constants/routes.ts`、`src/constants/layout.ts`、`src/constants/yst.ts`、`src/constants/menus.ts` 的结构
- `src/typings/index.ts`、`src/typings/workbench.ts`、`src/typings/yst.ts`
- `src/utils/workbench.tsx`

### 请求封装
- `src/apis/service.ts` — axios 实例与请求/响应拦截器（鉴权头注入、401 跳转）
- `src/apis/auth.ts`、`src/apis/login.ts`、`src/apis/welcome.ts` — 框架自带的认证/登录/欢迎接口

### 全局样式与静态资源
- `src/styles/**` — 全局样式、主题变量、CSS 变量
- `src/index.css` — 全局样式入口
- `src/assets/appIconList/**` — 应用图标资源
- `public/**` — 公共静态资源

### ⚠️ 切忌重新生成的工程配置文件

以下配置文件**绝对不能重新生成、覆盖或修改**，即使看起来需要加依赖、改配置也不行——它们由模板工程统一管理，重新生成会破坏构建、依赖锁定或部署：

- `package.json` — 依赖与脚本声明（**不要为了加依赖而改它**，所需依赖应假设已存在或向用户确认）
- `pnpm-lock.yaml` — 依赖锁定文件
- `vite.config.ts` — Vite 构建配置（别名、插件、代理）
- `tsconfig.json` — TypeScript 编译配置
- `tailwind.config.js` — Tailwind 主题与扫描规则
- `postcss.config.js` — PostCSS 插件链
- `index.html` — HTML 入口模板
- `Dockerfile` — 容器构建配置
- `.gitignore` — Git 忽略规则
- `README.md` — 工程说明

> 如果业务需求看似必须改这些文件（例如换主题色、加新依赖、改路由别名），**不要直接改**，先向用户说明这属于框架级改动，由用户决定。

## 🟡 只能增量修改的目录与文件

以下区域**只能新增文件或追加内容**，**不得删除或修改**框架已有的文件：

### `src/constants/menus.ts` — 菜单登记

若当前页面尚未在 `BIZ_MENUS` 任意层级注册，**只能**在 `BIZ_MENUS` 顶层数组末尾**追加**新菜单项；若已存在合法菜单项，无论位于顶层还是深层 `children`，都视为已注册，**不得**：
- 删除或修改已有的 `DefaultPage` 等菜单项
- 移动、提升、拍平、重排或重写已有深层合法菜单项
- 修改 `SYSTEM_MENUS`（系统菜单由框架维护）
- 改动文件中的 `import`、`export`、类型注解

追加的菜单项格式必须为：
```ts
{
  path: 'duty-list',      // 路由路径，不带前导 /，与页面路由一致
  name: '值班列表',        // 菜单显示名
  key: 'DutyList'          // 必须与 src/pages/<key>/index.tsx 的目录名完全一致
}
```

若追加菜单项的 `path` 包含 React Router 路径参数片段（如 `:id`、`detail/:id`），必须同时写入 `hideInMenu: true`，表示该不固定路径页面不出现在菜单中：
```ts
{
  path: 'duty/:id',
  name: '值班详情',
  key: 'DutyDetail',
  hideInMenu: true
}
```

`key` 命名规则：PascalCase（如 `DutyList`、`PageDashboard`），且与 `src/pages/` 下对应页面目录名一字不差，否则路由无法解析到页面。

### `src/apis/` — 业务接口

**只能新增**业务 API 文件（如 `dutyApi.ts`），通过 `import` 复用 `service.ts` 导出的 axios 实例，**不得**修改 `service.ts` 及框架自带的 `auth.ts`/`login.ts`/`welcome.ts`。

```ts
// src/apis/dutyApi.ts
import { service } from './service';

export function fetchDutyList(params: DutyListQuery) {
  return service.get('/duty/list', { params });
}
```

### 🔴 数据源类型决定 API 写法（Static vs Database）

页面详细设计里的 `data_origin.source_type` / ProjectPlan 的 `data_sources[].type` 决定 `src/apis/<biz>Api.ts` 用哪种写法，**必须严格匹配，不能混用**：

#### 情况 A：数据源是 static（实现来源 frontend_mock）

当正式数据源类型是 `static`，且详情实现来源为 `frontend_mock` 时，页面**不调用任何真实后端接口**，`src/apis/<biz>Api.ts` 里写**内存数据访问函数**：模块级维护一组测试记录，用 `delay(ms)` 模拟网络延迟，导出的 async 函数严格按 API 契约对记录做筛选、分页、增删改后返回。**不要** `import service`、**不要** `service.get('/api/...')`、**不要**改 `vite.config.ts` 加 Mock 插件；页面组件也不得自行维护业务静态数组。

```ts
// src/apis/dutyApi.ts —— Static 前端内存数据模块写法
import type { DutyListItem, DutyListQuery, PaginatedResult } from '@/typings/duty';

// 内存假数据
const mockDutyList: DutyListItem[] = Array.from({ length: 46 }, (_, i) => ({
  id: String(i + 1),
  dutyNo: `DUTY-2026-${String(i + 1).padStart(4, '0')}`,
  name: ['差旅费', '办公费', '招待费'][i % 3],
  amount: parseFloat((Math.random() * 5000 + 100).toFixed(2)),
  status: ['active', 'disabled', 'pending'][i % 3],
}));

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export async function fetchDutyList(params: DutyListQuery): Promise<PaginatedResult<DutyListItem>> {
  await delay(300); // 模拟网络延迟，保证 loading 动画可见
  const { page = 1, pageSize = 10, ...rest } = params;
  let filtered = [...mockDutyList];
  Object.entries(rest).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    filtered = filtered.filter((item) =>
      String((item as Record<string, unknown>)[key] ?? '').toLowerCase().includes(String(value).toLowerCase()),
    );
  });
  const total = filtered.length;
  const data = filtered.slice((page - 1) * pageSize, page * pageSize);
  return { data, success: true, total };
}

export async function updateDuty(payload: Partial<DutyListItem> & { id: string }) {
  await delay(300);
  const idx = mockDutyList.findIndex((item) => item.id === payload.id);
  if (idx === -1) return { success: false };
  mockDutyList[idx] = { ...mockDutyList[idx], ...payload };
  return { success: true };
}

export async function deleteDuty(id: string) {
  await delay(300);
  const idx = mockDutyList.findIndex((item) => item.id === id);
  if (idx === -1) return { success: false };
  mockDutyList.splice(idx, 1);
  return { success: true };
}
```

页面组件里 `import { fetchDutyList } from '@/apis/dutyApi'`，在 ProTable 的 `request` 里 `const res = await fetchDutyList({...}); return { data: res.data, success: res.success, total: res.total }`。这与所选页面模板（`commonTable`/`tabsTable`）的 `api.ts` 写法完全一致，直接参照模板。

#### 情况 B：数据源是真实接口（mysql / external_api / third_party）

当数据源类型是 `mysql`/`external_api`/`third_party` 等真实后端时，才用上面的 `service.get('/api/...')` 写法，复用 `service.ts` 的 axios 实例调用真实接口。

> 判断依据：ProjectPlan `data_sources[].type=static`，且 EndpointDetail 为 `source_type=static`、`effective_source.kind=frontend_mock` 时选择 A；`database/mysql_existing` 时选择 B。旧 `mock` 不是正式类型，不得据此生成。Static 场景误用 `service.get` 会导致页面请求不存在的后端接口、表格一直 loading。

## 🟢 自由编写与公共目录放置规则

页面业务代码的**主组件**在 `src/pages/<PageKey>/index.tsx` 自由编写；但页面用到的**类型、常量、hooks、工具函数、可复用组件**统一放到对应的**公共目录**下新增文件，**不要放在页面目录内**。

### 页面主组件：`src/pages/<PageKey>/index.tsx`

这是页面入口，必须 `export default` 一个 React 组件。可自由编写业务逻辑、交互、状态，调用 `src/apis/` 下的业务接口，使用 antd v4 组件、ProComponents、Tailwind 样式。

```tsx
// src/pages/DutyList/index.tsx
import { useState, useEffect } from 'react';
import { Table } from 'antd';
import { fetchDutyList } from '@/apis/dutyApi';
import type { DutyListItem, DutyListQuery } from '@/typings/duty';
import { DUTY_TYPE_LABELS } from '@/constants/duty';

export default function DutyList() {
  const [data, setData] = useState<DutyListItem[]>([]);
  useEffect(() => {
    fetchDutyList({}).then((res) => setData(res?.data ?? []));
  }, []);
  return <Table dataSource={data} columns={[/* ... */]} />;
}
```

### 类型文件：`src/typings/<page>.ts`

每个新增页面如果需要类型，在 `src/typings/` 下新建一个以页面名命名的 `.ts` 文件，**不要**把类型写在页面目录内，也**不要**改框架自带的 `index.ts`/`workbench.ts`/`yst.ts`。

```ts
// src/typings/duty.ts
export interface DutyListItem {
  id: string;
  name: string;
  dutyType: string;
}
export type DutyListQuery = { page?: number; size?: number };
```

### 常量文件：`src/constants/<page>.ts`

页面用到的常量（枚举映射、状态字典、固定配置）统一在 `src/constants/` 下新建文件，**不要**改框架自带的 `index.ts`/`routes.ts`/`layout.ts`/`yst.ts`/`menus.ts` 的结构。

```ts
// src/constants/duty.ts
export const DUTY_TYPE_LABELS: Record<string, string> = {
  day: '白班',
  night: '夜班',
};
```

### Hooks 文件：`src/hooks/use<Page>.ts` 或 `src/hooks/<page>.ts`

页面专属 hooks 统一在 `src/hooks/` 下新建文件，**不要**改框架自带的 `useGuard.ts`。

```ts
// src/hooks/useDutyList.ts
import { useState, useEffect } from 'react';
import { fetchDutyList } from '@/apis/dutyApi';

export function useDutyList() {
  const [data, setData] = useState([]);
  useEffect(() => { fetchDutyList({}).then(r => setData(r?.data ?? [])); }, []);
  return { data };
}
```

### 工具函数文件：`src/utils/<page>.ts`

页面用到的纯工具函数统一在 `src/utils/` 下新建文件，**不要**改框架自带的 `route.tsx`/`workbench.tsx`。

```ts
// src/utils/duty.ts
export function formatDutyTime(time: string): string {
  return /* ... */;
}
```

### 可复用组件：`src/components/<Module>/`

当一个页面代码太长，且其中存在**可复用**的模块（多个页面都会用，或同页面内可独立成块）时，拆分到 `src/components/<Module>/index.tsx`。注意：
- 只拆**真正可复用**的模块，页面内一次性使用的片段不必拆出。
- **不要**改框架自带的 `src/components/ErrorBoundary/`。
- 拆出的组件如需类型/常量，同样遵循上面的公共目录规则。

```
src/components/DutyTable/index.tsx   // 可复用的值班表格组件
```

### 公共目录新增文件命名规则

| 目录 | 文件命名 | 示例 |
| --- | --- | --- |
| `src/typings/` | `<page>.ts` | `duty.ts`、`shift.ts` |
| `src/constants/` | `<page>.ts` | `duty.ts`、`shift.ts` |
| `src/hooks/` | `use<Page>.ts` 或 `<page>.ts` | `useDutyList.ts` |
| `src/utils/` | `<page>.ts` | `duty.ts` |
| `src/components/` | `<Module>/index.tsx` | `DutyTable/index.tsx` |
| `src/apis/` | `<biz>Api.ts` | `dutyApi.ts` |

> `<page>` 取页面业务名的小写 kebab-case（如 `DutyList` 页面对应 `duty`），同一页面相关的类型/常量/hooks/工具用同一个 `<page>` 名，便于归类。

## 生成页面代码的标准流程

当需要为某个页面生成具体代码时，按以下步骤：

1. **确认页面目录与 key**：页面目录 `src/pages/<PageKey>/index.tsx` 已由脚手架创建（初始内容为 `hello agent!` 占位），`<PageKey>` 与 `menus.ts` 中登记的 `key` 一致。
2. **新增类型（如需）**：在 `src/typings/<page>.ts` 新建类型文件，不改框架自带类型文件。
3. **新增常量（如需）**：在 `src/constants/<page>.ts` 新建常量文件。
4. **新增业务 API（如需）**：在 `src/apis/<biz>Api.ts` 新建文件，复用 `service.ts` 的 axios 实例。
5. **新增 hooks/工具函数（如需）**：分别在 `src/hooks/`、`src/utils/` 下新建文件。
6. **编写页面主组件**：替换 `src/pages/<PageKey>/index.tsx` 的占位内容为真实业务代码，从公共目录 import 类型/常量/hooks/API。
7. **拆分可复用组件（如需）**：页面太长且有可复用模块时，拆到 `src/components/<Module>/`。
8. **不要碰菜单**：菜单登记由脚手架在创建页面时已完成，生成代码阶段不需要再改 `menus.ts`（除非用户明确要求新增菜单项，且当前页面未在 `BIZ_MENUS` 任意层级注册，此时追加到 `BIZ_MENUS` 顶层数组末尾；若新增项 `path` 包含 React Router 路径参数，必须同时设置 `hideInMenu: true`）。

## 禁止行为清单

- ❌ 在 `/frontend/` 根目录下创建任何新文件（`.py`、`.sh`、`.md`、`.json`、`.env` 等）
- ❌ 在工作区**任何位置**（`/frontend/`、`/tmp/`、工作区根等）生成脚本文件（`.sh`/`.py`/`.js`/`.mjs` 等检查脚本、安装脚本、部署脚本）。Frontend task 的项目级验证由外层 integration-test 阶段统一执行，Agent 不得自行调用验证命令。
- ❌ 在 `/frontend/` 下生成非前端代码文件（Python、Shell、Bash 等）
- ❌ 修改 `src/routes/index.tsx`、`src/utils/route.tsx` 以手动注册路由（路由由菜单自动生成）
- ❌ 修改 `src/App.tsx`、`src/index.tsx` 入口装配
- ❌ 修改 `src/layout/**`、`src/providers/**`、`src/hooks/useGuard.ts`
- ❌ 修改 `src/apis/service.ts` 请求封装（新增 API 复用即可）
- ❌ 修改框架自带的 `src/constants/index.ts`/`routes.ts`/`layout.ts`/`yst.ts`/`menus.ts` 结构、`src/typings/**`、`src/utils/route.tsx`/`workbench.tsx`
- ❌ **重新生成或修改 `package.json`、`pnpm-lock.yaml`、`vite.config.ts`、`tsconfig.json`、`tailwind.config.js`、`postcss.config.js`、`index.html`、`Dockerfile`、`.gitignore`、`README.md`**
- ❌ 删除框架自带的 `Login`/`Logout`/`System`/`DefaultPage` 页面
- ❌ 在 `menus.ts` 删除或修改已有菜单项（只能追加）
- ❌ 把页面类型/常量/hooks/工具函数写在页面目录内（必须放公共目录）
- ❌ 修改 `src/styles/**` 全局样式（页面样式用 Tailwind 或在公共目录规范内处理）
- ❌ 为了加依赖而改 `package.json`（所需依赖应假设已存在；若确实缺失，向用户说明，由用户安装）

## 依赖缺失处理

Frontend task 不自动安装依赖。即使任务被标记为 repair，依赖缺失也只能作为阻塞信息写入最终 JSON，并交由外层流程或用户处理；Agent 不得调用 `pnpm install`、`pnpm add`、`npm install` 或其他安装命令，也不得在 task 内重新执行项目构建验证。

## 与其他技能的关系

- **react-develop-specification**：提供 React 通用编码规范（命名、hooks、安全等），本技能是前端模板工程特有的文件边界约束。
- 生成前端页面代码时，**先遵守本技能的文件边界与放置规则**，再按 `react-develop-specification` 的规范编写组件内部代码。
