# Agent Runtime 流式接口设计改造方案

## 页面动作与跨阶段一致性修复（2026-10-31）

本节收敛此前回检的七类断点。主架构保持实体/Agent → 接口 → 页面 → DAG；通过共享语义和投影消除重复决策，不以跳过业务实现、伪造接口或取消产物确认为通过条件。

### 产品动作只作一次语义决策

业务操作继续使用 `business`，客户端界面操作使用 `interface`，Agent 会话操作显式使用 `agent`；导航、外部目标和有序组合分别沿用既有类型。缺失/非法类型返回产品阶段诊断，不能悄悄补成业务接口。

```json
{
  "actionId": "policy_follow_up",
  "name": "继续了解适用条件",
  "description": "在当前会话向政策助手追问。",
  "requiresConfirmation": false,
  "behavior": {
    "type": "agent",
    "agentId": "leave_policy_assistant",
    "operation": "send_message",
    "message": "请继续说明上述政策的适用条件。",
    "expectedResult": "用户在同一会话获得后续说明。"
  }
}
```

- `send_message` 使用当前线程；message 为空表示自由输入入口，非空表示产品明确的推荐问题/追问。固定组件持续显示这些声明动作，不依赖解析模型自然语言。
- `clear_conversation` 使用空 message：切换到新客户端线程并清空展示，保留服务端历史和既有业务操作。requiresConfirmation=true 时保留确认。服务端历史删除仍是独立业务能力，不能从“清空”推断出来。
- 页面绑定负责 Surface 与上下文；显式 Agent 行为负责动作归属。平台在归一化、UI 投影及 Build 时合并动作集合；不要求用户在两个阶段重复选择同一 Agent。未修改的多动作绑定保持原全集。
- sequence 允许 Agent 步骤，每步除 stepId 外使用相同的 Agent 行为字段。只有 business 步骤要求技术 Endpoint；整个 action 已有 Agent 绑定也不能吞掉业务、导航或界面步骤。一个组合可以顺序消费不同 Agent，不能被单一 Agent 独占。

### UI 与实现必须接得住产品表达

UI 设计支持产品已允许的同页一个 standalone_page 与一个 floating_panel，不再额外要求一页一个 Surface、一个 Surface 一个动作。正式逐页合同的主 Surface 保留原位置，其他 Surface 通过 companions 顺序列出；配置变量分别为 AGENT_UI_CONFIG、AGENT_UI_CONFIG_1 等，生成器与验收器完整消费。

固定资产提供 actions（actionId、label、operation、message、requiresConfirmation，以及组合步骤的 stepId）、推荐/追问按钮和客户端清空。混合流程由页面业务代码先调用已声明业务 client，再通过固定组件 actionRequest={requestId,actionId,stepId,message?} 发起 Agent 步骤。onActionResult 回传结构化状态；只有 completed 才允许继续后续步骤，等待、取消、失败或未知均不得自动重放。固定 Runtime Adapter 持有线程和官方 AG-UI 客户端，保留起始动作 ID 用于澄清/确认续接，并拒绝同一已提交 runId 再次发送。

动态模型生成的建议列表没有在当前文本输出上增加隐式协议；产品应明确按钮消息。若确实需要结构化动态建议，应另行声明正式结果合同，不能靠截取模型正文或虚构字段交付。

### 接口身份与修复职责

Endpoint 身份为 apiContractId + endpointId。页面 technicalReferences.endpointDependencies 保留完整身份，requiredEndpointIds 只是紧凑的依赖 ID 集合，不是全局身份索引。短 ID 仅在页面作用域唯一时可省略契约；同页引用同名接口时，action_implementations 的直接绑定及 stepBindings 使用 apiContractId 消歧。规范化、API 校验、页面编译、Build context、Frozen manifest 和 Runtime 目标解析均按完整身份处理，不再覆盖、取第一条或一面允许一面全局拒绝。

技术编辑器可用 request_product_revision 报告具体 pageId/actionId、产品原文与归属建议。该工具只记录建议并立即结束，不编辑已确认 ProductPlan，也不冒充用户确认。生成和确认修复入口收到此类诊断后保留候选，停止该技术修复循环；AG-UI clarification.repair_target=product_plan 指明需走正式产品修订。技术缺失继续留在技术阶段修复。前端不再在产品归属错误上展示“重新生成技术规划”的按钮，提示通过现有对话发起产品规划修订。

当前用户项目 ds1031a2 的已确认 ProductPlan 未被本补丁改写：推荐问题、追问、话题提问和客户端清空的既有业务分类，应通过正式产品修订更正并确认，之后重新生成受影响的 UI/技术候选。不能将平台修复当作已确认产物也已更新。

### 验证与边界

新增定向回归覆盖同页多 Surface/多动作的设计及正式验收、显式副动作归属、混合步骤保留、同名 Endpoint 跨阶段定位、缺失行为和局部修订保留绑定；技术编辑器用真实工具执行的离线模型验证上游修订出口立即结束。SDK 回归增加多动作、续接动作保留、重复请求拒绝和空白客户端线程不发请求。

代码已写入，尚未据此宣称端到端通过。按用户约束，仅执行一轮必要验证，任一步失败即停止，实际命令结果以本次交付回复为准；不自动重启服务、反复调试或修改已确认用户产物。

## 能力范围纠正（2026-10-31，模板 2026.10.31.2）

此前把首批纯文本 smoke 的测试范围提升成全局能力禁令，导致 `leave_policy_assistant` 启用 Tool/Skill/Knowledge 后在技术规划阶段被拒绝。这不是 AG-UI/SSE 的限制。本次用户明确要求修复，以下规则替代文内此前“首批关闭全部内部模块”的范围说明：

- 公开接口继续采用 messages 文本输入、文本输出；内部业务 Tool、Skill、Knowledge 按已确认七模块配置启用，不能为通过校验自动清空或降级。
- Tool 必须精确引用业务 Endpoint，并进入 Agent/接口/页面共同的前置依赖闭包，禁止绑定 Agent 运行入口构成递归。写入 Tool 必须显式声明 confirmation 或 approval_required，并继续经过原有实际执行前确认验收。
- interactionKinds 允许声明 clarification、confirmation、approval_required 的不重复子集；Runtime 和页面都按正式合同校验交互，不再一概拒绝恢复。其他结构化业务输入/结果仍需独立正式合同，不能由 Tool 启用隐式开放。
- 平台 executionCapabilities 分别记录业务工具名称、正式资源清单、模型请求上限；资源字节摘要同时交付。实际装配不能带入默认文件写入、task、Todo、shell 等未声明工具。
- Skill/Knowledge 沿用已支持的项目内精确文档合同，独立 FilesystemBackend + SkillsMiddleware/MemoryMiddleware；技能按需读取使用平台 resource_tools，只允许白名单中的技能文档，不能借资源启用读取任意工作区文件。每文档 64 KiB、总文档 128 KiB。
- 无业务工具/技能时每 Run 最多一次实际模型请求；业务工具或技能启用时最多八次，保持 120 秒整体期限和 10 秒清理期限。SDK 重试也计数。每授权验收 case 仍只发起一次会话，失败/未知不自动重放；targetAcceptance 明确列出各 case 的 modelRequestLimit。
- 工具循环产生的多段助手正文按同一用户轮次合并，再校验客户端镜像历史，所有消息 ID 仍按完整 checkpoint 防重复。

涉及代码：runtime_agent_contracts、runtime_endpoint_contracts、runtime_endpoint_contract、runtime_execution_policy、runtime_endpoint_dependencies、runtime_target_acceptance/launcher；生成提示词、Python 工具/资源和前端合同验收；固定页面适配器、类型、接入说明；外部 execution_policy/execution_guard、AG-UI、执行预算、历史服务及离线 fixture/用例。当前补丁计划单次验证，任何失败立即停止，不自动重试；实际通过/失败结果以本次交付回复为准。真实业务 Tool/Skill/Knowledge 生成、外部供应商及 Electron 行为仍需实际环境验收。

日期：2026-10-31。状态：R1 原有验证记录保留；R2–R4 纵向接线及 R5 验收执行代码已写入，等待统一人工验证。尚未证明端到端闭环通过。模板源码 revision：`2026.10.31.1`。

**本质问题：现有接口设计围绕实体、普通请求/响应和字段映射组织，无法完整描述、确认和交付 agent-runtime 的 AG-UI 流式接口。**

本文是解决这一问题的唯一方案文档，统一收录问题依据、目标合同、开发流程、Tool 扩展约束、实施清单、风险和验收。不再分别维护详细设计、实施补充与改造计划，也不需要按历史补充判断优先级。本文不等于当前已实现合同。

R1 本轮补丁覆盖线程互斥/busy、严格续接类型、单 worker/命名空间进程锁、输入/输出和身份/进程容量、整体 deadline 与清理。Studio 同步核对 `/health.deployment` 的实际持锁、worker、应用/namespace 摘要，并沿脱敏日志在不可收口时回收绑定进程一次。外部模板已授权写入，revision 为 `2026.10.30.1`；旧生成工程必须走现有模板更新/确认，不由启动器修补。完整流式接口开发闭环仍需后续 R2–R5。

本轮单次验证记录（2026-10-30）：

- 改动 Python 文件的定向 `ast.parse` 语法检查通过；Studio `tests.test_runtime_execution_guards` 与原健康合同 3 个用例合计 7 项通过。
- 模板 `mvn -f template-engine/pom.xml verify` 通过；`./scripts/ci/verify-code-template-release-revision.sh HEAD` 通过，其内部已执行 protected-surfaces，不再重复执行该脚本。
- 使用仓库 `application-local.yml` 和显式绝对源码路径真实启动 Engine JAR 一次，单次 Generate 产生包含 MySQL 扩展的 Runtime 工程；在生成工程运行 `test_execution_guards.py`、`test_runtime_admission.py`、`test_runtime.py`、`test_debug_profile.py`、`test_page_context.py`，39 项通过。包括第二进程锁竞争、严格续接值、身份/进程容量、只读不构造模型、真实 LangGraph/SQLite 中断查询、发送背压、断流和清理超时。
- `curl -sS --fail-with-body --max-time 5 http://127.0.0.1:8000/health` 通过。各项仅执行一次，无失败后的自动修复/重试。

验证边界：Python 回归复用 Studio 的 Python 3.12 环境，未验证生成工程的 `uv.lock` 独立安装；未调用真实模型或连接 MySQL。无前端改动，未运行前端 build/Electron；不作为 R5 端到端验收。轻量查询当前使用 LangGraph 的中断通道常量，测试存在该常量弃用警告，依赖仍限定 LangGraph 2.0 以下；不能据此承诺跨版本 checkpoint 读取。任意自建后台任务或不等待真实同步工作退出的业务扩展不在本轮清理保证内。

R2 推进记录：先增加内部能力模型和运行 Endpoint 编译器（`domain/runtime_endpoint_contract.py`、`services/runtime_endpoint_contracts.py`），不接通生产规划、设计或开发入口。编译器要求完整的新能力声明和显式 business/agent_runtime 判别，复用标准调用身份与七模块验证，物化无实体运行入口，拒绝派生编辑、悬空/重复入口、路由及文件名规范化冲突；能力快照摘要排除 Prompt/执行状态等非公开能力事实。现有生产 API 归一化会丢弃部分新字段、业务校验仍要求实体，因此不得只在规划 JSON 中增加派生入口后即开放。本批内部组件可单独验证；v8 双文件、execution_policy 交付/加载及规划/页面/依赖消费者须后续完整接线，R2–R4 切换要求不变。本轮定向验证结果以交付说明为准。

## 当前交付记录（2026-10-31，未运行验证）

前面的 R1 验证和 R2 内部基础记录描述此前阶段；当前生产接线以本节为准。用户明确要求整体接线后自行测试，本轮未执行 Python 语法/导入检查、回归、health、pnpm build、Maven、release gate、供应商调用或 Electron 操作。此前 R1 的通过结果不覆盖本次修改。

| 环节 | 本次代码接线 | 仍需验证的事实 |
| --- | --- | --- |
| 正式规划 | Agent 显式纯文本 interactionContract；确定性生成 `agent.<id>` / `agent.<id>.run`，普通 Endpoint 显式 business；当前生产归一化保留 Runtime 分支 | 完整规划、修订与 Markdown 同步均产生合法当前合同；旧生成工程需按原模板更新流程处理 |
| 接入设计 | v8 business/agent_runtime 判别；运行接口只读 Agent 绑定，无 CRUD 映射；JSON 最后发布并绑定 MD 原始字节及 revision，共用可重入锁与 fsync | 并发保存、双文件替换中断、Markdown 外部修改应阻断；不会将人工编辑静默当作已确认 |
| 执行策略 | 已确认 DAG 声明平台写路径；Build 成功后平台装配；实际注册可为正式 Agent 子集；启动前校验能力/实现/策略摘要 | 当前 DeepAgents 锁定版本的中间件替换与真实编译节点集合；缺策略或旧模板不能靠默认 chat 通过 |
| 输入与能力 | Runtime 原始请求白名单，禁止系统/工具角色和非空 state/tools/context；只追加新 user 尾项，历史由 checkpoint 持有，页面上下文计入完整输入上限；真实工具意图在分派前失败 | 真实官方 SDK 消息序列、第二轮历史镜像、恶意请求与不同供应商响应 |
| 三目标开发 | Agent、运行 Endpoint、页面共用解析器；运行 Endpoint 为无模型任务前置 Unit，依赖 Agent；任务依赖穿透空 Unit；共享工厂锁及符号依赖摘要 | 从三个入口分别创建/确认 DAG、复用和增量更新；不把消费页面反向纳入 Agent 开发范围 |
| 验收执行 | Pending 含 targetAcceptance、固定问题、次数、期限；确认文案明确真实模型费用；单测后完成前调用协调器；prepared/running/预算先落盘，同 DAG 不重发 | 真实供应商、进程取消、后端重启及结果未知；成功记录必须同时绑定 DAG/Build、当前输入、验证器及清理事实 |
| 页面验收 | 独立临时工程副本，保留实际 bootstrap/Provider/样式/主题，只替换副本路由；真实 Electron 操作目标输入区；页面官方适配器同一次发送提供协议和正文显示证据 | 当前生成页面的加载、独立/浮窗入口、两种主题；不改变正式路由或普通 Preview |
| 完成状态 | 运行接口复用 Agent 完成事实；接口与 Agent 不重复计数；登记前只读要求同轮真实验收记录；实现/模块/验证器变化标记需更新 | 目标成功后应用门禁、再次开发、模型设置变化与其他 Agent 保持独立 |

