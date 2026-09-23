# 拓扑设计与实现模板

每套拓扑必须按同一顺序交付，设计确认与代码注册之间设硬边界。

## 1. 设计合同

独立设计必须明确：

- 固定服务组成与不支持范围；
- Public Edge、认证终止点和内部信任边界；
- Entity、API、Agent、数据持久化的实现 owner；
- managed roots 与目录结构；
- Endpoint API Design 如何映射到该拓扑的实现；
- Build Unit、Generator、启动图和验收检查；
- Application Config 能力约束；
- 与其他拓扑切换时的失效范围。

拓扑不得修改选择前的业务事实，只能编译实现归属和工程蓝图。

## 2. Definition 合同

Definition 必须实现：

- `is_available(context)`：模板、生成器和运行闭环是否已经可用；
- `validate_selection(context)`：用户选择能否完整承载当前业务事实与配置；
- `compile_design(context)`：服务图、Public Edge、认证和 owner；
- `compile_planning(context)`：模板、managed roots、Unit/Edge Blueprint；
- `compile_development(context)`：Generator、测试、启动和验收边界。

验证失败只能返回结构化错误，不得自动改选其他拓扑或进入旧路径。

## 3. 注册与选择门禁

固定实施顺序：

1. 完成独立设计并由用户确认；
2. 准备模板、capability manifest 和路径策略；
3. 实现 Definition 与确定性编译器；
4. 实现 Build Unit、Generator、Testing、Launch 和 Acceptance；
5. 为可用、未实现、配置冲突和事实不完整补充测试；
6. 注册 Definition；
7. 在前端选择门禁解除禁用状态；
8. 删除该拓扑在旧流程中的重复推断和兼容回退。

UI 展示不代表已实现；只有 Registry、模板和完整运行证据同时存在时才允许选择。

## 4. 完成标准

- 用户选择通过 AG-UI 门禁提交并绑定准确 revision；
- 最终 TechnicalPlan 保存唯一 `topology.type` 和稳定投影；
- Endpoint API Design 保持拓扑无关；
- Build Context 能把同一 Contract 绑定到正确实现 owner；
- Generator 写范围不跨拓扑 roots；
- Testing、Launch 和 Acceptance 证明实际服务图与计划一致；
- 不存在自动匹配、静默回退、历史 schema 读取或双写。
