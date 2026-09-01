---
name: springboot-backend-generate
description: >-
  Implement Java 8 Spring Boot backend integrations for confirmed database and external
  API data sources. Use during Build execution for `data_source_type: database` or
  `data_source_type: external_api`. Exclude static data, database schema changes,
  migrations, and seed data.
---

# springboot-backend-generate

Use this Skill only during Build execution for backend tasks whose referenced entity
designs include `data_source_type: database` or `data_source_type: external_api`. One or
more entity designs may be referenced, and their source types form the task's source-type
set. The platform has already selected the task, dependencies, write scope, and stage;
this Skill governs implementation only and must not redesign the task plan.

## Execution Boundaries

- Treat `implementation_contract` as the only authority for implementation semantics.
  Treat task metadata such as `allowed_paths`, `change_scope`, `stage`, and `source_refs`
  as authoritative execution constraints. Return `contract_mismatch` before writing when
  the source binding or Endpoint contract is internally invalid.
- Implement only confirmed endpoint operations. Do not invent CRUD behavior, URLs,
  headers, fields, mappings, pagination, persistence behavior, or response semantics.
- Write only the task's `allowed_paths` and declared `change_scope`. Do not modify formal
  artifacts, the task DAG, API or Entity contracts, database schemas, migrations, seed
  data, or unrelated modules.
- Reuse the existing Maven project, base package, and compatible project abstractions. Do
  not replace an already satisfying persistence or HTTP implementation solely to migrate
  technology.
- Keep persistence and external transport models separate from internal API DTOs. Locate
  and reuse the template's response, pagination, exception, handler, and CORS classes in
  `common` as read-only dependencies.
- Keep all generated code compatible with Java 8. Do not use `record`, `var`, `List.of`,
  text blocks, `String.isBlank`, or other Java 9+ syntax or APIs.

Before the first write, read every instruction path listed by the task, all current target
files, and the nearest relevant implementation. Leave a fully satisfying target unchanged
and return `already_satisfied` with concrete evidence. Make only the minimum in-scope
correction when the target is partial. Return `change_request` when the confirmed contract
cannot fit the authorized scope; never expand scope autonomously.

## Data Source and Mode Routing

Derive the set of source types from all entity designs referenced by the current task. Do
not collapse a mixed task into a single source type.

Classify each referenced entity design from its confirmed `data_source_type`; do not infer
or override that value from the files, dependencies, annotations, or client libraries
found in the workspace:

- **Database** means the entity's business data is read from or written to the
  application's bound database. Its `database_design`, including the confirmed table and
  field bindings, must be consistent with that classification when the current operation
  requires persistence.
- **External API** means the entity's business data is obtained from or changed through
  an outbound call to a confirmed upstream operation in `external_api_design`. The fact
  that this application exposes an HTTP Controller or Endpoint does not make the source
  an external API.
- **Mixed** means the referenced entity-design set contains at least one `database` source
  and at least one `external_api` source. Route each member by its own source type and load
  both selected references in the required stable order.

Return `contract_mismatch` instead of guessing when `data_source_type` is missing, unknown,
or inconsistent with its source-specific design or the current Endpoint operation.

The `implementation_contract.kind` field accepts the following values:

- **Bootstrap** is a project-level dependency and shared-capability check. It verifies the
  Maven dependencies and global configuration required by the selected source types,
  injects only genuinely missing authorized infrastructure, and never implements an
  endpoint operation or generates endpoint-specific business code.
- **Endpoint Implementation** implements one confirmed endpoint operation through the
  task's current layer or stage. It may generate or update operation-specific persistence
  or external transport code, mappings, services, and Controllers, but it must consume
  bootstrap capabilities without changing Maven dependencies or global configuration.

The declared `implementation_contract.kind`, task purpose, `allowed_paths`, and
`change_scope` must agree with this boundary. Return `contract_mismatch` before loading a
mode reference or writing when they conflict. When they agree, use
`implementation_contract.kind` to choose the reference for each source type in the set.
Read only the selected references, in stable `database` then `external_api` order. A mixed
task reads both selected references but reads this `SKILL.md` only once. Reject `static` or
any unknown source type before writing.

### Database

- For `implementation_contract.kind=bootstrap`, read
  [database/bootstrap.md](references/database/bootstrap.md).
- For `implementation_contract.kind=endpoint`, read
  [database/layer-implementation.md](references/database/layer-implementation.md) and
  apply only the section for the current `stage`.

Prefer existing MyBatis-Plus conventions. Endpoint tasks must not edit Maven dependencies,
data source configuration, or global MyBatis-Plus configuration.

### External API

- For `implementation_contract.kind=bootstrap`, read
  [external-api/bootstrap.md](references/external-api/bootstrap.md).
- For `implementation_contract.kind=endpoint`, read
  [external-api/layer-implementation.md](references/external-api/layer-implementation.md)
  and apply only the section for the current `stage`.

Prefer Spring Cloud OpenFeign for a new Client, but preserve an already satisfying
RestTemplate, WebClient, or project-approved abstraction. Endpoint tasks must not edit
Maven dependencies, the application entrypoint, or global OpenFeign activation.
