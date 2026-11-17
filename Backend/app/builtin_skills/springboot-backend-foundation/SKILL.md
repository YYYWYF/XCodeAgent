---
name: springboot-backend-foundation
description: >-
  Prepare Java 8 Spring Boot module foundations for database and external API
  sources in the platform foundation stage.
---

# springboot-backend-foundation

## Prerequisite Checks Are Complete

**Dependencies and shared runtime configuration have already been checked by
preceding tasks. Do not repeat these checks.**

Do not read or search `pom.xml`, `application*.yml`, `application*.properties`, or
infrastructure configuration classes to recheck dependencies or runtime setup.
Reuse the completed setup and focus on the current task's implementation.

## Prepare Module Foundations

Use `implementation_contract.source_snapshots`, task paths and `execution_steps`
to identify the confirmed sources, module and types. Read target files first, using
the backend workspace context to locate them. Read related implementations only to
resolve unclear types or code relationships.

Prepare the shared foundations required by the current task. Preserve existing
implementations and their required dependencies; do not reduce them to empty
skeletons. Adding business methods, external operations, response mappings and
Controller routes belongs to later stages.

Finish when all required checks have a definite result.

## Source-specific Instructions

Read the references required by the confirmed source types, then follow their
instructions:

- Database: read [Database Foundation](references/foundation-database.md).
- External API: read [External API Foundation](references/foundation-external-api.md).
- Both: read both references, database first.
