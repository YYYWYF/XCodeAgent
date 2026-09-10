# Agent UI 固定模板标准化实施计划

> 状态：批次 0—8 已实施；固定 UI + Mock Adapter 已验证，真实 AG-UI/Gateway/Runtime 联调仍待后续单独实施
>
> 计划日期：2026-09-10
>
> 适用范围：业务 Agent 的独立会话页、普通业务页浮动会话面板，以及两种形态在 UiDesign 和生成应用前端开发阶段的复用
>
> 当前交付边界：固定 UI 模板、专用内置 Skill、设计阶段确定性生成、开发阶段 Mock 数据实现与相应验证
>
> 后续交付边界：Java Gateway、真实 AG-UI 前后端调用、Python Agent Runtime 联调、会话持久化和完整端到端验收

## 1. 文档目的

本文把已经确认的 Agent UI 模板优化方向拆成可以逐批实施、逐批验证和逐批回滚的任务。后续实施必须以本文为范围依据，每个批次开始前重新检查当前代码、未提交改动和外部模板仓库状态，并在用户确认该批范围后再编辑代码。

本文补充而不替代以下正式文档：

- [智能体开发流程集成规范](./AGENT_DEVELOPMENT_INTEGRATION_RULES.md)；
- [智能体开发流程实现状态](./AGENT_DEVELOPMENT_IMPLEMENTATION_STATUS.md)；
- [Agent 页面交互载体与 UI 模板设计](./AGENT_UI_SURFACES_AND_TEMPLATES.md)；
- [产品规划、UI 设计与技术规划分层](./PRODUCT_UI_TECHNICAL_PLANNING.md)；
- [Agent Runtime 模板仓库与初始化流程设计](./AGENT_RUNTIME_TEMPLATE_AND_INITIALIZATION.md)。

如果本文与上述强制规范、Canonical 文档或当前代码事实发生冲突，必须停止实施并重新确认，不能用兼容分支同时保留两套规则。

## 2. 需求理解与完成定义

### 2.1 需求目标

当前模型在生成 Agent UI 时拥有过大的自由度，即使 Prompt 描述了独立会话页和浮窗的交互细节，仍可能在布局、组件层级、消息形态、移动端行为、状态展示和主题实现上产生随机差异。

本次优化目标是：

1. 写好一套固定的独立页面对话模板；
2. 写好一套固定的普通业务页面浮窗对话模板；
3. UiDesign 阶段只允许模型根据已确认应用需求填写有限配置或组合模板，不再重新设计聊天核心；
4. 生成应用前端开发阶段继续复用固定模板，只实现业务配置和页面组合；
5. 当前真实后端链路未完成时，开发阶段使用明确的 Mock Adapter 和假数据；
6. 后续 Java Gateway 和 Agent Runtime 完成后，只替换数据/传输适配层，不重新实现页面 UI；
7. 用专用内置 Skill 记录固定部分、可变部分、上下游契约、Mock 边界和未来联调要求；
8. 用确定性校验证明模型确实复用了模板，不能仅依赖模型自述或 Prompt 约束。

### 2.2 完成定义

当前阶段只有同时满足以下条件，才能称为“Agent UI 固定模板与 Mock 开发链路完成”：

- 独立页和浮窗的核心 UI 均来自固定代码组件；
- 两种形态共用一致的消息、运行状态、Tool、审批、错误和 Composer 视觉语义；
- 模型只能填写正式契约允许的配置，不再自由生成聊天核心；
- UiDesign 生成稿仍通过 ProductPlan action、information item 和 Agent Surface 确定性校验；
- 生成应用前端通过固定组件和 Mock Adapter 展示完整交互状态；
- 普通无 Agent 页面及现有三个业务模板行为保持不变；
- 自动化、前端构建和 Electron 运行态验证通过；
- 文档明确标记真实 AG-UI、Gateway、Runtime、Launch 和 Acceptance 仍未完成。

只存在 Skill、Prompt、Mock、截图或静态设计稿，不得称为真实 Agent 前后端联调完成。

## 3. 当前代码基线

### 3.1 已存在能力

- `Frontend/src/renderer/src/templates/agentConversation/` 已包含独立会话页模板和模板 Manifest；
- `Frontend/src/renderer/src/service/templateService.ts` 通过 `import.meta.glob` 自动发现页面模板；
- `Frontend/src/renderer/src/service/templateCompatibility.ts` 按 `standard_page`、`floating_panel`、`standalone_page` 过滤模板；
- `Backend/app/services/ui_design_generation_pool.py` 在用户选择模板后把模板作为视觉参考交给模型适配；
- `Backend/app/services/ui_design_generator.py` 已包含两种 Agent Surface 的详细 Prompt 规则；
- `Backend/app/services/ui_design_agent_surfaces.py` 已投影并校验 Agent Surface 静态证据；
- `Backend/app/services/ui_design_manifest.py` 使用 `ui-manifest.v4` 保存页面 action、information item 和 Agent Surface 证据；
- `Backend/app/agents/frontend/generator.py` 在开发阶段读取已确认 UiDesign 稿，并要求 Frontend Agent 按设计稿还原页面；
- `Backend/app/builtin_skills/` 已具备内置 Skill 注册、只读挂载、打包和完整性检查机制。

### 3.2 当前缺口

