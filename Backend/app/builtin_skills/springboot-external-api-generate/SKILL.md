---
name: springboot-external-api-generate
description: Generate Java 8 Spring Boot backend integrations for entities backed by confirmed external APIs, including upstream client adapters, mapping, application services, and internal controllers without introducing persistence code.
---

# springboot-external-api-generate

Use this skill only for backend tasks whose `source_refs.entity_designs` contain
`data_source_type: external_api`.

## Required implementation boundary

- Treat the confirmed `external_api_design` and API contract as authoritative. Preserve the upstream method, path, request/response shape, and every confirmed field mapping.
- Reuse the project's existing HTTP client, configuration, exception, timeout, and serialization conventions discovered in the workspace. If no convention exists, use a small Java 8-compatible Spring `RestTemplate` adapter through centralized configuration; do not introduce WebClient, records, or an unapproved client framework.
- Keep upstream request/response DTOs and transport code separate from the internal API contract. Put field conversion and normalization in a mapper/assembler or equivalent boundary class.
- Generate only the layers needed for an external integration: upstream DTO/client or gateway, mapping/assembler, application service, and the internal REST controller that implements the confirmed contract.
- The integration must not read or write a database for the external entity. Do not create Entity/PO, Mapper, Mapper.xml, Repository, migration, seed SQL, datasource configuration, or table-management tasks.
- Follow existing package names, naming, error handling, authentication/configuration placeholders, and endpoint conventions. Never invent credentials, base URLs, headers, or response fields when the confirmed design does not provide them; surface a change request for missing implementation facts.

## Task sequencing

Keep independent source adapters separate from the endpoint-facing orchestration. A typical chain is:

1. upstream DTO and HTTP client/gateway;
2. field mapping/assembler;
3. application service that calls the adapter;
4. internal controller implementing the confirmed API contract.

Each task owns only its layer's files and passes its exact produced paths, class names,
and contracts to the next task. Use Java 8 syntax and APIs only. Do not add tests,
build commands, or verification tasks to the generated task plan; the outer workflow
owns verification.
