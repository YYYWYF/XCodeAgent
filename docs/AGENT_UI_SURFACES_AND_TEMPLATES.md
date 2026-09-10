# Agent 页面交互载体与 UI 模板设计

> 状态：前端模板范围批次 1—7 已实现并完成 Electron 设计阶段验收；批次 8—12 不在本次交付范围
>
> 确认日期：2026-09-07
>
> 本次交付范围：ProductPlan/UiManifest 的页面载体元数据、UiDesign 模板选择、独立会话页模板、普通页面浮层设计生成与 Electron 设计阶段验收
>
> 明确排除：生成应用运行时聊天、Java Gateway、Agent Runtime、Build DAG 与生产端到端验收

## 1. 文档目标

本文定义业务 Agent 在生成应用页面中的两种正式交互载体，并明确它们在 RequirementSpec、ProductPlan、UiDesign、TechnicalPlan、Build 和运行时中的归属。

核心结论是：

- 独立网页版问答是一个真正的页面模板，称为 `standalone_page`；
- 页面气泡不是第二个页面模板，而是挂载到普通业务页面的增强能力，称为 `floating_panel`；
- 两种载体在设计层采用一致的问答交互语言；未来进入生产实现时再复用同一个对话核心与运行协议，本次不实现运行时链路。

本文是现有 Agent 产品规划、Contract、Runtime 和 Workbench 设计的补充，不替代：

- [产品规划、UI 设计与技术规划分层](./PRODUCT_UI_TECHNICAL_PLANNING.md)；
- [Agent Contract 重设计](./AGENT_CONTRACT_REDESIGN.md)；
- [Agent Runtime 模板仓库与初始化流程设计](./AGENT_RUNTIME_TEMPLATE_AND_INITIALIZATION.md)；
- [Agent Development Workbench](./AGENT_DEVELOPMENT_WORKBENCH.md)；
- [智能体开发流程集成规范](./AGENT_DEVELOPMENT_INTEGRATION_RULES.md)。

## 2. 当前基线与问题

当前正式页面模板位于 `Frontend/src/renderer/src/templates/`，通过 `manifest.json` 自动发现。已有模板均为完整业务页面：

- `commonTable`：通用列表查询；
- `multiForm`：多分组表单；
- `tabsTable`：多标签页表格。

用户选择模板后，模板源码只作为布局和组件风格参考；UiDesign 生成器必须依据已确认 ProductPlan 重写业务语义，并继续执行 action、information item 和本地界面效果的确定性校验。

批次 0 确认时的缺口：

1. 模板 Manifest 没有业务页面与 Agent 页面适用范围，新增 Agent 模板后会被所有页面看到；
2. ProductPlan `agents[].pageActionBindings[]` 当时只有 `pageId` 和 `actionIds`，不能表达独立会话页与普通页面浮层的区别；该项已由批次 1 闭合；
3. UiManifest 不能证明设计稿实现了正确的 Agent、页面入口和交互载体；
4. UiDesign 单页生成上下文目前以页面产品事实为主，不能确定性生成 Agent 浮层；
5. 本次只交付前端模板与设计稿能力，不把静态 Mock 设计稿描述为真实 Agent 对话运行时。

## 3. 设计原则

1. **产品语义与视觉布局分离**：ProductPlan 决定页面使用哪种 Agent 载体；UiDesign 决定具体布局、位置、尺寸、响应式和视觉状态。
2. **模板与增强能力分离**：`standalone_page` 进入页面模板目录；`floating_panel` 不作为模板卡片，而是叠加到已选业务模板生成的页面中。
3. **一致的交互模型**：独立页和浮层只提供不同外壳，消息、Tool、审批、停止、重试等状态使用一致的视觉语义；真实 thread 和传输协议留给后续生产实现。
4. **稳定引用**：所有页面、Agent、action 和上下文引用使用已确认的稳定 ID，禁止按页面名、按钮文案、路由或组件名猜测。
5. **不越过运行时边界**：模板不得接入网络、凭据或真实 Runtime，只使用静态 Mock 与本地状态。
6. **设计稿不冒充生产实现**：UiDesign 使用 Mock 和本地状态表达交互；真实网络、会话、权限和 Runtime 在 Build 后验证。
7. **当前契约唯一版本**：契约升级时同步更新生产者、消费者、测试和文档，不增加旧版本 reader、fallback alias、双写或迁移逻辑。
8. **可替换视图层**：当前模板保持 React 18、Ant Design 5 设计运行时和 Pro Components；未来内部组件库替换不改变 ProductPlan、UiManifest、AG-UI、Gateway 或 Runtime 契约。

