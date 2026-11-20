# 实体建表接入

模拟数据库应用与普通实体共用“开发产物 → 实体”的标题、说明、字段设计与建表 DDL，在“字段设计”和“建表 DDL”之间增加“模拟数据表”页签。表名、平台内置数据库、刷新设计、创建表、重建表和刷新结果集中放在该页签；完整带前缀表名可通过原生剪贴板复制。DDL 页签展示 AI 根据已确认实体生成的 SQL，支持提交修改意见、确认保存、原生复制及失败重试。模拟数据表使用同一份正式 DDL 创建、重建和检查结构。外部数据库保留原有实体信息与绑定流程。

模拟数据表页签首次打开先 prepare，再 refresh。当前规划没有正式 DDL 时，AG-UI 返回 `DDL_CONFIRMATION_REQUIRED`，界面显示“去确认 DDL”。在当前实体确认保存后返回，界面自动刷新设计和结构。未建表、运行中或结果未知时不展示数据表字段；检查确认表存在后，复用数据源 database_columns AG-UI 动作读取当前应用的实际列，字段名称、标识、数据库类型、必填分列展示。实际字段读取失败时显示错误并允许刷新。

字段从已确认的 `plans/technical-plan.json` 读取。平台重新核对 application.json 中的 REGISTERED 绑定与 `entities[].table_name`，不在详情入口给旧产物补前缀。字段需要修改时，走原有技术规划修订与确认，再刷新设计。

模拟数据表提交的唯一 SQL 来源是已确认记录。后端重新校验完整物理表名、当前技术规划摘要、字段和约束，按原文发送给 SaaS。只有明确标注为唯一标识的正式字段才用作单列主键。正式规划中显式的 `id` 按业务字段处理，保持已确认的类型和约束。

仅内置数据库应用的实体 `name`、`description` 及字段 `label`、`description` 必须是非空中文文案，可包含 ID、API 等技术缩写，不能使用纯英文说明。技术规划提示词、生成及确认校验、正式修订提交校验均从 application.json 判断模式，外置数据库不增加此约束。表 COMMENT 取实体 description，字段 COMMENT 优先取字段 label；实体 ID、物理表名和字段名保持原有技术命名规则。不会自动翻译或改写已确认产物与已建表注释；需要通过技术规划修订、重新确认后再明确更新数据库。

点击“创建表”后展示确认提示，用户点击“确认创建”才提交。后端用正式 DDL 记录和当前注册绑定重新计算 previewHash，必须与预览一致且 confirmed=true 才调用 SaaS。技术规划和 DDL 各自经过明确确认。

Studio 使用独立 `/entity-tables/run` AG-UI 流，forwardedProps.entityTables 包含 action、workspaceRoot、entityId；模拟数据表创建和重建额外传 previewHash 和 confirmed，重建还必须明确传 allowDataLoss=true。各动作通过现有标准流发送消息、结果、状态快照、成功或失败和结束事件。密钥仅由后端使用。

- preview_ddl：优先读取当前规划版本的正式 DDL，其次使用后端进程内的待确认草稿。两者均缺失时调用模型生成新稿。`ddlPreview.confirmationStatus` 表示 `draft` 或 `confirmed`，`draftId` 标识当前内容。提示词要求每列、主键和普通索引各占一行，展示、复制和保存直接使用模型返回的 SQL。
- revise_ddl：携带当前 `draftId` 和 `feedback`，以审核中的 SQL 为基线重新生成。只有生成通过校验且已确认技术规划版本一致时才替换内存草稿，并签发新 `draftId`。失败保留当前草稿。草稿保存在后端进程内。
- confirm_ddl：携带当前 `draftId` 和 `confirmed=true`，核对计划摘要后原子保存 `.devagentstudio/plans/entity-ddl/<实体 ID 摘要>.json`。重复确认同一稿可读取上次保存结果。技术规划更新后须审核并确认新稿。确认只保存工作区产物。
- prepare：只读取当前正式 DDL；界面刷新设计后另行调用 refresh，更新实际表状态。
- execute：一次 `POST /managed-database/datasources/{datasourceKey}/table-operations`，只提交 `{mode: "CREATE", sql}`，HTTP 200 的 operationId/RUNNING 只表示受理。
- rebuild：通过弹窗展示表名、删除全部数据的影响及确认勾选；每次打开确认均重置勾选。后端复核 SQL 指纹与数据丢弃确认，与标准预览启动共用启动锁，检查受控预览 PID 已停止后再次核对当前设计，再提交一次 `{mode: "REBUILD", sql, allowDataLoss: true}`。SaaS 负责表归属、外键引用、逐步删除/创建及应用占用检查。本地不会自动停服务、生成 DROP SQL 或把 CREATE 失败自动转换成 REBUILD。
- refresh：读取 SaaS 最近最多 100 条历史，展示当前表最新原操作；以当前 SQL 调用 tables/check，只检查这个实体。历史成功和本次结构一致分别展示。返回数据源必须与已注册挂载一致，检查期间上游设计变化时丢弃结果。

AI 预览按已确认的字段类型选择 MySQL 类型。文本和枚举使用 `VARCHAR(n)`，长文本使用 `VARCHAR(512)`，整数默认使用 `INT`，设计明确要求 64 位整数或超出有符号 `INT` 的取值范围时使用 `BIGINT`。小数使用 `DECIMAL(p,s)`，日期和时间点均使用 `TIMESTAMP`，布尔使用 `TINYINT(1)`。用于主键或普通索引的 `VARCHAR` 上限为 255 个字符。提示词也约定列声明顺序、审计时间表达式、单列索引写法和模型 JSON 输出格式。整数范围、查询用途和审计时间语义由模型根据已确认设计判断。

