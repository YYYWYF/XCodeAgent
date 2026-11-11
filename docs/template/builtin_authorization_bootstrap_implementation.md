# Builtin 权限数据库自动编排实施与验收

实施基线为 `dev_agent@12e2c4599d2403e8d152db81c83aebe2161a97f9`。实施前查询远端并快进本地分支，用户提供的方案文件保持原样。工作按 PR1、PR2、PR3 顺序落在当前工作区。

## PR1 后端编排与持久化

`builtin_authorization_orchestrator.py` 复用原 Schema 和 Seed 单次服务。每个 tick 只推进一个协调阶段。Schema 完整核验后，下一轮自动进入 Seed。Seed 先查询原 ID 或原幂等键，明确查无记录时按原键、原载荷 POST；本地 submitting/unknown 记录尝试进度，不永久禁止提交。202 始终只代表受理。

编排文件为 `.devagentstudio/runtime/builtin-authorization-bootstrap/orchestration.json`，通过原子替换、文件和目录 fsync 保存。字段包括 `schemaVersion`、`generation`、`inputFingerprint`、`operationStateExpected`、`seedStateExpected`、`status`、`stage`、`failureCategory`、`updatedAt`。不保存 Subject、管理员列表、SQL、Seed 行数据或凭据。操作编号、幂等键、逐表结果和成功事实继续归属 `state.json`。

输入摘要覆盖 REGISTERED 绑定、DDL、已确认 Manifest、实际 Seed 输入及登录开关。DAG 保留已确认有效的准入校验，不参与权限业务摘要或 Seed 请求前后的等值比较；正常任务进度回写不使权限输入失效。远端请求及业务状态写入边界复核摘要。`orchestration.lock` 为工作区级 portalocker 短事务锁，覆盖首次提交判定、GET/POST 和落盘。原子服务、手工查询与编排器共享同一锁。进程内 RLock 及 worker registry 仅负责本进程串行和降噪。

任务状态为 `pending/running/waiting/ready/needs_attention/failed/cancelled`，阶段为 `schema/seed/verify/done`。线程池最多同时运行四个工作区，最多登记 32 个未结束任务。worker 释放锁后按 2、3、5、10 秒退避，后续封顶 10 秒。单次 worker 连续查询上限为 600 秒，超时保留原身份并进入待处理。测试直接驱动 tick，或注入时钟及等待事件。

`operationStateExpected` 在进入远端动作前持久化，确保原操作文件丢失后不会重新当作首次提交。原 Seed 提交保护落盘后，通过当前 tick 的状态观察器先保存 `seedStateExpected`，再允许原服务进入 POST。整份状态或 Seed 子对象丢失时都停止自动推进。观察器只更新编排文件，不改变原 Seed 的 `state.json` 快照比较。路径中任何软链接、损坏结构、空对象状态文件及身份失配均拒绝自动执行。worker 意外退出会写入 `worker_failed`，旧代次不能覆盖新的任务。

### 崩溃与安全恢复

| 中断位置或状态 | 恢复行为 |
| --- | --- |
| 首次 Schema POST 前 | 沿用原四表检查、历史检查及提交前 `submitting` 保护 |
| Schema POST 后有原 ID | 自动 GET 原 ID，SUCCEEDED 后再严格核验四表 |
| Schema POST 回执丢失，无 ID | 保留 submitting/unknown，不能自动再建表 |
| Seed POST 前后 | 原 `seed.idempotencyKey`、载荷指纹和已有 ID 持续落盘，submitting 不代表远端已受理 |
| Seed 回执丢失 | 按原幂等键 GET，成功后核验指纹、表名、归属、数量和 verified |
| 原 Seed by-key 明确返回查无记录 | 输入和 DAG 仍有效时按原键、原载荷提交，不删除本地记录 |
| 原 Seed ID 明确返回查无记录 | 自动协调先补查原键；纯刷新只返回未找到，不触发 POST |
| 临时网络错误、资源繁忙、408/429 或 5xx | 有界退避后先查询；明确未找到才同键提交 |
| POST 认证、参数或路由拒绝 | 停止自动尝试，修正后通过显式 retry 恢复；幂等冲突不放行 |
| 原操作 FAILED 或 UNKNOWN | 停止自动推进，保留证据和只读查询 |
| 绑定、DDL、管理员、Manifest 或登录开关变化 | 停止并返回 inputs_changed，迟到结果不写为成功 |