## 4. Agent Surface 产品契约

### 4.1 字段位置

Surface 属于页面如何向用户提供 Agent 能力的产品事实，应进入 ProductPlan `agents[].pageActionBindings[]`，不进入 TechnicalPlan，也不由 UiDesign 或代码生成器反向推断。

目标结构：

```json
{
  "pageId": "order_list",
  "actionIds": ["open_order_assistant"],
  "surface": {
    "type": "floating_panel",
    "enabled": true,
    "contextItemIds": ["selected_order_ids"]
  }
}
```

字段规则：

- RequirementSpec `agent_requirements[].entryPageIds` 必须列出所有有资格向用户提供该 Agent 入口的页面，包括独立会话页和可承载浮窗的普通业务页；不得只记录独立会话页，也不得用空数组代表“全部页面”；
- 用户提出“所有页面”或“其他页面”时，只覆盖该 Agent 明确面向的角色实际使用的页面；管理员专属、系统或其他角色页面默认不绑定，角色范围与页面范围冲突时必须先澄清；
- 未进入 `entryPageIds` 的页面保持普通页面，不生成 Agent action、Surface、浮窗或集成开关；
- `surface.type` 仅允许 `standalone_page` 或 `floating_panel`；
- `surface.enabled` 必须为布尔值；模型识别出的候选默认开启，`standalone_page` 必须始终为 `true`，只有 `floating_panel` 可以在联合确认前由用户关闭；
- 关闭浮窗只从下游投影移除该页的 Agent action、launcher 和 panel，不改变该页面其他信息项、业务 action、导航、状态或模板能力；
- `surface.contextItemIds` 是去重字符串数组，只能引用同一 ProductPlan 页面已有的 `information_items[].itemId`；
- `interaction.mode` 继续表达 `conversation` 等交互语义，不承担页面展现职责；
- ProductPlan 页面 action 继续表示用户可见的“打开或使用 Agent”行为，并由 `actionIds` 稳定绑定；
- 一个 Agent 可以绑定多个页面，每个页面绑定独立声明 Surface；
- 第一版一个页面最多绑定一个 `floating_panel` Agent，一个 `standalone_page` 页面最多有一个主 Agent；
- `entryPageIds=[]` 的应用级 Agent 不在本设计第一版可视化挂载范围内，保留现有非页面入口语义，不为它推断模板。

`surface.type/contextItemIds` 在 `product-plan.v7` 引入；加入严格 `surface.enabled` 与用户选择后，当前正式版本升级为 `product-plan.v8`，不读取或迁移旧版本。

### 4.2 用户选择位置与门禁

浮窗开关放在 RequirementSpec/ProductPlan 联合确认右侧面板的“智能体”页签内，并与每条“页面操作绑定”同行显示。该位置能够同时展示页面名称、路径、Surface 类型、上下文白名单和启停状态，避免用户离开需求确认流程后再修改已确认产品事实。

- 候选 `floating_panel` 显示“集成智能体浮窗”开关，默认开启；
- `standalone_page` 显示“独立页面固定启用”，不提供开关；
- ProductPlan 为 `pending_user_confirmation` 时可编辑，确认后只读；
- 保存沿用 `/application-page-planning/run` AG-UI 流程，并同步 ProductPlan 草稿 JSON 与 Markdown；
- 未识别为候选的页面不显示开关，也不会被补加浮窗。

### 4.3 两种 Surface

| Surface | 页面身份 | 模板选择 | 页面主体 | 页面上下文 |
| --- | --- | --- | --- | --- |
| `standalone_page` | 专门用于 Agent 对话的页面 | 只显示支持独立 Agent 页的模板 | 对话本身 | 通常为空或由页面明确声明 |
| `floating_panel` | 原有列表、表单、详情等业务页面 | 继续显示原业务模板 | 必须完整保留 | 仅允许 `contextItemIds` 白名单 |

