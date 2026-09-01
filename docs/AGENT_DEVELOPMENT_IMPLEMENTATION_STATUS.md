# 智能体开发流程实现状态

> 状态：当前事实台账
> 基线日期：2026-08-31
> 变基目标：`origin/dev_agent` 的 `7b1eb34`
> 原型功能基线：`603c10f`
> 维护范围：智能体作为用户应用中的业务产物，从需求、设计、构建、测试、审查到验收的完整开发流程

## 1. 文档目的

本文只记录已经存在且能够定位证据的能力，以及仍未实现的正式集成缺口。它不定义新的产品契约；开发约束以 [智能体开发流程集成规范](./AGENT_DEVELOPMENT_INTEGRATION_RULES.md) 为准，原型交互参考以 [智能体开发原型集成说明](./AGENT_PROTOTYPE_INTEGRATION.md) 为准。

必须区分以下两个概念：

- **XCodeAgent 内部执行 Agent**：Frontend、Data Source、Database、RepairPlanner、SmallTask、CodeAnalyze 等用于开发项目的一等 Agent。
- **用户开发的业务智能体**：作为生成应用中的正式业务产物，拥有设计、配置、代码、工具、知识、测试、预览、验收和版本状态。

本文跟踪的是第二类。内部执行 Agent 已存在，不代表业务智能体开发流程已经集成。

## 2. 状态定义

| 状态 | 判定标准 |
| --- | --- |
| 未开始 | 正式前后端、协议、持久化和主流程中均无该能力。 |
| 设计完成 | 已有经确认的规范，但没有可运行实现。 |
| 原型已实现 | `prototype/` 中存在可交互实现或模拟流程；不计为正式集成。 |
| 正式开发中 | 生产代码已开始实现，但尚未完整接入主流程或未完成验证。 |
| 已集成 | 已进入现有主流程，数据、协议、UI 和生命周期契约闭合。 |
| 已验证 | 已集成，并具备自动化、Electron 运行态和回归证据。 |

任何能力从一个状态升级到下一个状态，都必须附代码路径、协议或产物路径、验证命令及结果。只存在设计稿、截图、Mock 数据、确认卡或自然语言总结，不得标记为“已集成”或“已验证”。

## 3. 当前结论

- `prototype/` 已实现业务智能体作为页面/API 同级产物的主要交互原型，包含设计确认、依赖检查、配置修订、代码 Diff、试聊预览和验收等状态。
- 当前生产 `Backend/` 已在 RequirementSpec 中识别、归一化、校验和持久化业务智能体需求，并由 ProductPlan v6 生成产品级智能体能力、入口页面/操作、交互状态、边界和验收契约；两者复用现有 Markdown 编辑与联合确认链路。
- 正式前端需求文档面板已按需展示“智能体”章节；TechnicalPlan 已增加 `agent_contracts[]`、Python sidecar 架构、AG-UI 网关、工具/API 引用、安全和产物路径，并进入现有 Markdown 确认链路。TechnicalPlan 确认摘要和右侧阅读面板会按需展示“智能体契约”及运行时、网关、绑定、安全、产物和 required checks；普通应用仍使用 `agents: []`、`agent_contracts: []`，不出现智能体章节、契约页签或 Python 架构。
- 现有 `build-dag.v3` 已增加 `agent:runtime`、`agent:<agentId>`、`agent` owner 和独立 Agent Runtime Generation CodeRunner，写权限只允许 `agent-runtime/**`；工作台智能体设计/配置产物、真实生成应用端到端运行、专属测试/审查证据和候选版本晋升仍未完成。
- 因此当前总体状态是：**正式开发中，已完成 RequirementSpec、ProductPlan、TechnicalPlan 与 Build 接入切片，但尚未形成端到端智能体开发闭环**。
- 原型脚本 `test:agent-development`、`test:new-app-agent-planning`、智能体配置样式测试与 `typecheck` 可作为原型验证入口；本次变基后已重新运行并通过。

## 4. 能力矩阵

