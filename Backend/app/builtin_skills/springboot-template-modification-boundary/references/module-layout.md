# 业务模块参考目录结构

本文件以 `project` 模块展示参考目录布局，不代表工作区已创建这些业务文件。仅按当前 Endpoint Task 与授权路径生成或复用所需文件，不按实体批量生成全部层。数据库场景按确认操作与映射选择持久层；纯外部 API 场景不强制生成 PO、Mapper、Repository。包名、路径与接口以确认的 Endpoint 契约及真实工作区为准。

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
│   │   │   ├── auth/                          🔴 禁止修改（权限基础设施模块）
│   │   │   │   ├── adapter/web/
│   │   │   │   ├── application/
│   │   │   │   ├── bootstrap/
│   │   │   │   ├── common/
│   │   │   │   ├── domain/
│   │   │   │   └── infrastructure/
│   │   │   │
│   │   │   └── project/                      🟢 业务模块（按任务授权）
│   │   │       ├── domain/
│   │   │       │   ├── entity/
│   │   │       │   │   └── Project.java              按需生成或复用
│   │   │       │   ├── repository/
│   │   │       │   │   └── ProjectRepository.java   按需生成或复用
│   │   │       │   └── exception/
│   │   │       │       └── ProjectErrorCode.java    按需生成或复用
│   │   │       ├── infrastructure/
│   │   │       │   ├── po/
│   │   │       │   │   └── ProjectPO.java            按需生成或复用
│   │   │       │   ├── mapper/
│   │   │       │   │   └── ProjectMapper.java        按需生成或复用
│   │   │       │   └── repository/
│   │   │       │       ├── impl/
│   │   │       │       │   └── ProjectRepositoryImpl.java  按需生成或复用
│   │   │       │       └── converter/
│   │   │       │           └── ProjectConverter.java      按需生成或复用
│   │   │       ├── application/
│   │   │       │   ├── dto/
│   │   │       │   │   ├── ProjectDTO.java           按需生成或复用
│   │   │       │   │   ├── ProjectUpsertDTO.java     按需生成或复用
│   │   │       │   │   └── ProjectStatusDTO.java     按需生成或复用
│   │   │       │   ├── assembler/
│   │   │       │   │   └── ProjectAssembler.java     按需生成或复用
│   │   │       │   └── service/
│   │   │       │       └── ProjectApplicationService.java  按需生成或复用
│   │   │       └── adapter/web/
│   │   │           └── ProjectController.java       按需生成或复用
│   │   │
│   │   └── resources/
│   │       ├── application.yml               🔴 禁止修改
│   │       └── mapper/
│   │           ├── auth/                     🔴 禁止修改（已有 XML）
│   │           └── project/                  🟢 业务模块
│   │               └── ProjectMapper.xml          按需生成或复用
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

下表是可选文件职责参考，不是每个实体的强制产物清单。仅在当前 Endpoint 需要且任务授权时创建；已有实现按真实内容复用或最小修订。

| # | 文件 | 创建条件 | 说明 |
| --- | --- | --- | --- |
| 1 | `domain/entity/<Entity>.java` | 当前任务需要 | 领域实体 |
| 2 | `infrastructure/po/<Entity>PO.java` | 数据库职责需要 | 持久化对象 |
| 3 | `infrastructure/mapper/<Entity>Mapper.java` | 数据库职责需要 | Mapper 接口 |
| 4 | `resources/mapper/<module>/<Entity>Mapper.xml` | 数据库职责需要 | Mapper XML |
| 5 | `domain/repository/<Entity>Repository.java` | 数据库职责需要 | 仓储接口 |
| 6 | `infrastructure/repository/impl/<Entity>RepositoryImpl.java` | 数据库职责需要 | 仓储实现 |
| 7 | `infrastructure/repository/converter/<Entity>Converter.java` | 数据库职责需要 | PO 转换器 |
| 8 | `application/dto/<Entity>DTO.java` | 响应契约需要 | 响应 DTO |
| 9 | `application/dto/<Entity>UpsertDTO.java` | 请求契约需要 | 请求 DTO |
| 10 | `application/dto/<Entity>StatusDTO.java` | 状态操作契约需要 | 状态变更 DTO |
| 11 | `application/assembler/<Entity>Assembler.java` | 当前任务需要 | DTO↔Entity 转换 |
| 12 | `application/service/<Entity>ApplicationService.java` | 当前任务需要 | 应用服务 |
| 13 | `adapter/web/<Entity>Controller.java` | 当前端点需要 | Controller（端点+校验） |
| 14 | `domain/exception/<Entity>ErrorCode.java` | 当前任务需要 | 错误码 |
