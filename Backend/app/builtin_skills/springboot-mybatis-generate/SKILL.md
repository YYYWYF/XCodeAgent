---
name: springboot-mybatis-generate
description: >-
  为已确认数据库实体规划或实现 Java 8 Spring Boot + MyBatis-Plus 分层后端代码。
  适用于 Entity/PO/DTO、Repository/Mapper、ApplicationService 和 Controller；
  不用于数据库建表、迁移、种子数据或外部 API 集成。
---

# springboot-mybatis-generate

基于当前 endpoint 的已确认实体设计、API Contract 和真实工作区结构，复用现有
Spring Boot 工程并生成数据库来源实体的后端代码。

## 核心边界

- `database` 表示实体的数据来源，当前 Skill 只负责后端代码，不执行表结构变更、迁移或种子数据。
- 实体字段、目标表和字段绑定以当前 `entity_designs[].database_design` 为准；接口方法、路径和 Schema 以当前 API Contract 为准。
- 仅实现当前确认的 endpoint 操作，不顺带生成契约之外的 CRUD 接口。
- 复用工作区现有 Maven 工程、基础包、公共分页类、异常模型和配置；已有文件应修改或复用，不能重复创建。
- endpoint 任务不得创建或修改全局依赖、数据源配置和 MyBatis 配置。只有当前任务明确属于 `backend:bootstrap` 时，才读取并执行 [bootstrap.md](references/bootstrap.md)。
- 所有代码兼容 Java 8，不使用 `record`、`var`、`List.of`、文本块、`String.isBlank` 或其他 Java 9+ 语法/API。

## 任务规划

任务规划阶段只使用本入口文档，不需要读取 references。每个数据库实体在允许的
`backend:endpoint:*` Unit 中按以下四阶段拆分，阶段之间串行依赖；每个任务的
`change_scope` 只能包含本阶段拥有的文件。

设基础包为 `{basePackage}`、模块为 `{module}`、类名为 `{Name}`：

1. 对象与映射
   - `src/main/java/{basePackage}/domain/{module}/entity/{Name}.java`
   - `src/main/java/{basePackage}/infrastructure/po/{Name}PO.java`
   - `src/main/java/{basePackage}/application/{module}/dto/{Name}DTO.java`
   - `src/main/java/{basePackage}/infrastructure/repository/converter/{Name}Converter.java`
   - `src/main/java/{basePackage}/application/{module}/assembler/{Name}Assembler.java`
2. 仓储
   - `src/main/java/{basePackage}/infrastructure/mapper/{Name}Mapper.java`
   - `src/main/resources/mapper/{Name}Mapper.xml`，仅在当前操作需要自定义 SQL 时创建或修改
   - `src/main/java/{basePackage}/domain/{module}/repository/{Name}Repository.java`
   - `src/main/java/{basePackage}/infrastructure/repository/impl/{Name}RepositoryImpl.java`
3. 应用服务
   - `src/main/java/{basePackage}/application/{module}/service/{Name}ApplicationService.java`
4. 接口
   - 复用或创建当前模块的 `src/main/java/{basePackage}/adapter/web/{Name}Controller.java`

应用层统一使用 `application` 包；若工作区已有不同包结构，先复用并精确迁移当前任务涉及的文件，不要创建第二套并行包结构。若某一层的现有文件已经覆盖当前 endpoint，只规划精确修改，不重复添加。

后一阶段任务描述必须写明前一阶段产出的类名、路径和可调用契约。不同实体可以在文件范围不重叠时并行；同一实体的四阶段保持顺序。

## 代码执行

执行对象、仓储、服务或 Controller 任务时，读取
[layer-implementation.md](references/layer-implementation.md)，并只应用与当前阶段有关的章节。

只有显式执行 `backend:bootstrap` 任务时，才额外读取
[bootstrap.md](references/bootstrap.md)。普通 endpoint 任务不得因发现依赖或配置缺失而自行扩大修改范围，应报告给既有 bootstrap/修复流程处理。