| 能力 | 原型状态 | 正式前端 | 正式后端/协议 | 当前缺口与下一步 |
| --- | --- | --- | --- | --- |
| 新建应用时声明业务智能体 | 原型已实现 | 正式开发中 | 正式开发中 | RequirementSpec、ProductPlan v6 与 TechnicalPlan `agent_contracts[]` 已闭合创建规划和确认，并完成真实新建应用至 TechnicalPlan 生成/展示验证；仍需确认后的 Build、运行和验收端到端验证。 |
| 智能体作为工作台同级产物 | 原型已实现 | 未开始 | 未开始 | 复用现有应用大纲、会话隔离、阶段状态和写锁，不新增平行工作台。 |
| 模型、API、实体、知识依赖检查 | 原型已实现 | 正式开发中 | 正式开发中 | TechnicalPlan 已展示 `project_default` 模型策略、能力/工具/API Endpoint、安全和知识引用摘要；知识目录、实体级工具授权和失效传播仍待后续切片。 |
| 十部分 Markdown 设计文档 | 原型已实现 | 未开始 | 未开始 | 决定正式产物 schema、Markdown/内部 JSON 同步和 revision/hash 机制。 |
| 智能体设计显式确认 | 原型已实现 | 未开始 | 未开始 | 接入现有 artifact confirmation；澄清、保存草稿和确认必须分离。 |
| Build DAG 与代码生成 | 原型已实现（模拟） | 正式开发中 | 正式开发中 | 已进入同一 `build-dag.v3`、BuildScheduler 和 Repair 边界；真实生成工程与运行证据仍待验证。 |
| 智能体定义与工具适配代码 Diff | 原型已实现（模拟） | 未开始 | 正式开发中 | 已固定 `agent-runtime/` 路径、Python 3.12 + DeepAgents、`agent` owner 与受限 CodeRunner；尚无真实生成应用 Diff 验收。 |
| 页面集成与调用入口 | 原型已实现 | 正式开发中 | 正式开发中 | 页面 action、Java 网关与 Agent Contract 使用稳定 Endpoint 引用和 AG-UI SSE；尚未在生成应用中执行真实联调。 |
| 配置 active/draft/candidate | 原型已实现 | 未开始 | 未开始 | 设计正式配置状态、候选版本、CAS、失效和回滚边界。 |
| 配置确认后重新生成 | 原型已实现 | 未开始 | 未开始 | 必须走“确认变更 → 重新生成 → Diff → 测试 → 验收”，验收前不替换 active。 |
| 试聊与智能体预览 | 原型已实现 | 未开始 | 未开始 | 复用现有 Preview、会话与 AG-UI；明确 Mock、候选和已生效版本。 |
| 单测、集成测试、审查 | 原型已实现（模拟/复用） | 未开始 | 未开始 | 编译 required checks，并接入现有 unit test、integration test 和 code review 阶段。 |
| 智能体验收与版本完成态 | 原型已实现 | 未开始 | 未开始 | 验收必须绑定候选版本、真实 Diff、测试/启动证据和用户决定。 |
| 未发布版本编辑、历史只读 | 原型已实现 | 未开始 | 未开始 | 接入现有 lifecycle/revision 模型；已发布或历史版本不得被原地覆盖。 |
| 权限、安全与敏感信息隔离 | 仅有交互表达 | 未开始 | 正式开发中 | Agent CodeRunner 已只能写 `agent-runtime/**`，契约禁止客户端直连并只转发 scoped user context；运行时网络、凭据、知识数据与工具写操作审批仍待闭合。 |

## 5. 原型证据

主要原型入口：

- `prototype/src/renderer/src/agentDevelopment.ts`
- `prototype/src/renderer/src/agentConfig.ts`
- `prototype/src/renderer/src/components/AiChatPanel/components/AgentConfigPanel/`
- `prototype/src/renderer/src/components/AiChatPanel/components/AgentPreviewPanel/`
- `prototype/src/renderer/src/components/AiChatPanel/hooks/useAgentConfigStore.ts`
- `prototype/src/renderer/src/mock/scripts/agentWorkbench.ts`
- `prototype/mock-data/pms-new/`
- `prototype/scripts/run-agent-development-tests.mjs`
- `prototype/scripts/run-new-app-agent-planning-tests.mjs`

这些文件证明原型交互与状态存在，不证明生产 API、持久化、代码生成或运行时已经完成。

## 6. 正式集成影响面

后续每个开发批次都必须从下列范围中选择最小闭合切片，并在实施前给出准确文件清单：

