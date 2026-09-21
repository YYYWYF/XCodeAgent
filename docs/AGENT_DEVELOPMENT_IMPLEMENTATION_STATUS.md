# 智能体开发流程实现状态

> 状态：当前事实台账
> 基线日期：2026-09-07
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
- 正式前端需求文档面板已按需展示“智能体”章节；TechnicalPlan 的 `agent_contracts[]` 已升级为带 ProductPlan Hash 的完整派生执行快照，包含 identity、capabilities、interaction、七段 `agentSettings`、Invocation、Runtime、Security、Artifacts、Required checks 和 Evaluation。模型只返回候选设置和稳定引用，平台确定性展开 Endpoint 并拒绝派生字段漂移；TechnicalPlan Markdown 和右侧阅读面板已同步展示完整契约。普通应用仍使用 `agents: []`、`agent_contracts: []`，不出现智能体章节、契约页签或 Python 架构。
- 现有 `build-dag.v3` 已增加平台 readiness `agent:runtime` Unit、业务 `agent:<agentId>` Unit、`agent` owner 和独立 Agent Runtime Generation CodeRunner；模型不再为 `agent:runtime` 生成 bootstrap 任务，业务 Agent 任务写权限只允许对应 `agent-runtime/**` 路径。CodeRunner 已强制读取专用生成 Skill，只接收当前 Agent Contract 和其 Tool 实际引用的 Java API Contract/Schema，并按模板固定入口生成业务模块。工作台智能体设计/配置产物、真实生成应用端到端运行、专属测试/审查证据和候选版本晋升仍未完成。
- 已确认 [Agent Runtime 模板仓库与初始化流程设计](./AGENT_RUNTIME_TEMPLATE_AND_INITIALIZATION.md)，独立模板仓库 `Bettetman/agent-runtime-template@master` 可安装、测试、启动和基础对话。XCodeAgent 已把 `agentRuntime` 接入 TechnicalPlan 确认结果、Electron 模板下载、前后端协议类型、manifest 和 Backend readiness：`agent_contracts[]` 非空时下载并复核仓库、分支、commit 与关键文件，普通应用明确记录 skipped 且不创建目录。业务产物路径已统一切换到 `src/app/agent/`、`src/app/tools/`，`agent:runtime` 不再生成模型 bootstrap 任务；Testing、Code Review、Project Launch、Java Gateway 和 Electron 完整端到端仍未完成。
- 已完成 [Agent Development Workbench 第一期](./AGENT_DEVELOPMENT_PHASE1_IMPLEMENTATION_PLAN.md)：生产工作台已把 Agent 作为页面、API、实体同级开发目标，只读展示完整 Contract、七段 Settings、Runtime/Gateway/Tool/实体/页面依赖和固定实现文件状态；`type=agent` 已贯通 AG-UI 请求、Graph State、lifecycle、资源锁、EntitySourceBinding continuation、Build scope、required Unit 闭包及现有 Agent CodeRunner。当唯一阻断是实体绑定时，用户可在 Agent 门禁中显式暂时跳过，本次执行只生成 Python Runtime 和目标 Agent Unit。实现未增加新的产品 Endpoint，也未改变页面、Endpoint、实体和应用级 Build 的既有行为。
- 2026-09-21 当前分支已补齐 Agent Scope 与分 Unit DAG 规划链的衔接：GenerationRequirements 可从已确认 TechnicalPlan 精确选择当前 Agent 及 required Unit 闭包；`agent:runtime` 保持 prerequisite-only，`agent:<agentId>` 复用既有七模块编译器生成 deterministic Candidate，不进入规划模型；Frozen Contract manifest 只授权当前 Agent Contract。页面、Endpoint 和 application Scope 的既有职责规则保持不变；空职责仍使用本分支的 `not_required` 策略。从 `inspect_workspace` 等节点调试恢复时，`resumeExecutionRunId` 会钉住原工作台 Agent 执行目标，当前大纲选中的页面不能改写它。实体绑定后续接 Agent 会回到 `development_readiness_gate` 而不是页面 API 门禁；缺少 Formal DAG 或 workspace snapshot revision 的失败重试会先扫描工作区再生成任务，避免直接 Build。Agent Java Gateway 不要求 Endpoint API Design；入口页面和 Tool REST Endpoint 仍走各自开发目标。
- Agent Settings 第一批可视化编辑已接入生产工作台：七段配置不再展示原始 JSON，Prompt 与 Temperature 可生成 TechnicalPlan revision draft、查看字段 Diff、确认或放弃；确认时原子更新正式 TechnicalPlan 并使绑定旧 Contract Hash 的当前 Agent BuildTaskPlan 失效。Prepare 使用现有 Agent 资源锁，运行任务必须回到原会话停止，等待确认/失败/停止任务可由用户明确结束后释放；不会自动中断运行，也不影响无关 Agent、页面、Endpoint 或实体。
- 因此当前总体状态是：**正式开发中，已完成 RequirementSpec、ProductPlan、TechnicalPlan、条件式 Runtime 初始化、Agent Workbench 第一期与业务 Agent Build 接入切片，但尚未形成运行试聊、专属测试、启动和验收端到端闭环**。第一期自动化证据为 Backend 167 个定向测试和 Frontend Node/Renderer TypeScript + Electron/Vite Build 通过；Electron 实机完整 Agent Build 仍由用户验证。
- 原型脚本 `test:agent-development`、`test:new-app-agent-planning`、智能体配置样式测试与 `typecheck` 可作为原型验证入口；本次变基后已重新运行并通过。

