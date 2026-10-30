# 生成应用统一拓扑架构

> 2026-10-28 搬运裁决：以 `dev_agent_integration_1028` 当前合同为准。应用配置、内置数据库、Template Engine、正式产物确认、恢复及资源锁均沿用新版；本文描述的 Runtime 扩展在完成对应阶段前不代表已经可用。

> 状态：迁入 1028 的目标设计；本文不作为功能交付完成证明。
> 目标拓扑：`agent_runtime_direct`、`backend_direct`、`gateway_composed`

## 1. 核心结论

生成应用采用“拓扑无关业务规划 + 用户显式选择 + 拓扑确定性编译”。RequirementSpec、ProductPlan、UiDesign 以及 TechnicalPlan Core 中的业务语义不得因拓扑不同而改变；拓扑只决定这些业务事实由哪些服务实现、代码生成到哪里、公开入口是谁、如何构建和启动。

平台不得根据实体数量、Agent 数量、目录、认证开关或模型输出自动选择拓扑，也不得在用户选择无效时静默改选或回退旧流程。

```text
RequirementSpec + ProductPlan + UiDesign + Application Config
  -> TechnicalPlan Core
     - entities
     - api_contracts
     - pages / action bindings
     - agent requirements / settings
  -> topology_selection 用户确认门禁
  -> selected TopologyDefinition.validate_selection
  -> compile_design / compile_planning / compile_development
  -> 最终 TechnicalPlan + topology projection
  -> 用户确认最终 TechnicalPlan
  -> Endpoint API Design
  -> Build Unit / DAG / Generator / Launch / Acceptance
```

## 2. 阶段边界

### 2.1 拓扑选择之前

以下事实必须保持拓扑无关：

- 页面、信息项、Action、业务流程和验收标准；
- 业务 Entity、字段与关系；
- API Contract、Endpoint、Request/Response Schema；
- Action/Step 到 Endpoint 的绑定；
- Agent 能力、交互方式、Tool 业务需求和七模块设置；
- Endpoint 字段来源与业务处理需求。

TechnicalPlan Core 是计划阶段的内部候选，不是第五种正式产物，不单独作为用户可编辑 JSON 落盘。它进入拓扑选择门禁后才由平台编译为可确认的最终 TechnicalPlan。

### 2.2 拓扑选择门禁

前端展示 Registry 中声明的三种目标拓扑：

- 已完成实现并通过能力检查的拓扑可选择；
- 尚未实现的拓扑保留展示，但必须禁用并明确标记状态；
- 默认值只是前端初始选择，不得替代用户确认；
- 提交必须包含服务端签发的 gate/revision 身份和精确 `topologyType`；
- 后端只验证用户选择，不推断替代值。

用户改变选择时重新运行拓扑编译；最终 TechnicalPlan 未确认前不进入 Endpoint API Design、Bootstrap 或 Build。

### 2.3 拓扑选择之后

TopologyDefinition 负责把同一份业务事实投影为：

- 服务组成、Public Edge 和认证终止点；
- Entity/API/Agent 的实现 owner；
- managed roots、模板能力与 Build Units；
- Generator 写入边界；
- 启动图、集成测试和验收检查。

拓扑编译不得删除业务实体、Endpoint、页面动作或 Agent 能力来满足自身不变量。选择不满足实现约束时必须 fail-closed，并要求用户修改选择或先完成对应拓扑实现。

## 3. 三种拓扑

| 拓扑 | 固定组成 | 业务 Entity/API owner | Agent owner | Public Edge |
| --- | --- | --- | --- | --- |
| `agent_runtime_direct` | Frontend + Python Application Runtime | Python Runtime | Python Runtime | Python Runtime |
| `backend_direct` | Frontend + Java Backend | Java Backend | 不生成 Agent Runtime | Java Backend |
| `gateway_composed` | Frontend + Gateway + Java Backend + Python Agent Runtime | Java Backend | Python Agent Runtime | Gateway |

### 3.1 `agent_runtime_direct`

Python Runtime 不是只保存 Agent 状态的 Sidecar，而是完整 Application Runtime：同时承载业务 Entity、Repository、Application Service、FastAPI Endpoint、Agent、AG-UI、认证适配和运行状态。REST Endpoint 与 Agent Tool 必须复用同一 Application Service，不允许通过本机 HTTP 回调复制业务逻辑。

### 3.2 `backend_direct`

业务实体、数据访问、事务和 API 全部由 Java Backend 承载；不生成 Agent Runtime 或 Gateway。该拓扑的独立实现设计尚未完成。

### 3.3 `gateway_composed`