主要新增入口与责任：

- Studio `services/runtime_endpoint_contracts.py`、`runtime_endpoint_dependencies.py`、`runtime_agent_implementation.py`、`runtime_execution_policy.py`：公开合同、三目标范围、实际符号来源和平台策略。
- Studio `domain/agent_endpoint_design.py`、`services/agent_endpoint_design.py`、`workspace/endpoint_design_lock.py`、`endpoint_draft_pair.py`：当前 v8 运行接入分支及双文件事务。
- Studio `services/runtime_target_acceptance.py`、`runtime_acceptance_bridge.py`、`runtime_acceptance_frontend.py`：预算、证据、独立进程/视图协调；`protocols/runtime_acceptance.py` 与 `/agent-runtime-acceptance/run` 为独立 AG-UI 领取/提交桥，不能签发任意模型调用。
- Electron `main/runtimeAcceptanceWorker.ts`：官方 SDK 单次 Endpoint 调用、真实 BrowserWindow 页面观察；原始验证器源码随构建嵌入并在 AG-UI 领取时交付摘要，开发环境要求当前桌面源码一致。桥中断不把已领取任务重新入队。
- Renderer `FieldMapping/AgentEndpointReview.tsx`：业务 Hook 挂载前分流；正式目录、快捷任务与结果面板读取正式 Endpoint，不再另外派生一行 Agent 接口。
- 固定页面适配器 `builtin_templates/agent-ui-frontend/src/apis/agentConversationRuntime.ts`：官方客户端生命周期/身份/终态一致性检查、独立只读查询客户端、结果未知保留及显式新会话。
- 外部模板 `src/app/agent/execution_policy.py`、`execution_guard.py`、`server/execution.py` 及 factory/AG-UI/请求/历史路径同步；修订号 `2026.10.31.1`。模板执行策略不是客户端配置，也不由模型生成任务修改。

新增离线回归源码但本轮未执行：Studio `tests.test_runtime_target_acceptance`（范围一致、预算签认、防重、页面归属），模板 `tests/test_text_policy.py`（实际图无工具节点、冻结策略、恶意入参、模型工具意图、供应商二次发送拦截）。旧模板离线 fixture 显式装配当前测试策略和无网络模型，不能为生产提供默认策略。

### 用户统一测试顺序

1. 使用当前源码启动 Studio 后端并重启 Electron，确保桌面验收桥运行。先完成构建/health/定向回归；任一失败停止，不自动循环修复或继续消耗供应商请求。
2. 按现有 Template Engine/模板更新机制交付 `2026.10.31.1`；不直接修补旧生成工程。当前合同不兼容读取旧 v7 设计或缺 interactionContract/kind 的旧规划，需重新规划、审阅和确认。
3. 用一个无实体、关闭登录与授权、Tool/Skill/Knowledge 禁用的 Agent 应用检查：正式接口目录只有一个对应运行入口，接入工作台没有数据库选表/字段映射，确认设计不会发模型请求。
4. 分别从 Agent、运行 Endpoint、绑定页面发起开发并确认 Pending。确认文案应包含真实验收固定问题和调用预算；任务生成及 Build/单测后才发生真实调用。每个目标在自己的已确认 attempt 内最多一次真实供应商请求。
5. 页面目标应使用独立 Electron 视图的真实输入区和正式 Runtime Adapter，收到非空助手文本、匹配的 CUSTOM/快照/RUN_FINISHED 并显示正文，结束后回收临时前端和 Runtime；普通 Preview 与正式路由保持原样。
6. 使用两个正式 Agent、仅当前 Agent 已实现的工程验证当前目标 Probe/页面可验收；完整 Preview 仍要求正式集合齐全。再检查混合 business Endpoint、页面依赖和整体完成数，避免重复统计。
7. 对断流、取消、忙碌、超时和失败检查：不登记完成，不自动重发，不把只读同步当作原请求成功；已消费 attempt 重入应被拒绝，只有重新规划并明确确认的新 DAG 才产生新预算。
8. 修改 Prompt/模型：运行接口公开接入基线保持有效，实现与验收需更新；修改 Builder/共享符号或验证器则旧完成证据不得复用。编辑正式 Endpoint Markdown 后应标记失效并阻断使用，人工内容保持原样。

首批边界仍为纯文本会话。正式规划/修订候选会先证明原运行入口是合法的当前派生形状，再根据 Agent 权威重编译名称/职责描述或删除已移除 Agent 的合法派生入口；路径、身份或能力被独立篡改仍拒绝，开发/启动读取不进行修复。新版正式派生快照仍须按原正式确认与接入审阅门推进。对不成对或人工编辑的正式接口文档，当前入口会明确阻断覆盖，需先处理当前文档后重新审阅；系统不会擅自删除或覆盖人工版本。临时前端要求当前 rsbuild 模板及已安装依赖，不自动安装或猜测其他工程入口。所有真实环境行为与打包效果均待本轮统一测试。

## 1. 要解决的问题与目标

保留现有的需求与产品规划、技术规划、UI 设计、详细设计就绪检查、Pending DAG 确认、Build、单测及应用测试阶段。改造接口对象贯穿这些阶段的方式，使 Agent 运行接口成为能够正式设计、确认、开发和验收的 Endpoint。

首批交付范围：一个有正式产品页面入口、无业务实体依赖、无业务 Tool/Skill/Knowledge 依赖的纯文本 Agent。从页面、运行接口或 Agent 三种入口发起开发，均进入同一套确认与执行机制，最终在真实 Electron 页面完成 AG-UI 文本流调用。

这是接口相关流程的纵向改造，涉及前后端和 Runtime 模板；不重建工作流引擎、调度器或另一套开发生命周期。仅完成接口列表、来源下拉或七模块生成，均不算本次交付完成。

## 2. 统一设计决定

1. 将标准 Agent 运行入口纳入正式 api_contracts，成为可设计、确认和验收的 Endpoint；保留现有路径和调用身份。
2. 接口采用 business / agent_runtime 严格分支。Agent 流式合同描述消息、交互与结果语义，不套用数据库字段映射。
3. Agent 是公开能力和七模块的权威；运行 Endpoint 是其确定性入口，页面技术调用统一引用 Endpoint。
4. 同一依赖解析结果贯通设计门禁、Build 上下文、冻结、DAG、切片、资源锁、Probe 与失效。
5. 运行 Endpoint 使用无模型任务的依赖 Unit；目标范围内的现有 app:integration 节点承载独立验收屏障。复用现有通用路由和官方 AG-UI 适配器，不新增路由、转发层或 runtime_binding 平台任务。
6. 设计确认、实现完成和运行接入验收分别记录；应用层每个 Agent 只统计一次，聚合就绪必须包含接口验收。
7. 首批保持 Runtime v1，完成纯文本纵向闭环；Tool、结构化输出及写入能力按本文约束分批开放，不提前承诺未实现能力。

适用范围为显式 agent_runtime_direct 应用。普通业务接口的现有映射语义及 Java 实现路径保持；新增产品交互遵守仓库端到端 AG-UI 规则。

[Runtime 整体设计](AGENT_RUNTIME_DIRECT_DESIGN.md)、[正式合同](AGENT_RUNTIME_DIRECT_CONTRACTS.md)、[构建与运行](AGENT_RUNTIME_DIRECT_BUILD_RUNTIME.md)、[安全与调试](AGENT_RUNTIME_DIRECT_SECURITY_DEBUG.md) 作为基础架构资料保留。本次流式接口改造的范围、合同与实施要求统一由本文说明。

## 3. 当前基线与问题分类

以下结论来自本次会话的定向静态阅读，未执行运行验证。此前源码核对记录的 Studio 提交为 `7792fec3`，模板为 `b3e9141`、源码 revision 为 `2026.10.29.2`；这些是阅读记录，不是本次重新确认的 HEAD 或发布证据。实施时只复核相关文件变化。

外部模板依据 `/Users/frank/IdeaProjects/springboot-template/template-source/code/agent-runtime/base/src/app` 的已读源码。MySQL 实现由 `extensions/python_business_mysql/files/src/app/infrastructure/database.py` 提供；checkpoint SQLite 与业务数据库分开。模板支持数据库禁用时返回 not_required，实际生成包装配和启动仍需验收。

### 3.1 当前已具备的能力

- `agent_invocation()` 派生稳定的 `agent.<agentId>.run` 和 `/agents/<agentId>/run`。
- ProductPlan 已有 Agent 身份、页面动作归属和正式确认；TechnicalPlan 已有七模块合同。
- 已有 Agent 独立开发、Unit Graph、Pending/Formal DAG、执行切片、资源锁和同轮模块完成证据。
- 固定前端资产使用官方 AG-UI SDK，支持文本流、匹配 Agent/thread/run 的结果、待交互、断线需同步及禁止自动重放。
- 模板已有通用运行路由、Agent 注册、隔离 checkpoint 与只读交互查询。
- 当前工作区已经包含 Profile 健康检查代码修复及定向回归用例；真实 Debug 启动尚未验证，本方案不将其重复记为未修复代码。

### 3.2 现存问题

| 编号 | 问题与源码入口 | 影响 |
| --- | --- | --- |
| B01 | 模板 `interaction/service.py` 在 checkpoint 读取、续接和图执行之间缺少同线程互斥 | 并发续接同一待办；执行中的查询可能被返回为 completed |
| B02 | 模板 `InteractionResponse.value` 为 Any，续接仅核对 interactionId | 绕过前端后，错误类型的澄清/确认值仍可进入 Command(resume) |
| B03 | `integration_test_runner.py` 仅在 Agent scope 传入目标 Agent | 页面相关启动检查可能被不属于当前目标的未实现 Agent 阻断 |
| B04 | `agents/main/document_sync.py` 同时要求五字段 Agent 和旧 Gateway 等扩展 | Markdown 同步提示与当前严格校验冲突 |
| B05 | Endpoint 有效性绑定整个 TechnicalPlan；模块事实包含全部 factoryAgentIds | 无关修订扩大接口 stale 和 Agent 模块需更新范围 |

### 3.3 相对目标尚未实现的能力

| 编号 | 缺口 | 现有表现 |
| --- | --- | --- |
| G01 | 正式运行 Endpoint 与公开交互合同 | 接口目录仍为 Agent 派生只读视图，页面动作仍使用 bindingType=agent |
| G02 | Agent 分支的正式设计、草稿与确认门禁 | 当前 v7 仅支持业务映射；无 Tool Agent 不检查自身运行接口设计 |
| G03 | 三目标统一依赖解析与无任务 Unit 编译 | 单 Endpoint 仍按业务 bootstrap/映射处理；直接任务展开不会穿透空 Unit |
| G04 | 运行接口独立接入证据 | Agent 七模块成功与接口可调用未形成联合就绪条件 |
| G05 | 正式入口后的状态和聚合统计 | 新增正式 Endpoint 后需要去重；当前尚未发生该重复计数问题 |
| G06 | 空实体与判别字段 | project_plan 当前要求非空 entity_ids，运行 Contract 需独立规则，不能放宽全部业务校验 |
| G07 | 固定资产合同与文件身份 | Agent UI 精确配置校验需同步；endpoint_design_stem 需拒绝不同 ID 的规范化碰撞 |
| G08 | 验收的实际 Workflow 执行点 | app:integration 仅是 Unit 归属，尚无单测后、完成前的 targetAcceptance 协调与防重复执行记录 |
| G09 | 部分实现的真实页面联调 | 短 Probe 会回收，完整 Preview 要求全量；需增加独立临时 probe 会话及目标真实页面环境 |
| G10 | 能力策略与实际代码证据 | 缺少生成工程的受控执行策略交付；共享 factory.py 不能整体从实现摘要中排除 |
| G11 | 入口边界及可执行用例 | 标准 AG-UI 类型尚不等于入口白名单；需服务端历史投影、真实验收请求及全 run 预算 |

## 4. 目标对象与权威来源

### 4.1 三层关系

```text
产品关系：ProductPlan 页面动作 → 业务 Agent
技术引用：PageImplementationContract 动作 → 正式运行 Endpoint
实现关系：正式运行 Endpoint → TechnicalPlan Agent 合同
```

产品层决定动作属于哪个 Agent；技术层将该事实编译为唯一 Endpoint 引用；运行时沿 Endpoint 解析到现有适配器和标准 Agent 路由。这些是同一关系在不同层的投影。

Agent 的业务 Tool 继续引用普通业务 Endpoint。Agent 的标准运行入口不能同时成为它自己的 Tool 前置依赖，避免构造递归调用或依赖环；Agent-as-Tool 不在首批范围。

### 4.2 权威分工

