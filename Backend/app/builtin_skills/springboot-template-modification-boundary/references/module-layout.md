# 新增业务模块完整目录结构

本文件给大模型展示一个完整业务模块的目录布局，以 `project` 模块为例。平台在进入开发阶段前已确定性生成标注 ✅ 的文件，Agent 只需补充标注 🟡 的部分。

## 完整目录树

```
backend/
├── pom.xml                                    🔴 禁止修改
├── src/
│   ├── main/
│   │   ├── java/com/cmbchina/backend/
│   │   │   ├── Application.java              🔴 禁止修改
│   │   │   ├── common/                        🔴 禁止修改（公共基础设施）
│   │   │   │   ├── config/
│   │   │   │   │   ├── MybatisPlusConfiguration.java
│   │   │   │   │   └── CrosConfig.java
│   │   │   │   ├── exception/
│   │   │   │   │   ├── AbstractBaseException.java
│   │   │   │   │   ├── BaseExceptionHandler.java
│   │   │   │   │   ├── BizException.java
│   │   │   │   │   ├── IBizErrorCode.java
│   │   │   │   │   └── IErrorCode.java
│   │   │   │   ├── page/
│   │   │   │   │   ├── PageParam.java
│   │   │   │   │   └── PageResult.java
│   │   │   │   └── response/
│   │   │   │       └── ResponseEntity.java
│   │   │   ├── auth/                          🔴 禁止修改（仅 auth 分支，权限模块）
│   │   │   │   ├── adapter/web/
│   │   │   │   ├── application/
│   │   │   │   ├── bootstrap/
│   │   │   │   ├── common/
│   │   │   │   ├── domain/
│   │   │   │   └── infrastructure/
│   │   │   │
│   │   │   └── project/                      🟢 业务模块（自由编写）
│   │   │       ├── domain/
│   │   │       │   ├── entity/
│   │   │       │   │   └── Project.java              ✅ 预置（字段完整）
│   │   │       │   ├── repository/
│   │   │       │   │   └── ProjectRepository.java   ✅ 预置（CRUD 方法签名）
│   │   │       │   └── exception/
│   │   │       │       └── ProjectErrorCode.java    🟡 Agent 补充（错误码）
│   │   │       ├── infrastructure/
│   │   │       │   ├── po/
│   │   │       │   │   └── ProjectPO.java            ✅ 预置（注解完整）
│   │   │       │   ├── mapper/
│   │   │       │   │   └── ProjectMapper.java        ✅ 预置（BaseMapper）
│   │   │       │   └── repository/
│   │   │       │       ├── impl/
│   │   │       │       │   └── ProjectRepositoryImpl.java  ✅ 预置（BaseMapper 调用）
│   │   │       │       └── converter/
│   │   │       │           └── ProjectConverter.java      ✅ 预置（MapStruct）
│   │   │       ├── application/
│   │   │       │   ├── dto/
│   │   │       │   │   ├── ProjectDTO.java           ✅ 预置（字段）
│   │   │       │   │   ├── ProjectUpsertDTO.java     ✅ 预置（字段）
│   │   │       │   │   └── ProjectStatusDTO.java     ✅ 预置（字段）
│   │   │       │   ├── assembler/
│   │   │       │   │   └── ProjectAssembler.java     🟡 Agent 补充（DTO↔Entity 转换）
│   │   │       │   └── service/
│   │   │       │       └── ProjectApplicationService.java  🟡 Agent 补充（方法体）
│   │   │       └── adapter/web/
│   │   │           └── ProjectController.java       ✅ 预置（端点声明）+ 🟡 Agent 补充（校验）
│   │   │
│   │   └── resources/
│   │       ├── application.yml               🔴 禁止修改
│   │       └── mapper/
│   │           ├── auth/                     🔴 禁止修改（已有 XML）
│   │           └── project/                  🟢 业务模块
│   │               └── ProjectMapper.xml          ✅ 预置（namespace）+ 🟡 Agent 补充（自定义 SQL）
│   │
│   └── test/java/com/cmbchina/backend/       🟡 Agent 按需补充测试
│
└── docs/                                     🔴 禁止修改
```

