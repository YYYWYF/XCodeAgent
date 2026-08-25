# MyBatis 分层实现细节

仅在执行数据库来源实体的后端代码任务时读取，并按当前任务阶段选择相关章节。

## 命名与类型

- 表名 `product_category` 转类名 `ProductCategory`，转模块名 `productCategory`。
- 列名 `category_name` 转字段名 `categoryName`。
- REST 路径必须沿用已确认 API Contract，不能从表名重新推导并覆盖契约。
- MySQL 常用类型映射：`tinyint/smallint/int` → `Integer`，`bigint` → `Long`，
  `varchar/char/text` → `String`，`decimal/numeric` → `BigDecimal`，`date` →
  `LocalDate`，`datetime/timestamp` → `LocalDateTime`，`time` → `LocalTime`，
  `float` → `Float`，`double` → `Double`，`boolean/bit(1)` → `Boolean`。
- 实体字段与数据库列的对应关系必须使用 `database_design.bindings`，不能只依赖同名猜测。

## 对象与映射阶段

### Entity

- 表达领域对象和业务字段；主键类型来自已确认的字段/表设计。
- 默认不暴露纯持久化审计字段，但保留 `userId`、`orderNo` 等业务标识。
- 沿用项目现有 Lombok 风格；使用 MapStruct 时必须提供可写属性或构造器。

### PO

- 通过 `@TableName` 绑定已确认目标表，字段与数据库列一一对应。
- 主键策略依据现有表结构和项目约定，不能无依据固定为 `AUTO`。
- 列名与 Java 字段不一致时显式使用 MyBatis-Plus 映射注解。

### DTO、Converter 与 Assembler

- DTO 只包含当前 API Contract 和应用服务真正需要的字段。
- Converter 负责 PO 与 Entity；Assembler 负责 Entity 与 DTO，不在 Controller 中复制映射逻辑。
- 优先复用项目现有 MapStruct 配置和组件模型。

## 仓储阶段

- Mapper 继承 `BaseMapper<PO>`，基础单表能力优先使用 MyBatis-Plus。
- Mapper XML 只承载当前 endpoint 确实需要的联表、聚合、动态筛选或自定义 SQL；基础 CRUD 不创建空 XML 或重复 SQL。
- Repository 接口只声明当前 endpoint 和已存在模块需要的领域操作，不强制补齐五个通用 CRUD 方法。
- RepositoryImpl 注入 Mapper 与 Converter，查询条件和分页行为必须与 TechnicalPlan Endpoint 的参数及 Schema 一致。
- 逻辑删除、审计字段及租户过滤沿用项目和表的既有约定，不能凭空添加。

## 应用服务阶段

- ApplicationService 编排 DTO/Entity、Repository 和业务决策，不直接操作 Mapper。
- 写操作按 Spring 事务约定和实际持久化边界决定事务范围。
- 零匹配、多匹配、唯一性冲突和返回状态等行为必须与已确认 endpoint 决策一致。
- 只实现当前契约需要的方法；复用已有服务时做精确增量修改。

## Controller 阶段

- 使用项目现有响应封装、异常处理、校验注解和鉴权方式。
- HTTP method、path、请求 Schema、响应 Schema、状态码、错误码和执行语义严格来自当前 TechnicalPlan API Contract。
- Controller 只做协议适配和参数校验，业务判断放入 ApplicationService。
- 同一业务模块已有 Controller 时优先增加方法，不能为每个 endpoint 重复创建 Controller。

## Java 8 与工程约束

- 集合常量使用 `Arrays.asList`、`Collections.singletonList` 等 Java 8 API。
- 空白判断使用 `value != null && !value.trim().isEmpty()`。
- 时间类型优先使用 `java.time`；金额使用 `BigDecimal`。
- 新增或修改的类遵循工作区真实包结构、命名和中文注释规范。