| 对象 | 权威内容 | 不承担的职责 |
| --- | --- | --- |
| `.devagentstudio/application.json` | 当前应用开关和配置 | 不从模型、历史请求或文档投影反向覆盖 |
| ProductPlan.agents | Agent 产品身份、能力语义、启用入口和动作归属 | 不定义路由、模型或运行协议 |
| TechnicalPlan.agent_contracts | 七模块与公开 interactionContract | 不独立维护第二份页面归属 |
| TechnicalPlan.api_contracts | 正式 Endpoint 目录；运行入口由平台物化 | 不由模型另造运行路径或另一份 Agent 能力 Schema |
| Endpoint 详细设计 | 本入口的确认版本、实现绑定和交互合同快照 | 不直接修改 Agent 能力、路径或产品归属 |
| PageImplementationContract | 动作的正式 Endpoint 引用及适配器投影 | 不另建一套直接 Agent 执行配置 |
| 生命周期与验收记录 | 任务完成事实、当前就绪状态和接入证据 | 不作为能力或路由的配置来源 |

技术 Agent 的持久 pageIds 拟移除，页面列表从产品已启用入口派生为只读 consumerPageIds。规划、同步、设置修订、工作台、资源锁、前端类型与摘要消费者须同批切换，不增加旧字段回退。

## 5. 接口合同设计

### 5.1 实现关系与交互方式分开表达

`implementationKind` 负责选择详细设计和开发分支；Agent 的 `interactionContract` 表达公开交互能力；`invocation` 表达确定性调用描述。不能将“流式”与“绑定 Agent”混成同一个来源字段，也不增加可与合同冲突的自由 stream 开关。

| 分支 | 实现绑定 | 首批交互方式 | 详细设计内容 |
| --- | --- | --- | --- |
| business | 既有数据库、外部 API、静态来源 | 保持当前已支持合同 | 保留查询、写入、字段映射与业务取值语义 |
| agent_runtime | 唯一正式 Agent | 固定为 AG-UI 文本会话流 | 调用目标、公开能力快照和固定会话规则 |

本次不承诺所有普通接口自动获得流式能力，也不引入通用 SSE 转换器。新增或实质变更的产品动作端到端遵守仓库 AG-UI 规则，前端使用 `@ag-ui/client`、`@ag-ui/core`，不新写 fetch/SSE 解析协议。

### 5.2 正式身份与物化

以 `order_assistant` 为例：

| 字段 | 拟定值 |
| --- | --- |
| Agent ID | order_assistant |
| API Contract ID | agent.order_assistant |
| Endpoint ID | agent.order_assistant.run |
| Endpoint.kind | agent_runtime |
| Endpoint.agent_id | order_assistant |
| Method / Path | POST /agents/order_assistant/run |
| Contract.entity_ids | [] |

`kind` 是技术 Endpoint 声明的判别字段；详细设计使用 `implementationKind`，后端强制其与正式 Endpoint.kind 一致，二者不能独立选择。business 与 agent_runtime 均显式声明类型，不能通过 POST、路径或实体数量推断 CRUD/运行职责。所有引用使用 apiContractId + endpointId 复合身份，禁止仅按 endpointId 查找后取第一项。

复用 `agent_invocation()`，用同一个确定性函数完成规划生成、局部编辑、Markdown 同步、正式确认和重读校验。重复物化必须稳定；ID 冲突、悬空引用、重复入口或不匹配的路径明确拒绝。

ID 与实际设计文件 stem 均须唯一。不得因点号、连字符或复合 ID 规范化碰撞而覆盖其他设计，不自动改写正式 ID。

用户编辑 Markdown 的能力章节时进入正式修订；单独修改派生 ID、路径或目标 Agent 时返回明确错误，不静默丢弃编辑再确认。

### 5.3 首批公开能力

首批 interactionContract 的严格形状为：

```json
{
  "mode": "conversation",
  "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": false},
  "textOutput": true,
  "publicStateSchema": null,
  "outcomes": [],
  "interactionKinds": []
}
```

七模块仍须提供各自合法配置，不能用空对象代表实现完成。实际可调用工具集合必须与正式业务 Tool 和平台只读技能工具一致；框架默认工具不得自动进入集合。消息构造、模型调用、受控日志和 checkpoint 持久化属于 Runtime 基础设施，不因此获得业务写入、任意文件操作或命令执行权限。

用户提问通过标准 messages；页面 contextItems 是非可信业务说明，不是受类型约束的业务输入或授权依据。不另设 question 参数，不开放未声明的结构化业务成功结果或公开业务状态；交互种类由 interactionKinds 显式声明。

Runtime 共有的待交互与恢复基础设施仍须正确校验和安全收口；其存在不表示首批新能力合同已经开放审批或写操作。后续启用这些能力时，需要新增正式能力声明和验收，不能只修改 Prompt。

公开声明必须由服务端约束实际执行，不能仅用于页面展示：

| 检查边界 | 首批强制规则 | 不满足时的处理 |
| --- | --- | --- |
| 生成与装配 | 从已确认 Agent 合同和平台能力策略导出执行约束；有效可调用工具集合为空，无业务输入、公开业务 state/outcomes 和交互种类 | 拒绝生成或启动，不清空用户正式配置以伪装支持 |
| 启动与 Probe | 检查实际图、工具注册和适配器与约束一致，默认工具不能绕过工具集合检查 | 阻断该目标验收；仅 health 正常不能判 ready |
| 每次请求 | 输入通过标准 AG-UI 合同及首批字段约束；续接请求还须符合已声明 interactionKinds | 未声明交互返回 v1 结构化失败，不调用 Command(resume) |
| 图执行与输出 | 工具执行入口、interrupt 入口和公开状态/结果投影均检查能力；textOutput 不授权任意工具或业务写入 | 在调用工具或建立待交互之前拒绝；未声明业务字段不得发送给页面 |

这些执行约束是正式合同的确定性投影，不新增另一份用户可编辑能力配置。实际装配必须使用受控工具入口；无法约束的工具或图执行路径不能进入本批。不能等副作用发生后再靠最终结果校验报错。首批负向验收须覆盖默认工具调用、未声明 interrupt、直接伪造续接及内部 state 外泄；拒绝时不得执行目标工具、续接图或登记待批准项。

#### 5.3.1 能力策略的交付、加载和执行

能力合同不能只停留在 Studio 规划文件中。首批新增模板拥有的 `agent-runtime/src/app/agent/execution_policy.py`，包含类型化策略加载器及平台生成的只读策略表；它不定义路由，不承担业务编排，也不是另一份可编辑正式产物。

策略表按 agentId 唯一索引，至少包含：Endpoint 复合身份、公开能力合同、`capabilitySha256`、对应 Agent 实现摘要、当前策略规则标识及第 8.6 节执行预算。只为当前实际交付且满足正式合同的 Agent 生成可执行条目；未实现 Agent 不得靠占位条目获得调用资格。

交付与使用顺序固定为：

1. 原 Build 的确定性装配收尾阶段，在模块源码交付后、Build/单测及目标验收前，从已确认的冻结输入编译策略表。Pending 预先记录这项装配操作的输入和精确平台写路径；沿原 Diff/执行证据记录实际变化，不增加模型任务或第二套 runtime_binding 任务。
2. 策略路径归平台所有。七模块模型任务可以读取所需合同，但不能写策略表、扩大预算或修改能力白名单。多个 Agent 的策略更新在现有共享 Runtime 锁内合并当前有效条目；不能覆盖无关条目，也不能保留已经不符合正式集合的条目。
3. Studio 启动器重算当前正式能力、实际实现及策略文件摘要，通过受控进程参数 `AGENT_RUNTIME_EXECUTION_POLICY_SHA256` 传入期望值。该值是非敏感的交付校验事实，不是用户配置；客户端及工作区 dotenv 不能覆盖它。策略缺失、重复身份、摘要失配或格式无效时启动失败，不能回到模板 chat 默认策略。
4. Runtime 启动时加载并冻结策略，注册表中的每个可执行 Agent 必须存在匹配策略。工厂接收平台创建的不可变执行策略，装配结果必须验证真实工具集合、图节点及所用中间件；不能只相信 Builder 返回的自报标记。
5. 每次运行由路由 agentId 查当前不可变策略，再校验请求和执行；未登记目标在构造模型、读取 checkpoint 或执行图之前拒绝。运行中不从客户端读取能力配置，也不热加载新的正式合同。部署输入改变后旧进程标记需重启，新策略验收通过前不沿用旧就绪证据。
6. 策略文件本身的字节摘要校验交付完整性；Agent 实现摘要计算排除策略表，避免策略包含自身摘要的递归。策略加载器、执行保护代码作为模板公共实现依赖纳入验收输入摘要，不从实现证据中排除。

首批的图装配必须在当前锁定依赖版本上证明“实际可调用工具集合为空”。若现有 DeepAgents 构造会隐式加入工具，必须使用该版本可证实的受控关闭/裁剪方式，并在实际装配对象上复核；不能以传入 `tools=[]` 或 Prompt 禁止使用工具代替。图/中间件无法关闭或约束默认工具时，首批能力保持阻断，不能放宽空工具合同交付。

请求层拒绝 tool 相关输入；执行层在模型产生工具调用意图时终止，不分派实际工具。未声明 interrupt 的节点/中间件不得进入可执行装配；依赖版本不提供可靠的执行前保护时，不能仅在最终 snapshot 发现 pending 后宣称已阻止交互。策略交付、实际装配检查、工具执行保护及公开投影各有独立负向验收。

### 5.4 正式设计与草稿

拟统一切换为 `schemaVersion=endpoint-design.v8`、`artifactType=endpoint-design` 的判别合同，保留当前设计双文件位置。共同包含复合身份、implementationKind、artifactRevision、来源摘要及确认元数据；正式设计与草稿使用同一分支模型约束，草稿保留各自的未确认状态。Markdown 是用户可编辑产物，JSON 仅为内部状态。

- business：保留现有业务映射语义。
- agent_runtime：sourceBinding 固定为 `{sourceType: agent, agentId}`，首批 inputBindings=[]；invocation 和 interactionContract 为后端生成的只读快照。
- Agent 分支禁止 fieldMappings、databaseOperation、databaseWrites、databaseQuery、externalApiBindings 和 staticData；不伪造空映射以绕过旧校验。
- 会话按页面与 Agent 隔离，沿用当前组件创建的 threadId；同一隔离 thread 只允许一个活跃执行，不承诺跨页面共享历史或跨实例恢复本地消息。
- 草稿保存不确认设计，不启动开发，不改变 DAG 或完成状态。
- 编辑并发检查 technicalPlanHash + baseRevision；语义有效性使用直接依赖摘要，两者不得混用。

只支持切换后的当前格式，不迁移、不双读 v7/v8、不回填旧草稿。已有错误格式提示重新设计；这一影响必须作为评审和发布说明的一部分。

设计文件 v8 不要求运行协议升级：保留 `agent_runtime_result.v1`、`agent_runtime_error.v1` 和 agentRuntime 状态；endpoint-designs 操作信封仍为 schemaVersion=1。

运行调用继续走生成应用的 `/agents/{agentId}/run`；设计操作继续走 Studio 的 `/endpoint-designs/run`，复用 get/prepare/save/save_draft/discard_draft。两类服务的 thread/run 身份和调试凭据不得混用。处理成功与可处理业务失败均交付完整 AG-UI 生命周期、消息、结构化结果/错误和状态快照；协议帧解析交给官方 SDK。

保存使用现有应用修订/Endpoint 锁，复核基线与能力摘要；Markdown/JSON 按下述提交规则成对写入。前端局部状态和聊天卡片不能代替正式产物状态。

### 5.5 双文件提交与中断后的读取规则

双文件不宣称具备文件系统级跨文件原子替换。内部 JSON 的提交元数据包含同一 artifactRevision、对应 Markdown 原始字节的 SHA-256 及确认绑定的语义摘要；该 JSON 是本对产物的最后提交点，不另建能力或应用配置来源。

1. 在同一应用修订/Endpoint 锁内重读基线，先将 Markdown 人工编辑同步为候选结构并完成校验。准备新一对临时文件；JSON 引用最终 Markdown 字节摘要，确认元数据只属于这一候选版本。
2. 临时文件写完并刷盘后，先原子替换 Markdown，最后原子替换 JSON；对所在目录完成持久化后才返回成功。两个正式文件被替换期间，服务内读取与门禁使用同一锁，不能观察半次提交。
3. 可捕获的写入失败可在锁内恢复原字节；无论恢复是否成功，都返回失败。进程退出等无法回滚的中断，交由下一次读取的一致性检查阻断，不能承诺自动恢复成功。
4. 读取、确认、冻结和完成门禁均核对提交元数据、复合身份、版本与 Markdown 字节摘要。缺失文件、摘要不符或中断留下的不成对版本，均不得沿用 JSON 中的 confirmed。只返回明确的不一致原因并保留现存文件，不自动覆盖、猜测或确认。
5. 用户在外部编辑 Markdown 同样会使原确认不再适用于当前字节。确认操作先展示并同步当前编辑，再按第 1–2 步提交新的一致版本；它不是旧格式兼容或历史恢复。草稿采用同样成对提交规则，但永远不产生正式确认。

字节摘要用于发现未同步编辑和不完整提交，不代替第 9.4 节的语义摘要。未改变语义的编辑仍须完成当前版本确认，但不据此重建实现。故障验收分别覆盖第一次替换后中断、第二次替换失败及外部 Markdown 编辑，保证不成对版本不能通过确认或开发门禁。

## 6. 用户流程与确认边界