## 5. 页面模板 Manifest

页面模板 Manifest 增加明确适用范围：

```json
{
  "id": "agentConversation",
  "name": "智能体会话页",
  "description": "提供会话历史、消息流、状态反馈与固定输入区的独立智能体页面",
  "category": "agent",
  "supportedSurfaces": ["standalone_page"],
  "previewImage": "..."
}
```

现有三个业务模板统一声明：

```json
{
  "category": "business",
  "supportedSurfaces": ["standard_page", "floating_panel"]
}
```

选择规则：

1. 无 Agent 绑定的普通页面只显示 `standard_page` 兼容模板；
2. `standalone_page` 只显示独立 Agent 页面模板；
3. `floating_panel` 继续显示业务模板，并在模板选择和页面卡片中显示“包含智能体浮窗”的只读提示；
4. 模板适用性由当前已确认 ProductPlan 投影，前端不能提交模板分类来改变服务端事实；
5. 未知 category 或 Surface 采用安全拒绝，不回退显示全部模板。

## 6. 独立会话页模板

新增 `Frontend/src/renderer/src/templates/agentConversation/`，模板使用静态 Mock 和本地状态表达以下内容：

### 6.1 桌面结构

- 左侧会话栏：新建会话、搜索、最近会话、重命名和删除/归档入口；
- 主区顶栏：Agent 名称、用途、运行状态和可选的新会话操作；
- 消息区：用户消息、Agent 回复、Tool 调用摘要、审批、成功、失败和可重试状态；
- 空状态：Agent 能力说明和推荐问题；
- 固定 Composer：输入、发送、停止和 Contract 允许时才出现的附件入口。

### 6.2 移动结构

- 会话栏收进 Drawer；
- 主消息区占满可用宽度；
- Composer 避开安全区域并保持键盘可用；
- 状态卡和 Tool 摘要在窄屏中纵向排列，不产生水平滚动。

### 6.3 能力边界

- 模板可以展示会话历史、附件、Tool 或审批的视觉候选，但生产 Build 只能启用 Agent Contract 和 Runtime 已声明、已实现的能力；
- 不复制 ChatGPT 的模型商店、模型选择、账号菜单或其他与当前业务 Agent 无关的产品功能；
- 不展示模型隐藏思维链、完整 System Prompt、凭据、无界 Tool 原文或内部拓扑。

## 7. 普通页面浮动 Agent

`floating_panel` 由 UiDesign 在业务页面原主体之外生成，不建立模板卡片。

### 7.1 桌面交互

- 收起态为有可访问名称的 Agent 图标按钮；
- 点击打开迷你问答面板，拖动不触发点击；
- 图标只能在可视区域和安全边距内移动，松手后吸附最近的允许边缘；
- 初始位置由 UiDesign 决定，可位于左下或右下；
- 位置只保存无敏感含义的客户端偏好，读取后仍重新夹紧到当前视口；
- 面板支持关闭、最小化、停止和重试；存在独立会话页时可以提供最大化跳转，没有对应页面时不显示该入口。

### 7.2 移动交互

- 不提供自由拖动，避免与页面滚动、系统返回和浏览器手势冲突；
- 使用固定角落入口和受视口约束的 Card，不引入浮窗 Drawer；
- Card 关闭后把焦点还给入口按钮；
- 输入法弹出时 Composer 保持可见。

### 7.3 页面上下文与安全

- 前端只提交 `pageId`、当前 Agent action 和 `contextItemIds` 允许的值；
- 页面上下文属于不可信用户输入，Java Gateway 校验页面、Agent、action、Contract Hash 和上下文 Schema；
- user、tenant、scope、权限和内部 token 只能由 Java Gateway 注入；
- 不扫描完整 DOM，不复制未声明页面数据，不允许客户端字段覆盖可信身份；
- Tool 在执行前继续按当前用户、租户和资源权限校验，页面上下文本身不授予权限。

## 8. UiManifest v5

UiManifest 增加 Agent Surface 的引用与验证证据：

