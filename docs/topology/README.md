# 生成应用拓扑设计目录

本目录是生成应用拓扑的唯一设计入口。当前合同采用“拓扑无关业务规划 → 用户显式选择 → 拓扑确定性编译”，不再由后端自动匹配拓扑。

## 当前拓扑状态

| 拓扑 | 目标代码归属 | 新合同实施状态 | 设计文档 |
| --- | --- | --- | --- |
| `agent_runtime_direct` | Frontend + Python Application Runtime；业务 Entity/API 与 Agent 均生成到 Python | 设计重构完成，模板与生成器待重建；现有注册实现不满足新合同 | [设计目录](./agent-runtime-direct/AGENT_RUNTIME_DIRECT_DESIGN.md) |
| `backend_direct` | Frontend + Java Backend | 尚未形成独立 Definition | 统一合同中的目标边界 |
| `gateway_composed` | Frontend + Gateway + Java Backend + Python Agent Runtime | 规范设计保留，Registry、独立 Gateway 模板和运行闭环尚未完成 | [设计目录](../gateway/README.md) |

## 文档导航

- [统一拓扑架构](./APPLICATION_TOPOLOGY_DESIGN.md)：选择门禁、三种拓扑、正式事实和阶段边界。
- [拓扑实现模板](./TOPOLOGY_IMPLEMENTATION_TEMPLATE.md)：每套拓扑从设计到注册的固定交付顺序。
- [拓扑重构决策记录](./TOPOLOGY_REFACTOR_REVIEW.md)：本轮已确认结论、废弃结论和实施顺序。
- [Agent Runtime Direct](./agent-runtime-direct/AGENT_RUNTIME_DIRECT_DESIGN.md)：Python Application Runtime 的完整设计。
- [Gateway Composed](../gateway/README.md)：Gateway 组合拓扑的规范文档索引。

新增或迁移拓扑时必须先完成独立设计和用户审核，再接入选择门禁与 Registry。不得在 Bootstrap、Build、Generator 或 Launcher 中增加未注册的拓扑判断，也不得恢复自动匹配或旧流程回退。