## 4. 能力矩阵

| 能力 | 原型状态 | 正式前端 | 正式后端/协议 | 当前缺口与下一步 |
| --- | --- | --- | --- | --- |
| 新建应用时声明业务智能体 | 原型已实现 | 正式开发中 | 正式开发中 | RequirementSpec 已支持显式需求和“适合但未提及”时的一次性适配建议，用户同意后才进入 ProductPlan v6 与 TechnicalPlan `agent_contracts[]`；仍需真实模型适配命中率以及确认后的 Build、运行和验收端到端验证。 |
| 智能体作为工作台同级产物 | 原型已实现 | 已集成 | 已集成 | Agent 大纲、只读详情、`type=agent` AG-UI/Graph/lifecycle、确定性 readiness、实体续接、资源锁和 Agent scope Build 已进入现有生产工作台；仍需 Electron 实机完整 Build、Launch 和真实运行闭环。 |
| 模型、API、实体、知识依赖检查 | 原型已实现 | 正式开发中 | 正式开发中 | TechnicalPlan 已展示 `project_default` 模型策略、完整能力/工具/API Endpoint 快照、SQLite 短期记忆、安全、Skills、Knowledge 与 Context 状态；未实现的 Skills、Knowledge、Long-term Memory 和压缩能力固定关闭，正式 Catalog、实体级工具授权和失效传播仍待后续切片。 |
| 十部分 Markdown 设计文档 | 原型已实现 | 未开始 | 未开始 | 决定正式产物 schema、Markdown/内部 JSON 同步和 revision/hash 机制。 |
| 智能体设计显式确认 | 原型已实现 | 未开始 | 未开始 | 接入现有 artifact confirmation；澄清、保存草稿和确认必须分离。 |
| Build DAG 与代码生成 | 原型已实现（模拟） | 正式开发中 | 正式开发中 | 已进入同一 `build-dag.v3`、BuildScheduler 和 Repair 边界；Agent CodeRunner 已按专用 Skill、完整 Contract 和相关 API Schema 生成固定三文件，真实生成工程与运行证据仍待验证。 |
| 智能体定义与工具适配代码 Diff | 原型已实现（模拟） | 未开始 | 正式开发中 | 已固定 `agent-runtime/` 路径、Python 3.12 + DeepAgents、模板注入模型、动态业务模块入口、`agent` owner 与受限 CodeRunner；尚无真实生成应用 Diff 验收。 |
| 页面集成与调用入口 | 原型已实现 | 正式开发中 | 正式开发中 | 页面 action、Java 网关与 Agent Contract 使用稳定 Endpoint 引用和 AG-UI SSE；尚未在生成应用中执行真实联调。 |
| 配置 active/draft/candidate | 原型已实现 | 已集成 | 已集成 | 第一批复用现有 TechnicalPlan revision draft、changeId、CAS、预览、确认和放弃，不新增平行配置文件；独立 candidate/active 发布与历史回滚仍未实现。 |
| 配置确认后重新生成 | 原型已实现 | 正式开发中 | 正式开发中 | Prompt/Temperature 确认后正式 Contract 更新并定向废弃旧 Agent BuildTaskPlan，用户可重新进入现有 Build；完整单测、试聊、审查、验收证据失效与候选发布仍待后续闭合。 |
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

1. RequirementSpec `agent_requirements[]`、ProductPlan v6 `agents[]` 和 TechnicalPlan 完整 `agent_contracts[]` 已确定，七段 AgentSettings 与平台派生字段已进入契约页签；当前不新增独立智能体设计 Artifact，后续若需要独立配置候选、revision/hash 和工作台编辑体验，应基于本 Contract 的模型候选边界设计。
2. 业务智能体运行时已确定为生成应用根目录下独立 `agent-runtime/` Python 3.12 + DeepAgents sidecar，Java8 + Springboot 保持业务网关；最小模板、AG-UI Chat、模型/交互/checkpoint、健康检查和条件式初始化下载已实现，Java 网关联调、生成应用启动和端到端验收仍待后续批次完成。
3. 智能体定义、工具适配和测试文件路径以及 `agent` owner 已确定；共享 Runtime 改由独立模板提供，`agent:runtime` 目标上收敛为确定性模板 readiness，知识文件和配置候选的正式持久化仍待完成。
4. 能力→工具→API Endpoint、页面 action→Java AG-UI 网关的稳定引用和 Endpoint 快照已确定；Skill、知识库、实体级权限继承和工具写操作审批的 Runtime 闭环仍待实现，当前 Contract 对未实现能力保持关闭。
5. 配置 candidate 的持久化、激活、回滚、发布和历史只读模型。
6. 智能体试聊使用 Mock、候选运行时或真实已发布运行时的边界，以及各状态的用户文案。
7. 智能体专属 required checks 已写入 TechnicalPlan，但其实际执行、验收证据和失败后回到哪个正式上游产物仍待闭合。
8. Agent Runtime 模板正式 URL 与 `master` 分支已固定，manifest 会记录每次实际下载 commit；是否进一步固定发布 commit，以及 checkpoint 清理策略仍需在对应实施批次确认，不得增加旧契约 fallback。

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