```json
{
  "schema_version": "ui-manifest.v5",
  "pages": [
    {
      "pageId": "order_list",
      "bindings": {
        "actions": [],
        "information_items": [],
        "agent_surfaces": [
          {
            "agentId": "order_assistant",
            "type": "floating_panel",
            "actionIds": ["open_order_assistant"],
            "contextItemIds": ["selected_order_ids"],
            "controlIds": [
              "order_assistant-launcher",
              "order_assistant-panel"
            ],
            "parts": [
              {"part": "launcher", "controlId": "order_assistant-launcher"},
              {"part": "panel", "controlId": "order_assistant-panel"}
            ],
            "template": {
              "module": "@xcodeagent/agent-ui-design",
              "component": "AgentFloatingPanelTemplate",
              "version": "agent-ui.v1",
              "configSha256": "..."
            }
          }
        ]
      }
    }
  ]
}
```

TSX 使用平台固定组件和静态 `configJson` 提供可复算证据：

```tsx
import { AgentFloatingPanelTemplate } from '@xcodeagent/agent-ui-design'

<AgentFloatingPanelTemplate configJson="{&quot;templateVersion&quot;:&quot;agent-ui.v1&quot;,...}" />
```

确定性校验至少覆盖：

1. 固定组件导入来源、组件名和 `agent-ui.v1` 完全一致；
2. Agent ID、Surface、action 和 context 白名单与 ProductPlan 完全一致；
3. `standalone_page` 的消息区、状态区和 Composer 由固定组件版本保证；
4. `floating_panel` 的 launcher、panel、拖动/吸附和移动端固定 Card 由固定组件版本保证；
5. 业务页面原 action 和 information item 绑定继续完整；
6. 不允许未知 Agent、未知 action、多余 Surface、自制第二套聊天 DOM 或无归属交互；
7. ProductPlan Surface 或页面上下文白名单变化后，配置摘要、UiManifest 和下游 TechnicalPlan/Build 证据失效。

## 9. 设计稿与生成应用的边界

### 9.1 UiDesign

UiDesign 模板仍为自包含 React TSX：

- 模板源码写 `import ... from 'antd'`；
- DesignRenderer 在隔离 iframe 中把 `antd` 映射到 npm alias `antd5`；
- 使用 React 18、Ant Design 5、`@ant-design/pro-components`、`@ant-design/icons` 和已登记的 `dayjs`；
- 不调用 API、`fetch`、`axios`、Runtime 或真实会话存储；
- 不新增 UI 框架或聊天组件依赖。

XCodeAgent 主 Renderer 继续使用 Ant Design 4。主应用组件库与设计稿 iframe 运行时是两个已有边界，不能在本功能中混为一套依赖或做无关升级。

### 9.2 后续生成应用（不在本次范围）

真实生成应用使用共享模块：

```text
useAgentConversation
  └── AG-UI run/thread/message/tool/approval/error state
AgentChatCore
  ├── AgentMessageList
  ├── AgentRunStatus
  └── AgentComposer
AgentConversationPage
AgentFloatingLauncher
AgentMiniChatPanel
AgentMobileHistoryDrawer
```

业务状态、AG-UI 和视图组件分离。当前视图使用现有前端模板的 Ant Design/Pro Components；未来公司内网组件库明确后，只替换视图组件和主题映射，不修改上游产品契约或运行协议。

不得让每个绑定页面复制一份不同的 AgentChatCore。共享模块应由正式前端模板提供，或由平台按确认的 Build 任务确定性生成一次；最终方式必须在修改外部 `frontend-template` 仓库前完成只读核对并单独确认。

## 10. 后续运行链路（不在本次范围）

```text
独立会话页 / 普通页面浮层
  -> 共享 AgentChatCore
  -> Java Agent Gateway（公开 AG-UI SSE）
  -> Python Agent Runtime（内部 AG-UI）
  -> DeepAgents
  -> Java Tool Gateway
  -> 业务 API / 数据源
```

两种 Surface 必须具有相同的标准事件生命周期：run start、assistant message、结构化 result/error、state snapshot/delta 和唯一 run finish。新建会话、搜索、切换、重命名、归档或删除等产品动作也必须沿用正式 AG-UI 边界，不新增普通 REST 产品接口或手写 SSE parser。