编排文件丢失时，可从原 `state.json` 恢复。两份文件都不存在时，仍执行原远端结构/历史检查和 Seed by-key 查询。底层原 `explicit_recovery` 校验保留，自动 ensure/resume 不授予 Schema 重建或远端 Seed FAILED/UNKNOWN 重新执行权限。

### Seed 同键恢复合同（2026-11-10）

本机 `managed-database-service` 源码的 `AuthorizationSeedService.submit` 对已有键调用 `requireFingerprint` 并复用回执；`MybatisDataOperationRepository.begin` 在资源行锁内再次判重，管理库 `(datasource_key,idempotency_key)` 唯一索引兜底，只有新建 Admission 才调度。`AuthorizationSeedJdbcAdapter` 在四表事务内按自然键插入缺失数据、核验身份列，再提交。因此客户端可以重复发送同键同载荷请求，不能自行生成新键绕过原操作。

`run_builtin_authorization_seed` 的原 ID 未找到后补查原键；两者均未找到才进入同键提交。已有 ID 保留在提交标记中，GET 或 POST 返回不同 ID 均报 `seed_operation_identity_mismatch`，不静默覆盖原受理证据。所有可提交路径要求已确认 DAG；纯刷新显式使用只读模式，查询失败或未找到均不 POST。网络和落盘边界继续检查当前绑定、指纹、Manifest 与任务身份。

POST 传输失败或回执无效进入本地 unknown，后续先查询协调；明确拒绝记录本次 failed，不代表已有远端失败操作。400/401/403/404/422 拒绝停止自动尝试并允许显式 retry。409 `RESOURCE_BUSY` 退避后重新查询；`IDEMPOTENCY_CONFLICT` 及其他冲突停止。202 回执即使报告 FAILED/UNKNOWN 也只代表受理摘要，下一轮 GET 核实后停止，保留远端终态。SaaS 当前没有重新执行 FAILED/UNKNOWN 操作的接口，本次不修改 SaaS。源码核对不等同于远端部署验收。

## PR2 运行入口与生命周期

- Graph 在 `_build_plan_gate_error()` 通过后快速调度任务，保留两个既有结果键、`executor=builtin_saas` 和 Build 放行策略。新增 `initializationStatus/initializationStage` 描述独立任务，节点 completed 不推导数据库就绪。
- `/builtin-authorization/run` 的 `get` 仅本地读取，`run_patch` 调用统一 ensure，`resume` 仅安全接管；`retry` 是显式用户动作，可恢复查无记录、查询、认证、参数、服务配置或响应合同故障，不改变输入摘要、已知操作 ID 或幂等键，也不放行远端 FAILED/UNKNOWN 重新执行。`refresh` 与 `refresh_patch` 显式只读，不能自动接续 Seed POST。动作均经过标准 AG-UI 生命周期。
- `workspace_attach` 成功后恢复已有任务，不扫描其他工作区。前端已有生命周期读取会先 attach。卡片检测连接恢复、页面重新可见或 Backend 实例改变时，单独调用同一个 attach，再继续纯只读 get。
- Backend shutdown 唤醒等待中的 worker，取消排队任务并等待当前安全 tick 退出。删除准备停止 worker，保留远端操作证据。删除准备失败回滚时解除本地栅栏并接管原任务。
- Build 路由、Direct 模式、模板 Java Bootstrap 及预览 `seed_ready()` 门禁保持原逻辑。预览未就绪提示更新为自动初始化/待核对文案。

## PR3 前端旅程

工作流和调试补丁共用三阶段卡片，分别展示权限数据表初始化、权限数据初始化、初始化结果核验。运行中隐藏手动刷新和继续 Seed 按钮，显示无百分比的执行条。待处理时展示「重试」、「刷新状态」和详情；后端 `nextAction=resume|retry` 时启用重试，否则禁用并解释原因。重试经独立 AG-UI `retry` 动作，attach 仅沿原 resume 策略接管，不自动重试新增的故障类别。

当前 Run 通过只读 get 每三秒读取一次状态。隐藏页面暂停，卸载和切换工作区撤销旧请求代次。历史 Run 仅显示持久化快照。实时结果回投当前步骤的展示状态，使外层标题与卡片保持一致。颜色、背景、边框、按钮和焦点使用现有 `--wb-*` 明暗主题令牌。

