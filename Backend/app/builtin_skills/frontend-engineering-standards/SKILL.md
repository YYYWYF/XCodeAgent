---
name: frontend-engineering-standards
description: 前端 Build 的统一工程开发规范，覆盖 API、请求状态、组件、类型、React 状态、Hooks、安全、列表及命名。
version: "1.0"
---

# Frontend Engineering Standards

## 定位与使用原则

本 Skill 是前端 Build（`frontend.api_module`、`frontend.page`）的**通用开发规范**，回答“代码应如何实现”。当前工程的技术栈、实际文件结构、已有 API/组件导出、路由、认证与权限能力以**模板 `frontend/AGENTS.md` 和已授权的真实源码**为准；本 Skill 不重复维护工程架构说明。

- **实施事实**：以当前任务的 `implementation_contract`、已确认设计与 API 契约为准，不猜测接口或现有能力；优先保留已有符合契约的代码。
- **读写边界**：新增或修改文件必须得到任务授权。本 Skill 和 `AGENTS.md` 均不授予文件读写权限；实际权限由平台任务契约与工具层校验决定。
- **最小实现**：优先复用现有业务 API、组件和类型，不无依据新增依赖、修改工程配置或重构无关文件。
- **示例使用**：正文是必须遵循的规范，`references/` 仅在需要具体写法时按需阅读；示例不得覆盖真实工程 API 和任务契约。

## 开发规范

| 规则 ID | 适用任务 | 要求 |
| --- | --- | --- |
| `FE-API-BOUNDARY` | API 模块、页面 | 业务 HTTP 请求封装在 `src/apis/`，复用当前工程统一 `service`，严格遵守已确认的方法、路径、参数和返回类型。统一客户端成功返回业务值时不二次拆包；保留已有符合契约的导出。静态数据模块按正式数据源契约处理，不虚构 HTTP 接口。 |
| `FE-API-USE-REQUEST` | 页面 | 查询、列表与详情使用 ahooks `useRequest` 管理请求和 loading/error 等状态。简单查询直接在页面使用，不强制抽取自定义 Hook；复杂联动或复用场景再抽取。新增、修改、删除等事件型操作可在处理函数或 `onFinish` 中直接调用业务 API，并按需要刷新查询。 |
| `FE-API-UI-DECOUPLING` | 页面 | 业务查询与 UI 渲染解耦：ProTable 通过 `dataSource`、`loading` 和受控分页消费状态，ProFormSelect 等通过 `options` 消费数据，不在组件 JSX `request` 属性中直接调用业务 API。查询须按页面契约区分**加载中、成功有数据、成功无数据、请求失败**；请求失败提供可感知的错误反馈，不把失败伪装成空结果。受控分页的页码、页大小、列表记录与总数必须对应同一次成功查询；新页请求失败时，不得将上次成功的数据展示为失败请求对应的新页。若保留历史数据，须保持其成功查询的分页标识，或明确展示为非当前结果。 |
| `FE-CONSTANTS` | 页面 | 共享业务字典、枚举映射及固定选项放在已授权的 `src/constants/` 业务文件；仅使用一次的局部值留在使用处，不为形式统一而创建多余文件。 |
| `FE-TYPES` | API 模块、页面 | API 参数、响应数据和组件 Props 使用明确或可可靠推断的 TypeScript 类型，优先复用已有类型。共享类型放在已授权的 `src/typings/`，局部 UI 类型就近定义；避免重复定义及以 `any` 绕过类型检查。未知外部数据优先使用 `unknown` 并在使用前收窄。 |
| `FE-PRO-COMPONENTS` | 页面 | 依据确认设计选择 ProTable、ProList、ProCard、ProForm、ModalForm、DrawerForm、StepsForm 等；基础交互使用 Ant Design。避免为了使用高级组件而增加不必要的封装。 |
| `FE-HOOKS` | 页面 | React Hook 只在函数组件或自定义 Hook 的顶层调用，不在条件、循环、普通函数或嵌套回调中调用。复杂编排、联动和可复用逻辑可抽取到已授权 Hook 文件；简单查询直接使用 `useRequest`。Effect 用于必要的外部系统同步，不以 Effect 替代请求状态管理。 |
| `FE-CODE-LAYERING` | API 模块、页面 | 页面组合、交互与视图状态放在 pages；业务请求放在 apis；可复用 UI 放在 components；常量、类型、Hook、纯工具各归其职责。新增文件或修改既有文件均须在任务授权范围内。 |
| `FE-REACT-STATE` | 页面 | 通过 React State Setter 更新状态，不直接修改 state 对象或数组；渲染过程保持纯净，不在渲染阶段执行无条件状态更新。可由 props/state 计算的数据优先派生，不额外维护重复状态。 |
| `FE-RENDER-SECURITY` | 页面 | 外部数据和用户输入默认通过 React 文本插值渲染，不直接拼接或注入 HTML。确需渲染富文本时，仅复用工程已有的可信 HTML 净化机制，且 `dangerouslySetInnerHTML` 不与 children 同用。动态 `href`、`src` 按业务场景校验协议及来源，拒绝不可信的可执行 URL；不得擅自新增净化依赖。 |
| `FE-LIST-KEY` | 页面 | 列表项使用同级唯一且跨渲染稳定的 `key`；可增删、排序的列表使用稳定业务标识，不使用数组索引、随机数或每次渲染新生成的值。ProTable 使用与业务数据一致的稳定 `rowKey`。 |
| `FE-NAMING` | API 模块、页面 | React 组件名称用 PascalCase，变量/函数用 camelCase，自定义 Hook 以 `use` 开头；事件处理函数优先 `handleXxx`，回调属性优先 `onXxx`，业务常量命名须语义清晰。文件名遵循当前工程习惯（如页面入口 `index.tsx`），不强制文件名与组件名一致。 |