1. 独立会话页虽然已有模板源码，但选中模板后仍由模型重写完整 TSX，结构仍可能漂移；
2. 浮窗没有固定代码组件，主要依赖 Prompt 让模型现场生成；
3. UiDesign 的 Agent Surface 规则与普通页面生成规则堆叠在 `ui_design_generator.py`，缺少独立、可复用的模板契约；
4. 开发阶段只读取 UiDesign 和通用前端 Skill，没有强制读取 Agent UI 专用 Skill；
5. 尚未由 XCodeAgent 按已启用 Agent Surface 向生成应用条件式注入共享 `AgentChatCore`、独立页外壳、浮窗外壳和稳定 Adapter 接口；
6. 当前 Java Gateway 和完整后端调用尚未完成，前端不能真实实现 AG-UI 联调；
7. 缺少“固定模板确实被使用”的静态检查和模板版本证据；
8. 缺少 Mock 前端完成与真实 Agent 端到端完成之间的质量状态边界。

### 3.3 当前工作区保护

计划形成时工作区存在大量与 Agent ProductPlan、UiDesign、TechnicalPlan、工作台和文档相关的未提交改动。后续每个批次必须：

- 先运行 `git status --short`；
- 对将要修改的文件运行定向 `git diff -- <paths>`；
- 把现有变化视为用户已有工作；
- 使用精确补丁合并，不整文件覆盖；
- 不清理、不还原、不暂存、不提交未授权文件；
- 发现同一区域的新变化时暂停并重新检查计划。

## 4. 架构决策

### 4.1 Skill 不能代替固定代码

仅增加一个更长的 Prompt 或 Skill 不能真正消除随机性。Skill 负责告诉模型如何使用模板、哪些内容允许修改以及何时停止；真正固定的布局、交互和状态结构必须由代码组件提供，并由静态校验保证模型没有绕过这些组件。

### 4.2 独立页与浮窗保持不同产品归属

- `standalone_page` 是一个页面模板；
- `floating_panel` 是普通业务页面上的增强能力，不增加第二个页面模板卡片；
- 两者共享聊天核心，但不共享页面外壳；
- 浮窗不得删除、替换或弱化业务页面主体。

### 4.3 模板采用固定结构加配置槽位

建议定义一个严格的 `AgentUiTemplateConfig`。模型只能填写以下槽位：

- `agentId`；
- Agent 名称和职责；
- 已确认能力及推荐问题；
- ProductPlan action ID；
- `contextItemIds` 白名单及其展示标签；
- Contract 已声明能力对应的展示开关；
- 与业务需求一致的示例值和 Mock 回复。

以下内容固定，不允许模型重写：

- 消息列表、左右气泡和作者元信息；
- Composer、发送、停止、错误和重试结构；
- Tool 摘要和审批卡片结构；
- 桌面会话栏和移动 Drawer；
- 浮窗入口、拖动、视口夹紧、边缘吸附和移动端受视口约束 Card；
- 明暗主题、焦点管理、键盘行为和响应式断点；
- Mock Adapter 与未来真实 Adapter 的接口形状。

### 4.4 UiDesign 与生成应用使用不同实现、相同契约

UiDesign 运行在 XCodeAgent 的隔离设计 iframe 中，使用 React 18、antd5 和 Pro Components；生成应用的基础工程来自外部 `frontend-template`。批次 0 已只读确认该仓库的 `main`、`auth` 分支、真实目录、组件库版本、主题方式和测试命令，但 Agent UI 正式组件不永久写入这两个基础分支，而由 XCodeAgent 在模板下载完成后按已确认 ProductPlan 中的启用 Agent Surface 条件式注入。两侧不强求复制同一份源码，但必须共享：

- 同一配置字段；
- 同一 Surface 语义；
- 同一状态集合；
- 同一 action/context 白名单；
- 可由测试比较的结构不变量。

没有启用 Agent Surface 的应用不得获得 Agent UI 组件、类型或 Mock Adapter。条件式注入必须同时适用于当前 `main` 和 `auth` 模板变体，不修改二者的路由、Provider、请求封装、依赖或全局样式。

### 4.5 当前阶段只实现 Mock Adapter

当前开发阶段不得伪造真实 AG-UI 调用。生成应用前端只实现 `MockAgentConversationAdapter`，并明确显示模拟状态。真实 Gateway 完成后再用当前契约替换为 `AgUiAgentConversationAdapter`。

当前阶段不得为了显示“全流程通过”而弱化真实 Endpoint、Launch 或 Acceptance 门禁。Mock UI 可以作为独立前端交付物验证，但不能成为真实 Agent Runtime 完成证据。

### 4.6 不建立平行工作流

本功能继续进入现有：

```text
RequirementSpec
  -> ProductPlan
  -> UiDesign
  -> TechnicalPlan
  -> 模板初始化
  -> Build DAG
  -> Unit/Integration Test
  -> Code Review
  -> Launch
  -> Acceptance
```

不得新增第二套聊天工作流、状态事实源、Diff、Preview、测试或验收流程。

## 5. 目标组件与数据流

