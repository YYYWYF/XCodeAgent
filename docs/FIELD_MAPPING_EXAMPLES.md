# 单数据源字段映射示例与生成职责

本文是配置示例，不是实际项目的默认映射。实际任务只使用当前 Endpoint 已确认的映射和来源快照；示例中的字段、规则、时区与错误语义不自动注入生成提示词。界面沿用“选择数据来源 / 更换数据源”，不增加数据表和外部 API 切换 Tab。

## 示例一：数据表查询

应用接口 `GET /products`，只绑定 `catalog.products` 表。接口请求参数为 `keyword?: string`、`startDate?: string`、`scene?: string`；响应为商品数组。表包含 `id、name、code、created_at`。`scene` 只用于业务校验：允许空值或 `picker`，其他值按已确认接口错误契约拒绝，不写入数据库。

查询条件使用 AND：

| 目标字段 / 条件 | 值来源 | 依赖 | 已确认规则 | 缺值处理 |
| --- | --- | --- | --- | --- |
| `name` 包含 | 接口参数 + 业务处理 | `query.keyword` | 去掉首尾空白后参与包含查询；空白输入视为缺值；LIKE 通配符按字面量转义 | 跳过该条件 |
| `created_at >=` | 接口参数 + 业务处理 | `query.startDate` | 按 `yyyy-MM-dd` 严格解析，转换为 Asia/Shanghai 当天零点对应的 UTC 时刻；非法日期走已确认参数错误契约 | 跳过该条件 |

返回字段：

| 应用返回字段 | 配置类型 | 来源 / 依赖 | 已确认规则 |
| --- | --- | --- | --- |
| `items[].id` | 数据源字段，直接映射 | `products.id` | 保持 integer 类型 |
| `items[].displayName` | 数据源字段，业务处理 | 同一行的 `name、code` | 编码非空时返回“名称（编码）”，否则仅返回名称；名称缺失时报错 |
| `items[].generatedAt` | 业务生成 | 内置 `current_time` | 使用服务端时间，输出 UTC ISO-8601 字符串；时间不可用时报错 |

输入 `keyword=" 手机 "、startDate="2026-09-01"、scene="picker"` 时，先得到 `keyword="手机"` 与 `2026-08-31T16:00:00Z`，再参数化查询。若一行的 `name="手机"、code="P001"`，则 `displayName="手机（P001）"`。没有匹配行时返回空数组，不视为规则缺值。

此例 `databaseQuery.items[].right` 存储查询值规则；`scene` 使用 `business_description`；`displayName` 使用 `source_mapping + multi_field_description`；`generatedAt` 使用 `value_mapping`。多个字段都来自同一张表，不需要 JOIN。

### 同一数据表的写入变体

另一 Endpoint `PATCH /products/{id}` 仍只绑定上述表，可配置 `id = path.id`，并将 `request_body.name` 去掉首尾空白后写入 `products.name`；缺失、空白或非法输入按已确认错误契约拒绝。目标列及其取值存储在 `databaseWrites[].right`，不能把纯业务参数 `scene` 自动写入表。update/delete 即使允许某些条件缺值时省略，也必须在所有有效条件被省略时拒绝执行。

## 示例二：外部 API

应用接口 `GET /products/search`，只绑定外部 Operation `POST /v1/products/search`。应用请求为 `keyword?: string、page: integer、pageSize: integer、scene?: string`。外部请求接受 `query.q?、request_body.offset、request_body.limit、header.X-Caller`，返回 `data.items[]`，元素包含 `id、name、code`。

| 外部目标 | 值来源 | 依赖 | 已确认规则 | 缺值处理 |
| --- | --- | --- | --- | --- |
| `query.q` | 接口参数 + 业务处理 | `query.keyword` | 去掉首尾空白；空白视为缺值 | 省略 q，不能发送字符串 null |
| `request_body.offset` | 接口参数 + 业务处理 | `query.page、query.pageSize` | 校验 page >= 1，1 <= pageSize <= 100；计算 `(page - 1) * pageSize` 并检查整数溢出 | 报错 |
| `request_body.limit` | 接口参数 | `query.pageSize` | 直接赋值；复用 offset 的输入不冲突 | 必填参数缺失时报错 |
| `header.X-Caller` | 固定值 | 无 | 字符串 `product-portal` | 不适用 |

`scene` 只允许空值或 `picker`，用于应用层校验，不发送给外部 API。

返回映射为 `items[].id ← data.items[].id`；`items[].displayName` 由同一个 `data.items[]` 元素的 `name + code` 按前例规则生成；`page ← query.page` 使用 `value_mapping`。保持数组元素对应关系，不把多个商品拼成一个字段。

输入 `keyword=" 手机 "、page=2、pageSize=20` 时，上游调用应包含 `q=手机`、Body `{ "offset": 20, "limit": 20 }`、Header `X-Caller: product-portal`。page=1 时 offset=0 是有效值，不能因假值判断而被省略。上游超时按已确认上游错误契约处理，不能套用 q 的缺值策略返回空列表。

所有外部入参均保存为 `externalApiBindings[].right`；返回字段使用 `fieldMappings`。固定 Header 只是普通业务标记，不表示认证凭据配置。

### 无字段映射的调用边界

若绑定的是无入参的 `POST /refresh`，成功后应用仅返回固定 `{ "accepted": true }`，则 `externalApiBindings=[]`、响应为 `value_mapping`。`sourceBinding` 仍保留该 Operation：任务规划、代码生成和业务验收必须保留真实上游调用，不能直接返回 true 而跳过调用。

## 任务生成与后端实现

使用现有固定阶段，不增加逐字段任务或独立“映射层”任务：

| 阶段 | 数据表示例 | 外部 API 示例 |
| --- | --- | --- |
| objects | 应用请求/响应 DTO、类型化查询值与数据库结果边界 | 应用请求/响应 DTO、服务调用所需类型边界 |
| repository / upstream | BaseMapper 条件查询，保留括号与参数化；写入变体处理安全更新 | Client、传输 DTO、准确的 Path/Query/Header/Body 绑定与来源配置 |
| service | 校验 scene、计算 keyword/startDate、调用 Repository、逐行构造 displayName/generatedAt | 校验 scene/page/pageSize、计算 q/offset/limit、调用 Client、逐项转换返回值 |
| controller | 按应用接口契约接收请求并委托 Service | 按应用接口契约接收请求并委托 Service |

Service 任务描述必须写清“哪个目标、哪些依赖、怎样处理、何时执行、缺值怎么办”。例如外部调用场景应包含：调用前计算 offset、q 的缺值省略、发送 limit 与固定 Header、调用后按数组元素处理 displayName，以及上游失败的既定处理。不能只写“完成字段映射”。具体路径来自当前 WorkspaceSnapshot，新增类型及其调用者签名必须在现有负责阶段内成对规划。

规划只产生待确认任务；用户确认后才执行代码生成。实现使用普通类型化 Java 代码，不引入运行时表达式引擎或新的模型调用。缺少必需算法、时区、类型边界或错误语义时报告具体契约缺口，不能猜测补齐。