## 11. 实施批次

### 批次 0：正式设计文档

- 新增本文；
- 更新 Codebase Index；
- 更新实现状态台账；
- 不修改运行代码。

### 批次 1：ProductPlan v7 Surface 契约

状态：已实现。

- 增加 `surface.type/contextItemIds`；
- 更新模型提示、规范化、严格验证、Markdown 同步和 Hash；
- 增加普通应用、两种 Surface、非法上下文和冲突回归；
- 普通应用仍固定 `agents: []`。

### 批次 1A：ProductPlan v8 浮窗启停选择

状态：已实现，等待用户自行验证。

- `surface` 增加严格布尔字段 `enabled`，模型识别出的候选默认开启；
- 联合确认右侧“智能体 → 页面操作绑定”允许启停 `floating_panel`，`standalone_page` 固定开启；
- 保存复用 `/application-page-planning/run` AG-UI 生命周期，同时更新 ProductPlan 草稿 JSON 与 Markdown；
- 正式 ProductPlan 保留关闭记录，下游 UiDesign、TechnicalPlan 页面操作和 PageImplementationContract 只消费启用投影；
- 未识别页面没有开关，关闭候选页面也不影响其普通业务能力。

### 批次 2：工作台严格类型与产物展示

状态：已实现。

- 更新前端 ProductPlan 类型和只读投影；
- 展示 Surface、入口页面和上下文白名单；
- 未知值安全回退，不允许客户端回填服务端事实。

### 批次 3：UiManifest v4

状态：已实现。

- 增加 `agent_surfaces` 检查和落盘证据；
- 校验 ProductPlan、TSX 与 Manifest 一致性；
- 建立上游变更后的确定性失效。

### 批次 4：模板分类和选择规则

状态：已实现。

- Manifest 增加 category 和 supportedSurfaces；
- 现有三个模板声明为 business；
- 模板选择器按当前页面事实过滤；
- 浮层只显示增强提示，不出现伪页面模板。

### 批次 5：独立会话页模板

状态：已实现。

- 新增 `agentConversation` 模板；
- 完成桌面、移动、明暗主题和空/错/加载/Tool/审批视觉状态；
- 只使用静态 Mock 和当前设计稿运行时依赖。

### 批次 6：浮动 Agent 设计生成

状态：已实现。

- UiDesign 单页任务接收当前页面相关 Agent Binding；
- 业务模板适配后叠加浮层；
- PC 拖动和移动端固定 Card 进入设计稿；
- 保留原业务 action 和 information item 完整性。

### 批次 7：Electron 设计阶段验收

状态：已完成；只验证设计阶段，没有进入技术规划、Build 或运行时。

- 已在运行中的 Electron 临时应用验证独立会话页和订单列表浮层；普通业务模板的无 Agent 兼容性由模板筛选与兼容性自动化覆盖，本批没有伪造第三个产品页面；
- 独立页在窄预览使用会话历史 Drawer，在宽预览使用固定历史侧栏；明暗主题及正常、加载、错误、空状态均已实机切换；
- 订单页保留表格、筛选、详情与选中订单上下文，浮动入口实机完成拖动、边缘吸附、打开/关闭迷你会话面板，并覆盖正常、加载、错误、空状态；
- 320/768/1024/1440 响应式规则由模板源码、生成提示和自动化断言覆盖；Electron 通过拖动预览分隔线跨越 768px 分支验证窄/宽真实布局，不把 Vite 浏览器作为替代证据；
- 两页当时的 UiManifest product action、information item、无越界业务 UI、Agent Surface 和必需部件检查全部通过；当前实现已升级为 `ui-manifest.v5` 固定模板证据，旧版本不兼容读取。
- 2026-09-11 补充回归修复普通状态消息栈白屏；固定浮窗在 1440/1024/768/767/320 Electron 窗口中覆盖亮暗主题、正常/停止交互、Escape、焦点返回和无 Agent 网络请求，其余状态由聊天核心确定性渲染测试覆盖。按用户确认，窄屏浮窗继续使用受视口约束 Card，不再使用 Drawer；独立页历史列表 Drawer 不受影响，同时两种 Surface 都移除仅供预览的手工状态切换条。

