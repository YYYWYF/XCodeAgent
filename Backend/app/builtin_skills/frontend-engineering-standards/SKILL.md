---
name: frontend-engineering-standards
description: 前端 Build（frontend.api_module、frontend.page）及 SmallTask 实际修改/修复前端业务源码的统一工程开发规范，覆盖 API、请求状态、组件、类型、React 状态、Hooks、安全、列表及命名。
version: "1.0"
---

# Frontend Engineering Standards

## 定位与使用原则

本 Skill 是前端 Build（`frontend.api_module`、`frontend.page`）及 SmallTask 实际修改/修复前端业务源码时的**通用开发规范**，回答“代码应如何实现”。当前工程的技术栈、实际文件结构、已有 API/组件导出、路由、认证与权限能力以**模板 `frontend/AGENTS.md` 和已授权的真实源码**为准；本 Skill 不重复维护工程架构说明。

仅在实际涉及前端源码开发时使用本编码规范。非前端修改、工具安装修复、UI 设计与扫描仍按既有专用流程选择；不向 SmallTask 附加前端 Build 四步流程。

- **实施事实**：以当前任务的 `implementation_contract`、已确认设计与 API 契约为准，不猜测接口或现有能力；优先保留已有符合契约的代码。
- **读写边界**：新增或修改文件必须得到任务授权。本 Skill 和 `AGENTS.md` 均不授予文件读写权限；实际权限由平台任务契约与工具层校验决定。
- **最小实现**：优先复用现有业务 API、组件和类型，不无依据新增依赖、修改工程配置或重构无关文件。
- **示例使用**：正文是必须遵循的规范，`references/` 仅在需要具体写法时按需阅读；示例不得覆盖真实工程 API 和任务契约。

## 开发规范