| 集成面 | 需要接入的现有边界 |
| --- | --- |
| 创建规划 | RequirementSpec + ProductPlan 联合确认、UiDesign、显式规划入口、TechnicalPlan。 |
| 正式产物 | 用户可编辑 Markdown、内部结构化状态、稳定 ID、revision/hash、上下游失效。 |
| 生命周期 | `application-lifecycle.json`、按 run/thread/target 隔离的 execution 与待确认交互。 |
| 工作台 | 现有应用大纲、会话、阶段条、底部输入区、预览、Diff 和历史状态。 |
| 协议 | 现有 AG-UI client/core、完整 run 生命周期、状态快照/增量、结构化结果与错误。 |
| 构建 | `development_readiness_gate`、Workspace Inspection、Build DAG 确认、BuildScheduler。 |
| 质量 | Unit Testing、Integration Testing、Code Review、Launch、Acceptance。 |
| 修订 | Change/candidate/promotion、配置候选、影响分析、重新确认和增量重建。 |
| 安全 | workspace capability、工具策略、文件范围、命令/网络、凭据、审批和审计证据。 |

## 7. 当前开放决策

以下问题尚未由当前正式契约决定，任何实现批次不得自行假设：

1. RequirementSpec `agent_requirements[]`、ProductPlan v6 `agents[]` 和 TechnicalPlan `agent_contracts[]` 已确定，TechnicalPlan 契约页签已接入；独立十部分智能体设计 artifact 的正式 schema、revision/hash 和工作台展示仍待确定。
2. 业务智能体运行时已确定为生成应用根目录下独立 `agent-runtime/` Python 3.12 + DeepAgents sidecar，Java8 + Springboot 保持业务网关；部署配置、进程启动和健康检查仍待模板/运行阶段闭合。
3. 智能体定义、工具适配和测试文件路径以及 `agent` owner 已确定；共享 runtime bootstrap、知识文件和配置候选的正式持久化仍待完成。
4. 能力→工具→API Endpoint、页面 action→Java AG-UI 网关的稳定引用已确定；Skill、知识库、实体级权限继承和工具写操作审批仍待确定。
5. 配置 candidate 的持久化、激活、回滚、发布和历史只读模型。
6. 智能体试聊使用 Mock、候选运行时或真实已发布运行时的边界，以及各状态的用户文案。
7. 智能体专属 required checks 已写入 TechnicalPlan，但其实际执行、验收证据和失败后回到哪个正式上游产物仍待闭合。

这些决策必须在对应开发批次的计划和冲突分析中列出，获得用户确认后才能写入正式规范或代码。

## 8. 台账更新规则

每完成一个正式集成切片，在同一变更中更新本文：

1. 更新能力矩阵的生产状态，不覆盖历史事实或夸大验证等级。
2. 记录实际修改路径、公开协议、正式产物和生命周期变化。
3. 记录自动化命令、Electron 运行态检查、明暗主题和回归结果。
4. 明确未验证项、已知限制、下一切片以及未触碰的既有功能。
5. 若目录归属、公开 API、AG-UI payload、IPC、存储格式或功能边界变化，同时更新 `docs/CODEBASE_INDEX.md`。

## 9. RequirementSpec 第一切片

2026-08-27 开始正式集成，当前切片只覆盖创建应用后的需求识别与确认：

- RequirementSpec 当前契约始终包含 `agent_requirements` 数组；普通应用使用空数组，不推断业务智能体。
- 每个条目包含稳定 `agentId`、名称、职责、核心能力、入口页面引用、交互方式和业务边界。
- `agentId` 必须唯一且符合 `lower_snake_case`；`entryPageIds` 必须引用同一 RequirementSpec 的页面。
- 需求模型只识别用户明确提出的业务智能体；普通自动化与智能体语义不清时必须通过现有 `ask_user` 澄清。
- RequirementSpec 不保存模型、Prompt、API、工具、Skill、知识库、存储、实现类或代码路径；这些仍属于后续规划与详细设计。
- 用户可在现有需求 Markdown 或概览编辑器中修改智能体需求，确认前同步回内部 JSON 并重新执行确定性门禁。
- 本切片没有新增产品 Endpoint、AG-UI 事件、前端组件、依赖或平行工作流。

正式代码证据：

- `Backend/app/agents/main/requirements_analyzer.py`
- `Backend/app/services/requirement_spec.py`
- `Backend/app/agents/main/document_sync.py`
- `Backend/app/workspace/spec_documents.py`
- `Backend/tests/test_agent_requirement_spec.py`

当前限制：RequirementSpec 只定义用户需要什么智能体，不选择模型、API、工具、知识或运行时；这些实现事实必须等待后续 TechnicalPlan 切片。

### 9.1 业务边界可空字段修复

2026-08-31 修复需求模型遗漏 `agent_requirements[].boundaries` 时中断 Spec 阶段的问题：

