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
- 当前生产 `Backend/` 已在 RequirementSpec 中识别、归一化、校验和持久化业务智能体需求；新建应用能够结合业务提出合理智能体角色时，需求问答会先建议并等待用户选择，不再要求显著价值或复杂推理。适配判断依据完整需求的业务语义，不依赖应用名称、关键词或业务示例。ProductPlan v6 继续生成产品级智能体能力、入口页面/操作、交互状态、边界和验收契约；两者复用现有 Markdown 编辑与联合确认链路。
- 正式前端需求文档面板已按需展示“智能体”章节；TechnicalPlan 已增加 `agent_contracts[]`、Python sidecar 架构、AG-UI 网关、工具/API 引用、安全和产物路径，并进入现有 Markdown 确认链路。TechnicalPlan 确认摘要和右侧阅读面板会按需展示“智能体契约”及运行时、网关、绑定、安全、产物和 required checks；普通应用仍使用 `agents: []`、`agent_contracts: []`，不出现智能体章节、契约页签或 Python 架构。
- 现有 `build-dag.v3` 已增加 `agent:runtime`、`agent:<agentId>`、`agent` owner 和独立 Agent Runtime Generation CodeRunner，写权限只允许 `agent-runtime/**`；工作台智能体设计/配置产物、真实生成应用端到端运行、专属测试/审查证据和候选版本晋升仍未完成。
- 因此当前总体状态是：**正式开发中，已完成 RequirementSpec、ProductPlan、TechnicalPlan 与 Build 接入切片，但尚未形成端到端智能体开发闭环**。
- 原型脚本 `test:agent-development`、`test:new-app-agent-planning`、智能体配置样式测试与 `typecheck` 可作为原型验证入口；本次变基后已重新运行并通过。

## 4. 能力矩阵

| 能力 | 原型状态 | 正式前端 | 正式后端/协议 | 当前缺口与下一步 |
| --- | --- | --- | --- | --- |
| 新建应用时声明业务智能体 | 原型已实现 | 正式开发中 | 正式开发中 | RequirementSpec 已支持显式需求和“适合但未提及”时的一次性适配建议，用户同意后才进入 ProductPlan v6 与 TechnicalPlan `agent_contracts[]`；仍需真实模型适配命中率以及确认后的 Build、运行和验收端到端验证。 |
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

- RequirementSpec 当前契约始终包含 `agent_requirements` 数组；不适合智能体或用户明确拒绝的普通应用使用空数组，不自动创建业务智能体。
- 每个条目包含稳定 `agentId`、名称、职责、核心能力、入口页面引用、交互方式和业务边界。
- `agentId` 必须唯一且符合 `lower_snake_case`；`entryPageIds` 必须引用同一 RequirementSpec 的页面。
- 需求模型识别用户明确提出或在适配建议后明确接受的业务智能体；只要能从完整业务语义中提出合理智能体角色，即通过现有 `ask_user` 提出一次角色明确的是/否建议。普通业务流程也可支持建议，描述业务需要不等于明确要求 AI。用户同意前不加入智能体需求，拒绝后不得重复询问。
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

2026-09-02 扩展真实模型边界类型适配：

- Electron 实机在用户接受“回检任务智能助手”后，连续两次稳定复现模型把 `boundaries` 返回为非数组并阻断 RequirementSpec；通用格式重试不能修复该结构。
- 模型返回 `null`、空字符串或完全遗漏 `boundaries` 时，协议适配层确定性收敛为 `[]`；返回单个非空字符串时无损收敛为单元素数组。对象等无法安全解释的结构仍由正式严格校验拒绝，不放宽 RequirementSpec 契约。
- 新增回归用例先在修复前稳定失败，修复后与遗漏字段和严格类型门禁用例共 3 项通过；真实 Electron 重试已越过原错误，生成并联合确认 RequirementSpec 与 ProductPlan。
- 真实流程继续进入 TechnicalPlan 后，模型输出 `agent_contracts` 非数组，内置自动修复和一次用户触发的重新生成均失败；确认按钮保持禁用且没有落盘未确认 TechnicalPlan。该问题是独立的后续规划阻断，不能记为 TechnicalPlan 实机验证通过。

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

### 9.2 新建应用智能体适配建议