## 自动化验证

在仓库根目录执行以下命令。测试均使用临时工作区和假 SaaS，不接触真实管理员或远端业务表。

```sh
cd Backend
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m unittest \
  tests.test_builtin_authorization_orchestrator \
  tests.test_builtin_authorization_task_lifecycle \
  tests.test_builtin_authorization_seed \
  tests.test_builtin_authorization_bootstrap \
  tests.test_authorization_bootstrap_projection \
  tests.test_application_lifecycle_protocol \
  tests.test_application_deletion_protocol \
  tests.test_application_deletion_run_control \
  tests.test_application_deletion_pending_race
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m unittest tests.test_project_launcher
cd ../Frontend
pnpm test:authorization-bootstrap
pnpm build
```

覆盖首次完整旅程、四表未就绪、两阶段连续 RUNNING、FAILED/UNKNOWN、回执丢失、原键 404、错误成功回执、输入变化、损坏/丢失/软链接状态、原 ID 恢复、两进程 Schema/Seed POST 去重、worker 崩溃、排队取消、shutdown 与 attach。完整旅程断言远端 Schema POST 一次、Seed POST 一次，并核对原 ID GET 次数。前端覆盖后台恢复信号、当前/历史区别、迟到响应、页面隐藏及卸载清理，并保留原非 Builtin DDL 复制回归。

### 本次运行结果

| 检查 | 结果 |
| --- | --- |
| 上述后端组合命令 | 113 项通过 |
| `tests.test_project_launcher` | 56 项通过，进程树测试在沙箱外执行 |
| `pnpm test:authorization-bootstrap` | 18 项通过，正常退出 |
| `pnpm test:connection-recovery` | 113 项通过 |
| `pnpm build` | 通过，包含 Node/Web TypeScript 检查和 Electron 主进程、preload、renderer 构建 |
| 修改的 Python 文件 `py_compile` | 19 个文件通过 |
| `git diff --check` | 通过 |
| `curl -sS http://127.0.0.1:8000/health` | 最终通过，`tools.builtin_authorization.actions` 包含 resume |
| `pnpm test:workflow-preview` | 100 项通过、2 项失败，隔离的原始 HEAD 基线复跑同样失败 |

扩大回归的两个基线失败用例为“技术规划空状态不能遮住结果中的架构或阻止正式文件读取”和“恢复快照中的测试完成确认可从 state 或 result 读取”。现有实现只读当前 state，而断言仍要求从 result 恢复。对应产品源码和测试文件均未纳入本补丁。基线验证使用 `git archive HEAD Frontend` 的临时副本，仅复用已安装依赖。该套件尚未全绿。

后端组合测试中的 worker 崩溃和 recovery projection OSError 日志来自故障注入用例，测试结果为 OK。初次预览进程测试受到沙箱的 ps 限制，沙箱外复跑后 56 项通过。最终健康检查时发现开发后端进程已退出，使用 `scripts/start-backend.sh --env dev` 重启后通过检查。

跨进程假 Transport 使用 fork，当前 macOS 两阶段均已通过。缺少 fork 的平台会跳过该用例，尚未进行 Windows 端跨进程联调。

## Electron 与 SaaS 端到端验收步骤

1. 使用隔离的 Builtin 测试应用，开启权限，完成 REGISTERED 绑定、需求/计划确认、正式 TechnicalPlan 和有效 Build DAG 确认。先确认 SaaS 已部署 Seed 操作表及所需三个接口。
2. 选择只含 SaaS 当前可接收资源的正式 Manifest。资源描述满足当前非空合同。通过真实 Electron 点击开始开发。
3. 观察一个当前权限任务卡，从建表受理、自动 GET 原 Schema ID、四表结构核验，自动进入权限数据初始化。期间不点击刷新或继续。
4. 在受控 SaaS 日志中核对 Schema POST 一次、Seed 首次 by-key GET、Seed POST 一次及后续原 ID GET。卡片最终满足 schemaReady 和 seedReady，三个阶段全部完成。
5. Seed 就绪前尝试预览，应由原门禁拒绝。就绪后再启动，应通过相同 seed_ready 门禁。
6. 分别在 Schema RUNNING、Seed RUNNING、Seed POST 前落盘后退出和 Seed POST 受理但响应丢失处重启 Backend，再打开同一应用。确认 attach 接管原任务，ID/幂等键不变；查到原操作时仅 GET，明确查无记录时可同键 POST，业务效果不重复。
7. 保持窗口不切换，在两次查询间快速重启 Backend。确认卡片识别新实例，独立 attach 接管并自动继续。隐藏窗口后重启 Backend，再显示窗口，验证相同恢复路径。
8. 为原 GET 注入超时/503，应继续退避查询而不直接提交。为 Seed 本地 submitting/unknown 注入明确 by-key 404，应按原键原载荷提交并继续 GET 核验。为纯 refresh/refresh_patch 注入 404，不得 POST。为 Schema 注入 UNKNOWN 或丢失回执且无 ID，应禁止再建表。
9. 在请求途中关闭权限或更换绑定、管理员、DDL、Manifest，确认没有后继写入或过期成功回填。两窗口同时触发同一工作区，应共享原操作。
10. 检查浅色和深色主题下运行、完成、待处理及错误状态。历史会话保持静态，当前会话与其他工作区之间没有串台。
11. 删除准备期间应等待安全 tick 结束。远端已受理操作保留原证据，不能显示为远端撤销。