- `boundaries` 继续作为 RequirementSpec 的正式数组字段；模型完全遗漏该可空字段时，在严格校验前确定性补为 `[]`，不再浪费一次通用格式重试。
- 模型显式返回非数组 `boundaries` 时仍然拒绝；`agentId`、名称、职责、能力、入口页面和交互方式等字段的严格校验保持不变。
- 普通应用仍只接受 `agent_requirements: []`，没有新增智能体推断、产品 Endpoint、AG-UI 事件、前端行为或兼容旧契约的分支。

本次修复验证：

- 先新增回归用例并在修复前稳定复现两次模型调用后仍抛出 `agent_requirements[0](complete-fields)`；修复后对应遗漏字段与错误类型用例 2 项通过。
- `.venv/bin/python -m unittest tests.test_requirement_response_protocol tests.test_agent_requirement_spec -v`：16 项通过，包含普通应用空数组和单次模型调用断言。
- ProductPlan、规划重试、需求确认、流消息兼容与权限契约扩大回归共 80 项，其中 71 项通过、9 项失败；在未应用本次改动的 `e70ed17` 临时 worktree 中复跑需求确认模块，得到相同 9 项失败、35 项通过，证明失败属于当前基线旧断言，不是本次修复引入。
- `tests/test_requirements_json_recovery.py` 仍因当前虚拟环境未安装 `pytest` 无法通过 pytest 入口执行；本次未新增测试依赖。
- 变更 Python 文件 `py_compile`、`git diff --check`：通过；正式后端 `GET http://127.0.0.1:8000/health`：HTTP 200，`status=ok`。
- 本次没有修改前端或 UI，因此未运行前端构建、Electron 交互和明暗主题检查。

本切片验证：

- `.venv/bin/python -m unittest tests.test_agent_requirement_spec -v`：9 项通过。
- RequirementSpec、实体、数据源、授权、ProjectPlan 与 ProductPlan 直接消费方回归：109 项通过。
- 变更 Python 文件 `py_compile`：通过。
- `git diff --check`：通过。
- 正式后端 `GET http://127.0.0.1:8000/health`：HTTP 200，`status=ok`。
- 后端完整 `unittest discover` 执行 1228 项，仍有 18 个失败和 19 个错误；失败集中于生命周期枚举、联合确认状态旧断言、代码扫描边界和 AG-UI 投影测试，失败堆栈与断言没有指向本切片新增的 `agent_requirements` 逻辑。该结果不能记为全量回归通过。
- 在临时 `origin/dev_agent@7b1eb34` worktree 中对上述失败模块执行定向对照，生命周期、需求确认、代码扫描和规划投影失败均可复现；上游 `workflow_ag_ui` 对照停在取消测试未结束，已中止，因此不把上游全量计数记为已完成。
- 当前环境未安装 `pytest`，因此 `tests/test_requirements_json_recovery.py` 未能通过 pytest 入口执行；未为本切片新增测试依赖。
- 正式前端 Node/Web TypeScript 检查和 Electron-Vite development bundle：通过；本次没有修改正式前端 UI，未重复 Electron 交互验收。
- 正式前端全仓 ESLint 持续约十分钟仍未输出或结束，已主动中止并记为未完成；本分支相对 `origin/dev_agent` 没有正式前端文件变更。
- 原型 `agent-development`、新建应用智能体规划、智能体配置样式三组脚本：通过；原型 TypeScript 检查和 Vite production bundle：通过。
- 当前 shell 使用 Node 24.14.1，而仓库约定 Node 20.19.0；本机 pnpm 版本代理因受限网络无法校验并切换到 8.15.9，因此前端验证直接使用已安装在项目 `node_modules` 中的锁定二进制执行等价命令。
- 原型全仓 ESLint 仍有 134 个错误和 2367 个警告，包含大量原型既有格式与显式类型问题；未在本次变基中自动修复或大面积改写。该结果不能记为原型全仓 Lint 通过。

## 10. ProductPlan 第二切片

2026-08-28 完成创建应用的智能体产品规划契约，范围只到 RequirementSpec + ProductPlan 联合确认，不提前决定技术实现：