1. 用户联合确认 RequirementSpec/ProductPlan，确定 Agent 与正式页面入口。该联合确认规则保持。
2. UI 设计、技术规划按现有工作流顺序及各自确认门执行；视觉设计不等待运行代码完成。技术规划生成七模块及 interactionContract，平台物化标准运行 Endpoint，在对应技术规划确认边界整体复验。
3. 页面技术接入消费已确认产品、UI 与技术事实。离线 Mock 只作为设计预览，不构成真实 Runtime 验收。
4. 用户从统一接口目录审阅运行 Endpoint 的接入设计。展示目标、能力快照、流式结果及会话约束，显式确认 Markdown/内部 JSON 对应的同一版本。
5. 从页面、接口或 Agent 发起开发，统一检查相关运行接口设计及必要依赖；缺失或失效时展示具体原因，配置后由用户显式重新检测。
6. 原规划器生成 Pending DAG，用户确认后执行。保存设计或回答澄清均不替代 DAG 确认。
7. Build 与单测通过后，在当前目标范围执行接入验收；成功才登记对应目标就绪。
8. 全应用聚合就绪后，仍等待用户沿既有入口确认进入应用测试。

所有正式产物重新生成或修订后，必须重新确认该版本。Markdown 的人工修改在对应确认边界同步到内部 JSON，并保留隐藏结构；派生字段冲突须拒绝，不能只信 JSON 的旧确认标记。

换 Agent、改变公开能力或将 business 改为 agent_runtime，均进入现有 Formal Revision。首批不提供直接在详细设计里跨类型切换的快捷操作。删除入口前检查当前产品、页面、Tool、设计和任务来源引用，不能复用另一 Agent 的旧身份。

独立的运行接口设计确认是对“将当前 Agent 公开合同及会话约束作为本入口实现、接入和验收基线”的签认。技术规划确认负责能力与架构决策；接口设计确认负责选定这一具体接入基线；Pending DAG 确认才授权所列开发和真实模型验收动作。接口设计确认不授权立即调用模型、开放未声明工具或修改 Agent 能力。首批面板以“审阅并确认接入设计”呈现只读合同，不伪造可配置项；需要改变能力时导航到 Formal Revision，而不是在只读快照中编辑。

## 7. 开发依赖与执行机制

### 7.1 统一解析三种目标

拟新增一个无副作用的依赖解析服务，共用正式产品、技术和设计对象，为门禁、Build 上下文、切片、锁和 Probe 返回直接消费接口、实现依赖、必要 Agent 集合及对应摘要。

| 目标 | 纳入 | 排除 |
| --- | --- | --- |
| 页面 | 当前页面、直接运行 Endpoint、对应 Agent、必要模板及前端能力 | 无关页面/Agent、虚假数据库或 CRUD bootstrap |
| 运行 Endpoint | 当前设计、实现 Agent、必要 Runtime 验收 | 消费页面、普通 API 请求封装任务 |
| Agent | 自身七模块、标准运行 Endpoint 设计与接入验收 | 反向扩展的全部消费页面或其他 Agent |

依赖解析须有界，生成任务前拒绝悬空引用、重复身份和依赖环，并返回完整引用链。只读反向消费者用于影响提示与失效，不自动扩大执行范围。普通业务 Endpoint 开发不反向拉入消费它的 Agent；Agent 的 Tool 实现依赖不进入页面直接公开接口列表。

设计就绪检查可以要求 Agent 的接口设计已确认；这不形成实现图中的反向依赖边。实现前置方向固定为：

```text
已声明业务 Tool 的必要依赖 → Agent 实现 → 运行 Endpoint 接入验收 → 页面联调/完成
```

页面源码生成可以与 Agent 实现并行；源码任务成功不代表页面联调完成。

### 7.2 无任务运行 Unit

保留 `backend:endpoint:<contractId>:<endpointId>` 身份，运行 Endpoint 为非模型、prerequisite_only 节点。“无任务”仅指该 Unit 不产生源码生成任务，不表示它的接入验收可被跳过。已有通用路由不重复生成，不新建 runtime_binding 模型任务或 agent_surfaces.py。

必须联动处理：

- Unit 职责和 Worker 在进入 CRUD/业务字段规则前分派，不为运行 Unit 构造虚假非空 requirement。
- 冻结目录允许将正式运行合同作为相关 Agent、页面和验收的来源；不向模型开放未授权实现文件。
- 源码任务依赖穿透无任务 Unit，展开到真实 Agent 前置任务；完成门禁还须保留独立接入验收屏障，不能随空节点一起消除。保留原有前端并行生成规则。
- 切片复用现有 Unit 传递闭包，app:integration 的相关范围包含 Agent 实现与运行合同。
- 共享工厂仍使用 `agent:runtime` 锁；持锁不扩大为开发全部 Agent。共享注册修改遵守现有路径策略，并保持其他正式 Agent 的有效注册。
- 空任务集合、Unit metadata 或已存在路由都不能单独证明接口完成。

`app:integration` 是现有 Unit 图中的逻辑验收归属，不是已经具备自动执行能力的 Workflow 节点。目标范围的确定性 verifier 归属该 Unit，实际调用位置按第 7.2.1 节接入现有 Workflow，不交给模型 Worker。编译器对页面、运行 Endpoint 和 Agent 三种目标都必须保留该归属及其验收计划，并在 Pending DAG 展示必要 Agent 集合、Endpoint 身份、真实模型调用和完成阻断关系。这里使用当前目标闭包，不要求全应用产物先就绪。

```text
Agent 源码任务 + Build/单测成功
  → app:integration 的 Runtime 接入验收阶段
  → 写入当前 Endpoint 接入证据
  → 允许登记 Endpoint / Agent 就绪

页面源码任务 + Build/单测成功 + 当前 Endpoint 接入证据有效
  → app:integration 的页面联调阶段
  → 允许登记页面就绪
```

Runtime 阶段只依赖模块实现事实，不读取 Agent 应用级 ready 作为启动条件；页面阶段等待的是当前接入证据，不等待自身完成。多个目标引用同一 Endpoint 时，同一执行范围内按复合身份和验收输入摘要合并 Runtime 验收；跨执行只复用仍有效的证据，真实失败不自动重试。验收失败或中断时可保留源码成功事实，但不得登记 Endpoint、Agent 或依赖该接口的页面就绪。若切片或编译结果缺少所需验收阶段，直接拒绝该 Pending DAG，不允许等任务完成后再补一个未确认动作。

#### 7.2.1 验收计划、实际调用位置和完成事务

首批不增加名为 app:integration 的模型任务或假执行任务。原 Pending/Formal Build 计划增加严格的 `targetAcceptance` 段，纳入当前计划 Schema、规范化摘要、DraftIdentity 确认及不可变 Build Run 快照；当前生产消费者同步切换，不保留缺少该段时的历史放行路径。普通业务目标使用显式不需要 Runtime 验收的分支，不能缺省当作通过。

该段至少包含：计划 scope、逻辑 ownerUnitId=app:integration、Endpoint 复合身份集合、必要 Agent 集合、真实前置 task IDs、`runtime_stream`/`page_integration` 阶段、verifier/验收用例标识及摘要、调用预算、超时、策略装配写路径，以及所阻断的完成目标。所有字段由平台根据当前目标闭包编译；模型和客户端不能删除验收阶段、缩小依赖或伪造通过结果。

实际执行落在 `graph/nodes/lifecycle.py::test_phase_confirmation` 的前置协调段：在 `_completed_build_summary` 有效且 `unit_test_gate_passed=true` 之后，**调用 `complete_initial_development()` 之前**，委托拟新增 `services/runtime_target_acceptance.py` 执行。该入口负责当前目标验收，不等待用户进入全应用测试，也不触发后续全应用 `integration_test`。现有节点名可保留，节点内部划分验收协调与最终完成提交，不把图节点、Unit 和源码 task 混为同一执行对象。

执行前验证服务端 active execution、原已确认 plan digest、scope、Build Run 身份、全部必要任务和单测证据，并重算直接语义/实现摘要。读设计、刷新状态、普通聊天和无对应确认授权的节点重入均不能触发真实调用。

执行记录按 `.devagentstudio/runtime/target-acceptance/<workflowRunId>/<attemptId>.json` 保存内部状态，状态为 prepared/running/succeeded/failed/unknown；文件名与内容由服务端生成，路径和链接按原受控文件规则验证。记录绑定计划、输入摘要、阶段、调用次数及证据引用，不作为新的配置来源。

| 协调阶段 | 确定行为 |
| --- | --- |
| 首次合法进入 | 先持久化 prepared，再登记 running 后启动临时验收会话；每项模型调用先记预算消费，不能执行后才补账 |
| 已成功记录且全部输入仍匹配 | 复用有效证据，直接进入完成提交，不再次调用模型 |
| 相同 execution 已在 running | 返回该权威进度，不创建第二次 attempt 或重复验收 |
| 失败或进程中断后 running 无法确认 | 保存 failed/unknown 和已知调用事实，进入原失败收口；不回到 Build/单测修复分支自动重新调用 |
| 用户显式决定重试 | 沿原执行恢复/重新规划确认入口形成新的服务端 attempt 授权及预算；不把普通刷新或旧确认重复提交当作批准 |
| 全部必需阶段成功 | 在完成事务内再次验证当前正式版本、语义/实现摘要和执行身份，再调用完成登记；并发修订使证据失配时拒绝完成 |

长操作通过现有 AG-UI 进度、取消和 execution 状态发布；不得用等待结束的无状态后台线程绕开生命周期。`complete_initial_development` 及专项 Endpoint/Agent 完成函数自身也必须复验 targetAcceptance 成功证据，避免其他调用路径绕过前置协调。验收记录、原始证据和生命周期若未能一起提交，保持未完成；后续只允许凭同一条成功记录幂等提交，不重跑验收来修复落盘问题。

### 7.3 后续 Tool 依赖的固定边界

首批验收可以没有业务 Tool，但依赖模型从本次改造起必须能够表达 Tool；不能把“首批为空”硬编码成 Agent 永远没有前置依赖。当前已有的 `agentSettings.tools.bindings` 继续作为唯一 Tool 绑定来源，不另建 Endpoint 层 Tool 列表。

- 每个 Tool 通过稳定 toolId、apiContractId、endpointId 引用已确认的普通业务能力；平台解析到本地 ApplicationService/当前业务服务，不让模型自填 URL 或把 Agent 对外事件流当 Tool 返回值。
- Tool 业务 Endpoint 的设计可先确认，不要求其代码先存在；Agent 任务冻结、执行和验收须纳入该 Tool 的真实实现依赖与当前来源摘要。
- 页面的直接调用始终是运行 Endpoint。后端 Tool 的实现依赖不授权前端直接调用内部服务，服务端仍独立核验身份、参数、权限与资源归属。
- Tool 的请求/结果 Schema、确认语义和实现变更向相关 tools/context 模块及能力验收传播；不自动使全部 Agent、页面或接口设计失效。
- 仅新增内部查询 Tool、公开输入/输出未变时，Agent 配置走正式修订并重验相关实现/接入，运行接口详细设计可保持有效；如公开输入、结果或交互能力变化，则相关设计重新确认。
- 运行 Endpoint 不能作为任何 Tool 目标；自引用、间接环和 Agent-as-Tool 在规划前拒绝，报告引用链。

只读 Tool 开放前需实现参数与返回校验、权限/归属、错误与超时处理、有界调用及真实工具接线验收。它可以只产生文本回答，不强制同时开放结构化业务结果。需要业务输入或结构化输出时，再配套相应公开合同。

写入 Tool 的开放还依赖第 13 节的批准、幂等和提交证据。已有 Tool/Skill/Knowledge 配置不得自动清空以满足首批门禁；未支持的目标明确阻断，保留原正式配置。

## 8. Runtime 基础修正

### 8.1 同线程互斥与查询

在现有隔离线程键上覆盖“读 checkpoint → 决定输入/续接 → 执行图 → 取得最终状态”的互斥。冲突请求立即返回现有 v1 结构中的明确 busy 错误码，载荷 status 仍为 failed，不新增运行状态枚举、不排队后自动执行。

inspectInteraction 遇到执行中的 thread 返回 busy；空闲时只读 checkpoint。失败、取消、断流完成服务端执行收口后可靠释放互斥，锁表需清理；不能仅因客户端断开而在图仍执行时提前放锁。当前单进程进程内锁不宣称覆盖多 worker、多实例或持久写入幂等。

inspect 无 pending 只表达未发现待交互项；前端不能用它证明原业务提交成功。停止只中止网络流，不承诺撤销服务端已提交的业务操作。

首批 v1 不新增原 run 终态查询或文本重放协议，因此“同步”严格限定为查询当前是否忙碌、是否存在待交互事实，不承诺恢复丢失的终态或回答。前端按下表处理；其中“结果未知”是本地会话显示状态，不扩展 v1 载荷 status 枚举。

| 客户端事实 | 页面行为 | 后续操作 |
| --- | --- | --- |
| 当前 run 收到匹配的成功结果和 RUN_FINISHED，且文本流正常结束 | 完成本轮回答 | 允许用户在当前 thread 显式发送下一轮 |
| 收到当前请求的完整匹配失败生命周期 | 展示该请求失败；冲突请求的 busy 不改变另一活跃 run 的事实 | 不自动重发；仅在当前执行已收口且会话未处于结果未知时允许显式下一轮 |
| 断线、停止，或缺失匹配结果/结束事件 | 保留已有文本并标记“不完整，结果未知”；禁用原 thread 的发送和续接 | 允许用户发起只读同步，禁止重放消息或推断成功 |
| 结果未知后同步得到 busy | 保持未知，显示服务端仍忙碌 | 不发送、不续接、不自动轮询或重试 |
| 结果未知后同步无 pending | 仅显示当前未发现待交互项，原 run 仍为结果未知 | 可由用户显式新建 thread；不复制并发送旧消息，不将旧会话恢复成 completed |
| 结果未知后同步有 pending | 保持未知；首批 interactionKinds=[]，不得提供批准或续接动作 | 保留会话供诊断；按能力不匹配处理，不调用图 |

