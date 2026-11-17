---
name: springboot-backend-bootstrap
description: >-
  Check Java 8 Spring Boot Maven and data source configuration for the platform
  backend:bootstrap task.
---

# springboot-backend-bootstrap

## Check Shared Maven Dependencies

Read `pom.xml` with `read_file(limit=300)`; read additional pages only if its content
is incomplete.
Inspect its inherited dependency management. When the project uses
Lombok or MapStruct, check the corresponding dependencies:

- `org.projectlombok:lombok`, version `1.18.24`, scope `provided`.
- `org.mapstruct:mapstruct`, version `1.4.2.Final`.

## Check Maven Compiler Plugin Configuration

Inspect `maven-compiler-plugin` and its `annotationProcessorPaths` in `pom.xml`.
When the project uses Lombok or MapStruct, check the corresponding processors:

- `org.projectlombok:lombok`, version `1.18.24`.
- `org.mapstruct:mapstruct-processor`, version `1.4.2.Final`.
- When using both:
  `org.projectlombok:lombok-mapstruct-binding`, version `0.2.0`.

Keep existing compatible entries; add only missing dependencies and annotation
processor paths as needed.

## Source-specific Instructions

Read the references required by the approved source types in
implementation_contract, then follow their instructions:

- Database: read [Database Bootstrap](references/bootstrap-database.md).
- External API: read [External API Bootstrap](references/bootstrap-external-api.md).
- Both: read both references, database first.

The shared checks above apply to both references.

Finish when all applicable checks have a definite result. Existing configuration
that satisfies a check needs no further exploration or changes.