2026-09-02 首次接入未显式提及智能体时的需求问答策略（以下为当时规则，适配条件已由本节 2026-09-03 的更新替换）：

- 需求模型在五类必需产品事实之外执行一次业务智能体适配判断；只有能够通过上下文推理、多轮自然语言指导、跨功能协助或工具调用提供显著用户价值的具体角色，才提出建议。
- 建议必须是一个聚焦的是/否问题，并说明拟议智能体角色及其在当前应用中的用户价值；它复用现有 `ask_user`、Graph 中断和三轮澄清预算，不增加 Endpoint、AG-UI 事件、前端组件或平行工作流。
- 用户同意前不得写入 `agent_requirements`；同意后才根据已确认应用上下文生成产品级智能体需求，拒绝后保持 `agent_requirements=[]` 且不得重复询问。
- 普通 CRUD、仪表盘、报表、固定审批、搜索筛选、导入导出、通知和定时自动化本身不触发智能体建议；现有 RequirementSpec 字段、确认门禁和 ProductPlan/TechnicalPlan 下游契约保持不变。

本次验证：

- 新增提示契约测试先在修改前失败，修改后与智能体适配门禁测试共同通过，证明“先问再创建”和“回答前不写需求草稿”已被覆盖。
- `.venv/bin/python -m unittest tests.test_agent_requirement_spec tests.test_requirement_response_protocol tests.test_ask_user tests.test_requirements_confirmation.RequirementsConfirmationTests.test_agent_suitability_question_blocks_before_requirement_draft tests.test_requirements_confirmation.RequirementsConfirmationTests.test_substantive_ask_user_question_still_blocks_for_answer -v`：25 项通过。
- 扩大回归执行 67 项，结果为 57 项通过、9 项失败、1 项错误；失败与修改前基线完全相同，9 项均为需求确认状态旧断言，1 项为 `test_main_agent_boundaries` 的单模型测试替身未返回当前权限事实 JSON。本结果不能记为扩大回归全绿，但没有新增失败。
- 变更 Python 文件 `py_compile` 与 `git diff --check`：通过。
- 正式后端 `GET http://127.0.0.1:8000/health`：连接失败，因为本地 8000 端口没有运行后端服务；未为本次提示策略修改启动服务。
- 本次没有前端、公开协议、目录归属或功能边界变化，因此未运行前端构建、Electron/明暗主题验证，也未更新 `docs/CODEBASE_INDEX.md`。

2026-09-03 放宽智能体建议条件：

- 只要能结合用户描述的业务提出合理智能体角色，就可以询问；不要求显著价值、复杂推理、多轮对话或超出固定流程的能力，普通业务流程也适用。
- 根据完整需求及上下文理解用户意图，区分业务需要与明确要求 AI；建议范围限定在已描述的业务和权限内。提示词不设置应用名称或关键词触发规则，不嵌入具体行业、应用或操作列表作为适配示例。
- 明确要求 AI／智能体或已接受建议时直接记录需求；拒绝后保持空数组且不重复询问。继续复用 `ask_user`、既有 AG-UI／Graph 等待流程、三轮澄清预算及正式产物确认门禁。
- 本次仅修改 `requirements_analyzer.py` 的提示策略、两处相关测试文件及本状态文档，不新增字段、接口、依赖或前端组件。

本次验证：

- 新增业务语义提示契约测试在旧规则上失败，修改后通过；显式要求智能体、接受／拒绝后不再重复询问、回答前保持空数组且不写需求草稿的相关测试均通过。
- 在 `Backend` 执行 `.venv/bin/python -m unittest tests.test_agent_requirement_spec tests.test_requirement_response_protocol tests.test_ask_user tests.test_requirements_confirmation.RequirementsConfirmationTests.test_agent_suitability_question_blocks_before_requirement_draft tests.test_requirements_confirmation.RequirementsConfirmationTests.test_substantive_ask_user_question_still_blocks_for_answer -q`：28 项通过。
- 在 `Backend` 执行 `.venv/bin/python -m py_compile app/agents/main/requirements_analyzer.py tests/test_agent_requirement_spec.py tests/test_requirements_confirmation.py`，以及根目录 `git diff --check`：通过。
- 本轮移除名称相关规则前，真实模型临时抽样曾观察到建议问题、同意后生成 1 项智能体需求、拒绝后保持 0 项、明确要求 AI 时生成 1 项且不再询问；这些是早先提示词的模型边界抽样，不代表最终规则的稳定命中率或 Electron 端到端验证。业务输入仅用于临时调用，未写入项目文件。
- `curl -sS --max-time 5 http://127.0.0.1:8000/health`：复查返回 `status: ok`；检查期间曾短暂无法连接，未重启服务。
- 未运行前端构建、前端类型检查和 Electron／明暗主题验证，因为没有前端变更；后端环境未安装 Ruff、Mypy 或 Pyright，也没有相应项目配置，本次未新增检查依赖。未扩大至全量测试；公开契约与目录归属未改变，无需更新 `docs/CODEBASE_INDEX.md`。

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