```text
ProductPlan Agent Surface
        │
        ├── standalone_page
        │      └── AgentConversationTemplate
        │
        └── floating_panel
               └── AgentFloatingPanelTemplate
                         │
                         ▼
              AgentUiTemplateConfig
              - Agent identity
              - capabilities
              - action binding
              - context whitelist
              - display states
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
UiDesign 固定组件            XCodeAgent 内置生成资产
本地静态 Mock                         │
                                  按启用 Surface 条件注入
                                        │
                                        ▼
                                生成应用固定组件
                                MockConversationAdapter
          │                             │
          └──────── 后续联调 ───────────┘
                         │
                 AgUiConversationAdapter
                         │
             Java Gateway -> Agent Runtime
```

## 6. 分批实施计划

### 批次 0：实施前基线与外部模板核对

**目标**

建立可安全修改的基线，并确定外部 `frontend-template` 的真实结构，避免在当前仓库中凭空设计不存在的生成应用目录或依赖。

**只读动作**

1. 重新检查当前分支、HEAD 和 `git status --short`；
2. 阅读本计划引用的正式文档及自上次检查后变化的相关文档；
3. 定向检查计划触及文件的现有 Diff；
4. 只读检查外部 `frontend-template` 的当前 `main`、`auth` 分支：组件库版本、主题、路由、Provider、API 层、测试命令和可注入目录；
5. 确认外部模板是否已具备可复用组件目录和 Mock 数据约定；
6. 记录准确文件路径后再确认批次 1—5 的最终范围。

**验收标准**

- 当前未提交改动的归属和冲突文件已经列明；
- 外部模板两个当前变体的真实文件路径、依赖兼容性和验证命令已经确认；
- 没有写入代码或外部仓库；
- 发现架构冲突时停止，不进入下一批。

**依赖**：无。

### 批次 1：新增 Agent UI 专用内置 Skill

**目标**

建立 UI 设计和生成应用前端共同遵守的固定模板规则，并接入现有内置 Skill 打包与只读挂载机制。

**建议文件**

```text
Backend/app/builtin_skills/agent-ui-surface-template/SKILL.md
Backend/app/builtin_skills/agent-ui-surface-template/references/ui-design-stage.md
Backend/app/builtin_skills/agent-ui-surface-template/references/frontend-mock-stage.md
Backend/app/builtin_skills/agent-ui-surface-template/references/ag-ui-integration-stage.md
Backend/app/services/builtin_skills.py
Backend/tests/test_builtin_skills.py
```

**Skill 内容边界**

- 入口只保留触发条件、核心不变量和三种阶段路由；
- UiDesign 引用说明固定设计组件、配置槽位和静态证据；
- Mock 开发引用说明 Adapter、假数据、模拟状态和禁止网络调用；
- AG-UI 引用只记录未来替换要求，本批不实现真实调用；
- 禁止重复通用 React、Ant Design 和模板文件边界规范；
- 禁止把固定组件源码大段复制进 `SKILL.md`；
- Skill 必须自动可发现，不依赖用户手动选择。

**验收标准**

- Skill 名称建议固定为 `agent-ui-surface-template`；
- `validate_required_builtin_skills()` 能验证全部入口和引用文件；
- 源码运行和冻结打包都能读取 Skill；
- Skill 目录只读挂载给 UiDesign/Frontend Agent；
- 单测证明引用文件存在、入口长度受控且按需读取。

**验证**

```bash
cd Backend
python3 -m unittest tests.test_builtin_skills
python3 -m py_compile app/services/builtin_skills.py
```

**依赖**：批次 0。

### 批次 2：实现 UiDesign 固定 Agent 组件

**目标**

把独立会话页和浮窗的核心视觉、响应式和本地状态逻辑移入确定性设计运行时组件，使模型只能组合组件和填写配置。

**建议文件**

```text
Frontend/src/renderer/design-runtime/agent-ui/types.ts
Frontend/src/renderer/design-runtime/agent-ui/AgentChatCore.tsx
Frontend/src/renderer/design-runtime/agent-ui/AgentConversationTemplate.tsx
Frontend/src/renderer/design-runtime/agent-ui/AgentFloatingPanelTemplate.tsx
Frontend/src/renderer/design-runtime/agent-ui/index.ts
Frontend/src/renderer/design-runtime/antd5-runtime.ts
Frontend/src/renderer/src/components/DesignRenderer/compileTsx.ts
Frontend/scripts/build-design-runtime.mjs
Frontend/src/renderer/public/design-runtime/antd5-runtime.js
```

**实现要求**

- `AgentChatCore` 统一渲染消息、状态、Tool、审批、错误和 Composer；
- `AgentConversationTemplate` 只负责独立页桌面/移动外壳；
- `AgentFloatingPanelTemplate` 只负责页面挂载、拖动、吸附和移动端固定 Card；
- 增加受控虚拟模块，例如 `@xcodeagent/agent-ui-design`；
- `compileTsx` 只把该固定模块映射到设计 iframe 的只读 Runtime；
- 组件使用 antd5 token，不引入硬编码白底或偏离主题的孤立颜色；
- 每个函数和方法补充中文用途注释；
- 活跃文件接近 350 行时继续按组件或 Hook 拆分。

**验收标准**

- 设计稿可通过一个固定组件调用渲染完整独立页或浮窗；
- 两种 Surface 共用 `AgentChatCore`；
- PC 和移动端使用同一 DOM 语义，不为断点复制证据节点；
- 支持正常、空、加载、错误、停止、Tool、审批和成功状态；
- Escape、焦点返回、键盘操作和窄屏无横向滚动；
- 没有 API、凭据、真实会话存储或新生产依赖。

