# Managed Database 通信基线

源码基线 `60711561`。以下路径相对于 `/managed-database/datasources`，其中 `{key}` 为应用绑定的 datasourceKey。所有请求使用配置中的地址、Bearer 和超时。每次请求独立创建并关闭 httpx 客户端，禁用环境代理和重定向，支持 MockTransport。注册、表操作和 Runtime 使用内网 TLS 上下文及当前用户的 URL 编码身份头。Seed 和 SSE 使用默认 TLS 且不附用户身份。JSON 接口的 Accept 为 application/json，SSE 为 text/event-stream。无自动重试。

| 原入口 → 统一入口 | 方法与路径 | 请求与成功校验 | 异常与副作用 |
|---|---|---|---|
| register → register | POST 根路径 | 校验应用名（非空、无首尾空白、最多 255 字符），只编码 appName。200 后校验 RegistrationResponse | 连接失败/连接超时及 400/401 明确拒绝。其他传输、HTTP 或响应校验失败为结果未知。工作区锁内先保存 REGISTERING，成功保存绑定，失败标为 FAILED 或 UNKNOWN |
| Table.request → submit_table | POST /{key}/table-operations | CREATE 包含 mode/sql，REBUILD 另含 allowDataLoss=true。200 后校验 OperationReceipt | 先核对预览及用户确认，重建锁内复查运行及指纹。仅 CREATE 的 409 STRUCTURE_MISMATCH 引导重建 |
| Table.batch_create_tables → batch_create_tables | POST /{key}/table-operations/batch | mode=CREATE，sqls 保留顺序。200 后校验回执 | 提交前保存权限表操作状态，按原 ID 查询，结构复验后才就绪 |
| Table.request → get_table_operation / list_table_operations | GET /{key}/table-operations/{id} 或 /{key}/table-operations?limit=100 | 无请求体，200 后校验原操作、列表、表名及归属 | 纯读取。输入守卫存在时传输错误按临时故障处理，F02/F03 恢复 500/502/503/504 同类分支 |
| Table.request → check_tables | POST /{key}/tables/check | 只读 JSON sqls，200 后校验 TableReadiness | 与表操作详情/历史相同的临时故障规则。先执行任务输入检查，再校验 12 位小写十六进制 key |
| Table.request → list_tables / get_table_columns | GET /{key}/tables 或 /{key}/tables/{编码表名}/columns | 无请求体，200 后校验 ManagedTables/ManagedColumns、应用/表身份及字段唯一性 | 只读。先用当前表目录校验归属，再读取列，返回前复核绑定。元数据继续映射自有错误文案 |
| Runtime.request → check_runtime_tables / get_runtime_connection | POST /{key}/tables/check 或 GET /{key}/runtime-connection | 结构检查把 sqls 元组编码为数组，连接 GET 无体。200 后分别校验 TableReadiness / RuntimeConnection | 不经任务输入守卫。保留启动专用异常，检查前后复核快照，验证完整表范围和连接身份，密码仅供启动使用 |
| Seed.submit → submit_authorization_seed | POST /{key}/data-operations/batch | 明确 Content-Type: application/json，原 canonical_json 字节。旧 202 由 F01 修正为 200，校验 SeedReceipt | 验证原幂等键、落盘 submitting 后只 POST 一次。Timeout/RequestError 分开，409 保留资源忙/幂等冲突分类。回执仅记录原 ID |
| Seed.query → get_seed_operation / get_seed_by_key | GET /{key}/data-operations/{id} 或 /{key}/data-operations/by-key/{idempotencyKey} | 无请求体，校验 12 位 key、64 位小写十六进制幂等键及可选原 ID。200 后校验 SeedDetail | 仅合法 Bee 404 RESOURCE_NOT_FOUND 且 body 为 null 或空对象返回 None。按 ID 优先，原条件下再按幂等键查。核对原身份、指纹、表集合及 verified 后才就绪 |
| AgUi.query → query_table_data | POST /{key}/tables/{编码表名}/data/run | 标准 RunAgentInput 及随机 run/thread，转发 action=QUERY 和原 query。200 SSE，最终 DataPage | 校验 STARTED/FINISHED 身份与顺序、CUSTOM 结果与错误、快照及终值一致，限制 128 个事件和 12 MiB。上层校验真实表归属、分页、列及行；全程只读 |

