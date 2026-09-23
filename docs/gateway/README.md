# Gateway Composed 设计目录

本目录只保存 `gateway_composed` 的规范性设计。评审过程稿、架构备选稿和旧拓扑升级 Runbook 已删除，避免与当前“用户显式选择拓扑”的合同形成第二事实源。

`gateway_composed` 固定生成 Frontend、独立 Gateway、Java Backend 和 Python Agent Runtime。用户在计划阶段明确选择该拓扑后，平台验证实现可用性和不变量，再编译最终 TechnicalPlan；平台不得根据业务事实自动匹配、自动切换或回退到其他拓扑。

## 文档导航

- [总体架构](./GENERATED_APPLICATION_GATEWAY_DESIGN.md)：服务组成、职责边界、公开入口和实施状态。
- [正式合同](./GENERATED_APPLICATION_GATEWAY_CONTRACTS.md)：TechnicalPlan、GatewayPlan、Route 和失效规则。
- [模板、规划与构建](./GENERATED_APPLICATION_GATEWAY_BUILD.md)：模板能力、Build Unit、DAG 和启动门禁。
- [运行时、安全与验证](./GENERATED_APPLICATION_GATEWAY_RUNTIME.md)：认证、内部身份、协议、韧性和测试。
- [Agent 场景](./GENERATED_APPLICATION_GATEWAY_AGENT.md)：Agent 路由、委托票据、RPC 和恢复语义。

## 当前实施状态

- 架构与详细合同可以作为目标实现依据；
- `gateway_composed` 尚未进入统一 Registry；
- 独立 Gateway 模板、Build Unit、启动图和验收证据尚未完成；
- 现有 Java Backend + Python Agent 的旧生成路径只能作为迁移输入，不能声明为已经实现的 `gateway_composed`。