**验证**

```bash
cd Frontend
node scripts/build-design-runtime.mjs
pnpm typecheck
pnpm build
```

**依赖**：批次 1。

### 批次 3：收敛现有独立页模板和模板选择

**目标**

把现有 `agentConversation` 页面模板改成固定组件的薄包装，并保持浮窗是能力而非页面模板。

**建议文件**

```text
Frontend/src/renderer/src/templates/agentConversation/index.tsx
Frontend/src/renderer/src/templates/agentConversation/manifest.json
Frontend/src/renderer/src/templates/agentConversation/preview.svg
Frontend/src/renderer/src/service/templateService.ts
Frontend/src/renderer/src/service/templateCompatibility.ts
Frontend/tests/templateCompatibility.test.ts
```

**实现要求**

- `agentConversation/index.tsx` 只构造示例 `AgentUiTemplateConfig` 并调用固定组件；
- 不再内联一套可被模型自由改写的聊天核心；
- 独立页 Manifest 继续只支持 `standalone_page`；
- 浮窗组件不增加 Manifest，不出现在页面模板候选中；
- 普通业务模板继续支持 `standard_page` 和 `floating_panel`；
- 未知或冲突 Surface 继续安全拒绝。

**验收标准**

- 独立会话页模板仍可被模板列表发现并显示预览；
- 浮窗没有独立模板卡片；
- 三个现有业务模板的候选规则不变；
- 模板源码可以被 DesignRenderer 编译和运行。

**验证**

```bash
cd Frontend
pnpm test:template-compatibility
pnpm typecheck
pnpm build
```

**依赖**：批次 2。

### 检查点 A：固定设计组件与模板目录

批次 1—3 完成后必须先停止并检查：

- 内置 Skill 能被读取和打包；
- 固定设计组件可独立运行；
- 模板选择没有回归；
- 普通业务页面没有出现 Agent 模板；
- 当前工作区未提交改动没有被覆盖；
- Electron 尚未验证时不得进入“已验证”状态。

### 批次 4：UiDesign 生成链路改为模板配置化

**目标**

让 UiDesign 生成器只生成业务页面主体和受控 Agent 配置，不再生成聊天核心。

**建议文件**

```text
Backend/app/services/ui_design_agent_surfaces.py
Backend/app/services/ui_design_generator.py
Backend/app/services/ui_design_generation_pool.py
Backend/app/services/ui_design_manifest.py
Backend/tests/test_ui_design_generator.py
Backend/tests/test_ui_design_manifest.py
```

如 `ui_design_generator.py` 继续增长，应新增聚焦模块，例如：

```text
Backend/app/services/ui_design_agent_template.py
```

**实现要求**

1. 从 ProductPlan 确定性投影 Agent 名称、职责、能力、交互状态、action 和 context 白名单；
2. 只有页面存在 Agent Surface 时才内联新的 Agent UI Skill；
3. `standalone_page` 只生成固定组件配置和页面包装；
4. `floating_panel` 保留业务页面主体生成，但必须调用固定浮窗组件；
5. 模板选择和“换一换”都必须遵守同一固定组件约束；
6. 调整页面时只能改允许的配置和业务布局，不能重写固定组件；
7. 继续使用本地 Mock，不允许 `fetch`、`axios`、Runtime 或真实会话存储；
8. 生成失败仍进入现有 `generation_failed`，不新增平行状态。

**确定性校验**

- 固定组件导入来源正确；
- 页面只存在一个预期 Surface；
- `agentId`、surface type、action ID、context ID 完全一致；
- standalone 必需消息、状态、Composer；
- floating 必需 launcher、panel；
- 页面不存在自制的第二套聊天 DOM；
- 普通业务 action 和 information item 仍完整；
- Mock 控件不伪装成产品 action；
- 不允许未知 Surface、未知上下文或无归属交互。

**UiManifest 版本处理**

- 批次 0 已确认 `ui-manifest.v4` 已进入公开健康协议和当前文档，因此本次实施选择 `ui-manifest.v5`；生产者、消费者、测试和当前契约文档同步更新，不提供 v4 双读或迁移。
- 实施前先确认 `ui-manifest.v4` 是否已经外部发布；
- 若 v4 尚未发布，可在当前 v4 工作中一次性补充固定模板版本证据；
- 若 v4 已发布，升级到 v5，并同步所有生产者、消费者、测试和文档；
- 不增加 v4/v5 双读、旧字段 fallback 或迁移代码。

**验收标准**

- 同一 ProductPlan 多次生成时，Agent UI 的核心结构保持一致；
- 模型仅能改变允许的配置与业务页面主体；
- 无 Agent 页面不加载 Agent UI Skill，也不受新增校验影响；
- UiManifest 能证明使用了正确模板和正确 Surface。

**验证**

```bash
cd Backend
.venv/bin/python -m unittest \
  tests.test_ui_design_generator \
  tests.test_ui_design_manifest \
  tests.test_application_page_planning
python3 -m py_compile \
  app/services/ui_design_agent_surfaces.py \
  app/services/ui_design_generator.py \
  app/services/ui_design_generation_pool.py \
  app/services/ui_design_manifest.py
curl -sS http://127.0.0.1:8000/health
```