- ProductPlan 当前契约升级为 `product-plan.v6`，根对象固定包含 `agents`；普通应用必须使用空数组，历史 v5 不做迁移或兼容读取。
- 每个智能体按 RequirementSpec 的稳定 `agentId` 一一对应，保留名称、职责、入口页面、交互模式和业务边界，并补充稳定能力 ID、能力预期结果、页面 action 绑定、五类交互状态和产品验收标准。
- 每个 `entryPageId` 必须存在唯一 `pageActionBindings`，其中 action 必须真实存在于同一页面；模型输出遗漏、重复、越界引用或夹带技术字段都会在归一化前后被拒绝。
- ProductPlan 明确禁止模型、Prompt、API、endpoint、工具、Skill、知识库、运行时、存储和代码路径；这些事实只能由后续 TechnicalPlan/详细设计决定。
- ProductPlan Markdown 增加“智能体产品规划”章节，用户编辑后继续通过现有同步链路回写结构化 JSON，同时保护 RequirementSpec 已确认的稳定身份和边界。
- 正式前端联合确认视图只在存在智能体时增加“智能体”页签，展示能力、入口/操作、交互状态、边界和验收标准；普通应用的概览、页面与业务流程视图不变。
- 本切片没有新增产品 Endpoint、AG-UI 事件、依赖、平行工作流，也没有实现 TechnicalPlan 智能体运行时契约、工作台智能体产物或代码生成。

正式代码证据：

- `Backend/app/services/product_plan.py`
- `Backend/app/agents/main/product_planner.py`
- `Backend/app/agents/main/document_sync.py`
- `Backend/app/workspace/product_plan_documents.py`
- `Frontend/src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementDocPanel.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementAgentSection.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementDocPanelData.ts`
- `Backend/tests/test_agent_product_plan.py`
- `Frontend/tests/agentProductPlan.test.ts`

当前限制：本切片只闭合“用户要什么智能体、从哪里进入、能做什么、用户看到什么结果”。模型/API/工具/知识依赖、生成目录、运行时配置、测试与验收证据仍属于后续切片，不能把原型字段直接复制进 ProductPlan。

本切片验证：

- `.venv/bin/python -m unittest tests.test_agent_product_plan -v`：6 项通过，覆盖普通应用空数组、智能体稳定引用、技术字段拒绝、模型输出覆盖、提示词边界、Markdown 同步与能力 ID 保持。
- ProductPlan、RequirementSpec、规划重试、联合确认、模板生成、Workflow 请求和消息兼容定向回归：170 项通过。
- 包含生命周期旧测试的扩大回归共执行 188 项，其中 181 项通过、7 项错误；错误均为测试继续引用已删除的 `GENERATING_REQUIREMENT_SPEC` / `AWAITING_REQUIREMENT_CONFIRMATION` 枚举，未指向 ProductPlan v6 或 `agents` 字段。本结果不能记为扩大回归全绿。
- 所有变更 Python 文件 `py_compile`：通过；正式后端 `/health`：HTTP 200，`status=ok`，公开协议已报告 `product-plan.v6`。
- 正式前端智能体产品规划测试 2 项、规划产物状态测试 5 项：通过；Node/Web TypeScript 检查与 Electron-Vite development bundle：通过。
- 本次触及的前端文件定向 ESLint 和 Prettier：通过；全仓 ESLint 运行约 90 秒无输出且未结束，已中止，不能记为全仓 Lint 通过。
- Electron 实机 UI 检查因 macOS 处于锁屏状态无法执行；自动化未尝试绕过锁屏，因此智能体页签的真实窗口交互、明暗主题和视觉布局仍待解锁后验收。
- 当前 shell 为 Node 24.14.1，仓库约定 Node 20.19.0；系统 pnpm 版本代理拒绝切换，前端验证直接使用仓库已安装的锁定工具二进制完成。

## 11. TechnicalPlan 与 Build 第三切片

2026-08-31 完成业务智能体从产品规划进入技术契约与现有 Build DAG 的最小闭合接入：