同步请求自身失败时保留未知。旧 run 的迟到、重复事件不能在已脱离原连接或切换 thread 后补记成功，也不能覆盖新会话；再次支持原 thread 恢复需要单独设计并确认终态事实合同。新建会话不代表取消旧执行，旧执行仍由服务端在配置的最长执行时限内收口。

### 8.2 服务端续接校验

取得当前 pending 后，除 interactionId 外，按 pending.kind 校验值：clarification 为有界非空字符串，confirmation/approval_required 为严格 boolean，不能用真值转换接受字符串或数字。

先检查 pending.kind 已在当前正式能力中声明，再执行上述类型校验；首批 interactionKinds=[]，所有续接均拒绝。类型规则适用于后续已获准开放相应交互的能力以及模板基础设施定向用例，不意味着首批端到端验收必须执行一次真实审批。

校验在 Command(resume) 之前完成；错误返回现有完整 AG-UI 失败生命周期，不执行图。前后端使用一致的类型和长度规则。该补丁不等同于审批持久原子消费或写入幂等。

### 8.3 Profile、目标集合与模板交付

- 保留当前工作区已修正的健康矩阵：debug 要求 anonymousSession=not_required 且 debugSession=ready；preview/probe 要求匿名会话 ready；继续检查 launchId、Profile、路由和数据库。
- Probe 接收统一依赖解析得到的必要 Agent 集合，支持页面和运行 Endpoint 目标，而非仅传单个 Agent scope 的 ID。
- 单目标 Probe 要求必要集合已注册，允许其他正式 Agent 尚未实现，拒绝合同外注册；完整 Preview 要求正式集合齐全。
- Studio、模板源码、已发布模板和实际生成包的一致性分别记录，不能仅凭源码 revision 或健康常量认定交付通过。

### 8.4 单进程部署约束与启动门禁

本批运行拓扑固定为每个 Runtime 实例单 worker，且同一应用、Profile 和 checkpoint 命名空间只有一个服务进程持有执行权。该限制适用于 debug、probe、preview 及生成包的受控启动入口，不能仅在说明文档中提示。

- 启动器和模板入口共同验证有效 worker 数为 1，拒绝多 worker、多副本及无法确定有效拓扑的启动配置；不允许依靠开发默认值满足约束。
- 模板在绑定服务端口、开放 Agent 路由前，为当前应用/Profile/checkpoint 命名空间取得操作系统排他进程锁，锁随进程退出释放。第二个共享该命名空间的进程必须启动失败；不能用易残留的 PID 文件充当互斥，也不能把持久文件锁作为业务幂等证据。
- 首批只支持本机受控进程与本地 checkpoint，不支持跨主机共享存储或负载均衡到多个 Runtime。入口不能核验此约束时拒绝启动；启动失败的原因须可见且不得报告 ready。
- 启动事实须包含有效 worker 数、应用/Profile/命名空间及进程锁持有结论，Probe 和接入验收逐项核对。它们是受控启动事实，不接受客户端自报，也不以 health 常量代替实际锁状态。

同 thread 的异步执行仍使用第 8.1 节进程内锁；进程锁只保证此前提成立，不替代 thread 互斥。启动门禁验收包括 worker 数为 2、第二进程竞争同命名空间和正常退出后重新取得进程锁；任一不满足均阻断本批交付，不通过自动降级 worker 数规避错误配置。

### 8.5 请求白名单与服务端会话历史

官方 RunAgentInput 校验负责标准帧形状，平台随后执行本入口的有限合同校验。空 inputSchema 仅表示没有业务输入，不表示 messages、state、context、tools 或 forwardedProps 可以任意传入。

| 请求内容 | 首批允许值与处理 |
| --- | --- |
| threadId/runId | 有界非空身份，服务端按 Principal/namespace/Agent 核验 thread；同身份重复 runId 拒绝，不当作重新发送 |
| tools/context | 只允许官方 SDK 的空集合；非空工具定义、任意上下文注入明确拒绝 |
| state | 只允许空对象，或当前官方 SDK 回送的、与服务端最近交付一致的 agentRuntime 公开镜像；只做一致性检查，绝不作为图输入或恢复状态 |
| forwardedProps | 仅 pageId/actionId/contextItems、inspectInteraction，以及基础设施支持的 interactionResponse；未知键拒绝。首批 interactionKinds=[]，所有 interactionResponse 请求拒绝，不调用图 |
| 页面上下文 | 沿用有界 PageContext；首批仅供用户级参考，不提供身份/权限、不覆盖服务端策略或系统提示 |
| messages | 初次请求仅一条有 ID 的非空 user 文本；后续为官方 SDK 的已知公开历史镜像加一条新 user 文本，按下述规则校验 |

SDK 可能随新一轮请求回传此前 assistant 消息，因此不能简单拒绝所有 assistant，也不能把整份客户端历史再次写入图。Runtime 在当前 checkpoint 命名空间内保存已接受 user 消息及实际对外发送的 assistant 消息的有界公开投影，包含 wire message ID、角色和内容摘要；公开 ID 与内部图消息 ID 的对应由服务端产生并保存，不假设两者相同。

新请求先检查历史前缀与该公开投影一致，再只把末尾一条新的 user 消息交给图；可信历史由服务端 checkpoint 提供。客户端不能重写已接受消息，不能新增 assistant/system/developer/tool/function 消息、伪造工具调用或把同一 user ID 换成另一段内容。前缀不一致、未知 state 镜像、缺失必需历史或重复 run/message 身份返回明确 history/input 冲突，不静默采用客户端历史或自动重放。

首次调用、续轮和公开消息投影提交都在第 8.1 节同线程互斥内。消息已被接收但终态未能交付时，服务端保留已发生事实；客户端继续遵循结果未知、新建会话且不复制旧请求规则，不尝试靠客户端历史补造丢失终态。

inspectInteraction 可以携带 SDK 留存的消息/state 镜像，包括此前失败或结果未知调用留下的本地消息；只做有界形状校验后丢弃，不使用新一轮输入的历史前缀校验来阻断只读同步。查询不能改变上下文、提供批准或执行其他动作，镜像不能触发图或写入可信历史。首批不提供历史迁移或跨组件历史恢复；缺少当前服务端投影的旧 thread 不能猜测恢复，须显式新建会话。

### 8.6 整体执行期限、并发预算和清理

首批采用平台固定的保守预算，编译到执行策略并纳入摘要；不新增用户可编辑的应用配置来源，不接受客户端提高限制：

| 限制 | 首批值/语义 |
| --- | --- |
| 原始请求大小 | 256 KiB，进入 JSON/官方类型解析前限制；不能只依赖 Content-Length 自报 |
| 消息数量/内容 | 每请求最多 128 条公开历史消息；新增 user 文本最多 16 KiB，UTF-8 字节计数；超出明确拒绝，不静默截断可信历史 |
| 活跃执行 | 同一隔离 thread 最多 1；同一服务端匿名/登录身份每实例最多 2；每 Runtime 进程最多 4。名额按可信身份核算，换 threadId 不能绕过身份/进程总量 |
| 等待队列 | 不排队；容量不足立即返回 v1 busy/limit 错误，不在稍后自动执行原请求 |
| 整个业务 run | 最长 120 秒，覆盖接收后的状态读取、模型/图运行、公开输出和终态交付；模型 HTTP timeout 取现有设置与剩余总期限的较小值，不能替代总期限 |
| 公开输出 | 单次回答最多 128 KiB 文本，异常错误正文单独有界；超限终止并返回失败/结果未知，不截断后报告成功 |
| 清理期限 | 最长 10 秒，用于取消并等待实际模型/图任务退出及资源回收；不自动开启新执行 |

预算与 thread 互斥在构造模型/Agent、读取 checkpoint 和发起实际模型请求前统一登记。只读查询走独立轻量校验路径，不占模型执行名额、不触发模型构造；对正在执行的同 thread 查询直接返回 busy。锁表只保存活跃或正在清理条目，释放后清除，无界新 thread 不能无限积累锁对象。

整个 run 的 deadline 包含异步迭代及流式发送/背压等待；执行层和 HTTP 流层共享服务端取消令牌。超时、主动停止、断流均先取消底层任务，再等待实际收口；底层仍在执行时不能释放 thread 锁或并发名额。10 秒后仍不能确认停止的实例标记 unhealthy，拒绝新业务执行并由受控启动器回收该进程；不得留下 detached task 后继续接单。回收失败报告具体错误，用户决定后续，不自动反复重启。

模型连接错误、容量限制或超时均不自动重试业务 run。真实模型请求的现有供应商级重试也必须计入总期限；验收场景的实际请求预算按第 9.5 节执行，不能把内部重试隐藏成零额外调用。跨主机、多 worker 和持久写入 exactly-once 仍不在首批。

## 9. 接入验收、状态与完成证据

### 9.1 避免验收与应用测试门禁互相等待

目标运行接口的接入验收必须在当前目标开发流程内、Build/单测之后、登记初次完成之前执行。复用既有受控启动和验收机制，不等待全应用测试入口开放。

不能把唯一的接入验收放到“所有产物完成后才能进入”的应用级测试阶段，否则会形成接口等待验收、测试等待接口完成的闭环阻塞。应用级测试可以追加覆盖，不负责首次建立该目标证据。

保存、读取设计和刷新状态只做确定性处理，不自动调用模型。真实流式调用在当前目标已确认执行范围的验收动作中进行，保留可审查证据。

#### 9.1.1 目标范围临时联调会话

部分 Agent 已实现时，不能通过降低正式 Preview 门禁完成页面验收。首批复用现有 profile=probe，增加由 `runtime_target_acceptance.py` 管理的临时会话生命周期，不新增面向用户的第四种 Profile：

1. 为本 attempt 创建独立 sessionId、launchId、端口、日志、checkpoint 工作目录和 `probe:<sessionId>` namespace，使用当前正式代码快照与必要 Agent 集合启动 Runtime。此处 Probe 在会话结束时回收，不采用原“health 通过立刻退出”的短探测模式。
2. Runtime 注册检查要求必要集合齐全、实际注册集合不超出当前正式集合；允许其他正式 Agent 尚未实现。模板示例 chat 不得作为缺失正式 Agent 的替代。完整 Preview 继续要求全部正式 Agent，两个启动模式的判定不可混用。
3. 仅页面目标启动隔离的临时前端工程视图：使用受控复制的当前真实页面交付文件、必要公共依赖和模板入口，临时路由投影仅装载本目标页面；不修改工作区正式路由/源文件，不使用 symlink 绕过受控路径，不用 Mock 或替身页面补齐未实现页面。目录来自确认的文件/依赖清单，缺项明确失败。
4. 临时前端使用独立端口及会话专用的 Runtime 同源代理，Electron 打开其目标页面；核对前端 origin 对应的 /__runtime/health、launchId、profile=probe 及必要 Agent 集合。Cookie 名、namespace 和调试凭据与普通预览隔离，不复用 debug token，也不向浏览器暴露模型凭据。
5. 复用固定组件和官方适配器，受控验收观察桥只采集官方 SDK 已解析的事件、当前真实组件状态和身份；不替换页面 adapter、不新写 SSE 解析。观察桥仅在该临时会话启用，采集字段有界并脱敏，不成为产品运行 API。
6. 同一临时会话内执行 Runtime 接入及页面消费检查；一次真实页面提问的同一份事件证据可同时证明运行流和页面显示，不为两个阶段再调用一次模型。Agent/Endpoint 独立目标不启动前端，使用官方客户端执行同一请求用例。
7. 会话运行时持有当前源输入与模板摘要；验收前后复核摘要，人工修改或并发修订发生漂移时拒绝证据。成功、失败、取消均在 finally 回收本 session 的前端、Runtime、观察桥及临时资源；清理失败则本次不能标为成功。退出只回收自己登记的进程，不停止用户现有 Preview/Debug。

普通侧栏预览状态不被该临时会话覆盖；session 事实保存在当前 attempt 内，使用原工作区进程注册器与受控取消机制。前端构建/启动、Electron 或真实模型不可用时记录 blocked/failed 及具体原因，不能把纯 Probe 的 health 成功当作页面联调通过。

### 9.2 两类证据与一个聚合项

Agent 实现证据仍来自同轮确认 DAG、七模块及依赖成功、单测通过和当前实现来源匹配。新增运行接口接入证据，至少绑定：

- API Contract/Endpoint/Agent 复合身份、验收时已确认设计版本及语义摘要；版本用于审计，当前复用规则见第 9.4 节。
- 当前实现输入/实现产物摘要、适用模板与固定适配器合同摘要。
- 验收 run/thread、启动 launchId/Profile、必要注册集合、路由检查和明确结论。
- 标准调用的文本增量、匹配 Agent/thread/run 的 v1 结果与运行结束；错误/断流不能计为成功。

不持久化凭据、Cookie 或模型密钥；证据是内部状态，用户查看可读摘要与具体失败原因。