**依赖**：批次 1—3。

### 批次 5：生成应用 Agent UI 资产与条件式注入

**目标**

由 XCodeAgent 统一维护生成应用使用的固定 Agent UI 组件和稳定数据接口。模板初始化在外部 `frontend-template` 下载完成后，根据已确认 ProductPlan 中是否存在启用的 Agent Surface 决定是否把这些资产注入生成应用；外部模板仓库本身保持不变。

**建议文件**

内置资产按最终生成路径镜像组织，避免复制时重新拼接源码：

```text
Backend/app/builtin_templates/agent-ui-frontend/
└── src/
    ├── components/AgentConversation/
    │   ├── AgentChatCore.tsx
    │   ├── AgentConversationPage.tsx
    │   ├── AgentFloatingPanel.tsx
    │   ├── AgentMobileChatDrawer.tsx
    │   └── index.ts
    ├── apis/agentConversationMock.ts
    └── typings/agentConversation.ts

Backend/app/services/frontend_agent_ui_scaffold.py
Backend/app/services/application_template_generation.py
Backend/packaging/xcodeagent-backend.spec
Backend/tests/test_frontend_agent_ui_scaffold.py
Backend/tests/test_application_template_generation.py
docs/CODEBASE_INDEX.md
```

注入后的目标固定为：

```text
frontend/src/components/AgentConversation/
frontend/src/apis/agentConversationMock.ts
frontend/src/typings/agentConversation.ts
```

**建议接口**

```ts
interface AgentConversationAdapter {
  listThreads(): Promise<ConversationThread[]>
  createThread(): Promise<ConversationThread>
  loadMessages(threadId: string): Promise<AgentMessage[]>
  sendMessage(input: SendMessageInput): Promise<AgentRunResult>
  stop(runId: string): Promise<void>
  retry(runId: string, retryRunId: string): Promise<AgentRunResult>
  resolveApproval(input: ResolveApprovalInput): Promise<AgentRunResult>
}
```

**实现要求**

- 注入条件只来自当前已确认 ProductPlan：至少存在一个 `enabled=true` 的 `standalone_page` 或 `floating_panel`；不从文案、路由、DOM 或生成代码猜测；
- `main` 和 `auth` 使用同一套内置资产与注入服务，不在外部模板分支维护两份副本；
- 模板生成 Manifest 增加 `agentUiFrontend` 步骤，记录 `required`、`status`、目标文件、模板版本和源码摘要；需要时必须为 `succeeded`，不需要时必须为 `skipped`；
- 注入必须幂等且可审计：目标缺失时原子写入，内容及摘要一致时复用，发现同路径非一致文件时失败并保留现场，不覆盖用户代码；
- 无启用 Agent Surface 的新应用不得创建 Agent UI 目录、类型或 Mock Adapter，也不得改变普通页面初始化和 readiness；
- 当前只实现 `MockAgentConversationAdapter`；
- 假会话、假消息和运行状态集中在 Adapter，不散落在页面；
- 模拟延迟、停止、错误、重试、Tool 和审批；
- 重试必须使用新的 `retryRunId`，使运行中的重试仍可被同一 `stop(runId)` 精确停止；
- 返回值结构稳定，页面不感知未来是 Mock 还是 AG-UI；
- 页面只提交 ProductPlan 允许的上下文；
- 不扫描 DOM，不读取未声明页面数据；
- 不调用 HTTP、Python sidecar 或手写 SSE；
- 页面显示“模拟运行”或同等明确提示；
- 组件只使用 `frontend-template` 两个当前分支已经存在的 React、Ant Design 5 和 Pro Components 能力；不增加依赖；
- 不修改外部模板仓库及其路由骨架、全局 Provider、请求封装、包依赖、配置文件和全局样式；
- 不为既有生成应用增加迁移、清理、旧文件兼容或回填逻辑；本批只定义当前新建应用的注入契约。

**验收标准**

- 独立页和浮窗共用同一个聊天核心；
- 同一应用只存在一份共享组件和 Mock Adapter；
- 普通页面仅组合浮窗外壳，不复制聊天逻辑；
- 有启用 Agent Surface 的 `main`、`auth` 新应用均在 Build 前获得完整且摘要匹配的固定资产；
- 无启用 Agent Surface 的新应用不创建、不渲染也不打包 Agent UI 代码，Manifest 明确记录 `skipped`；
- 重复执行不会改写一致文件，冲突文件不会被覆盖；
- 注入后的 `main`、`auth` 前端工程类型检查、测试和构建通过。

**验证**

```bash
cd Backend
python3 -m unittest \
  tests.test_frontend_agent_ui_scaffold \
  tests.test_application_template_generation
python3 -m py_compile \
  app/services/frontend_agent_ui_scaffold.py \
  app/services/application_template_generation.py
curl -sS http://127.0.0.1:8000/health

# 在临时生成工作区分别验证 main/auth 注入结果；不修改或发布外部模板仓库。
cd <generated-main-or-auth>/frontend
pnpm test
pnpm build
```

**依赖**：批次 0、批次 1—4 的稳定 Surface 与配置契约。

### 批次 6：开发阶段 Frontend Agent 强制复用模板

**目标**

让正式 Build 的 Frontend Agent 在处理 Agent 页面时强制读取专用 Skill、已确认 UiDesign 和生成应用固定组件源码，只完成受控组合与配置。