### 批次 8：生成应用共享 Agent 前端模块

状态：按用户确认不在本次前端模板交付范围。

- 在正式前端模板中提供共享 AG-UI Hook 和展示组件；
- 独立页和浮层只组合不同外壳；
- 不增加第二个状态源或聊天依赖；
- 外部仓库修改前重新核对规范并单独确认。

### 批次 9：Agent Runtime 会话与历史

状态：按用户确认不在本次前端模板交付范围。

- 增加 thread 元数据、新建、列表、搜索、切换、重命名、归档/删除；
- 按 user、tenant、application、agentId 隔离；
- checkpoint 与交互恢复保持一致；
- 在独立 `agent-runtime-template` 仓库实施并单独发布验证。

### 批次 10：Java Gateway 与页面上下文

状态：按用户确认不在本次前端模板交付范围。

- 转发对话和会话管理 AG-UI；
- 校验 Contract、页面、action 和上下文 Schema；
- 注入可信身份和权限上下文；
- 覆盖取消、断线和安全错误终态。

### 批次 11：Build DAG、生成提示和确定性验收

状态：按用户确认不在本次前端模板交付范围。

- 把 Surface 和上下文白名单投射给 Frontend/Backend Build owner；
- 共享聊天核心只生成一次；
- 建立 Runtime、Gateway、页面入口和业务 Endpoint 依赖；
- 增加禁止直连 sidecar、禁止手写 SSE、页面主体保留等检查。

### 批次 12：完整端到端验收

状态：按用户确认不在本次前端模板交付范围。

- 从新建应用完成 ProductPlan、UiDesign、TechnicalPlan、Build、Testing、Review、Launch 和 Acceptance；
- 覆盖独立页、浮层、会话历史、连续追问、Tool、审批、错误恢复和权限隔离；
- 证据绑定 ProductPlan Hash、Agent Contract Hash、Build Plan digest、Run ID、真实 Diff 和用户决定；
- 未完成真实 Electron 和生成应用证据前，不标记为端到端完成。

### 后续独立批次：公司内网组件库迁移

- 取得内部组件库 API、主题、表单、弹窗和图标规范后建立组件映射；
- 先替换 Agent 视图组件，保留 Hook、AG-UI 和契约；
- 再迁移其他页面模板；
- 删除 Ant Design 依赖前执行全模板编译和视觉回归；
- 当前阶段不预建无法验证的通用适配层。

## 12. 验证策略

每个代码批次至少执行其聚焦测试，并遵循仓库统一验证要求。本次只执行前端模板范围对应的后端契约测试、前端测试/构建和 Electron 设计阶段验收；下列生成应用与运行时检查留给未来另行立项的批次：

```text
后端：相关 unittest + 变更文件 py_compile + /health
前端：相关 Node 测试 + pnpm typecheck + pnpm build
UI：已运行 Electron + 明暗主题 + 桌面/移动尺寸 + 控制台/网络
生成应用：前端 Build + Java 测试/打包 + Agent Runtime pytest/health
最终：现有 Testing -> Code Review -> Launch -> Acceptance
```

文档批次只执行文档一致性检查和 `git diff --check`，不把未运行的代码验证描述为通过。

## 13. 停止条件

出现以下任一情况必须停止当前批次并重新确认：

- Surface 契约要求改变普通页面/API/实体默认流程；
- Agent 模板无法与业务模板确定性隔离；
- 浮层需要替换、删除或弱化原业务页面主体；
- 生成前端必须直连 Python Runtime；
- 会话管理只能通过普通 REST、手写 SSE 或第二套会话状态实现；
- 需要新增生产依赖、扩大凭据/网络/文件权限或修改外部模板仓库；
- 同一页面需要同时挂载多个浮动 Agent；
- 应用级 `entryPageIds=[]` Agent 需要进入可视化挂载；
- 内部组件库约束提前进入且与当前 Ant Design 组件结构不兼容；
- 无法建立普通应用、无 Agent 页面、现有模板和现有工作流的回归证据。