- 该切片当时把 ProductPlan 契约升级为 `product-plan.v6`，根对象固定包含 `agents`；普通应用必须使用空数组，历史 v5 不做迁移或兼容读取。当前 v7 契约见第 15 节。
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
- 当次所有变更 Python 文件 `py_compile`：通过；正式后端 `/health`：HTTP 200，`status=ok`，当时公开协议报告 `product-plan.v6`。
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
- Build 任务保留 `agent` owner 与 `agent.runtime` deliverable。平台直接从完整 Agent Contract 与内置模板路径策略编译 Prompt、Model、Memory、Tools、Skills、Knowledge、Context 七个 `agent.code` 任务；生成应用不再携带 Manifest 或 Definition，任务优先修改模板现有组合入口，只有功能确实缺失且任务授权时才新增文件。
- `agent:runtime` 继续由开发前模板门禁确定性准备，不派发模型任务；模板已增加 stable/protected/七模块扩展点清单。模板感知 CodeRunner、动态 Python 新建权限、真实七步进度和质量链仍属于下一实施批次，当前执行入口会明确阻断而不会误报完成。Java 网关继续由 Backend owner 负责，页面入口继续由 Frontend owner 负责。
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

## 12. 完整 Agent Contract 第四切片

2026-09-06 按已确认的 [Agent Contract 重设计](./AGENT_CONTRACT_REDESIGN.md) 完成当前契约升级：

- ProductPlan 继续作为名称、用途、能力、交互、业务边界和产品验收标准的唯一权威；正式 Contract 保存规范化 ProductPlan JSON 的 `sha256:` 摘要和对应 `productAgentId`。
- 技术规划模型只输出 `agentId`、`gatewayEndpointId`、`capabilityBindings` 和七段 `agentSettings` 候选。平台从 ProductPlan 与 TechnicalPlan API Contract 编译 `source`、`identity`、`capabilities`、`interaction`、Endpoint 快照、Invocation、Runtime、Security、Artifacts、Required checks 和 Evaluation。
- `agentSettings` 固定包含 Prompt、Model、Memory、Tools、Skills、Knowledge 和 Context。当前只允许项目默认模型、按 ProductPlan 多轮要求启停的 SQLite Short-term Memory、真实 Java Endpoint Tool、固定 Context Budget 和 `compression.strategy=none`。
- MySQL Checkpointer、OSS/Long-term Memory、Skill Loader、Knowledge Retriever、Summary Compression、Vision、结构化最终输出和单 Agent 模型覆盖尚未实现，候选校验会拒绝将这些能力伪装为已启用。
- Tool Endpoint 被展开为 API Contract、Method、Path 和请求/响应 Schema 引用快照；Tool 的 `read/write` 必须与 HTTP 方法语义一致，Gateway Endpoint 不得同时注册为 Tool，写操作审批保持 `platform_managed`。
- 正式 Contract 校验使用“反投影模型候选 → 调用同一编译器 → 完整对象比较”，因此 ProductPlan、Endpoint 或平台派生字段发生漂移时不能通过确认。
- TechnicalPlan Markdown 同步只带回允许编辑的候选字段并重新编译；Markdown 与前端结构化阅读面板已展示身份、Prompt、Model、Memory、Capabilities/Tools、Skills、Knowledge、Context、Runtime、安全、产物和检查。
- Build Unit 的 Tool 依赖改为读取 `agentSettings.tools.bindings[].endpoint.endpointId`；Agent Runtime 生成提示明确使用 `init_chat_model`、`create_deep_agent`、编译后的 System Prompt、解析后的 Tools 与短期 Checkpointer。

正式代码证据：

- `Backend/app/services/project_plan.py`
- `Backend/app/agents/main/planner.py`
- `Backend/app/agents/main/document_sync.py`
- `Backend/app/workspace/plan_documents.py`
- `Backend/app/services/build_unit_skeleton.py`
- `Backend/app/agents/main/task_preparer_prompt.py`
- `Backend/app/agents/agent_runtime/generator.py`
- `Frontend/src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanAgentSection.tsx`
- `Backend/tests/test_agent_technical_plan.py`
- `Backend/tests/test_build_unit_skeleton.py`
- `Frontend/tests/agentTechnicalPlanView.test.ts`

当前限制：本切片只闭合规划、确认展示和 Build 输入契约，不等于生成应用已经实现七类 Runtime Adapter。真实“规划生成 → 用户确认 → Agent Runtime Build → Java Gateway → Electron 对话 → Testing/Review/Launch/Acceptance”端到端证据仍待完成。

本切片验证：

