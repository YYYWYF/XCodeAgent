# T2.3 Generation Requirements

## 实施边界

`required_unit_ids` 描述 Scope 所需 Unit，ReuseFacts 描述已有职责，
本模块计算本轮还缺少的职责。三者独立，不能用“required 减去已有 Task 的 Unit”替代。

本模块调用纯函数，输出冻结数据；不生成 Candidate，不调用模型，不实现 Retry、
Scheduler、Assembly、文件读写或确认流程。本 Task 未切换 `tasks.py` 的旧主流程。
旧流程仍采用整 Unit 替换；接入新结果需要与后续 append-only Assembly 一起完成。
T2.4 仅将 shell 模板前置事实接入现有入口，并固定 shell 的非生成状态；
不切换本模块对其他 Unit 的生成决策，见 `docs/FRONTEND_SHELL_PREREQUISITE.md`。

## 接口和输入

```python
resolve_generation_requirements(
    *,
    required_unit_ids,
    build_execution_scope,
    unit_skeleton,
    reuse_facts,
    formal_target,
) -> UnitGenerationRequirements
```

- `required_unit_ids`：上游已解析的列表或元组；本层不扩展依赖闭包。
- `build_execution_scope`：当前字段 `type`、`targetId`，Endpoint 还必须提供 `apiContractId`。
  支持当前 resolver 的 application/page/endpoint；其他类型显式失败。
- `unit_skeleton`：现有 `ensure_build_unit_skeleton` 输出。required Unit 必须存在，key 与节点 id 必须一致。
- `reuse_facts`：T2.2 ReuseFacts。存在 issues 时先失败，不返回部分 planning 结果。
- `formal_target`：完整、confirmed 的 TechnicalPlan 运行时投影，不是页面 ID 或任意描述。
  使用当前 `page_implementation_contracts`、`api_contracts`、`entity_detail_plans`、
  `authorization_manifest`；不会从磁盘补读缺失字段。

页面 Scope 通过 PageImplementationContract 的 `requiredEndpointIds` 选择 Endpoint；
每个引用必须在完整正式 API 目录中唯一可定位。Endpoint Scope 使用精确复合 ID。
application Scope 使用全部正式页面实现契约和 Endpoint。
数据源来自合同实体对应的 confirmed EntitySourceBinding，不根据 HTTP 方法或文件路径推断。
缺少类型时不能默认为 database。

## 输出和策略

输出只含三个字段，可通过 `model_dump(mode="json")`、`model_dump_json()` 和
`model_validate_json()` 序列化、恢复；嵌套 GenerationRequirement 和 source_refs 只读。

| 字段 | 含义 |
| --- | --- |
| `generation_requirements_by_unit` | 每个 required Unit → 本轮缺少的 GenerationRequirement 列表 |
| `planning_unit_ids` | 有非空缺项且策略为 model/deterministic 的 Unit，按 ID 排序 |
| `generation_strategy_by_unit` | 每个 required Unit 的策略 |

| Unit/情况 | 策略 | 是否进入 planning |
| --- | --- | --- |
| `application:root`、`app:integration` | structural_only | 永不 |
| `frontend:shell` | prerequisite_only | 永不 |
| 有正式职责，且全部由精确 reuse 事实满足 | reuse_only | 否 |
| `frontend:auth-guard` 有资源目录缺项 | deterministic | 是 |
| 其他支持的 Unit 有缺项 | model | 是 |
| model Unit 在当前 Scope 没有适用职责 | model，空需求 | 否 |
| 权限关闭，骨架仍包含 auth-guard | deterministic，空需求 | 否 |

shell 缺少 T2.2 提供的外部 `frontend.shell.ready` 平台证据时前置失败；
不会创建 shell Task 去修复模板。

`reuse_and_generate` 表示“ReuseFacts 中的历史任务继续保留，当前结果还有新增缺项”，
它不是第六种 generation strategy，也不在本 Task 中引入 UnitRunState。

## 职责身份

每个需求复用 T1.2 GenerationRequirement：`requirement_id`、中文 description、source_refs。
`source_refs.capability_id` 是后续 Candidate 必须显式保留的职责能力身份。
复合 ID 的各个目标分量分别进行百分号编码，保持大小写，不做历史别名匹配。

| 职责 | requirement_id / capability_id |
| --- | --- |
| 页面实现 | `frontend.page:<page_id>` |
| 公共 ResponseEntity adapter | `frontend.response-entity-adapter`（沿用当前已明确的能力名） |
| 前端正式接口模块 | `frontend.api_module:<api_contract_id>:<endpoint_id>` |
| 静态接口模块 | `frontend.static_data_module:<api_contract_id>:<endpoint_id>` |
| bootstrap 数据源能力 | `backend.bootstrap:database` / `backend.bootstrap:external_api` |
| 后端实体职责 | `<deliverable_kind>:<api_contract_id>:<endpoint_id>:<entity_id>` |
| 当前权限资源目录 | `frontend.auth.resources:<resource_catalog_fingerprint>` |

database Endpoint 涉及 domain_mapping、repository、application_service、endpoint_controller；
external_api Endpoint 涉及 external_api_client、external_api_mapping、application_service、
endpoint_controller。这里列出职责，不创建 Task 或规定模型必须返回同等数量 Task。
静态实体不产生后端职责。

复用条件为同 Unit 的精确 capability，或同 Unit 的外部能力；前端 API/静态模块还可由
精确 `api_contract_id + endpoint_id` 的 retained Endpoint owner 证明，owner 可属于其他 Unit。
仅保留 Task ID、执行成功、名称/描述相似都不证明职责满足。

现有 Task 使用其他 capability 命名时，本模块不猜测其与新职责 ID 等价。
后续 Candidate 生成与验证需使用需求中的 capability_id；本次不改写已有 DAG，也不增加兼容 reader。

## auth-guard

`resource_catalog_fingerprint` 对当前 `compile_frontend_authorization_projection` 输出的
完整 `resources` 目录进行 canonical JSON + SHA-256。目录自身已按常量组/名排序；
指纹不包含页面路由和展示名称。当前 Scope 外的资源变更也会改变目录身份。

精确指纹能力已在 ReuseFacts 中存在则 reuse_only，否则产生一条 deterministic 需求，
目标路径固定为 `frontend/src/constants/resources.ts`。R1 不能满足 R2。

本 Task 不读取或验证实际 resources.ts，也不调整现有平台资源写入职责。
T2.5B 已通过 ReuseFacts 提供 auth workspace 精确验证证据；本模块仍不直接读取 workspace。

## 失败与验证

失败通过 `GenerationRequirementsError.issues` 携带 T1.1 ValidationIssue，
均为前置、不可模型重试的问题。冲突 ReuseFacts 原样传递；非法 Scope、正式身份、
缺失绑定、未知 Unit 和缺失 shell 证据均显式失败，不返回 replacement requirement。

新增用例位于 `Backend/tests/test_unit_generation_requirements.py` 与
`test_unit_generation_requirements_auth.py`。必跑 R-PLAN、R-AUTH、R-TEMPLATE，
并联跑 T2.1/T2.2 和冻结 DTO 的相关回归。
