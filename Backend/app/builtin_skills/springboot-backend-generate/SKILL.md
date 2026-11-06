---
name: springboot-backend-generate
description: >-
  Implement Java 8 Spring Boot backend integrations for confirmed database and external
  API data sources. Use during Build execution for `data_source_type: database` or
  `data_source_type: external_api`. Exclude static data, database schema changes,
  migrations, and seed data.
---

# springboot-backend-generate

Use this Skill for the Build Java backend tasks that list it in instruction_paths.
The platform selects the stage, source types, dependencies, and exact write scope.

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

## Stages and References

Read the files in the task's instruction_paths, using the current stage to select the
relevant sections. Source types come from confirmed Endpoint API Design snapshots.
A mixed business task implements both confirmed physical branches for one endpoint.

- **Bootstrap** prepares Maven and global runtime capabilities for its declared
  source types.
- **Foundation** creates or reuses the module's PO, BaseMapper interfaces and injected
  Repository skeletons without business methods. For external sources it prepares
  configured FeignClient interfaces without operation methods.
- **Business** completes one endpoint's Service, contract DTOs, necessary Domain
  types, concrete Repository methods or upstream call. Reuse shared methods from
  prior tasks. Keep PO and upstream transport types internal.
- **Controller** adapts the scoped endpoints to HTTP after their Services exist.
  Apply each endpoint's own route, validation, response wrapper and authorization.

For database or external API work use the corresponding bootstrap or layer reference
listed in instruction_paths. Use the shared error handling guide for business tasks.
Use the Controller template for the final route task. See the execution packet for
the verified input scope, source paths, and structured result contract.
