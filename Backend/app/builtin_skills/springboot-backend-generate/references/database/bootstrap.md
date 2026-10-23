# Spring Boot + MyBatis-Plus Bootstrap

## Execution Scope

Read this file only when the current task is explicitly `backend:bootstrap` and its
modification scope includes the relevant project files. Normal `backend:endpoint:*`
tasks must not perform the checks or modifications described here.

Read the existing `pom.xml`, `application.yml`, and configuration classes first. Keep
existing configuration unchanged and add only the capabilities that are both required
to run database-backed code and genuinely missing.

Template initialization has already injected shared response, pagination, exception
handling, and CORS infrastructure under `common`. The bootstrap task may read these
classes to assess project capabilities, but it must not modify, copy, or regenerate
them. This task remains responsible only for Maven, data source, and MyBatis-Plus
infrastructure.

## Dependency Baseline

Confirm that the project already provides the following capabilities, preferring its
existing dependencies and version management:

- `mybatis-plus-boot-starter`
- The JDBC driver for the target database (MySQL by default)
- `lombok` when the project already uses Lombok
- `mapstruct` and its annotation processor when the project already uses MapStruct

Add a dependency only when it is missing and the project has no equivalent solution.
Acceptable defaults are MyBatis-Plus `3.5.2`, MySQL Connector `8.2.0`, Lombok
`1.18.30`, and MapStruct `1.5.5.Final`. Existing centralized version management always
takes precedence.

If `maven-compiler-plugin` already defines `annotationProcessorPaths` and the project
uses both Lombok and MapStruct, `lombok-mapstruct-binding` `0.2.0` may be added to
resolve annotation-processing order.

## Project Configuration

### Data Source Configuration

Only tasks whose confirmed mappings use a database need this check. Use the
workspace-provided `get_mysql_config` tool to read the runtime configuration policy
bound by the platform to the task's confirmed source ID. The tool does not return
credentials and does not let you select another database.

- `status=ok`, `mode=builtin`: the simulated database is platform-managed. The launcher
  checks its resources and injects the SaaS runtime connection. Do not request a user
  connection, retrieve passwords, create tables, or enable automatic schema creation,
  Flyway, Liquibase, or SQL initialization.
- `status=ok`, `mode=direct` or `dbid`: the launcher resolves the current database from
  the application's data-source catalog and injects its connection at startup.
- `status=not_required`: do not add database configuration for this task.
- `status=not_configured` or `error`: if this task requires a database, report the
  missing or invalid source and ask for configuration and mapping confirmation through
  the existing workflow. Do not guess a connection or report successful completion.

Spring accepts `SPRING_DATASOURCE_URL`, `SPRING_DATASOURCE_USERNAME`, and
`SPRING_DATASOURCE_PASSWORD` directly from the process environment. Missing literal
values in YAML are not a configuration error. If an in-scope configuration file needs
explicit bindings, use exactly the `springDatasource` placeholders returned by the
tool, without hard-coded defaults. Preserve an existing equivalent runtime binding;
replace hard-coded connection values only within the approved write scope, otherwise
report the scope conflict. Never write real credentials to project files, task results,
or logs, and never read the Studio backend's own `.env`.

### MyBatis-Plus Pagination Configuration

Add a global interceptor only when the current endpoint uses MyBatis-Plus pagination
and the project has no equivalent configuration:

```java
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

Follow the workspace's existing `infrastructure/config` path and package conventions,
or their equivalent. Do not create a second pagination configuration when an interceptor
already exists.

## Completion Criteria

- Modify only genuinely missing project infrastructure that falls within the authorized scope of the current bootstrap task.
- Do not generate business Entity, Repository, Service, or Controller code.
- Do not create database tables, run migrations, add seed data, or perform other schema operations.