```text
运行 Endpoint 当前就绪
  = 当前设计已确认且有效
  AND 对应 Agent 实现证据有效
  AND 当前接入验收有效

Agent 应用级就绪
  = 七模块及依赖实现有效
  AND 标准运行 Endpoint 当前就绪

应用初次开发聚合总数
  = 页面数 + 普通 business Endpoint 数 + Agent 数
```

运行 Endpoint 在接口目录独立显示设计、实现依赖和接入状态，但不再增加第二个应用聚合项。实体继续沿现有状态处理，不纳入上式。

保留首次完成时间、Run、Thread 和分支事实；后续变化用当前证据有效性和 change_status 表达。设计已确认、模块已完成、接入失败应分别可见，不能用一个绿色状态点掩盖差异。

### 9.3 失效规则

| 变化 | 接口设计 | 模块实现/接入证据 |
| --- | --- | --- |
| Tool 合同/实现变化，公开接口未变 | 运行接口设计保持有效 | 仅相关 Tool、Agent 模块与接入证据重验 |
| Prompt 或 Temperature | 公开合同不变时保持有效 | 受影响模块更新；实现变化使对应接入证据需重验 |
| interactionContract、运行身份或路径 | 相关设计重新确认 | 相关引用、Pending DAG 和接入证据失效 |
| 新增无关 Agent | 已有接口设计保持有效 | 仅共享注册相关事实变化，不使其他七模块全部失效 |
| 页面入口或动作归属变化 | 未变的标准接口合同保持有效 | 对应页面绑定、上下文和接入证据按直接依赖重算 |
| 模板运行合同或固定适配器变化 | 公开能力不变时无需重做设计 | 受影响目标接入证据需重验 |
| 仅确认时间或展示说明变化 | 不改变业务语义摘要 | 不据此重建任务 |

全文件哈希用于并发冲突；接口、模块、共享注册、页面与验收分别按直接语义依赖计算有效性。重新确认设计不能自动刷新旧验收为成功。

### 9.4 语义摘要输入与归一化合同

依赖解析服务输出带摘要类别、当前摘要规则标识和命名字段的投影，由单一确定性实现计算 SHA-256；门禁、冻结、DAG、Probe 和状态消费者只能使用该结果，不得分别拼接字段或散列整个 TechnicalPlan。摘要规则只支持本次当前定义，不探测或回退旧规则；规则变化使相关旧证据失效，重新建立当前证据。

| 摘要类别 | 必须纳入的直接语义输入 | 明确排除 |
| --- | --- | --- |
| 运行接口设计 | 复合身份、kind、Agent 绑定、method/path、完整 interactionContract、inputBindings 及固定会话约束 | Prompt、Temperature、consumerPageIds、其他 Agent、确认时间与纯展示说明 |
| Agent 单模块输入 | 本 Agent 身份、本模块消费的已确认配置/模型参数或 Prompt、所消费公开合同、相关 Tool 合同/实现摘要、对应模板策略及直接依赖摘要 | 其他模块不消费的配置、全量 factoryAgentIds、其他 Agent、共享注册文件整体字节 |
| Agent 实现产物 | 当前 Agent 独占实现文件的路径/字节摘要，以及共享工厂中该 Agent Builder 与静态依赖符号的语义摘要；另含对应 Build/单测输入摘要 | 纯注册关系、无关 Agent Builder、时间戳、日志和 checkpoint；不得按文件名排除整份 factory.py |
| 共享注册 | 实际 Agent 注册身份、完整实现引用映射、公共分派/加载器符号及对应模板装配规则的确定性投影 | 各 Agent 专属 Builder 内容归其自身实现摘要，不混入注册映射摘要；文件时间戳和仅格式变化 |
| 页面绑定 | 当前页面/动作身份、已确认产品和 UI 的直接语义投影、Endpoint 复合引用、上下文绑定、会话隔离和适配器配置 | 无关页面、反向消费页面列表、其他 Agent 配置 |
| Runtime 接入验收输入 | 运行接口设计摘要、当前 Agent 模块输入及产物摘要、直接 Tool 依赖摘要、共享注册摘要、必要 Agent 集合、模板/固定适配器/能力执行策略摘要、有效部署拓扑，以及 Runtime verifier 实现和验收请求/断言/预算摘要 | 验收 run/thread、launchId、确认时间及其他验收输出事实 |
| 页面联调验收输入 | 页面绑定和当前页面实现产物摘要、对应 Runtime 接入验收输入摘要、页面 verifier、临时页面会话/观察桥规则和适配器合同 | 不相关页面、Mock 结果、其他验收运行身份 |

共享注册变化单独使相关启动/接入证据需重验，不改写其他 Agent 单模块摘要。必要 Agent 集合仍由当前目标闭包决定；检查实际共享注册不等于要求全部正式 Agent 已实现。

归一化规则统一如下：

1. 使用类型化当前模型，先补齐有语义默认值，再生成投影；必填字段缺失立即失败。可选值按字段合同统一表示为 null 或默认值，不由各消费者自行省略；空字符串、空数组与 null 不互相转换。
2. 对象键递归按确定顺序编码为 UTF-8 规范 JSON，数字采用同一有限数值编码，拒绝 NaN/Infinity。身份、路径和大小写按正式校验后的值保留，不为摘要另造宽松别名。
3. 具有集合语义的 Agent/Endpoint/Tool 集合先拒绝重复，再按稳定身份排序；消息、步骤、Prompt、工具执行顺序等有序数据保留原顺序。禁止对所有数组统一排序。
4. Markdown 先同步为类型化语义投影，不散列整篇文档作为语义摘要。纯展示说明由明确字段白名单排除；无法判定归属的修改不能被默认为展示变化。Prompt、模板指令和用户业务文本保留内容，不作去空格等隐式改写。
5. 凭据只使用受控配置引用及非敏感修订标识，不将密钥、Cookie 或其可枚举摘要纳入证据。模型选择、有效模型参数及影响执行的配置纳入对应模块；凭据/Profile 归属变化须重新建立启动与验收事实。

artifactRevision 和双文件字节摘要用于并发、当前确认与提交一致性；验收记录保留当时的版本，不以版本号单独判定当前语义是否变化。仅展示编辑或重新确认时，必须先完成新版本显式确认；其语义与全部实现/验收输入摘要均未变，才可沿用此前成功证据。已有失败、缺失或摘要失配的证据不能通过重新确认变为成功，且不得改写旧验收记录冒充新运行。

实施时为每个 Agent 模块和 verifier 列出实际消费字段，并纳入上述唯一投影实现；无法归类的新增依赖必须先补齐该清单，不能默认忽略。定向验收至少覆盖集合重排不变、步骤重排改变、默认值等价、Prompt 只影响相应模块、展示编辑需重新确认但不重建、共享注册只影响注册及验收，以及相关实现文件变化使证据失效。

#### 9.4.1 共享工厂实现的摘要覆盖

当前七模块共享修改 `src/app/agent/factory.py`，不能把它整体视为“共享注册文件”后排除实现内容。首批采用有界 Python AST 符号投影，在现有静态注册 verifier 基础上统一提取：

- 注册投影：`_AGENT_BUILDERS` 中每个正式 ID 与 Builder 完整符号引用；集合排序、重复拒绝。注册映射不包含 Builder 函数体。
- 单 Agent 实现投影：该 ID 实际指向的 Builder 函数完整 AST，以及它引用的静态辅助函数、常量、默认值、装饰器、导入绑定和类实现的传递闭包；包括真正传给工厂的 Prompt、模型设置、工具/中间件配置和策略接线。
- 公共实现投影：`create_agent`、ID 校验、公共加载器及无法归属单个 Agent 的受支持辅助实现；所有实际消费它的目标验收都依赖该摘要。修改公共逻辑可以使多个目标失效，但不能因此把其未消费的模块配置判为改变。
- 不支持的动态导入、运行时重写注册、无法解析的函数/常量绑定及超出有界闭包的依赖直接阻断复用/验收，并返回具体符号链；不能默默忽略，也不能把无法证明的分支当作纯展示变化。

AST 投影保留常量字符串（包括 Prompt、文档字符串）、语句/调用/装饰器顺序及实际引用关系，去除行列号等位置属性；注释和缩进变化不改执行语义摘要。独占文件仍可按原字节摘要保守失效；同一依赖采用的规则在类别中固定，不由消费者临时切换。

每个实际执行符号至少归属单 Agent 或公共实现的一类；新增未归属可执行语句在加载/确认边界报错。生成任务及验收记录携带实际文件/符号清单，不能只携带模块输入配置摘要。策略表引用按已验证的 capabilitySha256/规则标识作为外部输入叶子，不递归展开表内 implementationSha256；模板策略加载器实现不排除，避免生成策略与实现摘要自我引用。

必须验收：单改 A 的 Builder 使 A 实现/接入证据失效，B 不因 A 函数体变化失效；改公共 helper 使其真实消费者失效；增加 B 的注册改变共享注册/启动验收而非 A 模块输入；把实际工具调用或策略绕过写入 factory.py 不能保持旧成功证据。首批不强制迁移成多文件 Builder 架构，后续若拆分，仅更新当前生产消费和符号清单，不保留旧布局读取分支。

### 9.5 可执行验收用例与真实模型预算

首批平台提供唯一当前用例 `runtime_text_smoke`，verifier 规则标识为 `runtime-text-smoke.v1`。这是验收规则身份，与 Runtime v1 事件和设计文件 v8 独立；规则/实现/请求/断言发生变化时验收输入摘要变化，旧证据不得继续复用。

| 用例项 | 固定合同 |
| --- | --- |
| 请求来源 | 平台确认的 targetAcceptance 用例；不读取聊天历史、业务数据库或任意产品文本自动拼接测试问题 |
| user 消息 | `这是一次连接验收。请简短说明你能够提供的帮助。`，不插入测试系统提示、不修改正式 Agent Prompt、不要求使用工具 |
| 会话 | 每个被验收 Endpoint 一条新的隔离 probe thread 和 run；内部 ID 用于关联，不影响用例语义摘要；不复用用户历史 |
| 模型调用预算 | 每个 Endpoint 每次明确授权 attempt 最多 1 次真实供应商请求；验收客户端和供应商请求层重试为 0，不将网络重试算作同一次免费调用 |
| 参数 | 使用当前正式模型身份及有效生成参数；验收不暗中改 Temperature/Prompt。额外执行期限和输出限额是平台策略，纳入输入摘要；参数不支持时明确失败 |
| 启动/执行期限 | Runtime 就绪最多 60 秒，页面临时前端就绪最多 90 秒，单业务 run 最多 120 秒，清理最多 10 秒；每个 Endpoint 整体 attempt 最多 300 秒，受总期限剩余值约束 |
| 调用方式 | Endpoint/Agent 目标由官方客户端发出；页面目标由真实 Electron 页面输入并发送同一问题，复用这一次调用完成 Runtime 与页面两阶段检查 |
| 运行流通过条件 | 标准生命周期及文本消息正确配对；至少一段非空白文本；匹配 Agent/thread/run 的 v1 completed 结果、结果快照和 RUN_FINISHED 一致；没有错误、awaiting_user、工具调用、未声明状态或超限 |
| 页面通过条件 | 当前真实固定组件显示同一回答和完成状态；来源为当前目标 Endpoint；未调用普通业务包装或 Mock；没有重复发送和旧会话覆盖 |

不要求模型复述固定字符串、满足精确措辞或证明所有业务职责。模型用正常文本表示拒绝但运行链符合合同，可以通过协议接入检查；产品语义与业务能力正确性须另有相应能力验收，不能用本用例替代。无文本、协议不完整、额度/认证失败或超时均不算成功。

一次调用在发送到供应商前持久记录预算已消费；发送结果不确定也不自动再次调用。前端观察和 Runtime 事件来自同一受控运行，由官方 SDK 解析后形成脱敏证据：关联身份、事件种类/顺序、文本长度及内容摘要、终态、参数/用例/实现摘要、开始/结束和清理结果；不持久化完整用户/模型对话、Cookie 或供应商认证 Header。

两阶段可共用同一原始成功调用，但生成各自明确的 Runtime/页面验收记录；Runtime 成功、页面渲染失败时可保留前者，不登记页面完成。新 attempt 是否复用当前成功 Runtime 证据由已确认计划确定，不能以刷新失败页面为由偷偷再调用一次模型。

受控替身测试负责负向输入、工具拒绝、并发、截止期限、事件缺失/重复/乱序和失败收口，不消耗真实供应商调用预算。真实模型只执行上述确认范围的最小 smoke；失败立即停止该目标验收，不自动修复或继续扩测。模型/额度、Electron 或服务不可用的环境失败与产品合同失败分别记录，但都不能形成通过证据。

## 10. 前端适配与文件改造范围

统一接口目录复用现有机器人接口行、搜索、详情和“查看智能体”导航，数据来源切换为正式 Endpoint，移除重复派生条目。浏览 agentInterfaceId 可保留，prepare/save/develop 使用正式复合身份。

Agent 设计面板在挂载前按 implementationKind 分派，避免业务映射 hook 提前读取 Agent 草稿或请求数据库元数据。显示固定 Agent 绑定和流式合同，不提供与产品归属冲突的任意 Agent 选择。

页面配置由 Endpoint 查找结果派生，继续使用 RuntimeAgentConversationAdapter。首批优先保留现有 runtime.path/pageId 网络配置；Endpoint 身份放在上层只读 Build 合同，不为展示身份新增运行载荷字段。

加载、空态、冲突、busy、断线需同步及失败状态沿现有紫色主题实现浅深色适配。错误保持在所属设计或会话界面，不冒充主 Workflow 连接失败。

以下为实施时最小读取与改造路由，不是扫描全项目的授权；具体消费者根据已读 import 定向补齐。路径相对仓库根。

