# 七模块 Agent DAG：复用 1028 主流程

## 与旧分支的差异

| 范围 | 0922 实现 | 当前搬运决定 |
| --- | --- | --- |
| 主流程 | Unit、候选、累计 DAG、确认、Build | 沿用 1028 的完整流程，不复制旧 Graph 或 Scheduler |
| Python 业务任务 | 新增 Entity、Migration、Repository、Service、Endpoint Units | 保留 1028 Endpoint Unit 和五阶段职责，只切换 Python 实现根与执行器 |
| Agent 规划 | 平台模板策略直接编译七模块候选 | 平台固定七个职责、任务身份和依赖；复用当前 Unit Worker 选择精确文件及实现步骤，经过同一 Local/Global Validation |
| 数据库 | 旧业务 SQLite/SQL 确认链 | 不搬入；Tool 依赖现有 Endpoint Design 与内置数据库业务实现 |
| 复用 | Agent/模块与模板摘要 | 按当前模块输入、服务边界、模板策略判断，复用当前 ReuseFacts 和失效替换机制 |
| 确认和恢复 | 原分支确认/恢复实现 | 保留 1028 PendingPlan、DraftIdentity、资源锁、正式确认、运行结束和 Recovery |

变化集中在 Runtime 的事实、职责、写入边界和执行分派，不新增 DAG 协议或持久化文件。

## 正式事实

唯一 Agent 定义放在当前 `TechnicalPlan.agent_contracts`，无 Agent 的业务应用可以不包含该字段。模型只能在已确认业务要求明确需要可执行 Agent 时生成定义，不根据项目名称或普通 CRUD 推断 Agent。

每项必须且只能包含：

- `agentId`：唯一 lower_snake_case，保留名称 `runtime` 不可用。
- `name`、`purpose`：非空业务名称和职责。
- `pageIds`：非空、不重复、真实存在的页面 ID。
- `agentSettings`：完整的 `prompt/model/memory/tools/skills/knowledge/context` 七个对象。

`prompt.systemPrompt` 必须明确；`model.source=project`，不保存模型或数据源凭据；tools/skills/knowledge 必须显式声明 enabled。Tool 的每条 binding 严格使用 `toolId/apiContractId/endpointId`，引用当前唯一 Endpoint；它表示调用该 Endpoint 背后的本地 ApplicationService，不表示经过 Java 网关或调用 loopback HTTP。

这是当前 1028 消费合同，不读取旧版 ProductPlan.agents、旧 Direct SQL 或旧 source 别名。当前产品事实和 Endpoint 设计继续由 1028 原流程拥有。

七模块设计在同一份技术规划 Markdown 展示，编辑后沿原同步和确认门提交。选型摘要包含 Agent 合同，修改后必须重新选择/确认正式版本，不增加第二套自动确认链。

## Unit 与任务

Runtime 应用为每个正式 Agent 增加 `agent:<agentId>` Unit，kind/owner 均为 `agent`。没有 Agent 的 Java 流程不增加 Unit、职责或执行器调用。

每个 Agent 首次规划有七个任务，ID 为 `agent:<agentId>::<module>`，顺序固定：

```text
prompt → model → memory → tools → skills → knowledge → context
```

每个任务恰好一个 `agent.runtime` deliverable，target_id 为 agentId，capability 为 `agent.<agentId>.<module>`。增量候选仅生成缺项模块，依赖最近的候选或有效 retained 前序模块；不引用不存在的旧任务。显式关闭的模块仍须检查实际 wiring，不能在规划阶段伪造 already_satisfied。

页面开发把绑定 Agent 及其 Tool Endpoint 实现纳入同一 Scope；独立业务 Endpoint 开发不反向扩展成全部 Agent 开发。Tool Endpoint 使用同一字段映射门禁、正式 API Design 和既有 Backend Unit。

Unit 图表达 Endpoint → Agent → 页面。Task 编译继续保留 1028 页面只继承前端执行依赖的并行规则：页面可按正式合同并行生成，不因 Runtime 改写普通前后端并行策略；Agent Task 必须等待其 Tool 的真实 Backend Task 依赖。前端 API 职责只包含页面直接调用的 Endpoint，不为内部 Tool 顺带生成前端模块。

## 复用公共规划基础设施

复用 `resolve_generation_requirements → SequentialPlanningInputs → FrozenContractStore/Catalog → Unit Worker → Local Validator → ScopeAssembly/Global Validation → PendingPlan → 用户确认 → Build`。

不新增 PlanningRun 状态、调度器、Graph 节点、确认端点、恢复存储或自动循环。七模块路径策略冻结在同一个 BuildContext，正式 Agent 与 Tool/API Design 通过当前 Frozen Store selector 授权。模块来源在平台编译阶段覆盖候选自报值，不能跨 Agent/模块伪造复用证据。

增量摘要只包含当前模块配置及直接输入：提示词/上下文增加名称和职责；tools 增加其引用的接口和 Schema；服务图只比较实现边界，不把全计划摘要或确认时间当作每个模块的直接依赖。模板策略变化使相关任务失效，继续使用当前固定 ID 替换过期任务和 Pending 确认机制。

## 执行边界

`agents/agent_runtime/modules.py` 通过原 Build owner 分派入口调用，不启动第二套执行流程。执行前重算当前正式模块摘要，并重新读取和验证真实模板策略；过期任务必须重新规划确认。

Local Validator 要求精确文件、同一目标集合、串行标志、模块固定身份、依赖和唯一 capability。只能修改平台 modifyPaths 或在 addRoots/testRoots 下新增允许的源码/资源文件，不能声明整个目录为 allowed_paths。单次执行器绑定精确文件集合，写入时再次拒绝符号链接和越界路径。

模型、文件工具、真实 Diff、逐任务结果、工具活动、取消登记、工程检查和错误展示均沿现有 Build 基础设施。结果使用当前严格 task_results 协议，不恢复旧 implementation_action 字段。失败停止本批后续模块调用；不自动安装、启动服务或执行验证命令。

Runtime 分支明确跳过 Java 骨架注入和 Java 预置文件提示。数据库建表、连接取密、模型环境注入和 Runtime 启停不由 Agent 任务执行。

## 文件归属与验证状态

- 合同和依赖：`runtime_agent_contracts.py`、`runtime_agent_dag.py`。
- 路径、摘要和 Local 规则：`runtime_agent_module_policy.py`、`runtime_agent_candidate_validation.py`。
- 规划规则：`agents/main/agent_unit_task_rules.py`；公共 Worker/Controller/Scheduler 无新增分支状态机。
- 生成执行：`agents/agent_runtime/modules.py`；公共 Build 仅增加 owner 分派。
- 正式文档：`workspace/runtime_agent_markdown.py`，复用 TechnicalPlan 原保存、编辑和确认。

按用户要求未运行测试、构建、health、语法检查或 Electron 验证。七模块 DAG 的代码接入不代表 Runtime 端到端验收已通过；Python 验收、数据库运行注入、前端 Agent 调用及启动调试仍需后续接通，架构选项继续保持不可用。