**建议文件**

```text
Backend/app/agents/frontend/generator.py
Backend/app/services/build_context_resolver.py
Backend/app/agents/main/task_preparer_prompt.py
Backend/tests/test_build_task_planner.py
Backend/tests/test_build_subgraph_scheduler.py
```

是否需要修改 `build_task_planner.py` 或业务验收模块，由批次 0 和批次 5 的真实任务契约检查决定，不能预先扩大范围。

**实现要求**

- 只有当前 page task 包含 Agent Surface 时才要求读取 `agent-ui-surface-template`；
- task context 提供准确的 Agent、Surface、action 和 context 白名单；
- Agent 必须先读取固定组件源码，再修改页面组合；
- 禁止从 UiDesign 复制聊天核心源码；
- 禁止重新创建另一个 `AgentChatCore`；
- 当前阶段使用 Mock Adapter；
- 删除 UiDesign 中 `data-preview-only="true"` 的评审控制器；
- 保留将来 Gateway Endpoint 的稳定引用，但不能生成虚假的真实请求；
- 无 Agent 页面沿用现有 Frontend Agent Prompt 和 Skill 集合。

**Mock 验收边界**

建议增加独立的 Mock UI 检查，至少验证：

- 页面引用固定组件；
- 页面引用固定 Mock Adapter；
- Agent、action 和 context 引用正确；
- 页面没有 `fetch`、`axios`、手写 SSE 或直连 sidecar；
- 普通业务页面主体仍完整。

真实 Gateway Endpoint 消费检查、Launch 和 Acceptance 不得因 Mock UI 通过而自动通过。若当前 BuildScheduler 无法表达“Mock UI 已完成、真实运行集成仍待办”，必须停止并单独设计状态契约，不能通过放宽现有验收规则绕过。

**验收标准**

- Frontend Agent 生成的是模板配置和页面组合，而不是新的聊天实现；
- Mock 页面可以独立编译和交互；
- Build 结果明确区分前端 Mock 完成与真实集成未完成；
- 非 Agent 页面、真实业务 API 页面和 static data 页面没有回归。

**验证**

```bash
cd Backend
python3 -m unittest \
  tests.test_build_task_planner \
  tests.test_build_subgraph_scheduler \
  tests.test_business_acceptance \
  tests.test_business_acceptance_verifier
python3 -m py_compile \
  app/agents/frontend/generator.py \
  app/services/build_context_resolver.py \
  app/agents/main/task_preparer_prompt.py
curl -sS http://127.0.0.1:8000/health
```

**依赖**：批次 4、批次 5。

**2026-09-11 实施结果**

- 服务端从当前已确认 ProductPlan 和 TechnicalPlan 确定性编译 `agent-ui-build.v1` 逐页合同，包含 `pageId`、Agent、Surface、唯一 action、context 白名单、固定组件、默认 Mock Adapter、专用 Skill 与未来 Gateway Endpoint；该合同只由平台注入 `source_refs.agent_ui`，模型候选提交同名字段会被拒绝。
- 页面范围和应用范围都按 `page:<pageId>` 获得独立合同；普通页面不携带合同，继续使用原 Frontend Agent Prompt。Agent Gateway 保留正式 `requiredEndpointIds` 和 Unit 引用，但作为 Mock 页面消费检查的显式豁免项，不伪造实体 CRUD 绑定。
- Frontend Agent 的 Agent 页面 Prompt 会由平台内联专用 Skill，并要求先读取已注入的固定组件、共享核心、类型和 Mock Adapter 源码；页面只能声明精确 `AGENT_UI_CONFIG` 并组合 `AgentConversationPage` 或 `AgentFloatingPanel`，不得传入 `adapter`、复制聊天核心或保留 `data-preview-only`。
- 新增 `frontend.agent_ui_mock_contract` 确定性检查，验证固定组件精确导入、配置完全一致、默认 Mock Adapter、浮窗业务主体以及页面无 `fetch`、`axios`、`XMLHttpRequest`、`WebSocket`、`EventSource` 或 `sendBeacon` 直连。该检查不受普通业务自检环境开关影响，失败会进入现有 Build 失败/修复路径。
- 含 Agent UI Mock 的 Build 全部完成后返回 `build_summary.status=mock_completed` 和 `delivery_boundary.real_integration=pending`，主图停在 `agent_ui_integration_pending`，不会自动进入 Unit Test、Launch 或 Acceptance；Acceptance 和 finalize 另有防御性拒绝。普通页面 Build 仍保持原 `completed` 路由。
- 当前固定模板只接受一个 Surface 对应一个 action；若已确认 ProductPlan 提供多 action，Build 上下文会明确失败，不静默取首项，也未在本批改变 ProductPlan 产品语义或升级批次 5 资产合同。

### 检查点 B：UI 设计与 Mock 开发闭合

批次 4—6 完成后必须确认：

- UiDesign 和生成应用开发阶段都使用固定模板；
- Skill 在两个阶段按各自能力边界生效；
- Mock 与真实集成状态没有混淆；
- 普通页面/API/实体流程回归通过；
- 未修改或弱化现有 AG-UI、Gateway、Runtime 和 Acceptance 契约；
- 未完成后端联调的应用仍明确显示待联调状态。

### 批次 7：Electron 与完整回归验证