- `.venv/bin/python -m unittest tests.test_agent_technical_plan tests.test_build_unit_skeleton tests.test_agent_build_runner tests.test_product_technical_planning tests.test_project_planning_confirmation tests.test_technical_plan_response_protocol tests.test_technical_plan_settings tests.test_planning_stream_message_compatibility tests.test_project_plan`：118 项通过。
- 新增和修改的后端 Python 文件 `py_compile`：通过；`git diff --check`：通过。
- `node scripts/run-agent-technical-plan-view-tests.mjs`：通过。
- Frontend Node/Web TypeScript 检查和 Electron-Vite production bundle 使用仓库内锁定二进制执行：通过。全局 pnpm 11.5.1 要求 Node 22.13，而当前 Node 为 20.20.2，因此未把失败的全局 `pnpm build` 包装脚本记为产品 Build 失败。
- 正式后端当前未运行，`GET http://127.0.0.1:8000/health` 无法连接；本轮没有为了健康检查单独启动长期服务。
- 未执行 Electron 实机交互、真实模型规划或生成应用端到端验证；用户将自行启动主流程验证。

## 13. Contract 到 Runtime Generation 第五切片

2026-09-06 完成完整 Agent Contract 到独立 Runtime 模板业务代码入口的最小闭合：

- 新增内置 `agent-runtime-generate` Skill，定义业务 Agent 模块、Prompt 编译、模型/checkpointer 注入、Java Tool Adapter、可信身份与测试边界；Agent Runtime CodeRunner 在写文件前必须读取该 Skill。
- Generator 根据当前 `agent:<agentId>` 任务筛选完整 Agent Contract，并从 `agentSettings.tools.bindings[].endpoint.apiContractId` 精确投射相关 Java API Contract 和 Schema；无关 API 不进入生成上下文。
- 业务模块固定公开 `create_agent(*, model, runtime_context, checkpointer)`，调用 `create_deep_agent`；项目默认模型由模板统一通过 `init_chat_model` 创建和注入，业务模块不得二次初始化。
- 独立模板 Factory 保留内置 `chat`，同时严格加载 `config/agents/<agent_id>.json` 并通用装配 Deep Agent；只有显式 Extension 才加载 `app.extensions.<agent_id>`。Runtime Settings 提供经过 Origin 校验的 Java Backend 地址和独立 Tool Gateway 凭据读取边界。
- Tool Adapter 只能访问 Contract 声明的 Java Endpoint，身份与 Scope 只能来自可信 `RuntimeContext`，不能由 Tool 参数覆盖。

正式代码证据：

- `Backend/app/builtin_skills/agent-runtime-generate/`
- `Backend/app/agents/agent_runtime/agent.py`
- `Backend/app/agents/agent_runtime/generator.py`
- `Backend/app/services/builtin_skills.py`
- `Backend/tests/test_agent_build_runner.py`
- `Backend/tests/test_builtin_skills.py`
- 独立仓库 `Bettetman/agent-runtime-template@master` 的 `src/app/agent/factory.py`、`src/app/settings.py` 与 `tests/test_runtime.py`

当前限制：尚未让一个真实生成应用完成“规划确认 → Build 生成三文件 → Java Gateway → Python Tool → AG-UI 页面 Chat”的端到端运行；Testing、Code Review、Project Launch 和 Acceptance 仍未接入 Agent Runtime 专项证据。

本切片验证：

- Agent Runtime Runner、TechnicalPlan 与 Build Unit 聚焦测试 25 项通过；新增内置 Skill 完整性测试 1 项通过。
- XCodeAgent 变更 Python 文件 `py_compile` 与 `git diff --check`：通过。
- 独立模板使用自身 Python 3.12 执行 `compileall`：通过；模板 `git diff --check`：通过。
- 模板完整 `pytest` 在依赖准备阶段因下载 `anthropic==1.3.0` 超时而未进入测试；不能记为测试通过或产品代码失败。
- XCodeAgent 全量 `tests.test_builtin_skills` 有 1 项 Spring Boot Skill 文案断言差异；对比当前 HEAD 后确认预期句子与 Skill 缺失均已存在，本切片没有修改该 Skill。正式 Backend 未运行，`/health` 无法连接。
- 未运行 Electron UI、真实模型、Java Gateway 或生成应用端到端验证。

## 14. Agent Settings 可视化编辑第一批

2026-09-07 完成开发阶段 Agent Settings 的首个正式编辑闭环：

- Agent 详情以人设与 Prompt、模型、记忆、工具、Skills、知识库和上下文七段摘要替代原始 JSON；未实现的 Long-term Memory、Skills、Knowledge 和 Compression 继续只读显示为当前版本未启用。
- 第一批只开放角色、表达风格、System Prompt、业务约束和 Temperature；模型策略、能力要求、安全前缀与全部平台派生字段保持只读。
- Renderer 只提交严格 Settings Patch、Contract Hash 和 TechnicalPlan SHA；现有 `/application-page-planning/run` 以 `agent-settings-revision` Custom Event 和 `agentSettingsRevision` State Snapshot 完成 `get/prepare/confirm/abandon` AG-UI 生命周期。
- Prepare 从已确认 ProductPlan、当前 TechnicalPlan 和允许字段重新编译完整 Agent Contract，只写 revision draft。Confirm 复验 lifecycle、draft、Contract 和文档 Hash 后原子写 TechnicalPlan Markdown/JSON；Abandon 删除 draft 且不修改正式计划。
- 同一 Agent 的现有开发 execution 继续拥有资源锁。本地表单可以编辑，但 Prepare 前必须处理占用：运行中只能打开原任务并由用户停止；等待确认、失败或停止任务可在二次确认后使用现有 AG-UI `planControlAction=end` 结束。没有 backing execution 的孤立 Agent 锁会被定向清理。
- Confirm 只使绑定旧 Contract Hash 的当前 Agent BuildTaskPlan stale，并提示用户重新进入现有 Build DAG；不自动启动 Build，也不修改 ProductPlan、页面、Endpoint、实体或无关 Agent。

