---
name: springboot-template-modification-boundary
description: 后端 Spring Boot 模板工程文件修改边界规范（后端 skill）。当大模型在从远程拉取的 Spring Boot 模板工程中生成或修改后端 Java 代码、新增业务模块（entity/po/mapper/repository/service/dto/controller）、新增 Mapper XML 时使用，明确哪些后端文件禁止修改、哪些只能增量追加、哪些可以自由编写，避免破坏模板工程的 DDD 分层骨架与公共基础设施。涉及 backend/src/main/java、backend/src/main/resources/mapper、pom.xml、application.yml、common 公共模块、auth 权限模块时使用。
---

# 后端 Spring Boot 模板工程文件修改边界规范

本技能规定大模型在**从远程拉取的 Spring Boot 模板工程**中生成后端 Java 代码时，各文件的**修改边界**与**放置位置**。模板工程提供 DDD 分层骨架、公共响应/异常/分页/配置基础设施和 auth 权限模块；框架骨架不能被破坏，业务代码只能在指定区域生成。

## 虚拟路径前缀（重要）

本技能里所有 `src/...` 路径都是**相对于后端工程根**的相对路径。但在代码生成工作流中，文件系统工具（read_file / write_file / list_files 等）使用的是**相对于工作区根的虚拟绝对路径**，虚拟根是 `/`。

后端工程根在工作区中的实际位置是：

```
/backend/
```

直接平铺在工作区根目录下，与 `.xcodeagent`、`/frontend/` 同级。

因此本技能里写的每一条 `src/...` 路径，在调用文件系统工具时都要加上前缀 `/backend/`：

| 本技能里的相对路径 | 实际虚拟绝对路径 |
| --- | --- |
| `src/main/java/com/cmbchina/backend/<module>/domain/entity/<Entity>.java` | `/backend/src/main/java/com/cmbchina/backend/<module>/domain/entity/<Entity>.java` |
| `src/main/java/com/cmbchina/backend/<module>/infrastructure/po/<Entity>PO.java` | `/backend/src/main/java/com/cmbchina/backend/<module>/infrastructure/po/<Entity>PO.java` |
| `src/main/java/com/cmbchina/backend/<module>/infrastructure/mapper/<Entity>Mapper.java` | `/backend/src/main/java/com/cmbchina/backend/<module>/infrastructure/mapper/<Entity>Mapper.java` |
| `src/main/resources/mapper/<module>/<Entity>Mapper.xml` | `/backend/src/main/resources/mapper/<module>/<Entity>Mapper.xml` |
| `src/main/java/com/cmbchina/backend/<module>/domain/repository/<Entity>Repository.java` | `/backend/src/main/java/com/cmbchina/backend/<module>/domain/repository/<Entity>Repository.java` |
| `src/main/java/com/cmbchina/backend/<module>/infrastructure/repository/impl/<Entity>RepositoryImpl.java` | `/backend/src/main/java/com/cmbchina/backend/<module>/infrastructure/repository/impl/<Entity>RepositoryImpl.java` |
| `src/main/java/com/cmbchina/backend/<module>/infrastructure/repository/converter/<Entity>Converter.java` | `/backend/src/main/java/com/cmbchina/backend/<module>/infrastructure/repository/converter/<Entity>Converter.java` |
| `src/main/java/com/cmbchina/backend/<module>/application/dto/<Entity>DTO.java` | `/backend/src/main/java/com/cmbchina/backend/<module>/application/dto/<Entity>DTO.java` |
| `src/main/java/com/cmbchina/backend/<module>/application/service/<Entity>ApplicationService.java` | `/backend/src/main/java/com/cmbchina/backend/<module>/application/service/<Entity>ApplicationService.java` |
| `src/main/java/com/cmbchina/backend/<module>/application/assembler/<Entity>Assembler.java` | `/backend/src/main/java/com/cmbchina/backend/<module>/application/assembler/<Entity>Assembler.java` |
| `src/main/java/com/cmbchina/backend/<module>/adapter/web/<Entity>Controller.java` | `/backend/src/main/java/com/cmbchina/backend/<module>/adapter/web/<Entity>Controller.java` |

**生成代码前，读取 `/.xcodeagent/template-generation-manifest.json` 的 `templateVariant` 和当前任务允许路径。** 不要把文件写到工作区根下的裸 `src/` 或 `Backend/src/`，那会写到错误位置。

## 🔴 后端工程根目录禁止创建文件

`/backend/` 根目录下**禁止创建任何新文件**，包括但不限于：

- ❌ 脚本文件：`.py`、`.sh`、`.bash`、`.ps1`、`.bat`
- ❌ 配置文件：任何 `.json`、`.yaml`、`.yml`、`.toml`、`.env`、`.ini` 文件
- ❌ 文档文件：`.md`、`.txt`、`.log` 文件
- ❌ 临时文件：`.tmp`、`.bak`、`.swp` 文件
- ❌ 任何其他非框架骨架的文件