## 包名规则

```
com.cmbchina.backend.<module>
```

| 业务实体 | module 包名 | 表名 | Controller 路径 |
| --- | --- | --- | --- |
| Project | `project` | `project` | `/api/projects` |
| OrderItem | `order` | `order_item` | `/api/orders` |
| ProjectMember | `project` | `project_member` | `/api/project-members` |

> 同一个业务模块下可以有多个实体（如 `project` 模块下有 `Project` 和 `ProjectMember`）。实体的包路径都在 `com.cmbchina.backend.<module>` 下，按 DDD 分层。

## DDD 分层依赖方向

```
adapter/web (Controller)
    ↓ 依赖
application/service (ApplicationService)
    ↓ 依赖
application/dto + application/assembler
    ↓ 依赖
domain/repository (接口) + domain/entity
    ↑ 实现
infrastructure/repository/impl + infrastructure/mapper + infrastructure/po + infrastructure/repository/converter
```

### 依赖规则

- **Controller** 只依赖 `ApplicationService` 和 `DTO`
- **ApplicationService** 依赖 `Repository`（接口）、`Assembler`、`DTO`、`Entity`
- **RepositoryImpl** 依赖 `Mapper`、`Converter`、`PO`、`Entity`
- **domain 层**（entity/repository 接口）不依赖 infrastructure 层
- **infrastructure 层**依赖 domain 层（实现接口、转换实体）

### 禁止的依赖

- ❌ Controller 直接依赖 Repository/Mapper/PO
- ❌ ApplicationService 直接依赖 Mapper/PO
- ❌ domain/entity 依赖 infrastructure/po（Entity 不能有 MyBatis-Plus 注解）
- ❌ 业务模块依赖 auth 模块的内部类（只能用 `@RequireAnyResource` 引用资源常量）

## 新增模块的文件清单

每新增一个业务实体，需要（平台预置 + Agent 补充）以下文件：

| # | 文件 | 预置/补充 | 说明 |
| --- | --- | --- | --- |
| 1 | `domain/entity/<Entity>.java` | ✅ 预置 | 领域实体 |
| 2 | `infrastructure/po/<Entity>PO.java` | ✅ 预置 | 持久化对象 |
| 3 | `infrastructure/mapper/<Entity>Mapper.java` | ✅ 预置 | Mapper 接口 |
| 4 | `resources/mapper/<module>/<Entity>Mapper.xml` | ✅ 预置 | Mapper XML |
| 5 | `domain/repository/<Entity>Repository.java` | ✅ 预置 | 仓储接口 |
| 6 | `infrastructure/repository/impl/<Entity>RepositoryImpl.java` | ✅ 预置 | 仓储实现 |
| 7 | `infrastructure/repository/converter/<Entity>Converter.java` | ✅ 预置 | PO 转换器 |
| 8 | `application/dto/<Entity>DTO.java` | ✅ 预置 | 响应 DTO |
| 9 | `application/dto/<Entity>UpsertDTO.java` | ✅ 预置 | 请求 DTO |
| 10 | `application/dto/<Entity>StatusDTO.java` | ✅ 预置（如有状态操作） | 状态变更 DTO |
| 11 | `application/assembler/<Entity>Assembler.java` | 🟡 Agent | DTO↔Entity 转换 |
| 12 | `application/service/<Entity>ApplicationService.java` | 🟡 Agent | 应用服务（方法体） |
| 13 | `adapter/web/<Entity>Controller.java` | ✅ 预置 + 🟡 Agent | Controller（端点+校验） |
| 14 | `domain/exception/<Entity>ErrorCode.java` | 🟡 Agent | 错误码（按需） |
