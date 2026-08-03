---  
name: springboot-mybatis-generate
description: >-
  生成 Spring Boot + MyBatis-Plus 分层后端代码的内置技能。当需要基于 MySQL 表生成后端模块
  （Entity/PO/DTO/Converter/Assembler → Repository/Mapper → ApplicationService → Controller）、
  按表结构推导命名与类型映射、补齐 pom.xml 依赖、application.yml 数据源或 MyBatisPlusConfig
  分页配置时使用本技能。
---  
  
# springboot-mybatis-generate
  
## When to use this skill  
Use this skill when the user needs to generate springboot-mybatis project.

---

## 目录

- [前置条件](#前置条件)
- [生成前校验](#生成前校验)
- [命名规则](#命名规则)
- [生成文件清单](#生成文件清单)
- [文件职责说明](#文件职责说明)
- [生成顺序](#生成顺序)
- [常见问题](#常见问题)

---

## 前置条件

生成前确保项目中已有以下公共基础设施（只创建一次，不随每个模块重复生成）：

| 文件 | 路径（相对于 `{basePackage}`） | 用途 |
|------|------|------|
| `PageParam.java` | `infrastructure/common/page/PageParam.java` | 分页参数（current/pageSize 校验） |
| `PageResult.java` | `infrastructure/common/page/PageResult.java` | 分页结果泛型封装 |
| `application.yml` | `src/main/resources/application.yml` | 数据源 + MyBatis-Plus 配置 |

> **Java 版本约束**：项目运行在 Java 8，生成代码必须兼容 Java 8：不使用 `var`、`record`、
> `List.of()` / `Map.of()`、文本块、`String.isBlank()` / `strip()`、`switch` 表达式等
> Java 9+ 语法与 API，改用显式类型、`Arrays.asList()` / `Collections.singletonList()`、
> `Optional.ofNullable()`、循环或 `Collectors.toList()` 流式处理等经典构造。

---

## 生成前校验

每次生成前，**先生成一个前置校验任务**（owner: backend，可单独生成），在业务文件生成之前执行。该校验只检查并补齐以下 3 项基础设施，不生成业务代码（缺失才补充，已有不动，重复执行是幂等的）：

> **依赖版本号不固定**：pom.xml 中已存在的依赖保持不动（无论版本号）；**仅当依赖缺失时**，默认按下列版本号补充。

### 一、检查依赖（pom.xml）

检查 `pom.xml` 是否包含以下依赖，**缺失则补充**：

```xml
<!-- MyBatis-Plus 启动器 -->
<dependency>
    <groupId>com.baomidou</groupId>
    <artifactId>mybatis-plus-boot-starter</artifactId>
    <version>${mybatis-plus.version}</version>
</dependency>

<!-- MySQL 驱动 -->
<dependency>
    <groupId>com.mysql</groupId>
    <artifactId>mysql-connector-j</artifactId>
    <version>${mysql-connector.version}</version>
</dependency>

<!-- Lombok 注解处理 -->
<dependency>
    <groupId>org.projectlombok</groupId>
    <artifactId>lombok</artifactId>
    <version>${lombok.version}</version>
    <scope>provided</scope>
</dependency>

<!-- MapStruct 对象映射 -->
<dependency>
    <groupId>org.mapstruct</groupId>
    <artifactId>mapstruct</artifactId>
    <version>${mapstruct.version}</version>
</dependency>

<!-- MapStruct 注解处理器 -->
<dependency>
    <groupId>org.mapstruct</groupId>
    <artifactId>mapstruct-processor</artifactId>
    <version>${mapstruct.version}</version>
    <scope>provided</scope>
</dependency>
```

以及 `<properties>` 中的版本号（仅当依赖缺失、需要补充时按默认值写入）：

```xml
<properties>
    <mybatis-plus.version>3.5.2</mybatis-plus.version>
    <mysql-connector.version>8.2.0</mysql-connector.version>
    <lombok.version>1.18.30</lombok.version>
    <mapstruct.version>1.5.5.Final</mapstruct.version>
</properties>
```

> 版本号统一在 `<properties>` 中管理。
>
> 若 `maven-compiler-plugin` 已配置 `annotationProcessorPaths`，需同时加入
> `lombok-mapstruct-binding`（0.2.0），避免 Lombok 与 MapStruct 注解处理顺序问题。

### 二、检查数据源配置（application.yml）

检查 `src/main/resources/application.yml` 是否配置数据源（`url` / `username` / `password`），**缺失则补充**：

```yaml
spring:
  datasource:
    url: jdbc:mysql://<host>:<port>/<database>?useUnicode=true&characterEncoding=utf-8&useSSL=false&allowPublicKeyRetrieval=true&serverTimezone=Asia/Shanghai
    username: <user>
    password: <password>
```

**数据源缺失时，调用 `get_mysql_config` 工具获取数据库连接信息**：该工具从 `MYSQL_*` 环境变量读取并返回 `host` / `port` / `user` / `password` / `database` / `jdbc_url`，据此填充 `spring.datasource.url` / `username` / `password`。不要凭空猜测数据库连接信息。

### 三、检查 MyBatis-Plus 配置类

检查 `{basePackage}/infrastructure/config/MyBatisPlusConfig.java` 是否存在并注册分页拦截器（`selectPage` 等分页查询依赖 `PaginationInnerInterceptor`），**缺失则创建**：

```java
import com.baomidou.mybatisplus.annotation.DbType;
import com.baomidou.mybatisplus.extension.plugins.MybatisPlusInterceptor;
import com.baomidou.mybatisplus.extension.plugins.inner.PaginationInnerInterceptor;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * MyBatis-Plus 配置类
 * 注册分页拦截器以支持 selectPage 等分页查询
 */
@Configuration
public class MyBatisPlusConfig {

    @Bean
    public MybatisPlusInterceptor mybatisPlusInterceptor() {
        MybatisPlusInterceptor interceptor = new MybatisPlusInterceptor();
        interceptor.addInnerInterceptor(new PaginationInnerInterceptor(DbType.MYSQL));
        return interceptor;
    }
}
```

### 前置校验的验收标准

- `pom.xml` 包含 `mybatis-plus-boot-starter`、`mysql-connector-j`、`lombok`、`mapstruct`、`mapstruct-processor` 五个依赖（缺失才补充，已有不动）
- `application.yml` 配置了 `spring.datasource.url` / `username` / `password`（缺失时先调用 `get_mysql_config` 工具获取数据库连接信息再填充）
- 存在 `MyBatisPlusConfig` 配置类并注册了 `PaginationInnerInterceptor`
- 前置校验任务的 `acceptance_criteria` 使用以上验收；模块生成任务（阶段一至四整合为一个任务）的 `acceptance_criteria` 覆盖 11 个生成文件编译通过、REST 端点可用

---

## 命名规则

| 项 | 规则 | 示例 |
|----|------|------|
| 表名 → 类名 | 下划线转大驼峰 | `product_category` → `ProductCategory` |
| 字段名 → 变量名 | 下划线转小驼峰 | `product_name` → `productName` |
| 表名 → 模块路径 | 下划线转小驼峰 | `product_category` → `productCategory` |
| REST 路径 | 小写 + 中划线 | `/api/v1/product-category` |

### 类型映射

| MySQL 类型 | Java 类型 |
|------------|-----------|
| `tinyint` | `Integer` |
| `smallint` | `Integer` |
| `int` / `integer` | `Integer` |
| `bigint` | `Long` |
| `varchar` / `char` / `text` | `String` |
| `decimal` / `numeric` | `java.math.BigDecimal` |
| `date` | `java.time.LocalDate` |
| `datetime` / `timestamp` | `java.time.LocalDateTime` |
| `time` | `java.time.LocalTime` |
| `float` | `Float` |
| `double` | `Double` |
| `boolean` / `bit(1)` | `Boolean` |

---

## 生成文件清单

以表 `product`（字段：`id` bigint, `name` varchar, `price` decimal, `status` tinyint）为例，共 11 个文件。先生成并执行[前置校验任务](#生成前校验)（可单独生成），再生成**一个模块生成任务**（阶段一至四整合，任务内按 4 个阶段依次生成全部文件）：

```
src/main/java/{basePackage}/
│
├── 阶段一：对象类（Entity / PO / DTO / 转换器 / 汇编器）
│   ├── domain/product/entity/Product.java                #  1.领域实体
│   ├── infrastructure/po/ProductPO.java                  #  2.持久化对象
│   ├── applicaiton/product/dto/ProductDTO.java           #  3.数据传输对象
│   ├── infrastructure/repository/converter/ProductConverter.java   #  4.PO ↔ Entity 转换器
│   └── applicaiton/product/assembler/ProductAssembler.java         #  5.DTO ↔ Entity 汇编器
│
├── 阶段二：仓储（Repository + Mapper）
│   ├── infrastructure/mapper/ProductMapper.java          #  6.Mapper 接口
│   ├── src/main/resources/mapper/ProductMapper.xml      #  7.SQL 映射文件（自定义 SQL）
│   ├── domain/product/repository/ProductRepository.java  #  8.仓储接口
│   └── infrastructure/repository/impl/ProductRepositoryImpl.java   #  9.仓储实现
│
├── 阶段三：应用服务（Service）
│   └── applicaiton/product/service/ProductApplicationService.java  # 10.应用服务
│
└── 阶段四：接口（Controller）
    └── adapter/web/ProductController.java                # 11.REST 控制器
```

依赖关系：`Controller → Service → Repository接口 → RepositoryImpl → Mapper → DB`

---

## 文件职责说明

### 1. Entity — `domain/{module}/entity/{Name}.java`

- 领域实体，包含主键 `id`（类型 `{idType}`，作为领域标识）与业务字段
- 排除：审计字段（`createdBy`, `createdAt`, `updatedBy`, `updatedAt`, `isDeleted`, `deletedBy`, `deletedAt`）
- 保留：业务标识符（`userId`、`orderNo`、`productCode` 等）
- 风格：`@Getter @Setter @NoArgsConstructor`（MapStruct 需要 setter 才能赋值；如需只读可改用 `@Data` 或带参构造器）

### 2. PO — `infrastructure/po/{Name}PO.java`

- MyBatis-Plus 持久化对象，字段与数据库列**一一对应**
- 加 `@TableName("{tableName}")` 指定表名
- 主键字段加 `@TableId(type = IdType.{ID_TYPE})`
- 包含所有字段：业务字段 + 审计字段（`createdBy`, `createdAt`, `updatedBy`, `updatedAt`, `isDeleted`, `deletedBy`, `deletedAt`）
- Lombok：`@Getter @Setter @NoArgsConstructor`（setter 供 MapStruct 转换赋值）

### 3. DTO — `applicaiton/{module}/dto/{Name}DTO.java`

- `@Data @Builder` 风格，字段与 Entity 暴露的业务字段一致（含 `id`）
- 包名 `applicaiton` 是项目原始拼写（`application` 的笔误），**统一保留该拼写**以保证与现有项目包结构一致

### 4. Converter — `infrastructure/repository/converter/{Name}Converter.java`

- MapStruct 接口，`@Mapper(componentModel = "spring")`
- 定义两个方法：`poToEntity(PO → Entity)`、`entityToPO(Entity → PO)`
- 编译时自动生成实现

### 5. Assembler — `applicaiton/{module}/assembler/{Name}Assembler.java`

- MapStruct 接口，`@Mapper(componentModel = "spring")`
- 定义两个方法：`dtoToEntity(DTO → Entity)`、`entityToDTO(Entity → DTO)`

### 6. Mapper — `infrastructure/mapper/{Name}Mapper.java`

- 继承 `BaseMapper<{Name}PO>` 即获得基础 CRUD，无需手写 SQL
- 加 `@Mapper` 注解
- 覆盖的方法：`insert`、`deleteById`、`updateById`、`selectById`、`selectPage`、`selectList`、`selectCount`、`exists` 等

### 7. XML Mapper — `src/main/resources/mapper/{Name}Mapper.xml`

- **只写自定义 SQL**（联表查询、动态 WHERE、分组聚合等）
- `namespace` 指向 Mapper 接口全限定名
- 基础 CRUD 由 `BaseMapper` 提供，无需在 XML 中重复编写
- 分页由 `PaginationInnerInterceptor` 自动处理，不要在 XML 中写分页 SQL

### 8. Repository 接口 — `domain/{module}/repository/{Name}Repository.java`

- 仓储接口，**5 个标准方法**（`{idType}` 为主键类型）：
  - `{Name} insert{Name}({Name} entity)` — 新增
  - `void delete{Name}ById({idType} id)` — 按主键删除
  - `{Name} update{Name}({Name} entity)` — 更新（Entity 含 `id`，据此定位记录）
  - `{Name} find{Name}ById({idType} id)` — 按主键查询
  - `PageResult<{Name}> find{Name}s(PageParam param)` — 分页查询

### 9. RepositoryImpl — `infrastructure/repository/impl/{Name}RepositoryImpl.java`

- `@Repository @RequiredArgsConstructor`，注入 Mapper + Converter
- 实现上述 5 个方法，Mapper 调用 MyBatis-Plus，Converter 做 PO ↔ Entity 转换
- 分页：`new Page<>(param.getCurrent(), param.getPageSize())` 传入 `mapper.selectPage()`，再把 `Page<PO>` 转成 `PageResult<Entity>`

### 10. ApplicationService — `applicaiton/{module}/service/{Name}ApplicationService.java`

- `@Service @RequiredArgsConstructor`，注入 Assembler + Repository
- 协调两者完成业务操作，每个方法结构：DTO → Entity → Repository → Entity → DTO
- 方法对齐 Controller：新增/更新返回 `{Name}DTO`，详情返回 `{Name}DTO`，分页返回 `PageResult<{Name}DTO>`
- 事务边界：写操作加 `@Transactional`

### 11. Controller — `adapter/web/{Name}Controller.java`

- `@RestController @RequiredArgsConstructor @RequestMapping("/api/v1/{restPath}")`
- 5 个 REST 端点：`POST`（新增，返回 DTO）、`DELETE /{id}`（删除）、`PUT`（更新，返回 DTO）、`GET /{id}`（查单个，返回 DTO）、`GET`（分页，返回 `PageResult<DTO>`）

---

## 生成顺序

先生成并执行**前置校验任务**（owner: backend，见[生成前校验](#生成前校验)，可单独生成）：检查并补齐 `pom.xml` 依赖、`application.yml` 数据源、`MyBatisPlusConfig` 配置类，缺什么补什么，依赖缺失时按默认版本号补充。通过后每个后端模块再生成**一个模块生成任务**，把**功能分 4 个阶段**的 11 个文件按顺序生成：先搭对象类（含转换器/汇编器），再落仓储，然后做业务服务，最后暴露接口。阶段内从底到上、逐层依赖下层，每个阶段产出的文件互相依赖完整；**4 个阶段整合为一个任务，阶段只是任务内的执行顺序，不再拆成 4 个独立任务**：

| 阶段 | 序号 | 文件 | 关键依赖 |
|------|------|------|---------|
| 〇、前置校验 | — | 前置校验任务（可单独生成）：检查并补齐 `pom.xml` 依赖、`application.yml` 数据源、`MyBatisPlusConfig` 配置类 | 无 |
| 一、对象类 | 1 | `domain/{module}/entity/{Name}.java` | 无 |
| | 2 | `infrastructure/po/{Name}PO.java` | 无 |
| | 3 | `applicaiton/{module}/dto/{Name}DTO.java` | 无 |
| | 4 | `infrastructure/repository/converter/{Name}Converter.java` | Entity + PO |
| | 5 | `applicaiton/{module}/assembler/{Name}Assembler.java` | DTO + Entity |
| 二、仓储 | 6 | `infrastructure/mapper/{Name}Mapper.java` | PO |
| | 7 | `src/main/resources/mapper/{Name}Mapper.xml` | Mapper |
| | 8 | `domain/{module}/repository/{Name}Repository.java` | Entity |
| | 9 | `infrastructure/repository/impl/{Name}RepositoryImpl.java` | Mapper + Converter + Repository |
| 三、Service | 10 | `applicaiton/{module}/service/{Name}ApplicationService.java` | Assembler + DTO + Repository |
| 四、Controller | 11 | `adapter/web/{Name}Controller.java` | DTO + Service |

> 阶段一至四的 11 个文件都在**同一个模块生成任务**内按顺序生成，不要为每个阶段单独建任务。

### 基础变量对照表

| 占位符 | 含义 | 示例值 |
|--------|------|--------|
| `{basePackage}` | 项目基础包名 | `com.myproject.module` |
| `{module}` | 模块名（小驼峰） | `product` |
| `{Name}` | 类名（大驼峰） | `Product` |
| `{fieldName}` | 变量名（小驼峰） | `product` |
| `{tableName}` | 数据库表名 | `product` |
| `{comment}` | 业务描述 | 商品 |
| `{ID_TYPE}` | 主键策略 | `AUTO` |
| `{idType}` | 主键 Java 类型 | `Long` |
| `{restPath}` | REST 路径（kebab-case） | `product-category` |

---

## 常见问题

### Q: 为什么 Entity / PO 默认用 `@Getter @Setter @NoArgsConstructor`？

**原因**：MapStruct 需要 setter 或带参构造器来赋值，只有 getter 的只读对象会让所有映射字段为 null。

**解决方案**：默认模板已含 `@Setter`；若某类确实需要只读，任选其一：
| 方式 | 改动 |
|------|------|
| 加 `@Setter` | `@Getter @Setter @NoArgsConstructor` |
| 改 `@Data` | `@Data @NoArgsConstructor` |
| 构造器注入 | `@Builder @AllArgsConstructor` + `@NoArgsConstructor` |

### Q: `applicaiton` 拼写问题？

`applicaiton` 是 `application` 的笔误（缺 `i`）。本项目统一保留该拼写以与现有项目包结构一致，同一仓库内不要混用两种拼写；迁移到正确拼写 `application` 需同步调整全部包引用，属于跨模块重构，不在单次模块生成范围内。

### Q: XML Mapper 需要写哪些 SQL？

`BaseMapper` 已提供基础 CRUD，XML 只在以下场景需要：
- 联表查询（JOIN）
- 动态复杂 WHERE
- 自定义 UPDATE/SELECT 字段
- 批量操作

### Q: `PageParam` / `PageResult` 的职责？

- **PageParam**：接收前端分页参数，含 `current`（页码，默认 1）和 `pageSize`（每页条数，默认 20，上限 100）
- **PageResult**：泛型封装，含 `total`、`list`、`current`、`pageSize`、`totalPage`，提供 `convert()` 方法用于 Entity ↔ DTO 转换