### 已知验收限制

当前 SaaS 与正式 Manifest 的操作资源 `action:` 引用，以及空资源描述合同仍有差异。本改造保留原 `validate_seed_transport()`，遇到该输入会返回 `needs_attention(seed_saas_contract_incompatible)`。必须独立完成跨服务合同对齐后，才可用这些正式资源验收完整成功旅程。

本次桌面控制服务两次返回 `Sky Computer Use service startup request failed`，真实 Electron 明暗主题与真实 SaaS 旅程尚未执行。现有 Electron 进程处于运行中，未使用 Vite 浏览器页面代替 Electron 验收。

## 完整修改文件清单

### PR1

```text
Backend/app/services/builtin_authorization_orchestrator.py
Backend/app/services/builtin_authorization_task_store.py
Backend/app/services/builtin_authorization_bootstrap.py
Backend/app/services/builtin_authorization_seed_runtime.py
Backend/app/services/builtin_authorization_state.py
Backend/app/services/managed_table_client.py
Backend/tests/test_builtin_authorization_orchestrator.py
Backend/tests/authorization_orchestrator_safety_cases.py
```

### PR2

```text
Backend/app/graph/nodes/builtin_authorization_bootstrap.py
Backend/app/main.py
Backend/app/protocols/application_lifecycle.py
Backend/app/protocols/application_deletion.py
Backend/app/protocols/builtin_authorization.py
Backend/app/protocols/workflow/projection.py
Backend/app/services/builtin_authorization_patch.py
Backend/app/services/project_launcher.py
Backend/tests/test_builtin_authorization_task_lifecycle.py
Backend/tests/test_builtin_authorization_bootstrap.py
Backend/tests/test_builtin_authorization_seed.py
```

### PR3

```text
Frontend/src/shared/authorizationBootstrap.ts
Frontend/src/renderer/src/service/builtinAuthorization.ts
Frontend/src/renderer/src/components/AiChatPanel/AiChatPanel.tsx
Frontend/src/renderer/src/components/AiChatPanel/authorizationBootstrapRecovery.tsx
Frontend/src/renderer/src/components/AiChatPanel/components/ProcessSteps/index.tsx
Frontend/src/renderer/src/components/AiChatPanel/components/ProcessSteps/AuthorizationBootstrapPanel.tsx
Frontend/src/renderer/src/components/AiChatPanel/components/ProcessSteps/AuthorizationBootstrapPanel.less
Frontend/src/renderer/src/components/AiChatPanel/components/ProcessSteps/AuthorizationBootstrapRefreshButton.tsx
Frontend/src/renderer/src/components/AiChatPanel/components/ProcessSteps/BuiltinAuthorizationTaskCard.tsx
Frontend/src/renderer/src/components/AiChatPanel/components/ProcessSteps/useBuiltinAuthorizationTask.ts
Frontend/src/renderer/src/components/AiChatPanel/components/WorkspaceDebugDock/BuiltinPatchCard.tsx
Frontend/scripts/run-authorization-bootstrap-tests.mjs
Frontend/tests/authorizationBootstrap.test.ts
Frontend/tests/authorizationBootstrapTask.test.ts
docs/CODEBASE_INDEX.md
docs/template/builtin_authorization_bootstrap_implementation.md
```