业务实体、事务和业务 API 由 Java Backend 承载；Agent 由 Python Runtime 承载；独立 Gateway 是唯一公开入口并负责路由、公开认证和受控身份委托。Gateway 不承载领域业务或 Agent 推理。

不支持 Frontend + Gateway + Backend 或 Frontend + Gateway + Agent Runtime 的半组合形态。

## 4. 正式事实与持久化

`.devagentstudio/application.json` 继续只拥有认证、授权及其他应用级能力配置，不增加 `gateway.enabled`、`backend.enabled` 或可绕过计划门禁的拓扑开关。

用户确认后的拓扑身份写入最终 `TechnicalPlan.topology.type`。最终 TechnicalPlan 同时保存最小稳定拓扑投影，包括服务、Public Edge、认证终止点、实现 owner 与输入事实摘要；端口、PID、模板 commit、DAG Task 和运行状态不得写入 TechnicalPlan。

拓扑选择不是从 Application Config、目录或历史产物反推的。任何消费者只能读取已确认 TechnicalPlan 的拓扑投影或统一查询服务。

## 5. Definition 与 Registry 合同

只有进入唯一 Registry 的 Definition 才可选择。Definition 必须提供：

- `is_available(context)`：验证模板、编译器、生成器和运行链路是否已经实现；
- `validate_selection(context)`：验证当前应用配置和业务事实能否由该实现完整承载；
- `compile_design(context)`：编译服务图、Public Edge、认证终止点和实现 owner；
- `compile_planning(context)`：编译 managed roots、模板能力、Unit/Edge Blueprint 和 required checks；
- `compile_development(context)`：编译 Generator、启动图、测试和验收边界。

不得保留以下接口语义：

- 遍历 Registry 自动调用 `matches()` 选择拓扑；
- 无匹配时进入旧生成流程；
- 多匹配时让平台自行决定；
- 通过目录存在、Agent 数量或 capability 开关重新推断拓扑。

未注册、未实现或验证失败的选择必须返回结构化错误，不得生成部分工程。

## 6. Build 与代码生成原则

- 所有拓扑复用同一个 Pending/Formal Build DAG 生命周期；
- 拓扑提供 Unit/Edge Blueprint，不建立第二套任务系统；
- Endpoint API Design 保持拓扑无关，Build Context 再按拓扑绑定实现 owner；
- Entity/API/Agent 代码路径只能由选中 Definition 和模板策略授权；
- Generator 不得修改其他拓扑的 roots；
- Build、Testing、Launch 和 Acceptance 必须重复校验正式 topology type 与模板证据一致。

## 7. Formal Revision

已确认应用改变拓扑时必须进入 Formal Revision：

1. 保留当前正式业务事实并生成新的 TechnicalPlan Core；
2. 用户显式选择新的拓扑；
3. 编译并展示服务、目录、数据、公开入口和失效范围变化；
4. 用户确认新的最终 TechnicalPlan；
5. 旧 Build DAG、模板证据、启动和验收结果失效；
6. 按新拓扑重新 Bootstrap 和 Build。

本项目只实现当前合同，不增加历史 schema 读取、字段别名、双写或自动迁移分支。

## 8. 1028 搬运状态

当前 Java 工作流保持原有合同；新增 Runtime 拓扑只作为显式扩展。基础类型迁入不等于注册可用拓扑。模板、生成器、数据库适配、启动和验收完成前不启用 Runtime 选项。不得自动把现有应用切换到 Python，或要求现有 Java 应用重新确认不存在的拓扑字段。

## 1028 实施补充：数据库、模板与传输

- Studio 新增或实质修改的产品动作采用 AG-UI 完整生命周期，Renderer 使用官方客户端。生成应用的新增产品动作也遵守仓库 AG-UI 规则；既有 Endpoint 的方法、路径和正式 Schema 不得在搬运中被暗改。
- 业务表使用平台内置 MySQL、当前完整物理表名和已有实体表操作；连接复用 prepare_managed_startup／resolve_managed_runtime。只向 Python 子进程注入所需连接，不传 SaaS 服务密钥，连接错误和日志统一脱敏。
- Runtime checkpoint／短期记忆与业务数据库分开；是否更换其存储需独立模板能力与验收，本次不把业务 MySQL 接入等同于已替换 checkpoint SQLite。
- Template Engine Generate 的当前包校验及配置更新事务保持 1028 合同。需要新增 Runtime 根目录与 capability 时同步生产者、消费者和测试，不使用旧 Git 克隆兜底或手造 TemplateState。
- 保留新版实体创建／重建／结构检查／操作记录、版本 SQL 交付、字段映射、配置事务、资源锁、任务结束及恢复语义，不恢复旧 Direct 独立 SQL／API 确认链。