| 改动组 | 文件/目录入口与职责 |
| --- | --- |
| 合同与物化 | `services/runtime_agent_contracts.py`、`runtime_agent_settings.py`；拟新增 `runtime_endpoint_contracts.py`，统一稳定身份、公开能力和派生入口 |
| 规划与确认 | `agents/main/planner.py`、`technical_plan_editor.py`、`technical_plan_agent_contracts.py`、`document_sync.py`；`services/project_plan.py`、`api_contracts.py`；原规划/正式修订确认入口 |
| 详细设计与存储 | `domain/api_design.py`；拟拆分 `domain/agent_endpoint_design.py`；`workspace/endpoint_design_documents.py`、`runtime_agent_markdown.py`、`plan_documents.py`；`services/binding_workspace.py`、`endpoint_design_detail.py`；`protocols/endpoint_designs.py`；拟拆分 `services/agent_endpoint_design.py`，实现双文件最后提交点及读取一致性门禁，避免继续膨胀业务映射模块 |
| 依赖与 DAG | 拟新增 `services/runtime_endpoint_dependencies.py`，统一依赖及语义投影；`api_design.py`、`build_context_resolver.py`、`build_unit_skeleton.py`、`build_unit_compiler.py`、`unit_generation_requirement_targets.py`、`frozen_contract_catalog.py`、`runtime_agent_dag.py`、`execution_resource_scope.py` 及当前切片消费者，编译 targetAcceptance 并保留 app:integration 逻辑验收归属 |
| 增量与完成 | `runtime_agent_module_policy.py`、`runtime_agent_development.py`、`development_completion_evidence.py`、`development_artifacts.py`、`integration_test_runner.py`、`python_runtime_checks.py`、`agent_runtime_launcher.py` `artifact_freshness.py`、`task_input_freshness.py` 及现有生命周期完成入口 |
| 页面与验收 | `page_implementation_contract.py`、`agent_ui_build_contract.py`、`runtime_business_acceptance.py`、`business_acceptance_verifiers/frontend_agent_ui.py`、`python_agent_binding.py`、`runtime_support.py` |
| 验收协调与证据 | 拟新增 `services/runtime_target_acceptance.py` 及对应严格计划/记录模型；`graph/nodes/lifecycle.py::test_phase_confirmation` 在完成登记前调用，`development_artifacts.py` 复验；计划持久化/摘要/确认/Build Run 快照同步 targetAcceptance，记录 prepared/running 与调用预算 |
| 目标临时会话 | 在当前 launcher、进程注册与取消基础上增加专用会话管理；临时前端工程视图、目标路由投影、Electron 观察桥、session 专属进程/端口/代理及 finally 清理，不覆盖普通 Preview 状态 |
| 执行策略交付 | 平台策略编译器及原 Build 确定性装配收尾；外部模板新增 `src/app/agent/execution_policy.py` 并同步发布清单/revision，Studio 路径策略区分模型只读与平台写入；启动器校验并传入期望策略摘要 |
| 符号摘要 | 在现有 Python AST/注册 verifier 上补有界 Builder、静态 helper、常量与公共分派投影；正式来源、任务复用和验收共用，不整体排除 factory.py |
| Electron UI | `ApplicationOutline/AgentInterfaceRows.tsx`、`DevelopmentArtifactsPanel/AgentInterfaceDetail.tsx`、`useDevelopmentArtifactDetail.ts`、`DocPanel/TechnicalPlanAgentContracts.ts`、FieldMapping 分派/草稿 hook、快捷目标与状态投影 |
| 类型与页面生成 | `Frontend/src/renderer/src/service/endpointDesigns.ts`、`typings/endpointDesign.ts`、`workflow.ts`、`application.ts`；`Backend/app/agents/frontend/runtime_instruction.py`、`generator.py`，同步当前类型、编译和消费规则 |
| 固定前端资产 | `Backend/app/builtin_templates/agent-ui-frontend` 内现有会话类型、组件及 `src/apis/agentConversationRuntime.ts`，落实结果未知与显式新建会话规则，保留官方 SDK 与当前路由 |
| 外部模板 | 已读模板根下 `interaction/service.py`、`interaction/schemas.py` 及定向相关的图/工具装配、受控启动/健康合同；实现策略加载、请求白名单/可信历史投影、能力执行保护、单 worker/命名空间进程锁、总 deadline/身份与进程容量门禁，遵守外部仓库权限、说明和发布规则 |

表内 Backend 的 `services/`、`domain/`、`workspace/` 等入口均位于 `Backend/app/`。实施时更新当前生产者、消费者、必要测试与 CODEBASE_INDEX，不保留旧形状兼容分支。

## 11. 实施顺序与阶段退出条件

| 阶段 | 内容 | 退出条件 |
| --- | --- | --- |
| R0 评审 | 确认本文范围、合同、交付及验证边界 | 用户明确评审通过后才开始代码工作 |
| R1 基础修正 | 同线程互斥/busy、续接类型、单 worker/命名空间进程锁、总 deadline/身份与进程容量；承接已有 Profile 修复 | 控制在创建模型/执行图前生效；超时不提前放锁，不可收口实例停止接单，现存问题与资源限制有定向证据 |
| R2 正式合同 | 确定性 Endpoint、interactionContract、执行策略交付/加载、请求白名单与历史投影、页面归属、v8 双文件提交和确认 | 当前生产消费者一致；实际工具集合为空，过期策略、伪造消息及不成对文件在执行前拒绝；策略表不产生摘要递归 |
| R3 开发链 | 统一依赖/符号摘要、非模型 Unit、冻结/空节点穿透、targetAcceptance、完成前协调及防重记录；目标 Probe/临时会话 | 三目标在单测后真实执行 verifier；预算先记账，失败/中断不重跑；共享 Builder 变化不漏失效，应用门禁无环 |
| R4 UI 接入 | 正式目录、Endpoint 绑定、结果未知/新会话、状态与聚合去重、目标真实页面临时视图及观察桥 | 页面通过既有适配器调用；部分 Agent 未实现也能验收当前页；不改普通 Preview 或用户正式路由，不重复发送 |
| R5 交付验收 | 固定 smoke 用例、明确请求/断言/预算与 verifier 摘要、真实 Electron、双 Agent 部分实现及业务接口边界 | 首批矩阵通过；每 Endpoint 每授权 attempt 最多一次供应商请求，无环境失败冒充通过，限制与失败/跳过记录完整 |

以上是实现顺序，可以分提交评审；R2–R4 必须作为同一完整合同切换交付。中间版本不能开放半成品设计入口，不通过双协议兼容或永久 feature flag 掩盖缺失能力。

首批不支持复杂 Agent 时明确阻断对应新链路，不自动删除 Tool/Skill/Knowledge、不降级已有配置、不保留旧直接绑定执行路径充当 fallback。依赖复杂 Agent 的应用不能被宣称完成本次完整升级。

## 12. 验收矩阵与执行约束

| 场景 | 必须观察到的结果 |
| --- | --- |
| 无实体纯文本 Agent | 不创建虚假实体、数据库任务或普通 API 包装；三目标均可进入正式开发链 |
| 正式身份与人工编辑 | 生成/局部编辑/Markdown/重读得到同一入口；改派生身份或 stem 冲突明确拒绝 |
| 草稿与确认 | 草稿不推进；当前版本显式确认后才满足门禁；过期版本拒绝，用户输入保留 |
| 接入设计确认含义 | 面板展示只读基线及授权说明；设计确认不调用模型、不执行 DAG，能力修改进入正式修订 |
| 双文件提交中断 | 首次替换后退出、第二次替换失败及外部编辑都不能沿用旧 confirmed；文件保留，当前编辑经同步和显式确认后才可提交 |
| 空任务运行 Unit | 不调用业务模型 Worker；真实 Agent 前置任务和 app:integration 验收屏障不丢失；缺少屏障拒绝 Pending DAG，无任务不算完成 |
| 验收实际执行与重复进入 | 已确认 targetAcceptance 在单测成功、完成登记前执行；读取/刷新不调用模型，同一 running attempt 不重复派发；进程中断后不自动重试 |
| 验收阶段失败 | 页面/接口/Agent 三目标均在 Build/单测后验收；Runtime 阶段失败不得登记接口、Agent 或消费页面就绪，源码成功事实可保留 |
| 两个 Agent 仅实现一个 | A 页面使用独立临时 probe Runtime/前端真实联调，B 未实现不阻断 A；完整 Preview 仍要求全量；原预览进程与正式路由未被改动 |
| 单进程启动门禁 | worker 数为 2 或第二进程竞争同命名空间均启动失败；正常退出后可重新取得进程锁，无法核验拓扑时不开放运行路由 |
| 同 thread 并发运行/续接/查询 | 一个执行，其余立即 busy；查询不误报完成；互斥在异常收口后可释放 |
| 首批能力限制 | 实际可调用工具集合为空；默认工具、未声明 interrupt 和伪造续接均在执行前拒绝，不产生工具副作用或待批准项，内部 state 不外泄 |
| 策略交付与装配 | 缺失/过期/伪造策略不能启动；Agent 不能由客户端换能力或写平台策略，实际装配检查发现默认工具时阻断；未实现目标没有可调用占位策略 |
| 请求与历史镜像 | 正常 SDK 多轮不重复写入历史；新增 system/tool、篡改 assistant/user 前缀、非空 tools/state 注入及未知 forwardedProps 拒绝；只读同步忽略本地镜像且不执行图 |
| 续接错误类型 | 模板基础用例在声明允许的对应 kind 下拒绝空澄清、非字符串澄清及非 boolean 确认；首批运行合同额外拒绝一切未声明 kind；不调用续接图 |
| 文本与终态 | 收到真实文本增量、匹配身份的 v1 结果和运行结束；仅 RUN_FINISHED 不构成成功 |
| 断线、停止、模型失败 | 完整失败与结果未知分开显示；未知 thread 禁止发送/续接；同步 busy 或无 pending 都不证明原 run 成功；显式新建会话不复制发送旧消息，迟到事件不覆盖新会话 |
| Prompt 与无关变更 | Prompt 不使接口设计 stale；仅相关模块/接入证据需更新；新增 Agent 不使全部模块失效 |
| 摘要归一化与复用 | 集合重排不变、步骤重排改变、默认值等价；展示编辑重新确认但不重建；共享注册不污染模块摘要，相关实现变化使证据失效；重新确认不将失败变成功 |
| 共享工厂实现覆盖 | 单改 A Builder 或其 helper 使真实消费者失效；不能因整文件排除而复用旧成功；未知动态依赖阻断，策略/实现摘要不递归 |
| 固定真实模型验收 | 固定请求只发一次，正常拒绝文本可满足协议接入但不证明业务能力；缺文本/结果/结束失败；用例、断言或 verifier 改动使旧验收失效 |
| 期限、容量和回收 | 第三个同身份执行及第五个进程执行立即拒绝；跨 thread 不能绕过；总期限包含图与发送背压；任务未收口不放锁，清理失败实例拒绝新调用 |
| 目标会话清理 | 成功/失败/取消都只回收本 session 的前端/Runtime/观察桥；回收失败不登记成功，用户原 Preview/Debug 不被停止 |
| 完成与统计 | 模块成功但接入未通过时 Agent 未就绪；运行接口有独立状态，应用总数不重复 |
| 普通业务接口 | 数据库/外部 API/静态映射的当前语义和对应开发目标保持正确 |
| Electron 浅深主题 | 设计、会话、加载、冲突、busy 与错误状态可读；真实调用不以 Mock 替代 |

实施前按改动阶段确定最小验证命令，不默认运行所有已知套件。代码变更需安排对应 focused 验证、一次 health；前端行为变更安排一次 `pnpm build` 和已运行 Electron 的直接验证。Python 使用对应 focused 测试或必要的定向语法检查，不重复覆盖同一结论。

每个约定验证命令最多一次。任何验证失败立即停止，返回命令、退出码、原始错误和剩余未执行项，由用户判断下一步；不自动修复重跑、不继续扩测。若启动环境或 Electron 不可用，如实记录未验证，不能报告通过。

已定位的定向回归入口包括 `Backend/tests/test_technical_planning_agent_context.py`、`test_agent_action_bindings.py`、`test_main_flow_closure.py`、`test_main_flow_followup.py`、`test_runtime_agent_development.py`、`test_runtime_gap_closure.py`；前端包括 `Frontend/tests/agentRuntimeAdapter.test.ts`、`agentFloatingPanel.test.tsx` 及既有 API 设计/开发产物套件。它们是选用入口，不是默认全跑清单。

本轮仅完善设计文档，上述验收仅是后续计划，测试、构建、health、语法/import、模板启动及 Electron 验证全部未执行。

## 13. 能力扩展与写入开放条件

### 13.1 只读 Tool、业务输入与结构化结果

下一批按实际需求开放只读 Tool、受类型约束的业务输入、公开 state/outcomes 和对应页面消费；这些能力可以分别受限交付，不把任意一项的成功等同于全部支持。

业务输入绑定限定为 client_input、非敏感 constant、平台白名单 server_context；每个必填参数只有一个来源，由 Runtime 在 Pydantic 边界校验，客户端不能覆盖 Agent、Principal 或服务端绑定。用户文本继续走 messages。JSON Schema 采用平台已实现的有界子集，拒绝远程引用、任意表达式和超限内容。

业务上下文首次调用后冻结；换订单等操作新建会话，不能在待确认时改变参数。公开 state 采用白名单投影，不能暴露内部图状态、工具参数、checkpoint 或凭据。

