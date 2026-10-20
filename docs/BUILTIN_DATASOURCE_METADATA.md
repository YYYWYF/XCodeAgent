# 内置数据库接入数据源与 API 映射

本次修改 Studio 平台，SaaS 使用已验证的应用表清单及字段查询接口。前端继续通过现有数据源 AG-UI 动作读取，不直连 SaaS、不接触服务密钥，不新增 REST 产品接口。

## 表详情与调试数据

内置表详情新增“表结构 / 表数据 / 操作记录”三个 Tab，默认表结构，非活动 Tab 按需挂载。切表通过 sourceId+完整表名重建详情；切换 Tab、关闭详情或翻页取消前端 AG-UI 请求，并丢弃旧回执。外部数据源保留原字段详情。

新增 `/data-sources/database-rows` 与 `/data-sources/database-operations`，继续使用原独立数据源 AG-UI 生命周期。表数据动作只接受 workspaceRoot、sourceId、table、pageNumber、pageSize；后端从权威配置解析数据源 Key，先通过 SaaS 归属目录，再向 SaaS `/tables/{tableName}/data/run` 提交空条件 QUERY。服务密钥始终留在后端。

`managed_database_agui.py` 使用 httpx-sse 处理标准 SSE、ag_ui.core 官方事件模型校验事件，核对开始/结束身份及 CUSTOM、STATE_SNAPSHOT、RUN_FINISHED 的结果一致性。完整结束且响应中的应用、表、页码、列名和行数都通过校验后才向页面返回，不自动重试、不自写 SSE 行解析器。httpx-sse 已显式声明在 requirements.txt。

数据分页使用 hasMore 和每页 10/20/50/100 条选项，不伪造总数；刷新重新读取当前页，实体建表通知后回第一页。显示精确文本、SQL NULL、空字符串和 HEX 格式；长内容可打开只读详情，截断结果有提示。错误、空结果和加载状态独立显示，使用现有紫色明暗主题与弹窗令牌。本期不提供筛选、SQL 控制台或数据编辑。

操作记录复用 SaaS 原 GET table-operations?limit=100，只展示本应用最近一百条资源操作中精确匹配当前表的记录，包含整应用操作的目标清单。页面明确说明范围，不当作全量历史或业务增删改审计。该只读历史不依赖当前表结构就绪，因此有在途或未知占用时仍能核对已保存的原操作。显示操作、状态、时间、文本操作 ID 和安全错误摘要，不返回 SQL、管理账号或操作重放入口。

操作响应模型与实体刷新共用 `domain/managed_database_operations.py`，四种模式统一为 CREATE、REBUILD、DROP、RELEASE。整份资源回收可使用空 tableName 和明确的 tableNames；两处均按完整表名或清单成员精确筛选，不按前缀推断归属。前端共用同一响应类型和“创建 / 重建 / 删除 / 资源回收”名称，历史读取不会增加任何可执行动作。

测试入口：Backend/tests/test_managed_database_table_data.py；Frontend/tests/databaseTableTabs.test.tsx 纳入 test:preview-runtime，覆盖真实 AG-UI 客户端及默认 Tab。真实 SaaS 查询及 Electron 视觉联调需要单独执行。

## 读取链路

`selected_tables` 对外部数据库继续投影手动添加的表；内置模式则实时读取 SaaS 应用归属目录，自动把本应用已建成且真实存在的表变成候选。不把 SaaS 表目录或实际字段复制到本地数据源文件，也不按前缀扫描共享 Schema。

本地目录的全局锁只保护读取快照，不覆盖 SaaS 网络请求，避免内置资源响应缓慢时阻塞其他应用的外置库操作。内置结果返回前复核目录是否变化。开发门禁只对内置映射实时复核资源与字段；外置冻结映射不新增实时目录或 SaaS 依赖，已有内置身份在切换来源后仍须重新确认。

`database_tables`、`database_columns` 根据目录中的数据库模式分流。内置模式从 canonical application.json 校验 useBuiltin 与 REGISTERED，读取 managedDatabase.datasourceKey，调用 `/managed-database/datasources/{datasourceKey}/tables` 和对应 columns GET。响应必须匹配应用和目标表，重复表名、重复字段、无效结构均明确拒绝。注册缺失、在途占用及读取失败不回退本地 MySQL、不返回伪造空列表。

数据源统一展示为“平台内置数据库”。数据源抽屉隐藏连接设置、添加和移除表入口，提供刷新；字段详情展示真实类型、必填、主键、默认值及说明。所有展示沿用已有工作台组件及明暗主题。

## 当前绑定合同

SaaS 不公开真实 Schema，内置来源的 schema 明确为 `""`，用 sourceId 和完整物理表名绑定，由 SaaS 固定挂载解析目标位置。不用数据源 Key 或“内置库”冒充真实库名。外部数据库仍在保存时核对实际 Schema，空 Schema 不能绕过真实元数据检查。

数据库返回字段、查询条件、写入列、SourceBinding、草稿选择和前端选择恢复共同支持此合同。确认时重新读取真实列和表归属。正式 sourceSnapshots 保留 managedDatasourceKey 和本次引用字段的真实元数据，完整表名保持原样，不剥离应用前缀。

## 刷新与失效

实体表操作取得 SUCCEEDED 或 FAILED 的确定结果后，按操作 ID 去重通知当前工作区的数据源读取者；失败重建也可能已经删表，因此同样回读。RUNNING/UNKNOWN 不当成完成。数据源抽屉及表详情丢弃迟到或卸载后的请求，失败时显示错误，不把旧表当最新表。

字段映射保留用户当前草稿，只刷新候选和真实字段；内置来源的已确认只读映射也检查实时字段，外部来源继续使用既有只读快照规则。字段删除、类型变化和失效写入/查询目标显示重新配置提示。保存确认仍走原有显式确认边界，不自动修改或确认映射。

开发门禁及 Build 读取正式映射时，重新检查内置来源快照引用的表和字段。绑定变化、引用字段删除或类型、必填、主键、自增、默认值变化时要求重新配置确认；仅新增未引用字段不会无条件使所有 API 失效。元数据读取是当次事实，不保证之后数据库不会变化。

生成应用的连接下发与全应用启动检查见 [整体方案](BUILTIN_DATASOURCE_OVERALL_REVIEW.md) 的“启动检查与连接注入”。

## 代码与验证

后端 `services/managed_database_metadata.py` 负责安全 HTTP 适配、响应校验及快照复核；`data_sources.py`、`api_design.py` 和 `binding_workspace.py` 接入现有目录和确认流程；数据源协议将同步元数据读取放入工作线程，避免阻塞事件循环。前端沿用 DataSourcesDrawer、DatabaseTables、FieldMapping 和现有 dataSourceEvents。

聚焦后端验证为 `python -m unittest tests.test_managed_database_metadata tests.test_data_sources tests.test_binding_workspace tests.test_api_design`，使用 HTTP 替身，不创建真实表。前端聚焦测试为 `pnpm test:api-design`，构建为 `pnpm build`。按项目约束各执行最多一次，失败停止；不额外启动服务。
