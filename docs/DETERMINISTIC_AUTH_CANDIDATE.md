# T2.5C Deterministic Auth Candidate Builder

`Backend/app/services/deterministic_unit_candidates.py` 只构造当前 auth 资源缺项的候选正文。
它不读取或写入工作区、不执行 Task、不调用模型、不进入 Worker/模型并发槽、
不分配 AttemptIdentity，也不读取或增加 Local model attempts。

## 接口

```python
build_auth_guard_candidate(
    *,
    unit_id="frontend:auth-guard",
    resource_catalog=catalog,
    fingerprint=resource_catalog_fingerprint(catalog),
    generation_requirements=requirements,
) -> dict | None
```

- `resource_catalog` 使用 T2.5A 从当前确认 manifest 编译的完整目录。
- `generation_requirements` 是 T2.3 `resolve_generation_requirements` 的完整结果，
  其中 reuse 判断已经消费 T2.5B ReuseFacts。builder 不再读取历史 Task 或自行判断 workspace。
- source refs 取自结果中的唯一 auth `GenerationRequirement.source_refs`，复制后原样保留。
  `artifact`、`kind`、`capability_id`、`resource_catalog_fingerprint`、`paths`
  必须与当前 TechnicalPlan 资源职责及唯一资源路径一致。
- 当前 Unit 无缺项时返回 `None`，包括 confirmed provider、workspace satisfied 和权限关闭。
  关闭权限的空缺项允许 `resource_catalog=None, fingerprint=None`。
- 当前 Unit 有且仅有一条当前 R 的 deterministic 缺项时，返回 `{"tasks": [task]}`。
  其他 Unit、模型策略、多个缺项、目录/指纹/来源冲突均显式失败，不自动修复输入。

前置失败使用 `GenerationRequirementsError` 携带不可重试的
`AUTH_CANDIDATE_INPUT_INVALID`；DTO 结构不合法仍由已有 Pydantic 契约拒绝。

## Task 契约

| 字段 | 值 |
| --- | --- |
| `id` | `frontend-auth-resources-<完整64位fingerprint>` |
| `unit_id` | `frontend:auth-guard` |
| `owner` | `frontend` |
| `task_type` | 既有 `frontend.code` |
| `execution_strategy` | `deterministic` |
| `platform_executor` | `authorization.frontend_resources` |
| `dependencies` | `[]` |
| `target_files` / `allowed_paths` | 仅 `frontend/src/constants/resources.ts` |
| `provides_capabilities` | 仅 `frontend.auth.resources:<R>` |
| `deliverables[0].kind` | 既有 `frontend.shared_capability` |
| `deliverables[0].provides` | 同一精确资源 capability |
| `deliverables[0].paths` | 同一唯一资源路径 |
| `source_refs` | 当前缺项中的正式来源、R、capability 及路径 |

使用完整 fingerprint 避免截断摘要的身份碰撞；ID 不受描述、Run、Attempt、时间或随机数影响。
provider 使用现有 `provides_capabilities` / `deliverables[].provides` 契约，
不增加历史字段别名。R1 Task 不混入新 Candidate，也不被删除或替换。
`routes.tsx` 不属于该 Task，跨 Unit dependency 仍由后续平台编译。

## 接入边界

返回的是标准 `tasks` 候选正文，不是已经判定 valid 的 `CandidateAttempt`。
现有 `CandidateAttempt` 强制携带 AttemptIdentity；本 builder 不为确定性生成虚构模型 Attempt。
候选记录的生命周期、持久化、Local/Global validation 和 Scope Assembly 由后续任务接入。

旧 `build_task_planner` 的 Task 归一化尚未保留 `execution_strategy` / `platform_executor`，
因此本任务不把输出送入旧归一化或 Build dispatch，也不修改 authorization projection writer。
Task 的执行和完整资源投影验收仍属于后续 executor/validation 工作，本任务不声称已执行。

## 验证

新增 `Backend/tests/test_deterministic_unit_candidates.py`：确定性、同 R/异 R 身份、
单文件 ownership、owner/strategy/executor/provider 契约、真实 reuse 门禁、输入冲突、
输入不变，以及不进入模型/Attempt/Worker/文件写入路径。

必跑 R-AUTH、R-PLAN，并联跑 T2.5A catalog、T2.5B auth reuse、T2.3 auth requirement 回归。
