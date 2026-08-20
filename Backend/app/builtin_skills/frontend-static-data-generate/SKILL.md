---
name: frontend-static-data-generate
description: Generate frontend in-memory data modules for confirmed static entities and endpoint contracts without creating backend APIs or scattering business data in pages.
---

# frontend-static-data-generate

Use this skill only for frontend tasks in `frontend:data:*` Units whose
`source_refs.entity_designs` contain `data_source_type: static`.

## Required implementation boundary

- Treat the confirmed static entity design and API contract as authoritative. Implement only the declared fields, operations, request shapes, response shapes, and seed values.
- Put all runtime records in a business API module under `/frontend/src/apis/<business>Api.ts` (or the existing project-equivalent module). Keep the collection module-scoped and expose typed asynchronous functions for the confirmed list, detail, create, update, and delete operations.
- Use a small deterministic delay helper when the template convention expects simulated network latency. Preserve the contract's response envelope, pagination, filtering, and mutation semantics.
- Pages and components must import these functions; they must not contain standalone business-data arrays, duplicate mutation logic, or direct data fabrication.
- Do not create or modify backend files, Spring/MyBatis classes, database migrations, HTTP clients, proxy configuration, `src/apis/service.ts`, or mock plugins. Static data is a frontend implementation, not a backend endpoint.
- Reuse the existing frontend template, TypeScript aliases, naming, and error-handling conventions. Keep shared scaffold files read-only except for the explicitly allowed menu append when a page task requires it.

## Task sequencing

The `frontend:data:*` task should own the static API module and its types/constants
that are not already present. A page task consumes that module through imports and
does not copy its records. Keep task change scopes exact, use `owner: frontend`,
and leave tests, builds, and verification to the outer workflow.