正式代码证据：

- `Backend/app/services/agent_settings_revision.py`
- `Backend/app/services/agent_settings_revision_models.py`
- `Backend/app/services/agent_settings_revision_support.py`
- `Backend/app/services/application_revision_lifecycle.py`
- `Backend/app/protocols/application_page_planning.py`
- `Frontend/src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/AgentSettingsView.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/AgentSettingsSummary.tsx`
- `Frontend/src/renderer/src/service/agentSettingsRevision.ts`
- `Backend/tests/test_agent_settings_revision.py`
- `Frontend/tests/agentTechnicalPlanView.test.ts`

当前限制：尚未开放 Tools、Memory、Skills、Knowledge、Context 的写能力；尚未实现独立 candidate/active 发布、配置历史回滚、完整质量证据定向失效和运行时热更新。Electron 实机交互、明暗主题和真实 Agent Build 由用户后续启动验证，不以自动化结构测试替代。
## 15. Agent 页面交互载体与模板设计

2026-09-07 确认 [Agent 页面交互载体与 UI 模板设计](./AGENT_UI_SURFACES_AND_TEMPLATES.md)。2026-09-08 用户进一步收敛本次交付：只完成前端模板和 Electron 设计阶段验收，Java Gateway、Agent Runtime、Build DAG 与生产端到端不在本次范围：

- 独立网页版问答确定为真正的 `standalone_page` 页面模板；普通页面中的 Agent 气泡确定为 `floating_panel` 页面增强能力，不建立第二个页面模板。
- 本次模板只使用静态 Mock 和本地状态，不接入真实 API、Java Gateway 或 Agent Runtime；生产对话核心与传输协议留待未来单独立项。
- ProductPlan 已在 `agents[].pageActionBindings[]` 增加 `surface.type/contextItemIds`，由 ProductPlan 确认页面载体和上下文白名单；UiDesign 仍只决定布局、位置、尺寸、响应式和视觉状态。当前契约已升级为 `product-plan.v7`，不兼容读取 v6。
- 当前 UiManifest 已升级为 `ui-manifest.v5`：`agent_surfaces` 保存固定模板模块、组件、`agent-ui.v1` 与配置摘要；独立页和浮层的必需部件由固定组件版本保证，原业务 action/information item 仍须完整。
- 当前三个业务模板继续用于普通页面和浮层宿主页；新增 `agentConversation` 模板只适用于 `standalone_page`。模板仍使用 React 18、隔离设计运行时中的 Ant Design 5、Pro Components 和静态 Mock，不引入新 UI 或聊天依赖。
- PC 浮层设计支持受限拖动、视口夹紧和边缘吸附；移动端使用固定入口和受视口约束 Card，不使用浮窗 Drawer，也不提供自由拖动。设计稿只使用已声明的 `contextItemIds`，不扫描页面 DOM。
- 原设计中的批次 1—7 属于本次前端模板范围；批次 8—12 涉及生成应用共享前端、Runtime、Java Gateway、Build 和生产端到端，已按用户确认排除，不作为本次“剩余批次”交付项。
- 后续迁移公司内网组件库只替换视图组件和主题映射，不改变 ProductPlan、UiManifest、AG-UI、Gateway、Runtime、thread 或安全契约；当前不预建无法验证的通用适配层。

本批次只新增和更新设计文档，没有修改 ProductPlan、UiManifest、模板源码、前后端类型、AG-UI、Build、Runtime 或外部模板仓库。未运行代码测试、Frontend Build、Backend `/health` 或 Electron 验证；这些结果不得被记为实现证据。

## 16. Agent Surface ProductPlan v7

2026-09-07 完成批次 1，只闭合 ProductPlan 当前契约及其直接消费者：

- ProductPlan 当前版本升级为 `product-plan.v7`，`pageActionBindings[]` 必须包含精确的 `surface: {type, contextItemIds}`；普通应用仍固定为 `agents: []`。
- `surface.type` 只允许 `standalone_page` 或 `floating_panel`；`contextItemIds` 归一化为去空白、去重的字符串数组，并只能引用绑定页面自身的 `information_items[].itemId`。
- 同一页面最多绑定一个同类型 Agent Surface；页面 action 仍必须真实存在并与 RequirementSpec 入口页面逐项闭合。
- 产品规划模型示例、提示词、严格原始 JSON 校验、确定性归一化、Markdown 展示/同步和 UiDesign 上游哈希均已覆盖 Surface。
- 后端公开页面规划协议和 Electron 当前 ProductPlan 版本门禁同步升级为 v7；批次 2 已继续完成前端 Surface 严格类型与只读展示，见第 16 节。
- 未修改 UiManifest、页面模板、UiDesign 生成、AG-UI、Build、Runtime、Java Gateway 或外部模板仓库，也没有新增依赖。