## 工程验收规则（沿用现有规则）

- `FE-NO-DIRECT-HTTP`：禁止页面、业务组件及任务 Hook 直接调用 `fetch`、`axios`、`service`，应通过已授权的业务 API 模块访问。
- `FE-NO-UI-REQUEST-PROP`：禁止 ProTable、ProFormSelect 等通过 JSX `request` 属性直接绑定业务 API。
- `FE-QUERY-USE-REQUEST`：对可确定的契约查询，检查实际 API 调用由 ahooks `useRequest` 的服务函数管理。

其他开发规范用于指导代码生成，**不因写入本 Skill 就自动成为新的强阻断验收项**；是否增加静态检查或质量门禁，须由平台另行确定。

## 页面任务 Step 4 自检（只读）

对 `frontend.page` 任务，在现有 Step 4 **只读核对**中结合已确认页面契约与已实现代码检查以下事项；优先复用 Step 1–3 已读取的证据，不为自检扩大读取范围：

1. **请求解耦**：查询由业务 API 与 `useRequest` 管理；组件消费 `dataSource`、`loading`、`options` 等数据及状态，而非在 JSX `request` 中绑定业务查询。
2. **状态区分**：加载中有反馈；成功无数据与请求失败有可区分的 UI 表现；失败信息不会被 `data?.list ?? []` 一类回退逻辑掩盖。复用现有 ProTable 空态及工程已有提示组件即可，不额外创建复杂状态层。
3. **分页一致性**：核对查询参数、成功返回的数据和页面显示的页码、页大小、总数是否一致。`useRequest` 的 `params` 可能代表**最近一次尝试**而非最近一次成功查询；不得在请求失败后，仅以该 `params` 更新页码、却仍渲染上一页成功返回的列表。
4. **结果处理**：如果契约允许失败后保留旧数据，应维持其对应的成功分页状态或明确标识为历史结果；否则展示失败态并提供按契约允许的重试方式。不为了满足规范擅自改变业务交互、添加权限动作或新依赖。

Step 4 不修改文件、不执行 Shell、不扩大授权。若核对发现问题，按现有步骤协议如实提交未满足项或失败结果，交由已有修复流程处理；**不得仅凭自检文字宣称已通过平台验收**。

## 按需参考

- [业务 API 与 useRequest](references/api-request-patterns.md)
- [表格、表单和选项](references/component-patterns.md)
- [复杂请求与页面分层](references/page-structure-examples.md)
