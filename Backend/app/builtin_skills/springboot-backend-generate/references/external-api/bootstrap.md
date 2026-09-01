# Spring Cloud OpenFeign Bootstrap

## Execution Scope

Read this reference only for `implementation_contract.kind=bootstrap` when
`capabilities` contains `spring_cloud_openfeign`. The task is an idempotent shared-capability
check; it must not generate endpoint Clients, transport DTOs, mappings, services, or
Controllers.

## Dependency Baseline

The current injected backend template is Java 8 with Spring Boot `2.7.2`. Its corresponding
Maven baseline is:

- property `spring-cloud.version` = `2021.0.3`;
- dependency `org.springframework.cloud:spring-cloud-starter-openfeign` without a direct
  version;
- imported BOM `org.springframework.cloud:spring-cloud-dependencies` at
  `${spring-cloud.version}` with `type=pom` and `scope=import`.

Read the real `pom.xml` before editing. If this exact or an equivalent compatible
dependency/BOM is already present, reuse it and do not add a duplicate. Preserve the
existing dependency order, formatting, properties, plugins, and unrelated versions. When
the template still uses Spring Boot `2.7.2` and the capability is absent, add only the
missing property, starter, and BOM elements from the baseline. Do not add a version directly
to `spring-cloud-starter-openfeign` when the BOM manages it.

If the real project has diverged to another Spring Boot/Spring Cloud baseline, reuse its
already compatible OpenFeign management when present. Do not guess a new compatibility
matrix or replace an existing BOM; return a plan/contract change request when the approved
paths and current contract do not provide a safe compatible dependency choice.

## Project Configuration

### Global Activation

Read the real Spring Boot application entrypoint and any existing Feign configuration. If
Feign scanning is already enabled, keep it unchanged. Otherwise add the Java 8-compatible
`org.springframework.cloud.openfeign.EnableFeignClients` annotation to the existing
`@SpringBootApplication` class authorized by the task. Do not create a second application
entrypoint.

If the approved task instead owns an existing integration configuration class, activation
may be placed there only when its component-scan location covers the generated Clients. Do
not edit template `common` infrastructure, endpoint source, runtime Base URL entries, or
client-specific timeout/error configuration from bootstrap.

## Completion Criteria

- Modify only the exact `pom.xml` and Feign activation path listed in `allowed_paths` and
  `change_scope`.
- Leave fully satisfying files unchanged and return `already_satisfied` with concrete
  dependency and activation evidence.
- Do not install dependencies, run Maven, or start the backend; outer integration testing
  owns verification.