文本只供展示；结构化分析须有实际生产节点和结果 Schema 校验；写入成功须由可信业务提交链产生。页面 sequence 只能按所声明的 outcome 推进，不能按文本、HTTP 200、RUN_FINISHED 或任意结果推进。结果不合规明确失败，不自动重跑模型或已提交操作。

页面绑定需验证公开输入/状态/结果路径、澄清回答、确认/拒绝入口；重复或迟到事件不得重复触发页面动作，不能再发一次请求重复执行 Agent 已完成的写入。

### 13.2 写入 Tool 与执行状态

写入能力另设批次，开放前必须具备：

1. 明确区分协议 run、逻辑执行、待交互项和业务 operation 的身份；重连或续接新 run 不产生第二次业务操作。
2. 确认绑定具体工具、参数及参数摘要；服务端重新检查身份、归属和当前权限，拒绝过期、改参或跨执行批准。澄清和拒绝均不授权写入。
3. 当前待交互原子消费，重复批准返回当前事实，图重入、服务重启也不能依赖进程内 set 去重。
4. 稳定 operationId 和持久幂等记录；本地业务写入及其幂等结果尽可能处于同一事务。
5. 外部服务不具备幂等键或提交查询证据时，不承诺 exactly-once；不确定提交保持 unknown，禁止自动重试。
6. 可信提交链生成业务结果并关联真实提交证据；模型自填 ID 或声称已成功不能触发页面业务成功动作。
7. 断线、取消与提交状态分离；取消需服务端确认，不能抹掉已提交结果。恢复只读查询当前事实，不重放原消息或写入。
8. 活跃执行绑定正式能力/参数摘要；修订前处理活跃执行，不加载旧合同副本续接不匹配批准。Cookie/Profile 变化不自动继承旧执行归属。
9. 状态具备当前执行身份及顺序约束，拒绝旧结果覆盖新结果；输入、状态、执行时长和工具次数有界。

进程内互斥、boolean 类型校验或对话 completed 都不能代替上述条件。未来如需执行身份、新控制动作或升级结果事件，应单独确认精确合同，再同步模板、页面和验收；不把候选 v2 事件、agentInput、runtimeControl 字段当作首批要求或当前已支持能力。

任意普通接口流式化、一个 Agent 多个独立公开能力接口、多 Agent 编排、Agent-as-Tool、无页面产品入口和跨页面会话统一管理不在首批。

## 14. 风险与开放门槛

以下是设计风险及开放条件，不是已发生缺陷或已关闭风险的证明。首批涉及项在发布前必须有证据；扩展前、写入前项目分别阻断对应能力，不要求首批伪造未开放能力的验收。

| ID | 适用阶段 | 风险 | 处理与验收要求 |
| --- | --- | --- | --- |
| R01 | 首批 | 普通接口设计直接改选 Agent，技术合同冲突 | 先按 kind 分支；跨类型/换 Agent 经 Formal Revision，确认前保持原正式版本 |
| R02 | 首批 | 页面直连 Agent 与 Endpoint 调用并存 | 编译后仅有 Page → Endpoint → Agent 一条关系，无重复执行入口 |
| R03 | 首批 | 提示、白名单、Markdown 或 TS 投影丢字段 | 生产、同步、确认、冻结全链往返保留合同；不匹配严格失败 |
| R04 | 首批及扩展前 | 模板未支持却被声明 ready | 检查实际模板、生成包、注册与真实调用；每批只验收已开放能力 |
| R05 | 首批及 Tool 开放前 | Tool 引用运行入口或形成间接环 | 有界闭包在任务生成前拒绝，展示完整引用链 |
| R06 | 首批 | 身份、namespace、thread 或 Profile 混用 | 服务端核验隔离；其他身份不能读取或续接当前会话 |
| R07 | 写入前 | 双击批准、重连或图重入重复写入 | 原子交互消费、持久幂等及真实提交证据 |
| R08 | 首批基础/写入前 | 错误类型、过期交互或改参复用批准 | 首批服务端严格类型校验；写入批准另绑定执行、参数摘要和权限 |
| R09 | 写入前 | 文本或伪造 ID 被当成业务成功 | 只接受可信提交链的 outcome；无实际提交不生成成功动作 |
| R10 | 首批 | 单目标依赖漏检或 Probe 要求全量 Agent | 同一闭包贯通三目标；页面目标也按必要集合验收 |
| R11 | 首批 | 运行入口套用 CRUD 或重复生成路由 | 非模型 prerequisite_only Unit；无转发层，真实路由唯一 |
| R12 | 首批 | v7/v8 或运行事件局部切换 | 当前设计生产消费者同批切换；保留明确的 Runtime v1，无旧格式 fallback；断线未知仅为本地显示状态 |
| R13 | 首批及结果扩展前 | RUN_FINISHED 被当业务成功 | 完整匹配结果才结束回答；业务动作另需声明的可信 outcome |
| R14 | 首批基础/写入前 | 断线、取消被当成回滚或同步后误报成功 | 首批结果未知禁用原 thread 发送/续接，同步不补记成功；显式新会话不重放；写入按提交证据确定结果或 unknown |
| R15 | 首批及结果扩展前 | 旧结果或重复事件覆盖新状态 | 首批匹配 Agent/thread/run；扩展后增加执行顺序和页面动作去重 |
| R16 | 首批及输入扩展前 | 同 thread 并发或业务上下文漂移 | 服务端互斥与 busy；有业务参数时冻结上下文，改参新会话 |
| R17 | 首批及扩展前 | 内部 state、工具参数或凭据外泄 | 有界白名单投影和日志/错误脱敏，不公开完整内部对象 |
| R18 | 结构化结果/写入前 | 结果校验失败后重跑已提交写入 | 输出校验与提交证据分离，失败停止、零自动重放 |
| R19 | 首批限制/交互能力开放前 | 未声明交互进入执行，或开放后页面缺少操作入口 | 首批服务端拒绝所有续接和 interrupt；开放交互后完整性验收包含回答、确认与拒绝，不能自动批准 |
| R20 | 交互能力开放前 | 修订后旧批准进入新逻辑 | 活跃执行摘要复核，先处理活跃执行，拒绝旧合同续接 |
| R21 | 首批 | 并行生成覆盖共享工厂注册 | 共享锁、当前精确路径和受控更新；保留其他有效注册 |
| R22 | 首批 | Agent 与 Endpoint 双份可编辑 Schema | Agent 唯一权威，Endpoint/页面只有引用与只读投影 |
| R23 | Tool 开放前 | 前端调用内部 Tool 模拟 Agent | 区分直接消费与实现依赖，服务端独立校验权限及归属 |
| R24 | 首批 | 静态说明字典冒充真实接线，或验收门互等 | app:integration 承载目标验收屏障，不依赖 Agent 应用级 ready；失败不登记完成，静态、启动、受控行为、真实流分别记录 |
| R25 | 首批 | Prompt 触发全量失效，或空 Unit 丢任务前置/验收屏障 | 统一第 9.4 节语义投影与归一化，直接依赖/共享注册分开；穿透空节点但保留三目标验收阶段 |
| R26 | 首批 | 新功能绕过未支持的登录/授权边界 | application.json 继续唯一权威，原拓扑门禁拒绝未支持组合 |
| R27 | 首批及 Tool 开放前 | 超长输入、无限模型/工具执行 | 复用有界输入、时长和工具次数配置，超限明确失败，不自动继续 |
| R28 | 会话管理扩展前 | 跨页面返回隐式重发或重复开始 | 首批明确本地会话边界；扩展后只读恢复当前事实，不自动重放 |
| R29 | 首批 | 无实体 Agent 被迫创建虚假表/数据库依赖 | 允许运行 Contract.entity_ids=[]；来源驱动依赖，真实无数据库启动验证 |
| R30 | 写入前 | 拒绝仍写入或仍推进成功 sequence | 拒绝是未授权执行，零写入且不触发成功后续动作 |
| R31 | 首批 | 双文件替换中断后沿用旧 confirmed | JSON 最后提交并绑定 Markdown 字节摘要；所有门禁拒绝不成对版本，保留人工编辑 |
| R32 | 首批 | 多进程使 thread 互斥失效 | 单 worker 强制校验、命名空间进程锁及受控本机拓扑；无法核验或锁冲突时拒绝启动 |
| R33 | 首批 | 声明纯文本但默认工具仍可执行 | 生成、装配、请求及执行入口共同强制能力约束，首批可调用工具集合为空 |
| R34 | 首批 | 只读接入设计重复确认且授权范围不清 | 区分技术能力决策、接入基线签认和 DAG 执行授权；确认设计不触发模型调用 |
| R35 | 首批 | app:integration 仅留在图上但无真实调用点，或节点重入重复验收 | targetAcceptance 纳入确认/Build 快照，单测后完成前实际调用；prepared/running 和调用预算先落盘，失败/未知不自动重跑 |
| R36 | 首批 | 部分实现通过 Probe 却无法做页面联调，或验收扰动正常 Preview | 独立临时 probe+目标真实前端会话、同源身份核对和专属清理；完整 Preview 全量门禁保留 |
| R37 | 首批 | 能力合同未交付到 Runtime、策略过期或由客户端覆盖 | 平台编译只读 execution_policy、启动摘要核验、不可变装配策略及执行前保护，缺策略不降级 |
| R38 | 首批 | 排除共享 factory.py 导致实际 Builder 变化漏失效 | Agent Builder/静态依赖/公共符号完整投影，未知依赖拒绝；实际执行代码有明确摘要归属 |
| R39 | 首批 | 标准 AG-UI 类型合法但注入角色、工具、state 或改写历史 | 请求有限白名单，服务端历史为权威；只接收新 user 尾项，镜像不写图，只读查询不承载执行 |
| R40 | 首批 | 验收没有固定输入或重复消耗模型，verifier 变化复用旧结果 | 固定 smoke 请求/断言及单次供应商预算，verifier/用例/参数摘要入证据；发送不确定不自动再发 |
| R41 | 首批 | 换 thread 绕过并发、图或流背压无总期限、取消后留后台任务 | 可信身份/进程容量、整体 deadline、输出限额和清理期限；未收口不释放名额，失控实例停止接单 |

首批另须验收设计文件 stem 唯一、运行接口状态独立与应用聚合去重。上述风险不能以“设计已保存”“七模块成功”“health 正常”或单一静态检查代替关闭证据。

## 15. 请评审的决策项

| 编号 | 建议决定 | 主要影响 |
| --- | --- | --- |
| D1 | 保留现有开发阶段，围绕 Endpoint 做纵向改造 | 不重建工作流/调度器；合同与消费者需联动修改 |
| D2 | 首批一个 Agent 一个派生运行 Endpoint；页面统一引用 Endpoint | 接口设计中不能随意换 Agent，变更走正式修订 |
| D3 | implementationKind 区分实现分支，interactionContract 描述交互能力 | 首批仅开放 Agent 文本 AG-UI 流，不承诺通用接口流式开关 |
| D4 | 正式设计与草稿统一 v8，技术 pageIds 权威移至产品派生 | 所有当前消费者同批切换；既有旧格式需要重新设计，无迁移兼容 |
| D5 | 非模型运行 Unit，app:integration 作为逻辑验收归属；targetAcceptance 在单测后、完成登记前实际执行 | 需明确 Workflow 调用与防重复记录；无源码任务不等于无验收前置，不生成第二份路由，也不等待全应用测试 |
| D6 | 应用层每个 Agent 只计一次，就绪包含实现和接入两类证据 | 接口独立展示状态，Agent 聚合门禁包含接口缺失原因 |
| D7 | 首批纯文本闭环，依赖模型同时容纳后续 Tool | 只读与写入按能力条件分批开放，不能宣称所有已有 Agent 已完成升级 |
| D8 | 首批可调用工具集合为空，未声明交互由服务端拒绝 | 默认工具不能旁路公开能力；无法受控装配的目标明确阻断 |
| D9 | Runtime v1 断线不恢复原 run 终态，未知会话仅允许只读同步和显式新建会话 | 不新增恢复协议；同步无 pending 不能恢复为成功或继续原 thread |
| D10 | 双文件最后提交点、单进程启动门禁及统一摘要规则纳入首批 | 文件一致性、互斥前提和证据失效均有确定合同与负向验收 |
| D11 | 部分实现的页面验收使用独立临时 probe 会话和真实前端视图 | 不降低完整 Preview 门禁，不改用户正式路由或停止原预览；引入会话级启动、代理、观察与清理职责 |
| D12 | 能力策略由平台编译交付，实际 Builder/公共符号参与实现摘要 | 增加模板 execution_policy 和平台精确写权限；策略/实现摘要分层，禁止自引用和整文件漏算 |
| D13 | 标准 AG-UI 后继续校验入口白名单与可信历史，整体 run 有明确预算 | 多轮只追加一条新 user，镜像不覆盖服务端；身份/进程并发、总期限及取消清理纳入首批 |
| D14 | 固定 smoke 用例，每 Endpoint 每授权 attempt 最多一次供应商请求 | 协议接入与业务能力分别验收；用例/verifier 变化使证据失效，失败不自动重试 |

评审意见记录：已补齐 7 项后续审阅缺口——验收实际执行与防重、部分实现的临时页面联调、能力策略交付/加载、共享工厂符号摘要、AG-UI 请求及历史边界、可执行验收用例，以及整体期限/容量/清理。当前共 41 项风险登记，首批固定预算是拟实施合同，不是当前已生效设置。文档完善不构成本方案整体代码实施授权，也不证明风险已关闭；此前单独修复的 Debug 健康代码状态保持。评审和后续实现只使用本文件中的统一要求。
