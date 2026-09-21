# 拓扑设计与实现模板

每套新拓扑使用同一交付顺序，设计审核和代码注册之间设硬边界。

## 1. 设计文档

新建独立文档并明确：

- 拓扑目标与不适用范围；
- 自动匹配的充分必要条件；
- 与其他拓扑同时匹配时的冲突处理；
- Public Edge、服务边界、认证终止点与可信身份传播；
- Auth、Authorization、DataSource 等正交能力约束；
- 本地调试与生产运行边界；
- 当前正式产物需要新增或替换的字段；
- 模板 capability、失败关闭条件及安全不变量。

设计未确认时只允许预留 `TopologyType`，不得注册实现或修改下游结构。

## 2. 三阶段 Definition

审核通过后，在 `Backend/app/topologies/definitions/` 新增一个 Definition，实现：

- `matches(context)`：只读取正式候选事实和 canonical application config；
- `compile_design(context)`：返回 Public Edge、service ids、认证终止点和架构摘要；
- `compile_planning(context)`：返回 managed roots、Unit ids、模板能力和 required checks；
- `compile_development(context)`：返回 Generator owners、启动阶段和验收检查。

Definition 只编译差异蓝图，不直接读写工作区、生成代码、启动进程或修改 DAG 状态。

## 3. 注册与主流程接入

1. 在唯一 Registry 中绑定枚举和 Definition；
2. 为唯一匹配、无匹配、多匹配和未注册编译补测试；
3. 让 TechnicalPlan 保存最小稳定拓扑投影；
4. Bootstrap 只消费 Planning 蓝图；
5. 通用 Build DAG 将 Planning 蓝图展开为 Unit/Edge；
6. Generator 和 Launcher 只消费 Development 蓝图；
7. 删除该拓扑在旧路径中的重复结构推断；
8. 更新 `docs/CODEBASE_INDEX.md` 和实现状态。

## 4. 完成标准

- 用户已确认该拓扑的独立设计；
- Registry 是唯一可执行入口；
- 下游不根据目录存在、Agent 数量或某个开关重复猜测拓扑；
- 正交能力没有膨胀为组合枚举；
- 模板能力缺失时 fail-closed；
- 设计、计划、开发和端到端验收证据完整。