本批次验证：

- 测试先行：新增 Surface 契约测试在 v6 实现上 9 项中 8 项失败、1 项通过；实现后 `tests.test_agent_product_plan` 9 项全部通过。
- ProductPlan、TechnicalPlan Agent 消费、生命周期协议与模板生成聚焦回归共 82 项通过；应用生命周期和 Workflow 请求回归 96 项通过；页面规划公开协议 v7 断言 1 项通过。
- `tests.test_product_planning_retry` 纳入的扩大回归共 103 项，其中 102 项通过；既有 `test_4e_uses_materialized_page_implementation_contracts` 因测试输入缺少当前必填 `TechnicalPlan.agent_contracts` 失败。该测试及对应校验在本批次无 Diff，未越界修改，不能把扩大回归记为全绿。
- `tests.test_application_page_planning` 全模块 23 项中 18 项通过、3 项失败、2 项错误；失败集中在既有需求确认投影与权限事实 FakeModel 路径，本批次只新增的 ProductPlan v7 公开协议断言单独通过，不能把该模块记为全绿。
- 变更 Python 文件 `py_compile` 通过；前端规划版本测试 5 项、定向 ESLint、Prettier 和 Node/Web TypeScript 检查通过。
- 标准 `pnpm build` 在进入项目脚本前被本机 pnpm 代理的注册表签名/网络校验拒绝；直接使用仓库已安装的 `electron-vite` 完成 main、preload、renderer 构建并通过。该等价构建不能抹去标准命令的环境失败。
- 后端 `/health` 返回 `status: ok`，页面规划公开协议报告 `product-plan.v7`；未运行 Electron 实机 UI，因为本批次没有改变用户界面或视觉行为。

## 17. Agent Surface 前端只读投影

2026-09-07 完成批次 2，只把已确认的 ProductPlan Surface 投影到现有确认页和开发工作台，不提前实现模板、UiManifest 或聊天运行时：

- Electron 主进程与 Renderer 镜像类型为每个 Agent 页面操作绑定增加严格的 `surface` 投影，类型只允许 `standalone_page`、`floating_panel` 或安全只读的 `unknown`。
- 主进程投影同时携带入口页面名称、页面操作和 `contextItemIds`；上下文白名单只接受真实非空字符串并去重，不把数字、对象或未知 Surface 当作可执行配置。
- RequirementSpec/ProductPlan 联合确认视图展示载体中文名称、页面操作与上下文白名单；Agent 开发详情新增“页面交互载体”只读区块。
- 新增样式仅复用现有 `--wb-*` 主题变量和 Ant Design v4 `Tag`，没有增加依赖或独立颜色体系，明暗主题继续由现有主题变量覆盖。
- 本节记录的批次 2 未修改 UiManifest、UiDesign 生成或模板分类；这些已在后续前端模板切片完成。外部模板仓库、AG-UI、Runtime、Java Gateway 与 Build 仍未纳入本次范围。

本批次验证：

- 测试先行：确认视图、主进程投影和开发详情渲染测试在旧实现上失败；实现后 `node scripts/run-agent-product-plan-tests.mjs` 4 项通过，`node scripts/run-agent-technical-plan-view-tests.mjs` 通过。
- `tsc --noEmit -p tsconfig.node.json --composite false`、`tsc --noEmit -p tsconfig.web.json --composite false`、定向 ESLint、Prettier check 与 `git diff --check`：通过。
- 直接运行已安装的 `electron-vite build --mode development`：main、preload、renderer 全部构建通过。
- 标准 `pnpm build` 未进入项目脚本：pnpm 版本代理因 npm registry 签名/网络校验失败而终止，不能记为通过；等价的已安装 `tsc` 与 `electron-vite` 检查已分别通过。
- 已检查当前运行中的 Electron 应用，可正常进入现有开发工作台；最近的智能体项目仍是 `product-plan.v6`，按当前契约规则不能兼容读取。为避免改写用户历史项目，本批未伪造 v7 数据，因此新增 Surface 卡片没有 Electron 实例级视觉证据，使用真实 React + Less 打包及静态渲染断言覆盖。

## 18. Agent Surface 前端模板与 Electron 设计验收

2026-09-08 完成 [Agent 页面交互载体与 UI 模板设计](./AGENT_UI_SURFACES_AND_TEMPLATES.md) 的批次 3—7，并保持用户确认的纯前端模板边界：

