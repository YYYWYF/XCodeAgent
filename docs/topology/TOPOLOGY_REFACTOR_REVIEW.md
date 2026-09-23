# 生成应用拓扑重构决策记录

> 状态：本轮设计结论已确认，代码尚未按新合同完成迁移。

## 1. 已确认结论

1. RequirementSpec、ProductPlan、UiDesign 和 TechnicalPlan Core 保持拓扑无关。
2. 业务 Entity、API Contract、页面行为和 Agent 业务需求先完整规划，再选择拓扑。
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

## 4. 现有实现归类

现有代码同时包含：

- Java Backend 的 Entity/API 生成；
- Python Agent Runtime 七模块生成；
- Java 侧 Agent Gateway Endpoint 语义；
- 一个仅允许空 Entity/API 的 `agent_runtime_direct` 原型。

这套实现只能作为迁移输入：Java + Python 的组合语义归入未来 `gateway_composed`，但在独立 Gateway 模板、Definition、Build 和启动图完成前不能标记为该拓扑已经可用；旧 Direct 原型则必须由新的 Python Application Runtime 模板替换。

## 5. 实施顺序

1. 把前端展示升级为正式 AG-UI `topology_selection` 门禁；
2. 将 TechnicalPlan 生成拆成 Core 与 topology compilation；
3. 删除 `resolve_registered_topology()` 自动选择和 `_agent_runtime_direct_candidate()`；
4. 重建 `agent_runtime_direct` 模板和 Python 业务生成器；
5. 接通 Python Endpoint API Design、Build Units、测试和启动；
6. 完成 `gateway_composed` 独立 Gateway 与迁移；
7. 设计并实现 `backend_direct`；
8. 删除全部旧路径推断和兼容回退。

## 6. 当前风险

- 前端已经展示拓扑，但提交载荷尚不包含选择；
- 当前 Registry 和代码仍按旧自动匹配合同运行；
- 新 Direct 模板未完成前，不能把现有 Direct 选项视为生产可用；
- Entity/API Contract 当前默认面向 Java Backend，需要增加拓扑无关的实现 owner 编译层；
- Agent Tool 与 REST Endpoint 必须共享 Application Service，避免 Python 内部通过 HTTP 自调用形成两份业务逻辑。