2026-09-02 Electron 实机验证已完成 RequirementSpec + ProductPlan 联合确认、UI 明确跳过和显式进入规划阶段，但 TechnicalPlan 模型连续两轮在 `agent_contracts` 数组契约上失败；平台正确拒绝产物并停在可重新生成状态。该证据只证明门禁和恢复 UI 有效，不证明 TechnicalPlan 已成功生成。

2026-09-02 增加单智能体模型输出的窄范围规范化：

- 仅当已确认 ProductPlan 恰有一个智能体，模型 `agent_contracts` 为包含全部且仅包含模型侧契约字段的单对象，且 `agentId` 完全匹配时，复制并包装为单元素数组；正式产物仍只有数组契约，不修改原始输入。
- 缺失、空值、映射、缺字段、身份不符和多智能体场景仍拒绝；规范化后继续执行原有页面 action、工具 Endpoint、能力及会话校验，不合成任何业务绑定。
- 4 个新增测试先复现失败，再验证无损结果、输入不变和严格拒绝边界；`.venv/bin/python -m unittest tests.test_agent_technical_plan tests.test_project_planning_confirmation tests.test_product_technical_planning tests.test_product_planning_retry`：82 项通过。变更文件 `py_compile` 与 `git diff --check` 通过。
- 该规范化只覆盖可无损解释的单对象，不能据此断言前述实机失败的原始值一定是单对象；真实模型重新生成的结果需单独记录。
- 窄修复后在已启动 Electron 中重新生成，后端三次尝试仍失败；本轮终端诊断明确显示外层 JSON 在字符串中途结束（`Unterminated string starting at`），`extract_json_object` 随后回退到仅有 `backend/data/frontend` 的内层对象，最终才报 `agent_contracts` 数组错误。因此本次单对象规范化未解决真实阻断，不得记为 TechnicalPlan 生成成功。
- 当前规划调用沿用 `default_max_tokens=8192`，且未保留模型结束原因；截断现象已确认，但尚不能把输出预算认定为唯一原因。下一步需独立确认规划输出完整性、结束原因诊断与输出预算的修复范围，不以补空数组或虚构契约绕过失败。
- 本轮 checkpoint 于 `2026-09-02T06:13:03Z` 停在 `technical_plan_generation_error`，`project_plan` 为空，正式 `technical-plan.json/md` 未落盘。Electron 当时仍显示生成中；刷新并重新打开该计划后，恢复为可“重新生成”、不可“确认并继续”的错误卡。该结果证明恢复后的门禁有效，不证明实时终态投影正常。
- RequirementSpec 与响应协议及新增建议问答门禁回归：19 项通过；扩大智能体规划/Build/Runner/权限组合回归执行 236 项，235 项通过，1 项 `test_live_page_path_is_reconciled_without_menu_route_task` 失败。在未修改的 `HEAD=d9b478b` 导出副本中单独复跑得到相同断言差异，确认是当前基线问题，未扩展修改 Build 逻辑。
- 后端健康检查：HTTP 200，`status=ok`；变更 Python 文件 `py_compile` 与 `git diff --check`：通过。后端未配置独立 lint/type-check 入口；本轮无前端代码变更，未运行前端 lint/type-check/build，也未声称完整端到端、控制台或明暗主题验收通过。

2026-09-02 用户确认后的 TechnicalPlan 输出完整性修复：

