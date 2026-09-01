# External API Layer Implementation Details

## Execution Scope

Read this reference only for `implementation_contract.kind=endpoint`. Implement only the
current task `stage`; do not perform bootstrap dependency or global activation work here.

## Authoritative Inputs and Scope

- Treat `implementation_contract.entities[].source_binding`, the endpoint-scoped
  `operations[]`, and `implementation_contract.api_contract` as the only business
  authority. `operations[]` must contain exactly one operation linked to the current
  endpoint; otherwise return `contract_mismatch` before writing.
- Preserve each operation's stable `operation_id`, effective `base_url_config_key`, HTTP
  method and path, typed Path/Query parameters, merged non-sensitive headers,
  request/response shapes, payload/cardinality semantics, and confirmed field mappings.
  Never invent a URL, credential, header, response field, or mapping.
- Write only `allowed_paths` and `change_scope`. If the confirmed contract cannot fit the
  scope, return the execution protocol's `change_request` instead of expanding it.
- Keep the external transport model separate from internal API DTOs. Conversion belongs in
  a mapper/assembler boundary, not in the Controller or HTTP Client.
- Treat `request_shape` and `response_shape` as field/type descriptions, never literal
  examples. Bind internal request fields to upstream Path, Query, and body fields only by
  exact name; a required unmatched field is a `contract_mismatch`.

## Required Implementation Sequence

1. Locate the single endpoint-linked operation and read its effective connection.
2. Resolve the Base URL through the exact `base_url_config_key`; for Feign use the
   mechanically equivalent `${property.key}` placeholder rather than a Java constant.
3. Create typed request transport DTOs from `request_shape`, bind exact-name inputs, and
   apply only confirmed non-sensitive headers.
4. Call through the preferred typed OpenFeign interface, or a compatible
   already-satisfying project Client, and deserialize the declared response root into typed
   transport DTOs derived from `response_shape`.
5. Accept only `success_status_codes`; translate other HTTP responses, declared
   `error_message_path`, timeouts, and deserialization failures through existing exceptions.
6. Traverse `mapped_entity_path` when present and apply every `field_mappings` entry exactly.
7. Return the internal API Contract response through the application service and Controller.

## Client Implementation

### Preferred HTTP Client: Spring Cloud OpenFeign

- Prefer a Java 8-compatible typed `@FeignClient` interface for every newly created external
  Client. Do not reject or rewrite an existing RestTemplate, WebClient, or project-approved
  abstraction solely because it is not Feign when it already satisfies the operation.
- Reuse an existing compatible Feign Client when it represents the same `operation_id`. If
  none exists, create the smallest interface and client-local configuration authorized by
  the upstream task. Missing shared Feign capability is a plan mismatch; endpoint tasks
  must not edit Maven dependencies or `@EnableFeignClients`.
- Persist `effective_connection.base_url` directly under `base_url_config_key` in the
  authorized Spring Boot YAML/properties file as a plain scalar. Never wrap the stored value
  in `${ENV_NAME:default}`, derive an environment variable, or place the URL in Java.
- Declare the upstream method/path with the exact Spring MVC mapping annotation. Bind typed
  Path, Query, header, and body values with `@PathVariable`, `@RequestParam`,
  `@RequestHeader`, and `@RequestBody` as required by the confirmed operation.
- Apply only confirmed non-sensitive headers. Do not add authentication, credentials,
  cookies, tracing, arbitrary interceptors, or unconfirmed headers.
- Configure confirmed connect/read timeouts through the project's existing client-scoped
  Feign convention. Preserve the declared millisecond value; do not guess another unit.
- Use typed request and response DTOs. Preserve object, array, envelope, pagination, and
  `entity_payload` cardinality instead of using untyped maps or `Object`.
- Preserve JSON property spelling with the project's Jackson annotation convention.
- Reuse an existing Feign `ErrorDecoder`/exception convention. Add a client-local decoder
  only when required by confirmed error semantics and authorized by `allowed_paths`.