后端工程根目录下已有的文件（`pom.xml`、`application.yml`、`.gitignore` 等）由模板工程管理，**禁止修改**。新增文件**只能**放在下述允许的子目录中。

## 🔴 验证边界：由外层质量门禁统一执行

在 XCodeAgent 的 Backend task 中，写完代码后不要运行 Maven 构建、编译、单元测试或启动命令。外层 integration-test 阶段会在所有 owner task 完成后统一执行仓库级检查；如果发现依赖或命令缺失，应在最终 JSON 中报告，不能通过安装依赖或临时脚本绕过边界。

### ❌ Backend Agent 禁止行为

- ❌ 创建 `run_build.sh`、`run_test.sh`、`run_check.py` 等任何脚本文件，再想办法执行它。
- ❌ 在 Backend task 中调用 `mvn compile`、`mvn test`、`mvn install`、`mvn spring-boot:run` 或项目级构建/测试命令。
- ❌ 把脚本写到 `/tmp/`、工作区根目录或 `/backend/` 根目录来"绕过"限制——任何位置都不允许生成临时脚本。

## 核心原则

模板变体必须隔离：

- **main**：后端模板只有 `common` 公共基础设施，没有 auth 权限模块。业务模块直接在 `com.cmbchina.backend.<module>` 下新建。
- **auth**：后端模板包含 `common` 公共基础设施 + `auth` 权限模块（完整的 RBAC）。业务模块在 `com.cmbchina.backend.<module>` 下新建，**不得修改 `auth` 模块的任何已有文件**。

- 业务模块包名固定为 `com.cmbchina.backend.<module>`，`<module>` 取业务实体名的小写驼峰（如 `project`、`orderItem`）。
- DDD 分层固定为：`domain/entity` → `infrastructure/po` → `infrastructure/mapper` → `domain/repository` → `infrastructure/repository/impl` + `infrastructure/repository/converter` → `application/dto` + `application/assembler` + `application/service` → `adapter/web`。
- 框架骨架文件（启动类、公共模块、auth 模块、配置）**禁止修改**。
- **预置代码**：平台在进入开发阶段前已根据 TechnicalPlan 确定性生成了部分骨架文件（Entity/PO/Mapper/Repository/DTO/Controller 声明），Agent 只需补充业务逻辑，不要重新生成这些文件。

## 文件修改边界总览

| 分类 | 含义 | 涉及文件 |
| --- | --- | --- |
| 🔴 禁止修改 | 后端框架骨架与配置，改了会破坏整个工程 | 启动类、`common/` 全部、`auth/` 已有文件、`pom.xml`、`application.yml`、`mapper/` 已有 XML |
| 🟡 只能增量 | 只能追加新文件/新方法，不能删改现有项 | `auth/application/dto/`、`auth/application/service/`、`auth/adapter/web/`（Controller 只能追加方法） |
| 🟢 自由编写 | 业务代码生成目标，可任意编写 | 新增业务模块的 `domain/`、`infrastructure/`、`application/`、`adapter/` 下新建文件 |

## 🔴 禁止修改的文件（后端框架骨架）

以下文件**任何情况下都不得修改**，包括内容、结构、导入关系、配置项：

### 启动类与配置
- `src/main/java/com/cmbchina/backend/Application.java` — Spring Boot 启动类
- `pom.xml` — Maven 依赖与构建配置（**不要为了加依赖而改它**，所需依赖应假设已存在或向用户确认）
- `src/main/resources/application.yml` — 数据源、MyBatis-Plus 配置
- `.gitignore`、`.env.example`、`docs/`、`scripts/`

### 公共基础设施（common 模块，所有业务模块复用）
- `src/main/java/com/cmbchina/backend/common/response/ResponseEntity.java` — 统一响应体
- `src/main/java/com/cmbchina/backend/common/exception/` — `BizException`、`AbstractBaseException`、`BaseExceptionHandler`、`IErrorCode`、`IBizErrorCode`
- `src/main/java/com/cmbchina/backend/common/page/` — `PageParam`、`PageResult`
- `src/main/java/com/cmbchina/backend/common/config/` — `MybatisPlusConfiguration`、`CrosConfig`

> 业务模块**复用**这些公共类：响应统一用 `ResponseEntity.success(body)` / `ResponseEntity.failed(errorCode)`，分页用 `PageParam`/`PageResult`，异常抛 `BizException`。**不得重复定义**响应体、异常基类、分页类。

