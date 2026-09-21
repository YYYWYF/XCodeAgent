# 生成应用拓扑设计目录

本目录是生成应用拓扑重构的唯一设计入口。拓扑只描述服务组成、公开入口、模板 roots、Build Unit 差异和运行服务图；认证、授权、数据源等正交能力继续由应用配置和正式业务产物决定。

当前拓扑设计只允许三种目标形态：`agent_runtime_direct`、`backend_direct` 和 `gateway_composed`。候选拓扑必须先形成独立设计并由用户审核确认，之后才允许加入注册表和主流程。Gateway 不提供带单一 upstream 的半组合拓扑。

## 当前拓扑状态

| 拓扑 | 状态 | 设计文档 |
| --- | --- | --- |
| `agent_runtime_direct` | 首套直连拓扑；设计与迁移实现独立管理 | [整体设计](./agent-runtime-direct/AGENT_RUNTIME_DIRECT_DESIGN.md) |
| `backend_direct` | 目标形态已确定，尚未迁移 | 后续独立设计 |
| `gateway_composed` | 四模块组合设计已固定，尚未迁入统一注册表 | [Gateway 设计](../gateway/GENERATED_APPLICATION_GATEWAY_DESIGN.md) |

## 文档导航

- [统一拓扑架构](./APPLICATION_TOPOLOGY_DESIGN.md)：枚举、注册表、三阶段策略、选择与迁移规则。
- [拓扑实现模板](./TOPOLOGY_IMPLEMENTATION_TEMPLATE.md)：后续每套拓扑从设计审核到注册实现的固定结构。
- [Agent Runtime Direct 整体设计](./agent-runtime-direct/AGENT_RUNTIME_DIRECT_DESIGN.md)：Frontend + Runtime 直连拓扑、自动选型和阶段边界。
- [Agent Runtime Direct 正式合同](./agent-runtime-direct/AGENT_RUNTIME_DIRECT_CONTRACTS.md)：TechnicalPlan、Agent Contract、TemplateState 和失效规则。
- [Agent Runtime Direct 构建运行](./agent-runtime-direct/AGENT_RUNTIME_DIRECT_BUILD_RUNTIME.md)：Bootstrap、Build DAG、Generator、Testing、Launch 和 Acceptance。
- [Agent Runtime Direct 安全调试](./agent-runtime-direct/AGENT_RUNTIME_DIRECT_SECURITY_DEBUG.md)：Auth、多用户 ownership、匿名会话和本地调试。
- [Gateway 设计目录](../gateway/)：Gateway 的设计、合同、构建、运行时和 Agent 附录；继续保持原有文档归属。

以后新增拓扑必须先在本目录形成独立设计并完成审核，再在 `Backend/app/topologies/` 注册；不得在 Bootstrap、Build 或 Launcher 中单独增加一套未注册的拓扑判断。
