# Studio 接入 SaaS 数据源资源注册

本文件记录内置数据源资源注册合同。实体建表与单表结构检查见 [实体建表接入](ENTITY_TABLE_EXECUTION.md)，全应用启动检查与连接注入见 [整体方案](BUILTIN_DATASOURCE_OVERALL_REVIEW.md)。SaaS 接口与部署说明见同级项目的 [项目说明](../../managed-database-service/README.md) 和 [服务设计](../../managed-database-service/docs/managed-database-design.md)。

## 资源标识

`datasourceKey` 标识应用绑定的一份逻辑数据源资源，`datasourceId` 标识其物理挂载。Studio 应用 ID 保持原定义；当前每应用绑定一份资源，同应用分支复用。资源接口根路径为 `/managed-database/datasources`，全新管理库使用同级项目的 [初始化建表脚本](../../managed-database-service/src/main/resources/db/manual/managed-database-schema.sql)。

## 触发位置

新建应用选择“模拟数据库”，点击“创建并规划页面”后，先创建本地项目、保存 application.json 和首页索引、创建应用生命周期，再通过 `/application-lifecycle/run` 的 `register_managed_database` 动作注册。成功后携带保存的 datasourceKey、datasourceId 和最新 configRevision 进入规划。外部数据库或未配置数据库的应用不调用注册。

Studio 产品动作继续使用标准 AG-UI 生命周期、消息、结果、状态快照和结束事件。对已有 SaaS 普通 HTTP 的调用封装在后端适配器，Renderer 不直连 SaaS，不接触固定服务密钥。

## SaaS 的实际合同

后端发送 `POST /managed-database/datasources`，从权威 application.json 读取应用名称，仅提交 `{"appName":"应用名称"}`，请求头携带固定 Bearer 服务密钥。当前 Studio 不生成或提交 appKey。SaaS 接口允许调用方提供可选 appKey，作为独立应用关联信息；缺省时 app_key 保持 NULL，不用名称或 datasourceKey 补填。

appName 为 1～255 个字符的非空文本，appKey 提供时为 1～64 个字符的非空文本，均不接受首尾空白或错误 JSON 类型。SaaS 资源注册表保存 app_key、app_name；表目录与操作记录继续通过 datasource_key 关联资源，不重复保存应用信息。注册响应保持原资源绑定字段，SaaS 同步返回 HTTP 201：

```json
{
  "datasourceKey": "12ab34cd56ef",
  "datasourceId": 2,
  "status": "REGISTERED",
  "isDeleted": false
}
```

datasourceKey 由 SaaS 生成，固定为 12 位小写十六进制编码，可以数字开头。Studio 不预生成 datasourceKey，也不在请求中指定挂载。每次 POST 都创建新记录，没有上游幂等键，禁止为失败或超时自动重发创建。

## 唯一持久化位置

绑定保存在 `.devagentstudio/application.json` 顶层 managedDatabase，首页索引和 lifecycle 不另存一份注册事实：

```json
{
  "managedDatabase": {
    "status": "REGISTERED",
    "datasourceKey": "12ab34cd56ef",
    "datasourceId": 2
  }
}
```

注册事实改变时增加 configRevision。前端通用工作区保存保留磁盘中的 managedDatabase 和不降低的配置版本，避免旧规划快照、分支操作或设置保存擦除成功绑定或在途标记。应用改名、重启、移动目录和同一应用的新分支复用该绑定，不因这些动作再次注册。

实体真实表名使用完整的 `datasourceKey_业务表名`，确定和消费规则见下节。注册只提供稳定身份；实体侧明确确认后提交 SaaS CREATE，并刷新当前结构。启动时另行检查全部实体并获取调试连接，不能把注册成功当成数据库表和调试连接已经可用。

## 实体真实表名

模拟数据库应用生成 TechnicalPlan 时，平台读取权威 application.json 中已成功注册的 datasourceKey，将稳定实体 ID 规范化为 snake_case 业务表名，再固定 `table_name`。初次规划、Markdown 编辑后的确认，以及工作台 TechnicalPlan 正式修订都使用同一确定性规则，不由模型决定前缀。

```json
{
  "entities": [
    {"id": "Order", "name": "订单", "table_name": "12ab34cd56ef_order"},
    {"id": "OrderItem", "name": "订单明细", "table_name": "12ab34cd56ef_order_item"}
  ]
}
```

示例省略业务字段。table_name 是真实物理表名，随技术规划正式保存并在 Markdown 中展示。实体详情、目标表结构、结构差异需求、字段范围校验和业务生成的实体摘要都直接读取它，不分别重算前缀。实体详情刷新、字段修改和展示名称变化保留同一表名；实体 ID 仍是业务身份，Java 类和 API 路径不增加应用前缀。