## Layer Responsibilities

1. The upstream stage owns the preferred `@FeignClient` interface or compatible existing
   HTTP Client, transport DTOs, parameter binding, client-scoped timeout, Base URL property,
   and upstream error translation. It does not own Maven or global Feign activation.
2. The mapping stage owns field conversion, payload-path traversal, array/cardinality
   normalization, and internal entity mapping only when `entity_payload=true`.
3. The service stage owns endpoint-facing orchestration and business decisions; it does not
   issue HTTP calls directly when an upstream adapter exists.
4. The controller stage owns the confirmed internal HTTP method/path, request validation,
   response envelope/status mapping, and delegation to the application service.

For `entity_payload=false`, preserve acknowledgement/status semantics and do not create an
Entity, PO, or invented mapper. When multiple internal Endpoints reference the same
`operation_id`, reuse one Client method and compatible transport DTO set.

## Template `common` Infrastructure

Template initialization injects shared response, pagination, business-exception, global
exception-handler, and CORS classes. Locate their real files and packages before writing;
treat them as read-only template dependencies.

- When compatible with the confirmed internal response Schema, Controllers return the
  template `common.response.ResponseEntity<T>` through `success()` / `success(body)`. Do not
  accidentally import `org.springframework.http.ResponseEntity` or create another wrapper.
- Use `PageParam` and `PageResult<T>` only when the confirmed internal Endpoint declares
  pagination. Their existence never authorizes new pagination semantics.
- Translate confirmed upstream failures through the existing concrete `IErrorCode` and
  `BizException` flow so `BaseExceptionHandler` owns the public failure response. Do not
  catch `BizException` or call `ResponseEntity.failed` in the Controller, and never invent
  an error code.
- Reuse existing CORS configuration. Do not add `@CrossOrigin`, another
  `WebMvcConfigurer`, or edits to template `common` files.

The API Contract remains authoritative. If its response/status Schema is incompatible with
the injected common types, do not modify shared template code or force a wrapper; return a
contract mismatch/change request when the task cannot satisfy both within scope.

## Mapping and Pagination

For entity responses, `mapped_entity_path` is the record-extraction boundary. When it is
`list[]`, keep the response root DTO as an object, traverse its `list` collection, and map
every element. When it is empty, apply confirmed source paths directly. Never guess another
envelope or array.

`field_mappings` is the only assignment authority. Map `decimal` fields with `BigDecimal`,
`datetime` through the project's Java time/Jackson convention, and `enum` only to confirmed
`enum_values`. Use project-compatible Java 8 types for other fields.

Implement pagination only when `response_handling.pagination` and `total_path` are both
declared. Familiar field names such as `total`, `current`, `pageSize`, `list`, or `items` do
not authorize pagination by themselves.

## Error and Safety Behavior

- Do not swallow upstream HTTP errors, timeouts, or decode failures; do not return fake
  success or expose credentials/raw sensitive payloads.
- The current contract is public/no-auth only. Return a change request for authentication,
  sensitive headers, missing Base URL/configuration key, missing request/response semantics,
  or an operation not linked to the current Endpoint.
- Do not create Entity/PO persistence classes, MyBatis Mapper/XML, Repository, datasource
  configuration, migration, seed SQL, tests, build commands, or verification tasks.
- Return a change request rather than expanding scope when a required capability is absent.

## Java 8 and Project Constraints

- Keep generated code compatible with Java 8 and follow the actual workspace package,
  naming, annotation, and configuration conventions.
- Do not use `record`, `var`, `List.of`, text blocks, `String.isBlank`, or other Java 9+
  syntax or APIs.
- Add Chinese purpose comments to every new or materially changed class and method as
  required by the repository.

## Completion Criteria

- Implement only the current endpoint stage and only within `allowed_paths` and
  `change_scope`.
- Leave a fully satisfying target unchanged and return `already_satisfied` with concrete
  evidence.
- Otherwise return the exact structured result required by the outer Build flow.