表操作先执行既有输入守卫、校验 datasourceKey，传输故障在任务内的只读链路使用 ManagedTableQueryTransientError。其余网络故障保留 ManagedDatabaseRegistrationError 与原 uncertain 规则。Seed 失败以 SeedRemoteError.category 报告，Runtime 失败以 ManagedDatabaseStartupError 报告。响应中原有解析缺陷按方案 F01–F06 单独修复，Bee JSON 的成功包装校验独立于端点业务模型。SSE 继续使用 SDK 事件解码。

原核心套件执行了 181 项，3 个断言失败、17 个错误。注册旧 201、表提交旧 202 和表非 200 错误处理导致 19 项失败。另一个测试的外部数据库 resolver mock 指向已失效的入口。Seed 原 202 fixture 在基线中能通过，修复后改用 200 并明确测试拒绝 202。原回归的业务断言继续保留。

将注册响应 fixture 更新至 HTTP 200 后，既有的整数 `isDeleted: 0` 拒绝断言触达了 Pydantic 模型。当前依赖中 `Literal[False]` 会接受 `0`。原断言单列为预期失败，模型字段和校验仍按方案冻结。外置数据库测试的 mock 已迁至当前的目录选择与连接解析入口。Runtime 原测试裸 JSON 改为对照提交中的 Bee 包装，保留原表检查和启动断言。

只读核对了本地对照仓库提交 `39ce1a402804e34e37f93d4cf25c1c8dccf02939`。对应 Controller 使用 `@BeeResponseBody`，Seed POST 未设置 202，业务异常从 Bee 的 `returnCode` 返回。验证未使用对照服务进程。

## 最终差异与文件审计

| 修复 | 当前结果 |
|---|---|
| F01 | Seed POST 仅将 HTTP 200 作为成功状态。符合模型的回执仍需要查询原操作 |
| F02 | 表操作非 200 将 HTTP 整数状态、Bee 错误码及 CREATE 上下文传给原错误映射 |
| F03 | 非 JSON 走原合同错误；受任务输入守卫的只读表请求在 500/502/503/504 时走临时故障分类 |
| F04 | 普通 JSON 成功要求完整三字段 Bee 包装和 SUC0000，裸 JSON 或失败业务码均拒绝 |
| F05 | Runtime 从 returnCode 读取业务错误，继续使用原启动异常及提示 |
| F06 | 仅成功码允许 errorMsg 为空字符串或 null；Seed 仍要求对象 body |

