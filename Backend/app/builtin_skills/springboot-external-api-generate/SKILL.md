---
name: springboot-external-api-generate
description: Generate Java 8 Spring Boot backend integrations for confirmed external APIs with RestTemplate, typed transport boundaries, mapping, application services, and internal controllers without persistence code.
---

# springboot-external-api-generate

Use this skill only during Build execution for a backend task whose
`source_refs.entity_designs` contains `data_source_type: external_api`. The platform has
already selected the task, dependencies, write scope, and layer; this document governs
implementation only and must not be used to redesign the task plan.

## Authoritative inputs and scope

- Treat `implementation_contract.entities[].source_binding`, the
  endpoint-scoped `operations[]`, and `implementation_contract.api_contract` as the only
  business authority. `operations[]` must contain exactly one operation linked to the
  current endpoint; otherwise return `contract_mismatch` before writing.
- Preserve each operation's stable `operation_id`, effective
  `base_url_config_key`, HTTP method and path, typed Path/Query parameters, merged
  non-sensitive headers, request/response shapes, payload/cardinality semantics, and
  confirmed field mappings. Never invent a URL, credential, header, response field, or
  mapping.
- Write only the task's `allowed_paths` and declared `change_scope`. Do not modify formal
  planning artifacts, the task DAG, API or Entity contracts, database schema, migrations,
  seed data, or unrelated modules. If the confirmed contract cannot fit the scope, return
  the execution protocol's `change_request` instead of expanding the scope.
- Keep the external transport model separate from internal API DTOs. Conversion belongs in
  a mapper/assembler boundary, not in the Controller or raw HTTP client.
- Treat `request_shape` and `response_shape` as type/field descriptions derived from design
  examples. Never hard-code example keywords, page numbers, page sizes, identifiers, prices,
  timestamps, or other sample scalar values. Bind internal request fields to upstream Path,
  Query, and body fields only by exact name; a required unmatched field is a
  `contract_mismatch`, not permission to invent a value.

## Required operation sequence

Execute the confirmed operation in this order without skipping or reordering semantics:

1. Locate the single endpoint-linked operation and read its effective connection.
2. Resolve the Base URL through `base_url_config_key`, then construct the encoded URI from
   the confirmed method, path, Path parameters, and Query parameters.
3. Create the typed request transport DTO from `request_shape`, bind exact-name internal
   request inputs, and apply only confirmed non-sensitive headers.
4. Call the upstream API with RestTemplate and deserialize the declared response root into
   typed transport DTOs derived from `response_shape`.
5. Accept only `success_status_codes`; translate other HTTP responses, declared
   `error_message_path`, timeouts, and deserialization failures through existing exceptions.
6. Traverse `mapped_entity_path` when present and apply every `field_mappings` entry exactly.
7. Return the internal API Contract response through the application service and Controller.

## Mandatory HTTP client: RestTemplate

- Every external request MUST use Java 8-compatible Spring
  `org.springframework.web.client.RestTemplate`. Do not use WebClient, Feign, OkHttp,
  Apache HttpClient directly, `HttpURLConnection`, raw sockets, or a hand-written HTTP
  client.
- Reuse an existing compatible `RestTemplate` Bean and its project-approved error,
  serialization, and timeout conventions when one is available. If none exists, add the
  smallest integration-local Bean/adapter only when that path is already authorized by
  the current task. If a new shared configuration path is required but unauthorized,
  return a scope/contract change request; never widen the task silently.
- Persist the confirmed `effective_connection.base_url` directly under
  `base_url_config_key` in the authorized Spring Boot YAML/properties file. Use a plain
  scalar value; never wrap it in `${ENV_NAME:default}`, derive an environment-variable
  name, or emit another placeholder. For example, `base_url_config_key=product.url` and
  `effective_connection.base_url=http://99.17.197.63:8090` must produce:

  ```yaml
  product:
    url: http://99.17.197.63:8090
  ```

  Java code must still read the URL through the configuration key rather than a Java
  constant. Build the request URI with the project convention (prefer
  `UriComponentsBuilder`) so Path and Query parameters are typed, encoded, and passed
  exactly once.
- Apply only the confirmed non-sensitive headers and request body. Do not add
  authentication, credential, cookie, tracing, or arbitrary headers. Configure the
  confirmed connect/read timeout and preserve the project's centralized configuration
  boundary.
- Use typed `exchange`/equivalent `RestTemplate` calls with request and response DTOs;
  preserve object, array, envelope, pagination, and `entity_payload` cardinality instead
  of converting responses to untyped maps. Use Java 8 syntax and APIs only.
- Keep JSON property spelling exactly as declared. When Java naming differs, use the
  project's Jackson annotation convention instead of renaming the transport property.

## Layer responsibilities

Keep the approved layer boundaries independent:

1. The upstream layer owns the RestTemplate adapter/client, transport request/response DTOs,
   URI construction, headers, timeout, and upstream error translation.
2. The mapping layer owns field conversion, payload-path traversal, array/cardinality
   normalization, and internal entity mapping only when `entity_payload=true`.
3. The application service owns endpoint-facing orchestration and business decisions; it
   does not issue HTTP calls directly when an upstream adapter exists.
4. The Controller owns the confirmed internal HTTP method/path, request validation,
   response envelope/status mapping, and delegation to the application service.

Operations with `entity_payload=false` are acknowledgement/status responses: preserve their
declared response semantics and do not create an Entity/PO or an invented mapper. When the
same `operation_id` is referenced by multiple internal endpoints, reuse one RestTemplate
client method and one compatible transport DTO set; do not duplicate adapters.

For entity responses, `mapped_entity_path` is derived deterministically from confirmed
`source_field` paths and takes precedence for record extraction. When it is `list[]`, keep
the response root DTO as an object, traverse its `list` collection, and map every element.
When it is empty, apply each confirmed source path directly; never guess another envelope or
array. `field_mappings` is the only assignment authority. Map entity `decimal` fields with
`BigDecimal`; map `datetime` through the project's existing Java time and Jackson format;
map `enum` only to the confirmed `enum_values`; and use project-compatible Java 8 types for
other integer, boolean, string, object, and array fields.

Implement pagination only when `response_handling.pagination` and `total_path` are both
declared. Familiar names such as `total`, `current`, `pageSize`, `list`, or `items` do not by
themselves authorize page semantics.

## Error and safety behavior

- Map upstream HTTP errors, connection/read timeouts, and deserialization failures through
  the existing project exception/error conventions. Do not swallow errors, return a fake
  success, or expose upstream credentials or raw sensitive response data.
- This current contract is public/no-auth only. Reject or surface a change request for
  authentication requirements, sensitive headers, missing Base URL/configuration key,
  missing request/response semantics, or an operation that is not linked to the current
  endpoint.
- Do not create Entity/PO, MyBatis Mapper, Mapper.xml, Repository, datasource config,
  migration, seed SQL, table-management code, tests, build commands, or verification tasks.

## Execution protocol

Before the first write, read the task's required instruction files, every current target,
and the nearest relevant implementation. Reuse the real package structure and naming.
For an existing target, leave it unchanged when it fully satisfies the contract and make
only the minimum correction when it is partial. For an absent target, create it directly
from the confirmed contract. Return `already_satisfied` with evidence when no write is
needed; otherwise return the exact structured result required by the outer Build workflow.
