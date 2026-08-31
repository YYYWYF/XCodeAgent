---
name: springboot-mybatis-generate
description: >-
  为已确认数据库实体实现 Java 8 Spring Boot + MyBatis-Plus 分层后端代码。
  适用于 Entity/PO/DTO、Repository/Mapper、ApplicationService 和 Controller；
  不用于数据库建表、迁移、种子数据或外部 API 集成。
---

# springboot-mybatis-generate

基于当前任务包中的已确认实体设计、API Contract 和真实工作区结构，复用现有
Spring Boot 工程并实现数据库来源实体的后端代码。本 Skill 只在 Build 执行阶段
使用；任务拆分、依赖和文件授权已经由上游平台确定。

## 执行边界

- `database` 表示实体的数据来源，当前 Skill 只负责后端代码，不执行表结构变更、迁移或种子数据。
- 实体字段、目标表和字段绑定以当前 `implementation_contract.entities[].source_binding` 为准；接口方法、路径和 Schema 以当前 `implementation_contract.api_contract` 为准。
- 仅实现当前确认的 endpoint 操作，不顺带生成契约之外的 CRUD 接口。
- 只写当前任务的 `allowed_paths` 与 `change_scope`，不得修改正式计划、任务 DAG、API/Entity 契约、数据库 schema、迁移或种子数据。
- 复用工作区现有 Maven 工程、基础包、公共分页类、异常模型和配置；已有文件应修改或复用，不能重复创建。
- endpoint 任务不得创建或修改全局依赖、数据源配置和 MyBatis 配置。只有当前任务 `implementation_contract.kind=bootstrap` 时，才读取并执行 [bootstrap.md](references/bootstrap.md)。
- 所有代码兼容 Java 8，不使用 `record`、`var`、`List.of`、文本块、`String.isBlank` 或其他 Java 9+ 语法/API。

执行前读取任务列出的 Skill/reference 文件、目标文件和最近的同层实现。按任务包中
已批准的阶段和有序描述执行：现有目标先判断是否完整满足契约，完整时不写入，部分满足时只做最小补齐；任务范围无法满足时返回结构化失败或 change request，不能自行扩大范围。

## 代码执行

执行对象、仓储、服务或 Controller 任务时，读取
[layer-implementation.md](references/layer-implementation.md)，并只应用与当前阶段有关的章节。

只有显式执行 `backend:bootstrap` 任务时，才额外读取
[bootstrap.md](references/bootstrap.md)。普通 endpoint 任务不得因发现依赖或配置缺失而自行扩大修改范围，应报告给既有 bootstrap/修复流程处理。