| 文件 | 用途 |
|---|---|
| `Backend/app/services/managed_database_client.py` | 唯一 HTTP/SSE Client。共享连接构建与 Header，保留注册并增加具名方法 |
| `Backend/app/services/managed_database_bee.py` | 共享普通 JSON 的 Bee 成功包装校验 |
| `Backend/app/services/managed_table_client.py` | 移除 ManagedTableClient，保留表错误、提示和响应处理 |
| `Backend/app/services/builtin_authorization_seed_client.py` | 移除 ManagedAuthorizationSeedClient，保留 Seed 模型和业务错误解析 |
| `Backend/app/services/managed_database_agui.py` | 移除 ManagedDatabaseAgUiClient，保留标准事件消费和终态检查 |
| `Backend/app/services/managed_database_runtime.py` | 移除 ManagedRuntimeClient，保留启动检查，使用独立的 check_runtime_tables 保留启动错误语义 |
| `Backend/app/services/entity_tables.py` | 迁移单表执行、重建与刷新请求 |
| `Backend/app/services/builtin_authorization_bootstrap.py` | 迁移权限表批量提交、原操作与结构检查请求 |
| `Backend/app/services/builtin_authorization_seed_runtime.py` | 迁移按原 ID/幂等键查询及单次 Seed 提交 |
| `Backend/app/services/managed_database_metadata.py` | 迁移表目录和列请求，复用原错误转换 |
| `Backend/app/services/managed_database_table_data.py` | 迁移表数据 SSE 和历史请求 |
| `Backend/tests/test_managed_database_unified_client.py` | 新增逐端点请求快照、安全配置、F01–F06 和协议失败回归 |
| `Backend/tests/test_managed_database_registration.py` | 更新注册状态 fixture，保留落盘、并发及响应校验断言 |
| `Backend/tests/test_builtin_authorization_bootstrap.py` | 更新权限批次状态 fixture，迁移 transport 注入 |
| `Backend/tests/test_builtin_authorization_seed.py` | 更新 Seed 状态 fixture 和具名查询，保留幂等与终态核验 |
| `Backend/tests/authorization_seed_recovery_cases.py` | 更新 Seed 恢复测试的状态 fixture 和注入入口 |
| `Backend/tests/test_builtin_authorization_orchestrator.py` | 更新批次状态 fixture 和统一 transport 注入 |
| `Backend/tests/authorization_orchestrator_safety_cases.py` | 更新受理回执 fixture，保持失联恢复和终态断言 |
| `Backend/tests/test_managed_database_runtime.py` | 更新 Bee fixture 和当前外置数据库 mock 入口 |
| `Backend/tests/test_managed_database_metadata.py` | 迁移目录与字段测试的客户端替身 |
| `Backend/tests/test_managed_database_table_data.py` | 迁移 SSE 与历史测试的客户端替身 |
| `Backend/tests/test_managed_table_errors.py` | 使用具名方法验证原 issue、错误及 AG-UI 投影 |
| `Backend/tests/test_entity_tables.py` | 更新建表回执 fixture 与测试替身，保留 DDL、确认和刷新断言 |
| `Backend/tests/test_entity_ddl.py` | 保留普通 DDL 预览不构造远端客户端的断言 |
| `Backend/tests/test_authorization_bootstrap_projection.py` | 保留状态投影不触发 HTTP/SSE 的断言 |
| `Backend/tests/test_database_delivery_authorization.py` | 更新建表回执 fixture，继续核对交付范围、SQL 与零额外请求 |
| `docs/CODEBASE_INDEX.md` | 更新客户端职责和本地回归入口 |
| `docs/MANAGED_DATABASE_REGISTRATION.md` | 对齐注册普通 JSON 合同 |
| `docs/ENTITY_TABLE_EXECUTION.md` | 对齐建表成功状态与受理语义 |

原注册服务、编排器和领域模型未改动。Python AST 对照确认 11 个相关模型定义与基线等价，差异仅包含 SeedReceipt 的说明文字。持久化、AG-UI 对外合同、前端及 Java 代码未改动。

## 本地复现

从 `Backend` 执行。

```sh
.venv/bin/python -m unittest tests.test_managed_database_unified_client -v

.venv/bin/python -m unittest \
  tests.test_managed_database_registration \
  tests.test_builtin_authorization_bootstrap \
  tests.test_builtin_authorization_seed \
  tests.test_builtin_authorization_orchestrator \
  tests.test_builtin_authorization_task_lifecycle \
  tests.test_managed_database_runtime \
  tests.test_managed_database_metadata \
  tests.test_managed_database_table_data \
  tests.test_managed_database_operations \
  tests.test_entity_first_datasources -v

.venv/bin/python -m unittest \
  tests.test_managed_table_errors \
  tests.test_entity_tables \
  tests.test_entity_ddl \
  tests.test_authorization_bootstrap_projection \
  tests.test_database_delivery_authorization -v

.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

最后一次合并执行前三组共 233 项，232 通过、1 项预期失败、0 跳过。Seed 恢复案例由 Seed 与编排套件聚合执行。静态检查仅发现统一类中的一个 HTTP Client 构建点及一个 SSE 建连点。修改及新增的 Python 文件通过 `py_compile`，补丁通过 `git diff --check`。本机 XCodeAgent 的 `/health` 返回 `ok`。

全量发现执行 4200 项，150 个断言失败、133 个错误、2 个跳过、1 个预期失败。使用同一份虚构配置对照原提交，原提交执行 4189 项，155 个断言失败、161 个错误、4 个跳过；有 281 条失败或错误与当前分支同名同类。原提交中跳过的两项真实 tsc/Vite 测试，补入同一份前端依赖路径后也复现失败。应用创建层的未知注册重复调用断言在原提交和当前代码上均复现，属于独立于本次通信迁移的既有问题。全量套件未作为通过项记录。