规范化和实体合并保留已分配的 table_name。内置实体的选表、字段绑定、建表建议或操作目标不能被重新指定为裸表名或另一个实体的表。重复绑定不叠加前缀；不同 datasourceKey 即使实体 ID 相同，也产生不同真实表名。

datasourceKey 可以数字开头，SQL 必须引用完整标识符，例如 `` `12ab34cd56ef_order` ``。完整表名限制为小写字母、数字和下划线，总长不超过 64；超长直接报错，不截断。不同实体规范化后发生同名冲突时拒绝，不随机改名。已绑定名称与当前 datasourceKey 或实体稳定 ID 不一致时拒绝，不静默迁移表。

注册缺失、REGISTERING、FAILED 或 UNKNOWN 的模拟库应用不能生成真实表名，不回退到本地应用 ID，不在规划节点里再次调用 SaaS 创建。用户自备数据库沿用现有真实表选择与绑定规则，不强制添加 datasourceKey 前缀；外部 API、静态数据也不创建数据库表。

真实表名在技术规划确认之前确定，仍经过既有明确确认边界。本次不自动修改已经确认的旧产物、不迁移或重命名真实数据库表；需要重新生成或正式修订并确认技术规划。SaaS 单实体 CREATE 与当前结构检查见实体建表接入文档；业务 SQL 全面校验在后续阶段完成。

相关代码：`services/entity_table_binding.py` 负责应用前缀绑定与详情目标校验；`entity_definitions.py` 负责统一读取及规范化保留；`entity_detail_plan.py`、`entity_design.py` 与 `database_requirement_schema.py` 消费固定名称。`graph/nodes/planning.py` 和 `application_revision.py` 在确认前读取权威配置，`workspace/plan_documents.py` 展示正式名称。聚焦验证入口为 `Backend/tests/test_entity_table_binding.py`。

## 防止重复创建

| 本地状态 | 含义与行为 |
| --- | --- |
| 未登记 | 校验服务地址和密钥后才开始注册 |
| REGISTERING | 外部调用前原子落盘；同一工作区进程内串行注册；中断后禁止再次 POST |
| REGISTERED | 严格校验 SaaS 结果后保存 datasourceKey 与 datasourceId；重复动作直接返回已有绑定 |
| FAILED | 能明确判定未提交或被拒绝，例如建立连接失败、HTTP 400/401；不自动重试，允许后续明确动作重新申请 |
| UNKNOWN | 请求发出后超时、异常响应、服务端失败等可能已经创建；禁止再次 POST，先人工核对 |

如果 SaaS 返回成功，但本地结果保存失败，之前的 REGISTERING 标记继续阻止重复创建。错误提示携带已取得的 datasourceKey 和 datasourceId 供人工恢复，不把它们当成已落盘成功。

结果不确定时不要删除 managedDatabase 或改成 FAILED 来重试。先在 SaaS 管理侧核对注册结果，找到对应身份后由明确的人工操作恢复完整 REGISTERED 绑定；无法证明原请求未创建时不再提交。当前 SaaS 没有按上游应用查回注册的接口，本次不伪造自动恢复能力。

注册失败时本地项目与首页索引保留，不自动删除工作区或云端记录。首次创建提交会停止在进入规划之前并展示错误。后续重试沿用同一工作区的独立 AG-UI 注册动作，不重新点击创建同一个目录，也不从只读打开或读取生命周期动作后台自动申请。

## 配置

后端配置如下，密钥与 SaaS 的 MANAGED_DATABASE_SERVICE_KEY 保持一致：

```dotenv
DEVAGENTSTUDIO_MANAGED_DATABASE_BASE_URL=http://127.0.0.1:18081
MANAGED_DATABASE_SERVICE_KEY=
DEVAGENTSTUDIO_MANAGED_DATABASE_TIMEOUT_SECONDS=15
```

地址默认使用本次确认的本机端口。远端使用 HTTPS，本机回环允许 HTTP。服务密钥留在后端环境，不写工作区、AG-UI 结果、生成源码或日志。外部错误使用固定脱敏文案，不透传原始响应或异常正文。

## 代码位置与验证范围

`domain/managed_database.py` 定义注册状态合同；`services/managed_database_client.py` 适配 HTTP；`services/managed_database_registration.py` 负责防重和权威配置保存；`protocols/managed_database_registration.py` 适配现有生命周期 AG-UI 动作。前端在创建提交流程等待注册结果，并通过通用工作区保存保留注册事实。

自动验证使用 HTTP 传输替身，覆盖应用名称提交、非法名称拒绝、复用、并发、连接失败、响应丢失、异常成功体、成功后保存失败和 AG-UI 成功/失败生命周期。不创建真实 SaaS 记录，不执行数据库 SQL。实库注册需要两端已配置相同密钥，并由新建模拟数据库应用的实际流程触发。