**目标**

验证设计阶段真实 Electron 展示、自动化、主题、响应式、可访问性和网络边界。

**Electron 检查矩阵**

| 形态 | 宽度 | 主题 | 状态 | 关键检查 |
| --- | --- | --- | --- | --- |
| 独立页 | 1440 / 1024 | 亮 / 暗 | 正常、停止 | 固定会话栏、消息气泡、Composer |
| 独立页 | 767 / 320 | 亮 / 暗 | 正常 | 会话 Drawer、无横向滚动、安全区 |
| 浮窗 | 1440 / 1024 | 亮 / 暗 | 收起、展开、停止 | 拖动不误触、视口夹紧、边缘吸附 |
| 浮窗 | 767 / 320 | 亮 / 暗 | 收起、Card、停止 | 不使用 Drawer、禁止自由拖动、Escape、焦点返回 |

还必须检查：

- 业务页面 action 和 information item 仍然存在；
- 浮窗只读取白名单上下文；
- 空、错、加载、Tool、审批和成功状态保留确定性渲染测试，不提供手工状态切换控件；
- 控制台没有运行时错误；
- Network 中没有 Agent API 请求；
- Vite URL 只作为健康信号，不替代 Electron UI 证据。

**统一验证命令**

```bash
cd Backend
.venv/bin/python -m unittest \
  tests.test_builtin_skills \
  tests.test_agent_product_plan \
  tests.test_ui_design_generator \
  tests.test_ui_design_manifest \
  tests.test_application_page_planning \
  tests.test_frontend_agent_ui_scaffold \
  tests.test_application_template_generation \
  tests.test_build_task_planner \
  tests.test_build_subgraph_scheduler \
  tests.test_business_acceptance \
  tests.test_business_acceptance_verifier
curl -sS http://127.0.0.1:8000/health

cd ../Frontend
node scripts/build-design-runtime.mjs
pnpm test:template-compatibility
pnpm test:agent-ui-design-runtime
pnpm test:agent-ui-electron-runtime
pnpm typecheck
pnpm build

cd ..
git diff --check
```

**依赖**：批次 1—6。

**2026-09-11 实施结果**

- 修复 `AgentChatCore` 把始终为真值的 React element 当作互斥状态判断、导致普通/Tool/审批/成功状态只显示空容器的白屏问题；专项测试覆盖普通状态消息可见及错误状态互斥。
- UiDesign 固定浮窗与生成应用内置浮窗在窄屏都保留受视口约束的 Card，不再使用底部 Drawer；独立会话页的会话历史仍使用左侧 Drawer。
- 按用户确认移除独立页和浮窗中的预览状态切换条；加载、错误、停止、Tool、审批等状态组件仍保留，由后续 Adapter/运行事件驱动，不暴露手工切换入口。
- 新增项目内 Electron Runtime 验收脚本，覆盖 1440、1024、768、767 边界和 320 宽度、亮暗主题、正常/停止交互、无横向滚动、Escape/焦点返回、可访问名称、控制台错误和零 Agent 网络请求；其余状态由 `AgentChatCore` 确定性渲染测试覆盖，视觉截图已检查。768px 按当前 Ant Design `md` 桌面断点执行，767px 以下进入紧凑规则。
- Agent UI/Build 定向 Backend 回归 160 项通过；Frontend 专项测试、两套 TypeScript typecheck、定向 lint、Electron/Vite build、Electron Runtime 矩阵、Backend `/health` 和 `git diff --check` 通过。
- 统一 Backend 组合回归共运行 254 项，248 项通过；其余 6 项集中于既有 Spring Boot Skill 精确文案和 application page planning FakeModel/确认投影路径，独立重跑仍失败，本批未越界修改，也未把它们记为通过。

### 批次 8：状态台账与代码索引更新

**目标**

在代码和 Electron 证据完成后，准确更新实现状态，不提前宣称真实 Runtime 完成。

**建议文件**

```text
docs/AGENT_UI_SURFACES_AND_TEMPLATES.md
docs/AGENT_DEVELOPMENT_IMPLEMENTATION_STATUS.md
docs/CODEBASE_INDEX.md
```

**记录要求**

- 列出实际修改文件、协议、模板版本和验证命令；
- 明确“固定 UI + Mock Adapter 已验证”；
- 明确“Java Gateway、AG-UI、Runtime、Launch、Acceptance 未完成”；
- 更新目录所有权和后续阅读入口；
- 不把设计稿、Mock 或 Frontend build 当作端到端证据。

**依赖**：批次 7。

**状态**：2026-09-11 已完成；实现状态、目录索引、移动端 Card 决策及真实联调边界已同步。

## 7. 后续真实 AG-UI 联调计划

本节只保留后续目标，不授权当前批次实施。

后端完成后，单独制定并确认以下批次：

1. 在生成应用前端实现 `AgUiAgentConversationAdapter`；
2. 使用 `@ag-ui/client` 和 `@ag-ui/core`，不写自定义 SSE parser；
3. Java Gateway 校验 user、tenant、application、agentId、pageId、actionId、Contract Hash 和上下文 Schema；
4. Java Gateway 向 Python Agent Runtime 转发内部 AG-UI；
5. 页面只能连接 Java Gateway，不能直连 Python sidecar；
6. 实现 run start、assistant message、result/error、state snapshot/delta 和唯一 run finish；
7. 覆盖会话列表、新建、切换、搜索、重命名、归档/删除及 checkpoint 恢复；
8. 覆盖停止、重试、Tool、审批、断线恢复、权限拒绝和上下文越界；
9. 删除 Mock Adapter 和模拟提示，按当前契约切换到真实实现；
10. 运行完整 Build、Integration Test、Code Review、Launch 和 Acceptance。

