# 生成应用拓扑重构决策记录

> 2026-10-28 搬运裁决：以 `dev_agent_integration_1028` 当前合同为准。应用配置、内置数据库、Template Engine、正式产物确认、恢复及资源锁均沿用新版；本文描述的 Runtime 扩展在完成对应阶段前不代表已经可用。

> 状态：迁入 1028 的目标设计；功能交付进度以 [搬运记录](<AGENT_RUNTIME_PORT_1028.md>) 为准

## 1. 已确认结论

1. RequirementSpec、ProductPlan、UiDesign 和 TechnicalPlan Core 保持拓扑无关。
2. 业务 Entity、API Contract、页面行为和 Agent 业务需求先完整规划，再选择 Runtime 实现。
3. 拓扑在计划阶段由用户显式选择，平台不再自动匹配。
4. 未实现拓扑可以展示，但必须禁用；UI 展示不等于实现完成。
5. 用户选择后，TopologyDefinition 只负责编译实现 owner、模板、Build Unit、Generator 和启动图。
6. 选择无效时 fail-closed，不允许自动改选或回退旧流程。
7. 最终拓扑身份只写入已确认 TechnicalPlan，不进入 Application Config 开关。

## 2. 三种拓扑结论

| 拓扑 | 业务代码 | Agent 代码 | 公开入口 |
| --- | --- | --- | --- |
| `agent_runtime_direct` | Python Application Runtime | Python Application Runtime | Python Runtime |
| `backend_direct` | Java Backend | 无 | Java Backend |
| `gateway_composed` | Java Backend | Python Agent Runtime | 独立 Gateway |

`agent_runtime_direct` 必须支持业务 Entity、Repository、Application Service、FastAPI Endpoint 和数据库持久化。现有“entities/api_contracts 必须为空、只生成 Agent 七模块”的实现不再代表目标设计。

## 3. 被废弃的结论

- 平台根据产品事实自动调用 `matches()` 选择唯一拓扑；
- 无匹配时继续进入尚未迁移的当前流程；
- 纯 Agent 才能使用 `agent_runtime_direct`；
- `agent_runtime_direct` 不允许业务 Entity、API 或数据库；
- 现有 Java Backend + Python Agent 路径已经等于 `gateway_composed`；
- 通过 Application Config 开关或目录存在反推拓扑。

## 4. 1028 裁决

旧分支的自动匹配、历史计划回退、Git 模板兜底、本地 SQLite 业务建表、独立 Direct API 确认链均不直接搬入。现有 Java 工作流维持当前合同，Runtime 是新增的显式能力。共享配置、内置数据库及任务恢复使用 1028 实现。

## 5. 实施顺序与风险

具体阶段、依赖和完成证据见 [1028 搬运记录](AGENT_RUNTIME_PORT_1028.md)。Template Engine 当前只交付 frontend/backend，Python 模板和 capability 支持是后续接入的前置依赖。不得凭 UI 展示或 Python /health 成功宣称完整业务链路已通。

## 1028 实施补充：数据库、模板与传输

- Studio 新增或实质修改的产品动作采用 AG-UI 完整生命周期，Renderer 使用官方客户端。生成应用的新增产品动作也遵守仓库 AG-UI 规则；既有 Endpoint 的方法、路径和正式 Schema 不得在搬运中被暗改。
- 业务表使用平台内置 MySQL、当前完整物理表名和已有实体表操作；连接复用 prepare_managed_startup／resolve_managed_runtime。只向 Python 子进程注入所需连接，不传 SaaS 服务密钥，连接错误和日志统一脱敏。
- Runtime checkpoint／短期记忆与业务数据库分开；是否更换其存储需独立模板能力与验收，本次不把业务 MySQL 接入等同于已替换 checkpoint SQLite。
- Template Engine Generate 的当前包校验及配置更新事务保持 1028 合同。需要新增 Runtime 根目录与 capability 时同步生产者、消费者和测试，不使用旧 Git 克隆兜底或手造 TemplateState。
- 保留新版实体创建／重建／结构检查／操作记录、版本 SQL 交付、字段映射、配置事务、资源锁、任务结束及恢复语义，不恢复旧 Direct 独立 SQL／API 确认链。