- `ui-manifest.v5` 现可保存并验证 Agent Surface 的 Agent、类型、action、context、固定组件模块/名称/版本、配置摘要和派生必需部件证据；ProductPlan 页面事实变化会使旧设计证据失效，不兼容读取 v4。
- 三个既有业务模板继续支持普通页面与 `floating_panel` 宿主页；新增 `agentConversation` 只支持 `standalone_page`，模板选择器按当前页面 Surface 严格过滤。
- 独立会话模板提供桌面历史侧栏、窄屏历史 Drawer、消息/状态/Composer、明暗主题以及正常、加载、错误、空、Tool 和审批等静态设计状态。
- 浮动 Agent 由 UiDesign 在完整业务页面上增强：桌面入口区分点击与拖动并执行视口夹紧/边缘吸附，窄屏禁用自由拖动并切换到底部面板；上下文只来自 ProductPlan 声明的 `contextItemIds`。
- 生成器为 Surface 页面保留至少两轮有界契约修复，并把确定性固定组件 import 与静态 `configJson` 契约提供给模型；模型只能生成业务页面主体和组件组合，不再生成 Agent 聊天气泡、状态、拖动、独立页历史 Drawer 或主题实现。行选择操作仍不得只隐藏在 Ant Table `onRow` 返回值中。
- 本次没有修改 `frontend-template`、`springboot-template` 或 `agent-runtime-template`。早先误入三个外部模板仓库的工作树变更已备份后恢复，Java Gateway、Agent Runtime、Build DAG 和生成应用端到端仍不在本次范围。

本批次验证：

- Backend `tests.test_ui_design_generator` 30 项通过；UiManifest、模板兼容性、ProductPlan 与相关生命周期扩大回归共 225 项通过。
- `tests.test_application_page_planning` 的 5 项失败可在独立当前 `HEAD` 归档中原样复现，集中于既有权限事实 FakeModel 与确认投影路径；未把该基线失败记为本功能通过，也未越界修复。
- Frontend 模板兼容、Agent ProductPlan 与规划产物状态测试共 16 项通过；`pnpm build`（Node/Web typecheck + Electron main/preload/renderer bundle）通过；变更前端文件定向 ESLint 通过。
- 已运行 Electron 临时应用 `智能体页面模板验收`：独立页和订单浮层均生成并确认；两页 UiManifest 五类确定性检查全部通过。独立页实机覆盖窄/宽布局、明暗主题及正常/加载/错误/空状态；订单页实机覆盖业务表格保留、选中上下文、浮动入口拖动与吸附、迷你面板开关及正常/加载/错误/空状态。
- Electron 验收只停留在设计阶段；没有进入技术规划、Build、Java/Python Runtime 或生成应用端到端，因此不得把本节解释为批次 8—12 已完成。

## 19. Agent 入口页面范围识别修复

2026-09-09 修复 RequirementSpec 可能只记录独立会话页、遗漏普通业务页浮窗入口的问题，保持现有 ProductPlan v7 与 UiManifest v4 契约不变：

- `entryPageIds` 现在明确表示所有可见 Agent 入口页面，包括独立会话页和承载浮窗的普通业务页；空数组只表示没有可见页面入口，不能作为“全部页面”的缩写。
- “所有页面”或“其他页面”只覆盖 Agent 明确面向角色实际使用的页面；管理员专属、系统页和其他角色页面默认不绑定，角色范围与页面范围冲突时先请求一次聚焦澄清。
- 未进入 `entryPageIds` 的页面继续作为普通页面，不增加 Agent action、`floating_panel` 或模板限制。ProductPlan 仍只为已确认入口页面生成 `pageActionBindings`，UiDesign 仍只投影 ProductPlan 已声明的 Surface。
- 本修复只调整需求模型的产品语义提示和对应测试，不新增字段、Schema、API、AG-UI 事件、Graph 节点、兼容分支或依赖，也不改写已有应用的已确认产物。

本批次验证：

- 测试先行：新增入口范围测试在修改前按预期失败，修改后 `tests.test_agent_requirement_spec` 14 项全部通过。
- RequirementSpec 响应协议、ProductPlan Surface 和 UiManifest 下游回归共 38 项通过，覆盖普通应用空 Agent、独立页、浮窗和未绑定页面空 `agent_surfaces`。
- 真实需求模型探测成功连通并进入澄清，但模型先询问应用信息、角色和功能，没有在该轮生成 RequirementSpec，因此不能记为真实 `entryPageIds` 命中证据。
- 变更 Python 文件 `py_compile`、`git diff --check` 和后端 `/health` 检查通过；本次没有前端代码变更，因此未运行前端 Build、Electron 视觉、明暗主题或生成应用端到端验证。

## 20. Agent 浮窗候选启停选择

2026-09-10 在入口范围识别之后增加用户可控的页面级集成选择，当前 ProductPlan 契约升级为 `product-plan.v8`：

- 模型识别出的每个 Agent Surface 都必须包含 `enabled`，候选默认开启；`standalone_page` 固定开启，只有 `floating_panel` 可关闭。
- 开关位于 RequirementSpec/ProductPlan 联合确认右侧面板的“智能体 → 页面操作绑定”行，展示页面名称和路径；未识别页面不显示开关。
- 只有 `pending_user_confirmation` ProductPlan 可修改。保存通过 `/application-page-planning/run` 的 `agentSurfaceSelectionDraft` AG-UI 动作完成，并同步草稿 JSON 与 Markdown；联合确认会重新读取该结构化草稿，使选择进入原 Graph checkpoint 的后续流程，确认后仅只读展示。
- 正式 ProductPlan 保留关闭的候选及选择记录。UiDesign、TechnicalPlan 页面操作输入、PageImplementationContract 和工作台入口只消费启用投影，因此关闭页面不生成 Agent launcher、panel 或对应 Agent action；该页面其他信息项、业务操作、导航、状态和模板能力保持不变。
- ProductPlan 修订会保留用户已经做出的启停选择，不允许模型的默认开启值覆盖它。
- 修复保存成功后开关仍显示开启的问题：根因是右侧结构化视图优先消费了尚未刷新的外层 Workflow ProductPlan。前端现在为当前 run/thread 和 ProductPlan 版本身份保留最新保存快照；保存后立即显示新值，进入下一轮、确认完成或产物重新生成时自动回到新的 Workflow 权威快照。
- 未增加新依赖、普通 REST 产品接口、历史版本读取、迁移、兼容分支或双写逻辑。