提交执行并收到 RUNNING 受理回执后，前端等待 10 秒自动刷新一次结果；无论该次查询成功、失败或仍在执行，都不再自动查询。提前手动刷新、刷新设计、切换实体或离开面板会取消未触发的定时器；同一实体内切换字段设计、模拟数据表和 DDL 页签会保留组件及原定时器。响应丢失或执行失败提示后仍由用户手动刷新，不自动重发建表请求；RUNNING/UNKNOWN 不再提交。刷新会读取应用占用，因此其他实体的在途操作也会阻止新提交。SaaS 应用占用与 CREATE 自身校验负责服务端并发保护，不依赖前端按钮状态。历史查询有条数上限，未找到原记录不证明请求未受理；结构检查才反映本次目标和应用占用。重新进入实体界面先只读检查，不缓存永久绿色同步标记。

重建受理后也复用 10 秒单次刷新。“最近操作”标签仅表示原操作类型与结果，状态包含成功、失败、执行中、结果待核对；当前实体是否符合本次设计以相邻的结构检查标签为准。其他实体造成的 SaaS 应用占用不冒充当前实体的最近操作。

实体刷新与数据源操作记录共用完整的只读历史合同，接受 CREATE、REBUILD、DROP、RELEASE；先校验安全记录，再按 tableName 或 tableNames 的精确成员筛选当前表。资源回收允许 tableName 为空，其他表的删除或回收记录不阻断本实体刷新。已知操作类型时，标签展示“最近操作：创建 / 重建 / 删除 / 资源回收 · 状态”；只有受理回执时仍只展示状态。该响应合同不扩大实体动作入口，不能提交删除或回收，也不新增按操作 ID 查询接口。

SaaS 拒绝请求时，根据 HTTP 状态与当前错误码共同生成安全提示，经 AG-UI 失败载荷的 issue 返回 code、title、message、action，不透传原始 message。CREATE 收到 HTTP 409 / STRUCTURE_MISMATCH 时显示“表结构已变更”，说明普通建表不会更新已有结构，并引导用户明确确认重建及清空数据；只有这一明确未受理分支解除提交锁。RESOURCE_BUSY 提示等待并刷新，TABLE_BINDING_CONFLICT 提示核对表归属；未知 409、异常响应及网络错误仍先核对结果，不能解锁重建。新提交会清除上次结构检查展示，避免旧绿色状态误导。

重建仅在提交时检查平台管理的标准预览进程，不探测用户从终端或其他机器启动的应用。全应用启动检查会拒绝存在未完成操作的资源，本地启动锁与 SaaS 操作占用不构成跨机器运行租约。缺少表归属、跨应用或外键引用等拒绝由 SaaS 返回，响应丢失后不自动重发 REBUILD。

启动流程读取全部实体的正式 DDL 并提交全应用结构检查。任一实体缺少当前版本的正式 DDL 时，提示前往该实体确认。Java 启动前复核结构和正式工件，并注入运行时连接。前端沿用现有 Ant Design 和工作台主题。见 [整体方案](BUILTIN_DATASOURCE_OVERALL_REVIEW.md)。

建表回执、操作历史和详情、结构检查及表元数据使用 SaaS 的统一 JSON 响应。后端先核对接口预期的 HTTP 状态及 `returnCode: "SUC0000"`、空字符串或 null 的 `errorMsg`，再将 `body` 交给各业务模型校验。历史数组为空时正常返回空历史。非成功响应按 HTTP 状态和 `returnCode` 映射提示。

## 版本交付 SQL

用户明确通过项目验收后，既有 `finalize_project` 节点为内置数据库应用生成：

```text
backend/db/v1_0/create.sql
backend/db/v1_0/alter.sql
```

目录取权威 application.json 的 branchName，带斜杠的合法分支保留目录层级。create.sql 直接汇集全部实体的正式建表原文，缺少正式 DDL 时停止交付。首个已交付版本的 alter.sql 为零字节空文件。后续版本与上一交付版本比较，输出增删表、字段、字段顺序、默认值、主键、索引和注释变更，无差异时为空。

增量不读取 SaaS 调试库，不以目录名称排序推断上一版。内部 `.devagentstudio/database-deliveries.json` 保存已交付版本链、业务表正式 SQL 和文件摘要，随工程提交，发起新迭代时保留。该 JSON 是平台内部基线，不作为用户编辑产物。同一版本重新验收时继续相对原上一版生成累计增量；后续版本已依赖的旧版脚本不允许重写。

基线缺失、损坏，脚本被手工修改，资源身份改变，或输出路径越界时均明确停止，不覆盖用户文件、不从真实数据库猜测结构。写入 SQL 和快照前完成全部校验，正常写入异常回滚本次已替换内容；失败通过现有工作流消息返回，不能标记项目交付完成。外部数据源和非数据库应用保持原有交付行为。

仅生成文件，不执行 CREATE、ALTER 或 DROP。字段和表改名按删除加新增表达，不推断数据搬迁；删列、删表及类型变更需要在执行前评估数据影响，新增非空字段需评估存量数据回填。单表的键和列变更汇总为一条 ALTER；加入新主键前需核对存量数据的非空性与唯一性。

代码：`services/entity_tables.py`、`services/entity_ddl.py`、`services/entity_ddl_validation.py`、`services/database_delivery.py`、`services/database_delivery_sql.py`、`domain/database_delivery.py`、`services/managed_table_client.py`、`domain/entity_tables.py`、`protocols/entity_tables.py`、`routes/entity_tables.py`；前端 `service/entityTables.ts` 和 `AiChatPanel/components/EntityTablePanel/`。聚焦验证：`python -m unittest tests.test_database_delivery tests.test_entity_tables`，使用临时工程和 HTTP 替身，不创建真实表。