### auth 权限模块已有文件（仅 auth 分支模板）
- `src/main/java/com/cmbchina/backend/auth/` 下的**所有已有文件** — 包括 `bootstrap/`、`adapter/web/`（4 个 Controller）、`application/service/`（4 个 Service）、`application/dto/`、`application/assembler/`、`common/`（拦截器/注解/上下文）、`domain/`、`infrastructure/`
- `src/main/resources/mapper/auth/` 下的已有 XML

> auth 模块是平台托管的权限基础设施。业务模块**不得**修改 auth 的任何已有文件，也**不得**在 auth 包下新建文件。如需权限控制，通过 `@RequireAnyResource` 注解引用 auth 的资源常量。

### ⚠️ 切忌重新生成的工程配置文件

以下配置文件**绝对不能重新生成、覆盖或修改**：

- `pom.xml` — Maven 依赖与构建配置
- `application.yml` — 数据源与 MyBatis-Plus 配置
- `.gitignore`、`.env.example`

> 如果业务需求看似必须改这些文件（例如加新依赖、改数据源），**不要直接改**，先向用户说明这属于框架级改动，由用户决定。

## 🟡 只能增量修改的目录与文件

以下区域**只能新增文件或追加方法**，**不得删除或修改**框架已有的文件（仅 auth 分支）：

### `auth/application/dto/` — 权限模块 DTO
只能新增 DTO 类，不得修改已有的 `RoleDTO`、`MemberDTO`、`ResourceDTO` 等。

### `auth/application/service/` — 权限模块应用服务
只能追加新方法，不得修改已有的 `RoleApplicationService`、`ResourceApplicationService` 等的方法签名和已有方法体。

### `auth/adapter/web/` — 权限模块 Controller
只能追加新端点方法，不得修改已有的 `RoleController`、`ResourceController`、`MemberController`、`MockLoginController` 的已有端点。

## 🟢 自由编写与 DDD 分层放置规则

新增业务模块的代码在 `src/main/java/com/cmbchina/backend/<module>/` 下按 DDD 分层新建文件。**每个业务实体对应一个模块**，模块内分层目录如下：

### 领域实体：`domain/entity/<Entity>.java`

纯 POJO，用 Lombok `@Data`，只含业务字段，不含持久化注解。

### 持久化对象：`infrastructure/po/<Entity>PO.java`

用 MyBatis-Plus 注解绑定数据库表：`@TableName`、`@TableId`、`@TableField`、`@TableLogic`。含审计字段（createdAt/updatedAt/isDeleted/deletedAt/deletedBy）。

### Mapper 接口：`infrastructure/mapper/<Entity>Mapper.java`

`extends BaseMapper<XxxPO>` + `@Mapper`。基础 CRUD 由 MyBatis-Plus 提供，只在需要自定义 SQL 时加方法。

### Mapper XML：`src/main/resources/mapper/<module>/<Entity>Mapper.xml`

只含 namespace 声明。需要自定义 SQL 时在此写 `<select>`/`<insert>` 等。

### 仓储接口：`domain/repository/<Entity>Repository.java`

定义业务需要的持久化方法签名（page/findByXxx/save/update/softDelete 等）。

### 仓储实现：`infrastructure/repository/impl/<Entity>RepositoryImpl.java`

`@Repository` + `@RequiredArgsConstructor`，注入 Mapper 和 Converter，调用 `BaseMapper` 方法实现接口。

### PO 转换器：`infrastructure/repository/converter/<Entity>Converter.java`

MapStruct `@Mapper(componentModel = "spring")` 接口，Entity ↔ PO 互转。

### DTO：`application/dto/<Entity>DTO.java` 等

请求/响应数据载体，用 `@Data` + `@NoArgsConstructor` + `@AllArgsConstructor`。带 `javax.validation` 注解。

### Assembler：`application/assembler/<Entity>Assembler.java`

`@Component`，DTO ↔ Entity 互转，含业务组装逻辑。

### 应用服务：`application/service/<Entity>ApplicationService.java`

`@Service` + `@RequiredArgsConstructor`，注入 Repository 和 Assembler，编排业务用例。`@Transactional` 标注写操作。

### Controller：`adapter/web/<Entity>Controller.java`

`@RestController` + `@RequiredArgsConstructor` + `@RequestMapping("/api/<module>s")`，注入 ApplicationService，声明 REST 端点，返回 `ResponseEntity<T>`。

## 预置代码说明（重要）

平台在**进入开发阶段前**已根据已确认的 TechnicalPlan，确定性生成了以下骨架文件：