- TechnicalPlan 根对象固定包含 `agent_contracts[]`。普通应用使用空数组，并且不增加 Python 架构、Agent Unit、Agent task 或 Agent owner。
- 存在业务智能体时，平台确定性增加 `architecture.agent_runtime`，固定为生成应用根目录下独立 `agent-runtime/`、Python 3.12、DeepAgents sidecar；Java8 + Springboot 继续承担认证、业务 API 和面向客户端的 Agent 网关。
- Agent Contract 按 ProductPlan `agentId` 一一对应，闭合能力→工具→TechnicalPlan API Endpoint、ProductPlan 页面 action→Java Agent 网关 Endpoint、会话、项目默认模型、知识引用、安全边界、产物路径和 required checks；模型不能改写运行时、传输、安全和路径事实。
- 客户端调用固定经过 Java 网关并使用 AG-UI SSE；禁止浏览器直连 Python sidecar，Java 网关只转发受限用户上下文，工具适配器只能调用声明过的 Java API Endpoint。
- 现有 `build-dag.v3` 在 Agent Contract 非空时增加 `agent:runtime` 和 `agent:<agentId>` Unit，并建立工具 API Endpoint → Agent → Java Agent 网关 Endpoint → 页面依赖；没有新建第二套任务计划或执行 Graph。
- Build 任务增加 `agent` owner、`agent.code` task 与 `agent.runtime` deliverable。共享 runtime bootstrap 和单 Agent 定义/工具适配/测试分别写入确定性 `agent-runtime/**` 路径。
- 新增独立 Agent Runtime Generation CodeRunner，通过现有 BuildScheduler/Build Subgraph 执行，文件权限只能写 `/agent-runtime/**`；Java 网关继续由 Data Source Generation Agent 负责，页面入口继续由 Frontend Generation Agent 负责。
- Java 网关与前端生成提示按匹配 Agent Contract 增加 AG-UI 约束，防止把 Agent 实现在 Java 中、使用普通 REST 代替 AG-UI，或让前端直连 sidecar。

正式代码证据：

- `Backend/app/services/project_plan.py`
- `Backend/app/workspace/plan_documents.py`
- `Backend/app/agents/main/planner.py`
- `Backend/app/agents/main/document_sync.py`
- `Backend/app/services/build_unit_skeleton.py`
- `Backend/app/services/build_unit_compiler.py`
- `Backend/app/services/build_task_planner.py`
- `Backend/app/agents/main/task_preparer_prompt.py`
- `Backend/app/agents/agent_runtime/`
- `Backend/app/agents/workspace_scope.py`
- `Backend/app/agents/registry.py`
- `Backend/app/graph/nodes/tasks.py`
- `Backend/app/graph/subgraphs/build.py`
- `Backend/app/agents/data_source/prompt_context.py`
- `Backend/app/agents/data_source/generator.py`
- `Backend/app/agents/frontend/generator.py`
- `Backend/tests/test_agent_technical_plan.py`
- `Backend/tests/test_build_unit_skeleton.py`
- `Backend/tests/test_build_task_planner.py`
- `Backend/tests/test_agent_build_runner.py`

当前限制：本切片建立的是正式技术契约、DAG 归属、CodeRunner 和写权限边界；尚未完成一次真实“新建应用→生成 Agent Runtime/Java 网关/页面→启动 sidecar→AG-UI 对话→测试/审查→验收”的端到端运行证据。`agent.runtime` 已成为正式 deliverable，但专属确定性业务 verifier、Python 依赖安装/启动/健康检查、工作台独立智能体设计与配置候选、试聊和版本晋升仍属于后续切片。

本切片验证：

- `.venv/bin/python -m unittest tests.test_agent_technical_plan tests.test_agent_build_runner tests.test_agent_product_plan tests.test_agent_requirement_spec tests.test_requirement_response_protocol tests.test_product_technical_planning tests.test_product_planning_retry tests.test_project_planning_confirmation tests.test_build_unit_skeleton tests.test_build_task_planner tests.test_agent_registry_workspace tests.test_workspace_scope tests.test_data_source_generation_prompt tests.test_code_graph_agent_scope tests.test_build_result_coordinator`：210 项通过。
- 组合回归覆盖普通应用空 Agent 数组、Agent TechnicalPlan 五段契约、页面 action→Java 网关、工具→API Endpoint、多轮 memory、Markdown 同步、Build Unit/任务/依赖、独立 Runner、Java/前端生成提示、Agent Runtime 写权限和结构化任务结果。
- 新增与变更 Python 文件 `py_compile`：通过；`git diff --check`：通过。
- 正式后端 `GET http://127.0.0.1:8000/health`：HTTP 200，`status=ok`，公开 `forcedAgents` 已包含 `agent_runtime`。
- 合并前五轴代码审查发现并修复两项：移除 Agent Runtime Runner 可绕过文件权限的通用 shell 工具；修正 TechnicalPlan 动态示例中的页面 action→网关绑定和单轮 `memory=none`。
- 本切片没有修改正式前端文件；不把后端提示词与 DAG 测试记为 Electron UI、明暗主题或真实生成应用联调通过。
