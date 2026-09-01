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

Confirm that `application.yml` contains parseable `spring.datasource.url`, `username`,
and `password` values. When configuration is missing, use the workspace-provided
`get_mysql_config` tool to obtain the connection bound to the current application. Do
not guess credentials, read the backend service's own `.env`, or expose real credentials
in logs or task descriptions.

A typical JDBC URL may include UTF-8, time-zone, SSL, and public-key-retrieval
parameters, but prefer the existing project format and the `jdbc_url` returned by the
tool.

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
