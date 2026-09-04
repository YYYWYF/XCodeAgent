# T2.2 ReuseFacts

## 边界与现状

ReuseFacts 回答 confirmed DAG 已登记哪些职责、workspace 已证明哪些能力。
它不回答任务是否执行成功，也不计算本轮缺项、`planning_unit_ids`、
`generation_requirements`、Candidate、Scope Assembly 或执行依赖。

现有 `tasks.py` 的 `_unit_tasks_are_reusable` 依赖任务完成状态，
`_replaceable_unit_ids` 与 `_merge_prepared_scope_tasks` 按整 Unit 替换，
与新规划基线不同。T2.2 提供独立事实层，暂不把结果接入这些旧决策。
后续接入必须同时遵守“同一共享 Unit 可 retain + generate”和 append-only 约束。

T2.3 的 `unit_generation_requirements.py` 已消费本层事实计算缺项；输入、精确职责身份
和生成策略见 `docs/UNIT_GENERATION_REQUIREMENTS.md`。两层仍未接入旧 Planning 主流程。

## 接口

```python
resolve_reuse_facts(
    *,
    confirmed_plan,
    unit_skeleton,
    build_context,
    workspace_snapshot,
    formal_plan,
    template_readiness=None,
) -> ReuseFacts
```

- `confirmed_plan`：来自 `load_confirmed_build_task_plan(workspace_root)` 的正式文件内容，或 `None`。
  函数自身不读取文件，也不能替调用方证明任意内存 dict 的文件来源。
  防御检查拒绝非 confirmed、失败计划、非 v3 或未通过 task graph validation 的输入。
- `unit_skeleton`：当前 `ensure_build_unit_skeleton` 输出，使用 `build_units` 的精确键。
- `build_context`：现有定向上下文。`required_unit_ids` 仅用于 workspace 前置问题归因，
  不裁剪历史 Task；`template_variant` 在存在时必须与只读检查结果一致。
- `workspace_snapshot`：当前工作区快照，以 `workspace_revision` 标识证据所属快照。
- `formal_plan`：完整正式 TechnicalPlan 的运行时投影；Endpoint 目录来自
  `api_contracts[].id + endpoints[].id`，不能只传当前目标的裁剪目录。
- `template_readiness`：可选，必须由调用方在同次工作区检查中调用现有
  `inspect_template_generation_readiness` 获得，不能由模型生成。
  本 Task 不改变 WorkspaceSnapshot 存储格式。

## 输出

输出复用 T1.1/T1.2 的冻结模型基础，可通过 `model_dump(mode="json")`、
`model_dump_json()` 和 `model_validate_json()` 序列化及恢复。

| 字段 | 内容 |
| --- | --- |
| `retained_task_ids_by_unit` | Unit ID → 已确认计划中的 Task IDs，包含 pending/failed/completed Task |
| `reusable_capabilities_by_unit` | Unit ID → 精确 capability ID → provider Task IDs |
| `retained_endpoint_owners` | `api_contract_id`、`endpoint_id`、`owner_task_id`、`owner_unit_id` |
| `external_capabilities` | 无 Task provider 的能力，包含 Unit、capability、workspace revision、平台证据引用 |
| `issues` | 不可重试的 T1.1 `ValidationIssue`，存在问题时后续调用方必须阻断规划 |

所有 Task 均从完整 registry 读取，不使用执行状态、当前 Scope 或描述来删除 Task。
输出按精确身份排序。改变输入字典顺序、capability 声明顺序或 Task 执行状态，
不会改变事实；函数不修改输入对象，不读写工作区文件。

## capability 规则

只收集 `provides_capabilities` 与 `deliverables[].provides` 中的显式身份。
现有 Unit 编译器会默认把 Unit ID 写入 `provides_capabilities`；该结构占位值
不作为“整个 Unit 的职责已满足”的能力证明。它不会阻止保留该任务。

例如 `frontend:api-client` 中有 adapter 和 users API，只登记它们各自声明的
capability 和正式 Endpoint owner。不会因此产生 orders API 的 capability，
也不会产生 Unit 级 `reuse_only` 决策。

`frontend.auth.resources:R1` 与 `frontend.auth.resources:R2` 是不同身份。
本层保留已经声明的精确身份，不在 T2.2 中计算资源目录 fingerprint 或生成 auth Task。

## Endpoint owner 规则

仅从平台编译的 `frontend.api_contract`、`frontend.static_data_contract`
业务检查的 `expected.endpoints` 读取实现 owner。Repair 继承检查与
`frontend.page_endpoint_usage` 不产生新的实现 owner。

匹配使用完整、区分大小写的正式复合 ID。不用 Task ID、文件路径、HTTP 路径、
方法或自然语言相似度猜测等价。缺失身份、未知正式身份均显式报错。
同一任务对同一 Endpoint 的多条检查只证明一个 owner，不合并任何 Task。

多个 confirmed Task 声明同一 Endpoint 时，保留全部 owner 供诊断，并返回
`CONFIRMED_ENDPOINT_OWNER_CONFLICT`：`level=pre_generation`、
`category=platform`、`retryable=false`、`retry_unit_ids=[]`。
调用方不能任取一个 owner，也不能让模型修复 confirmed baseline。

## workspace external capability

第一项有现成确定性证据的能力是 `frontend.shell.ready`：

- 骨架包含 `frontend:shell`；
- 平台模板只读检查 `ready=True`、无 errors，具有 manifest 和 main/auth 变体；
- 存在 workspace revision，且与 BuildContext 的模板变体不冲突。

外部能力记录 template manifest 路径及内容 SHA-256，提供证据追踪。
它不创建 shell Task，也不创建 Task dependency。
缺少 `template_readiness` 时不宣称 shell 已满足；已有上游 gate 仍负责前置检查。

扫描到 package.json、App.tsx、HTTP client 或模型声称 `inspection_status=completed`
都不构成能力证明。当前服务不从这些弱线索推导 adapter、Endpoint 或权限资源已满足。
其他 workspace 能力必须先有相应的平台精确验证契约，不能添加猜测式 fallback。

## 验证入口

- 新增：`Backend/tests/test_build_task_reuse.py`、`test_build_task_reuse_workspace.py`。
- 前置契约：`test_planning_issues.py`、`test_unit_generation_contracts.py`、`test_confirmed_build_task_plan_loader.py`。
- 必须回归：R-PLAN、R-TEMPLATE、R-AUTH 全部文件。