- 新增独立 `technical_plan_response.py`，完整规划和定向 Contract 修复均使用严格根对象解析；共享 `extract_json_object` 和非 TechnicalPlan 调用不变。截断、多个根对象、围栏外文字和非对象响应在物化前拒绝，不再误取内层 architecture 对象。
- 技术规划输出预算默认 `32768`，通过独立环境变量配置；不修改真实 `.env`、全局预算或其他阶段模型配置。invoke/stream 均保留结束原因及 token 用量，模型声明 `length` 时即使 JSON 可解析也拒绝，日志只新增有界脱敏元数据。
- 回归先在旧实现确认截断根对象及 `length` 结束原因丢失的 RED；修复后协议、独立配置、ProductTechnicalPlanning 和完整消息流式回归共 54 项通过。变更文件 `py_compile` 与后端健康检查通过；Electron 实机重新生成结果单独记录，不以单元测试替代。
- 同一 Electron 测试应用 `智能回检工作台`（`test0902`）实机重新生成成功：终端记录 `finish_reason=stop`、`output_tokens=11716`、`configured_max_tokens=32768`、`response_chars=53894`。本轮完整输出超过原先 `8192` 上限，未再出现内层 architecture 被误当根计划的错误。
- 已落盘 `technical-plan.md/json`，包含 5 个实体、5 个 API Contract、7 个页面和 1 个 `agent_inspection_assistant` 契约；Java 网关为 `task_api.agent_message`，工具绑定 `task_api.detail` 与 `rule_api.list`，均为只读，支持多轮 `conversation` 会话。对真实产物执行 `validate_technical_plan_agent_contracts` 返回空错误列表。
- 本轮 checkpoint 时间 `2026-09-02T06:47:37Z`，`clarification.mode=technical_plan_confirmation`，计划存在且修复错误为空。Electron 实时展示智能体契约及“查看技术规划 / 修改 / 确认保存”；JSON 为 `pending_user_confirmation`，生命周期为 `awaiting_technical_plan_confirmation/awaiting_user`。本次未点击确认保存，未进入模板物化、开发或生成应用运行时验收。
- 产物 SHA-256：JSON `f3165c33743f07a716c3cb546bf8f87ea2331421a3f06baa5acac34be041270c`；Markdown `c4f419f4a29523052f7bc0762601024dc612bccb075ad3c7adaca377eb542671`。
- 独立代码审查后补齐完整生成/定向修复/普通规划的入口路由隔离测试。最终命令 `.venv/bin/python -m unittest tests.test_agent_technical_plan tests.test_project_planning_confirmation tests.test_product_technical_planning tests.test_product_planning_retry tests.test_technical_plan_response_protocol tests.test_technical_plan_settings tests.test_planning_stream_message_compatibility tests.test_project_plan`：125 项通过。变更 Python 文件 `py_compile`、`git diff --check` 和正式后端健康检查通过；本轮未重跑已知有基线失败的完整后端套件。无前端源代码变更，未运行前端 lint/type-check/build 或明暗主题检查；后端无独立 lint/type-check 入口。

本切片验证：

- `.venv/bin/python -m unittest tests.test_agent_technical_plan tests.test_agent_build_runner tests.test_agent_product_plan tests.test_agent_requirement_spec tests.test_requirement_response_protocol tests.test_product_technical_planning tests.test_product_planning_retry tests.test_project_planning_confirmation tests.test_build_unit_skeleton tests.test_build_task_planner tests.test_agent_registry_workspace tests.test_workspace_scope tests.test_data_source_generation_prompt tests.test_code_graph_agent_scope tests.test_build_result_coordinator`：210 项通过。
- 组合回归覆盖普通应用空 Agent 数组、Agent TechnicalPlan 五段契约、页面 action→Java 网关、工具→API Endpoint、多轮 memory、Markdown 同步、Build Unit/任务/依赖、独立 Runner、Java/前端生成提示、Agent Runtime 写权限和结构化任务结果。
- 新增与变更 Python 文件 `py_compile`：通过；`git diff --check`：通过。
- 正式后端 `GET http://127.0.0.1:8000/health`：HTTP 200，`status=ok`，公开 `forcedAgents` 已包含 `agent_runtime`。
- 合并前五轴代码审查发现并修复两项：移除 Agent Runtime Runner 可绕过文件权限的通用 shell 工具；修正 TechnicalPlan 动态示例中的页面 action→网关绑定和单轮 `memory=none`。
- 本切片没有修改正式前端文件；不把后端提示词与 DAG 测试记为 Electron UI、明暗主题或真实生成应用联调通过。