| 规则 ID | 适用任务 | 要求 |
| --- | --- | --- |
| `FE-API-BOUNDARY` | API 模块、页面 | 业务 HTTP 请求封装在 `src/apis/`，复用当前工程统一 `service`，严格遵守已确认的方法、路径、参数和返回类型。统一客户端成功返回业务值时不二次拆包；保留已有符合契约的导出。静态数据模块按正式数据源契约处理，不虚构 HTTP 接口。 |
| `FE-API-USE-REQUEST` | 页面 | 查询、列表与详情使用 ahooks `useRequest` 管理请求和 loading/error 等状态。简单查询直接在页面使用；查询条件和分页保存在页面 State，通过 refreshDeps 驱动查询，这不是重复请求状态。不强制抽取自定义 Hook，复杂联动或复用场景再抽取。新增、修改、删除等事件型操作可在处理函数或 `onFinish` 中直接调用业务 API，并按需要刷新查询。 |
| `FE-API-UI-DECOUPLING` | 页面 | 业务查询与 UI 渲染解耦：ProTable 通过 `dataSource`、`loading` 和受控分页消费状态，ProFormSelect 等通过 `options` 消费数据，不在组件 JSX `request` 属性中直接调用业务 API。查询须按页面契约区分**加载中、成功有数据、成功无数据、请求失败**；加载时可保留列表并用 loading 明确标识，不强制清空数据；失败用 Result 等明确失败视图及重试处理，不仅在旧表格旁附加错误后仍当正常结果展示；请求失败提供可感知的错误反馈，不把失败伪装成空结果。受控分页的页码、页大小、列表记录与总数必须对应同一次成功查询；新页请求失败时，不得将上次成功的数据展示为失败请求对应的新页。若保留历史数据，须保持其成功查询的分页标识，或明确展示为非当前结果。 |
| `FE-ERROR-PRESENTATION` | 页面 | 区分业务错误、请求失败和面向用户的提示。复用当前工程统一 `service` 的响应与错误处理，不在页面重复解析业务错误协议。不得把错误码、堆栈或未经转换的技术异常直接作为用户提示；应优先使用工程已确认的安全提示机制，否则展示清晰、非敏感的通用提示及必要的恢复操作。请求失败不得伪装为成功空结果。 |
| `FE-CONSTANTS` | 页面 | 共享业务字典、枚举映射及固定选项放在已授权的 `src/constants/` 业务文件；仅使用一次的局部值留在使用处，不为形式统一而创建多余文件。 |
| `FE-TYPES` | API 模块、页面 | API 参数、响应数据和组件 Props 使用明确或可可靠推断的 TypeScript 类型，优先复用已有类型。共享类型放在已授权的 `src/typings/`，局部 UI 类型就近定义；避免重复定义及以 `any` 绕过类型检查。未知外部数据优先使用 `unknown` 并在使用前收窄；可选值使用前收窄或给出符合 UI 语义的默认值，不借默认值改写业务契约。 |
| `FE-DATA-PRECISION` | API 模块、页面 | 业务标识、金额及高精度数值必须遵循已确认 API 契约的数据类型、精度和序列化方式。不得将可能超出 JavaScript 安全整数范围的服务端标识强制转换为 `number`，也不得用存在浮点精度误差的计算处理需要精确计算的金额。优先复用工程现有的精度处理机制，不擅自修改接口字段类型；若上游 JSON 数值已经丢失精度，不能通过前端字符串转换假装恢复。 |
| `FE-PRO-COMPONENTS` | 页面 | 依据确认设计，结构化多列表用 ProTable，图文条目用 ProList；分组、概览与卡片容器复用 ProCard，复杂布局使用 Row/Col 栅格。常驻表单用 ProForm，短操作用 ModalForm，多字段且需保留页面上下文用 DrawerForm，确有阶段校验用 StepsForm。Pro 系列从 `@ant-design/pro-components` 导入，基础交互从 `antd` 导入。遵循模板现有样式与主题方案，局部样式限定业务作用域，不泄漏到全局，明暗主题均需可读。不为使用高级组件增加封装。按需见 `references/component-patterns.md` 的对应组件章节。 |
| `FE-HOOKS` | 页面 | React Hook 只在函数组件或自定义 Hook 的顶层调用，不在条件、循环、普通函数或嵌套回调中调用。复杂编排、联动和可复用逻辑才抽取 Hook；简单查询直接使用 `useRequest`。Effect 仅同步外部系统，列全实际依赖并清理订阅、定时器等资源，不以 Effect 替代请求管理。沿用 useRequest 的并发结果管理，不添加手动竞态标记、自定义缓存或重复请求状态；useMemo/useCallback 仅用于确有收益的计算或引用稳定性。 |
| `FE-CODE-LAYERING` | API 模块、页面 | 页面组合、交互与视图状态放在 pages；业务请求放在 apis；可复用 UI 放在 components；常量、类型、Hook、纯工具各归其职责。新增文件或修改既有文件均须在任务授权范围内。 |
| `FE-REACT-STATE` | 页面 | Props 只读，不修改传入对象、数组或 state；通过 Setter 不可变更新，依赖旧值时使用函数式更新。render 不执行请求、DOM 操作或状态更新等副作用。可由 props/state 计算的数据直接派生，不维护重复状态。筛选、重置及改变页容量时复位分页，异步查询的 Loading/Empty/Error 与当前结果一致；不要用 useRequest 的旧 data 表示新查询成功。参考 `references/component-patterns.md` 的基础分页查询。多标签页显式选择独立或共享状态，见 `references/page-structure-examples.md`。 |
| `FE-RENDER-SECURITY` | 页面 | 外部数据和用户输入默认通过 React 文本插值渲染，不直接拼接或注入 HTML。确需渲染富文本时，仅复用工程已有的可信 HTML 净化机制，且 `dangerouslySetInnerHTML` 不与 children 同用。动态 `href`、`src` 按业务场景校验协议及来源，拒绝不可信的可执行 URL；不得擅自新增净化依赖。 |
| `FE-LIST-KEY` | 页面 | 列表项使用同级唯一且跨渲染稳定的 `key`；可增删、排序的列表使用稳定业务标识，不使用数组索引、随机数或每次渲染新生成的值。ProTable 使用与业务数据一致的稳定 `rowKey`。 |
| `FE-NAMING` | API 模块、页面 | React 组件和类型用 PascalCase，变量/函数用 camelCase，Hook 以 `use` 开头；事件处理优先 `handleXxx`，回调属性优先 `onXxx`，布尔值和业务常量语义清晰。文件名遵循当前工程习惯（如页面入口 `index.tsx`），含 JSX 使用 `.tsx`。导入置于模块顶部，函数以中文注释说明目的，复杂决策说明原因。 |
| `FE-JSX` | 页面 | 使用当前版本支持的 JSX/DOM 属性（如 className、对象式 style），组件必须已声明/导入，属性不重复，不将业务 Props 无差别透传 DOM。组件显式返回 JSX 或 null，避免无意义 Fragment 和过深嵌套。操作用 Button、导航用链接并支持键盘；img 提供有意义的 alt（装饰图为空），iframe 提供 title，新窗口外链按需设 rel="noopener noreferrer"。不修改受管 HTML 根文件。使用当前 React API 和 ref，不新增字符串 ref、findDOMNode 或废弃生命周期。 |
| `FE-FORM` | 页面 | 创建/编辑明确初值；initialValues 仅用于初始化，切换记录及关闭重开时重置或重建表单，不能假定其随 props 自动同步。校验后在 onFinish 调用业务 API，提交中禁用重复操作及切换/关闭，失败保留输入并安全反馈，成功后刷新列表。联动字段隐藏时按契约清理或排除旧值（可用 preserve=false），步骤表单先校验当前步骤再推进。按需见 `references/component-patterns.md` 的 ModalForm、DrawerForm / StepsForm、ProFormDependency。 |
| `FE-SELECTION` | 页面 | 行选择使用稳定业务 ID，提交 ID 必须来自当前有效勾选项；分页、筛选、刷新后按需求清理或重新核对，不能把旧行对象当作当前数据。默认不跨页保留选择，批量按钮在无选择、查询中或提交中禁用，操作成功后清理选择并刷新，失败反馈并保留可重试状态。跨页选择须有明确契约。按需见 `references/component-patterns.md` 的行选择与批量操作。 |

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
