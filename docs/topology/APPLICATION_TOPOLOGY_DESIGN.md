# 生成应用统一拓扑架构

> 状态：拓扑框架合同  
> 目标拓扑：`agent_runtime_direct`、`backend_direct`、`gateway_composed`

## 1. 目标

生成应用主流程采用“固定流水线 + 可注册拓扑策略”。拓扑差异只通过同一注册表提供设计、计划和开发三个阶段蓝图，现有 Workflow、Build DAG 生命周期、调度、确认、修复和验收继续复用。

```text
Application Config + 已确认产品事实
  -> DesignTopologyPolicy
  -> TechnicalPlan.topology
  -> PlanningTopologyPolicy
  -> Template roots + Unit/Edge Blueprint
  -> 通用 Build DAG
  -> DevelopmentTopologyPolicy
  -> Generator binding + Launch/Acceptance Plan
```

## 2. 拓扑选择

框架完成后，创建应用不要求用户每次手动选择模式。平台根据已确认产品事实和应用配置自动匹配唯一拓扑：

1. 只有一个拓扑满足时直接使用；
2. 没有已注册拓扑满足时，迁移期间继续走尚未迁移的当前主流程；当前批次属于这种状态；
3. 多个拓扑同时满足时禁止静默选择，设计阶段必须澄清一次；
4. 用户明确要求“纯 Agent、无 Java Backend/Gateway”时，该约束进入设计上下文，但最终仍必须通过拓扑确定性校验；
5. 拓扑类型确认后写入 `TechnicalPlan.topology.type`，下游不得重新根据目录是否存在进行猜测。

目标选择矩阵：

| 拓扑 | 固定组成 | Public Edge | 能力约束 |
| --- | --- | --- | --- |
| `agent_runtime_direct` | Frontend + Agent Runtime | Agent Runtime | `auth.enable` 可选；`authorization.enabled` 必须为 `false` |
| `backend_direct` | Frontend + Backend | Backend | 不生成 Agent Runtime 和 Gateway；业务 RBAC/数据权限由 Backend 负责 |
| `gateway_composed` | Frontend + Gateway + Backend + Agent Runtime | Gateway | `authorization.enabled=true` 时必须 `auth.enable=true`，并在 Gateway 生成 Agent RBAC |

不支持 Frontend + Gateway + Backend 或 Frontend + Gateway + Agent Runtime。Gateway 只是完整组合拓扑的统一公开边界，不是为单一 upstream 独立开启的通用代理。

以上是框架的目标选择合同，不表示任何候选拓扑已经启用。未进入 Registry 的枚举不能参与匹配、编译或持久化。

拓扑是技术设计事实，不在 `application.json` 增加 `backend.enabled`、`gateway.enabled` 或 `topology.type` 开关。`application.json` 继续只拥有认证、授权及其他应用级能力配置。能力配置必须满足已选拓扑的不变量，但不能反向伪造或部分启用某个拓扑。

## 3. 实现模式

`TopologyType` 可以先预留已命名、待审核的拓扑枚举。只有在唯一 Registry 中绑定 `TopologyDefinition` 后，该拓扑才属于可执行实现。Definition 必须实现：

- `matches`：判断正式事实是否满足拓扑不变量；
- `compile_design`：服务、Public Edge、认证终止点和架构摘要；
- `compile_planning`：managed roots、Unit、模板能力和 required checks；
- `compile_development`：Generator owner、启动阶段和验收检查。

直接编译未注册枚举、同一输入匹配多个实现或已确认拓扑不再满足不变量时必须 fail-closed。自动匹配只遍历已经注册的实现，不会把预留枚举当作可用拓扑。

## 4. 阶段边界

### 设计阶段

- 平台而不是模型最终决定拓扑；
- TechnicalPlan 只持久化稳定拓扑身份和设计事实；
- managed roots、进程端口、密钥、DAG Task 和启动状态不得写入 TechnicalPlan；
- 拓扑变更必须重新确认 TechnicalPlan。

### 计划阶段

- 拓扑提供 Unit/Edge Blueprint，不建立第二套 DAG；
- Bootstrap、TemplateState、Build Context 和 Unit Skeleton 只消费已确认拓扑；
- 平台继续拥有 Pending/Formal Build DAG、确认、复用、调度和修复。

### 开发阶段

- 拓扑只声明 Generator、服务启动图和验收检查；
- 现有 Frontend、Backend、Agent Runner 和通用 Launcher Executor 执行蓝图；
- Auth、Authorization、DataSource 等能力通过独立 Capability Compiler 绑定到 Public Edge，不能扩展成拓扑组合枚举。

## 5. 迁移规则

`agent_runtime_direct` 作为首套拓扑进行迁移；`backend_direct` 和 `gateway_composed` 必须各自完成 Definition 设计、审核和注册，不得沿用 Gateway 开关或旧 Agent sidecar 推断代替拓扑。在 Bootstrap、DAG、Launch 和回归证据完整前，不能声称某套拓扑已完成迁移。

每迁移一个拓扑，必须先提交独立设计供用户审核；确认后才允许注册实现，并同步删除该路径在旧模块中的结构推断。

当前项目不增加历史 schema 读取、字段别名或双写；迁移桥接只存在于运行路径选择，不把旧合同伪装成新拓扑合同。