本批次验证：

- 用户明确要求由其自行验证，因此本批次完成实现后未运行测试、lint、typecheck、build、后端 `/health` 或 Electron 明暗主题/交互验证；不得把本节解释为这些检查已通过。

## 21. Agent UI 生成应用 Mock Build 边界

2026-09-11 完成 Agent UI 固定模板标准化批次 6，并保持真实 AG-UI/Gateway/Runtime 未完成的交付边界：

- BuildContext 按页面从已确认 ProductPlan 与 TechnicalPlan 编译平台所有的 `agent-ui-build.v1` 合同；任务模型不能提交或覆盖 `source_refs.agent_ui`。
- Agent 页面 Frontend Agent 由平台内联 `agent-ui-surface-template` Skill，并读取生成应用中已注入的固定组件源码；页面只组合精确配置，不创建第二套聊天核心、Adapter、传输或 Mock 数据。
- Gateway Endpoint 继续保留在正式页面和 Unit 合同中，但 Mock 页面不消费它；普通业务 Endpoint 仍按原业务 API 检查执行。
- `frontend.agent_ui_mock_contract` 是 Agent 页面强制检查，即使普通 DAG 业务自检关闭也会执行；它检查固定组件、精确配置、默认 Mock Adapter、浮窗业务主体及页面侧网络禁用。
- Mock Build 完成后状态为 `mock_completed / real_integration=pending`，Workflow 停止在真实集成待办，不进入测试、启动或最终验收；Acceptance/finalize 也拒绝把该状态标记为完成。
- 本节只证明固定 Agent UI Mock 的生成应用开发合同。真实 Gateway 消费、Python Runtime 联调、会话持久化、Launch 和端到端 Acceptance 仍未完成。

阶段 6 及其 Agent ProductPlan、固定模板、Build DAG、业务验收和 Workflow 相邻回归共 303 项通过；阶段 7 的完整 Electron 主题/响应式/状态矩阵及最终边界见下一节。

## 22. Agent UI 固定模板 Electron 回归与移动端 Card 收敛

2026-09-11 完成固定模板标准化批次 7—8，并按用户确认撤销浮窗移动端 Drawer 方案：

- 修复 `AgentChatCore` 普通状态白屏：旧实现先创建 `AgentExclusiveState` React element，再用 element 真值选择分支，导致 normal、tool、approval 和 success 都只渲染空状态容器。当前改为按 `loading/empty/error/stopped` 状态值判断，其他状态稳定渲染消息栈。
- UiDesign 固定组件与生成应用内置资产统一使用受视口约束的浮窗 Card；320/767 宽度禁用自由拖动，768 及以上沿用 Ant Design `md` 桌面断点。`AgentMobileChatDrawer` 只承担独立会话页历史列表，不再承载浮窗聊天。
- 移除 UiDesign 独立页和浮窗中仅供预览的手工状态切换条；normal/empty/loading/running/stopped/error/tool/approval/success 的状态组件保持不变，后续只接受 Adapter/运行事件驱动。
- 新增 `test:agent-ui-electron-runtime`，在项目安装的真实 Electron 中覆盖 1440/1024/768/767/320、亮暗主题、normal/stopped 交互、无横向滚动、Escape、焦点返回、ARIA live/Composer 标签、渲染异常和零 HTTP/HTTPS Agent 请求，并输出可复核截图；其余状态由 `AgentChatCore` 确定性渲染测试覆盖。
- 当前交付只证明 `agent-ui.v1` 固定 UI、`ui-manifest.v5` 确定性证据、生成应用 Mock Adapter 和 Mock Build 边界；Java Gateway 消费、真实 AG-UI、Python Runtime、会话持久化、Launch 和端到端 Acceptance 仍未完成。

本批次验证：

- Frontend Agent UI Runtime 与模板兼容专项通过；Node/Web TypeScript typecheck、定向 ESLint、Electron/Vite build、真实 Electron Runtime 矩阵和 `git diff --check` 通过。标准 `pnpm` 入口因本机 pnpm 8.15.9 registry 签名校验失败未进入项目脚本，等价检查均直接使用仓库已安装二进制完成。
- Backend Agent UI scaffold、模板生成、Build DAG 和业务验收定向回归 160 项通过；`/health` 返回 healthy。
- 统一 Backend 组合回归运行 254 项，248 项通过；剩余 6 项可在各自模块独立重跑中复现，分别为既有 Spring Boot Skill 精确文案断言 1 项，以及 application page planning FakeModel/确认投影 5 项。本批未修改这些无关区域，也未将失败记为通过。