不增加 Mock/真实双写、历史 reader、fallback alias 或旧文件兼容逻辑。

## 8. 明确不在当前范围

- Java Gateway 和真实业务 Agent Gateway Endpoint；
- Python Agent Runtime 对话、历史和持久化联调；
- 新的 REST 聊天接口或自定义 SSE；
- 第二个页面模板形式的浮窗；
- 多浮窗、多 Agent 同页挂载；
- `entryPageIds=[]` 应用级 Agent 的可视化入口；
- 修改普通页面、API、实体的默认流程；
- 引入新 UI 框架或聊天组件依赖；
- 历史 UiManifest 或模板版本迁移；
- 修改、提交、push 或发布外部模板，以及发布生成应用。

## 9. 风险与缓解措施

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| Skill 仍允许模型自由重写 UI | 高 | 固定 UI 必须由代码组件提供，并增加导入、结构和 Surface 静态校验 |
| UiDesign 与生成应用模板漂移 | 高 | 使用相同配置契约和结构不变量，分别执行自动化和视觉回归 |
| Mock 被误认为真实接入 | 高 | 显式模拟提示，真实 Endpoint/Launch/Acceptance 保持未完成 |
| 浮窗破坏业务页面主体 | 高 | 保留 action/information item 校验并增加业务主体回归 |
| 注入资产与 `main`、`auth` 模板依赖不兼容 | 高 | 批次 0 只读核对两个分支，使用共同已有依赖，并在临时生成工作区分别构建 |
| 无 Agent 应用被错误注入 | 高 | 只读取已确认 ProductPlan 的启用 Surface，Manifest 记录 `required/skipped`，增加无 Agent 回归 |
| 重复初始化覆盖生成应用代码 | 高 | 摘要一致才复用，目标内容冲突时失败并保留现场，禁止静默覆盖 |
| 当前未提交改动被覆盖 | 高 | 每批定向 Diff，精确补丁，不整文件替换或清理工作区 |
| 固定设计 Runtime 增大 bundle | 中 | 记录构建产物变化，检查主 Renderer 与 antd5 iframe 仍隔离 |
| UiManifest 版本处理错误 | 中 | 先确认 v4 是否发布，再选择补齐 v4 或升级 v5，不双读 |
| Agent 页面 Skill 污染普通页面 | 中 | 只在服务端已投影 Agent Surface 时注入和执行 |
| 模型绕过组件创建另一套聊天 UI | 中 | 静态分析固定 import、唯一 Surface、禁止重复聊天结构 |

## 10. 回滚策略

每个批次必须保持独立可回滚：

- 批次 1 只增加 Skill 和注册，不改变生成行为；
- 批次 2 只增加设计 Runtime 组件和虚拟模块；
- 批次 3 才切换独立页模板；
- 批次 4 才切换 UiDesign 生成规则；
- 批次 5 只增加 XCodeAgent 内置生成资产和条件式注入，不修改外部模板仓库；
- 批次 6 才切换开发阶段 Frontend Agent；
- 任一检查点失败时回退当前批，不跨批删除现有稳定能力；
- 不使用 `git reset --hard`、`git checkout --` 或 `git clean`；
- 内置资产注入失败时保留原生成工作区和 Manifest 证据，不删除或覆盖冲突文件。

## 11. 必须停止并重新确认的条件

出现以下任一情况必须停止：

1. 需要修改 ProductPlan Agent Surface 的产品语义；
2. 需要把浮窗改为第二个页面模板；
3. 需要改变普通页面/API/实体默认流程；
4. 需要增加新生产依赖、UI 框架或聊天 SDK；
5. 条件式注入无法在不修改外部模板仓库的路由、Provider、全局配置、请求封装或依赖的前提下实现；
6. 需要放宽现有真实 Endpoint、Build、Launch 或 Acceptance 门禁；
7. 当前 BuildScheduler 无法区分 Mock UI 和真实集成状态；
8. 已发布的 UiManifest 版本需要原地改变契约，而不是升级当前版本；
9. 固定组件无法在 DesignRenderer 的隔离 iframe 中安全加载；
10. 无法建立普通页面、无 Agent 页面和现有模板的回归证据；
11. 需要扩大网络、凭据、文件系统、数据库、发布或 Git 权限；
12. 发现当前未提交改动与计划目标发生无法自动合并的冲突。

## 12. 推荐实施顺序与确认门

```text
确认批次 0
  -> 完成只读核对
  -> 确认批次 1—4
  -> 检查点 A
  -> 单独确认 XCodeAgent 条件式注入批次 5
  -> 确认批次 6
  -> 检查点 B
  -> 批次 7 Electron/自动化验收
  -> 批次 8 更新状态台账
  -> 后续另行确认真实 AG-UI 联调
```

每次确认只授权对应批次列出的文件和行为。发现新架构决策、公开契约变化或范围扩大时，必须重新提交计划并等待确认。