| 文件 | 预置内容 | Agent 需补充 |
| --- | --- | --- |
| `domain/entity/<Entity>.java` | 全部字段 + `@Data` | 无（已完整） |
| `infrastructure/po/<Entity>PO.java` | `@TableName` + 全部字段注解 + 审计字段 | 无（已完整） |
| `infrastructure/mapper/<Entity>Mapper.java` | `extends BaseMapper` + `@Mapper` | 需要时加自定义方法 |
| `resources/mapper/<module>/<Entity>Mapper.xml` | namespace 声明 | 需要时加自定义 SQL |
| `domain/repository/<Entity>Repository.java` | CRUD 方法签名 | 需要时加自定义方法 |
| `infrastructure/repository/impl/<Entity>RepositoryImpl.java` | BaseMapper 调用实现 | 需要时加自定义实现 |
| `infrastructure/repository/converter/<Entity>Converter.java` | MapStruct Entity↔PO | 无（已完整） |
| `application/dto/<Entity>DTO.java` | 字段 + `@Data` | 需要时加校验注解 |
| `adapter/web/<Entity>Controller.java` | 端点声明 + service 调用 | 补业务校验、补 service 方法体 |

**Agent 读取任务 `prebuilt_files` 字段**确认哪些文件已预置。对已预置的文件：
- ✅ 只补充缺失的业务逻辑（service 方法体、controller 参数校验、自定义 SQL）
- ✅ 只追加缺失的方法，不改已有方法签名
- ❌ 不重新生成整个文件
- ❌ 不改已有字段、注解、方法签名
- ❌ 不删除已有内容

如果预置文件已完整满足契约，返回 `already_satisfied`，不要做任何修改。

## 生成后端代码的标准流程

当需要为某个业务实体生成具体代码时，按以下步骤：

1. **读取预置文件**：检查 `prebuilt_files` 列出的文件是否已存在且完整。
2. **补充 Repository 自定义方法**（如需）：在接口加方法签名，在 Impl 加实现。
3. **补充 Mapper 自定义方法**（如需）：在 Mapper 接口加方法，在 XML 写 SQL。
4. **编写 ApplicationService 方法体**：补充 CRUD 方法体内的业务编排逻辑、校验、事务。
5. **补充 DTO 校验注解**（如需）：加 `@NotBlank`/`@Size` 等。
6. **编写 Assembler 转换逻辑**：DTO ↔ Entity 互转。
7. **补充 Controller 业务校验**：参数校验、权限检查、调用 service。
8. **不要碰公共基础设施**：复用 `common/` 的 ResponseEntity/BizException/PageParam/PageResult。

## 禁止行为清单

- ❌ 在 `/backend/` 根目录下创建任何新文件（`.py`、`.sh`、`.md`、`.json`、`.env` 等）
- ❌ 在工作区**任何位置**生成脚本文件（`.sh`/`.py`/`.js`/`.mjs` 等检查脚本、构建脚本）
- ❌ 修改 `pom.xml`（加依赖应向用户说明，由用户决定）
- ❌ 修改 `application.yml`、`Application.java`
- ❌ 修改 `common/` 下的任何文件（ResponseEntity/BizException/PageParam/MybatisPlusConfiguration 等）
- ❌ 修改 `auth/` 下的任何已有文件（Controller/Service/Entity/PO/Mapper/Repository 等）
- ❌ 修改 `resources/mapper/auth/` 下的已有 XML
- ❌ 重新生成或覆盖已预置的文件（只补充缺失部分）
- ❌ 在业务模块里重复定义 ResponseEntity/BizException/PageParam/PageResult
- ❌ 使用 Java 9+ 语法（`record`、`var`、`List.of`、text blocks、`String.isBlank`）

## Java 8 兼容性约束

所有生成的 Java 代码必须兼容 Java 8：
- ❌ 不用 `record`（用 `@Data` class）
- ❌ 不用 `var`（显式声明类型）
- ❌ 不用 `List.of`/`Map.of`（用 `new ArrayList<>()`/`new HashMap<>()` 或 `Arrays.asList`）
- ❌ 不用 text blocks（用字符串拼接）
- ❌ 不用 `String.isBlank()`（用 `trim().isEmpty()`）
- ✅ 用 Lombok `@Data`/`@RequiredArgsConstructor`/`@AllArgsConstructor`/`@NoArgsConstructor`
- ✅ 用 `javax.validation` 注解（不是 `jakarta.validation`）

## 依赖缺失处理

Backend task 不自动安装依赖。即使任务被标记为 repair，依赖缺失也只能作为阻塞信息写入最终 JSON，并交由外层流程或用户处理；Agent 不得调用 `mvn install` 或其他安装命令，也不得在 task 内重新执行项目构建验证。

## 与其他技能的关系

- **springboot-backend-generate**：提供后端数据源实现规范（database/external_api 的 bootstrap 和 layer-implementation），本技能是后端模板工程特有的文件边界约束。
- 生成后端代码时，**先遵守本技能的文件边界与预置代码规则**，再按 `springboot-backend-generate` 的规范编写具体实现。
