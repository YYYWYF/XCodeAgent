# External API Layer Implementation Details

## Execution Scope

Use this reference when the task lists it in `instruction_paths` for external API work.
Implement only the declared `stage` for the scoped endpoints. Bootstrap owns Maven
dependencies and global `@EnableFeignClients` activation.

## Stage Responsibilities

- **Foundation** prepares the module's shared Client skeletons and connection configuration.
  Reuse compatible Clients and preserve existing methods. This stage adds no Operation
  methods or transport DTOs.
- **Business** implements one internal Endpoint. It reuses or adds the concrete Client
  method and complete transport DTOs alongside the Service, internal API DTOs, and any
  Domain types required by the confirmed logic. Upstream helpers adapt transport. Add a
  Converter only for confirmed transport adaptation. The Service calls through the Client
  or compatible adapter and owns `externalApiBindings`, `fieldMappings`, source-path and
  array mapping, and final response composition. Prefer this stage for the module ErrorCode
  and endpoint-facing `BizException`, following the module's existing convention.
- **Controller** runs after the module's scoped business tasks. Use their completed Service
  signatures and internal DTOs for the confirmed HTTP method and path, request validation,
  response envelope and status, and endpoint-specific authorization. Delegate to the Service.
  The upstream signature does not change the internal HTTP contract.

## Authoritative Inputs and Scope

- Read `implementation_contract.api_design` for the current confirmed Endpoint design in
  the business task. An external `sourceBinding` identifies the selected Operation.
  Match confirmed external Operations to `sourceSnapshots` by `sourceId`,
  `details.directoryId`, and `details.operation.id`. Read `details.connection` and the
  complete `details.operation` from the matching entry. `externalApiBindings` owns
  request values and `fieldMappings` owns application response values. The internal
  `implementation_contract.api_contract` owns the Controller contract.
- Preserve each Operation's stable `id`, `connection.baseUrlConfigKey`, HTTP method
  and path, complete `pathParameters`, `queryParameters`, operation-level `headers`,
  and `requestStructure`/`responseStructure`, together with confirmed field mappings.
  Never invent a URL, credential, header, response field, or mapping.
- Write only `allowed_paths` and `change_scope`. If the confirmed contract cannot fit the
  scope, return the execution protocol's `change_request` instead of expanding it.
- Keep transport models separate from internal API DTOs. Upstream helpers perform
  transport adaptation only; service owns business mappings and final response composition.
- Treat `requestStructure` and `responseStructure` as complete field/type structures,
  never literal examples. Retain optional and unmapped fields and parameters.
  Every operation-level Header has one explicit typed Client method parameter.
  Apply `externalApiBindings` at the service call site using the exact target identity;
  application and external parameter names may differ. Never infer same-name mappings.

## Business Implementation Sequence

1. Locate the selected external Operation in `api_design.sourceSnapshots` and read
   `details.connection` and the complete `details.operation`.
2. Use the prepared Client for the exact `details.connection.baseUrlConfigKey`.
3. Create complete typed request/response transport DTOs, envelopes, and nested objects
   from `requestStructure` and `responseStructure`; do not flatten body leaves into
   separate method arguments.
4. Declare all Path, Query, and operation-level Header parameters, including optional
   and unmapped parameters. Service computes confirmed `externalApiBindings` values
   before calling the Client; upstream binds those already-computed values.
5. Call through the preferred typed OpenFeign interface, or a compatible
   already-satisfying project Client, and deserialize the declared response root into typed
   transport DTOs derived from `responseStructure`.
6. Preserve confirmed error semantics through existing exceptions for upstream HTTP
   failures, timeouts, and deserialization failures; never invent status or error mappings.
7. Service applies every confirmed `fieldMappings` entry after the response is available,
   preserving declared source paths and array-element correspondence.
8. Return the confirmed internal response type from the Service for the final Controller task.

## Client Implementation

### Foundation Client Skeleton and Connection

- Prefer a Java 8-compatible typed `@FeignClient` interface for every newly created external
  Client. Do not reject or rewrite an existing RestTemplate, WebClient, or project-approved
  abstraction solely because it is not Feign when it already satisfies the operation.
- Use `details.connection` from `implementation_contract.source_snapshots`. Reuse a compatible
  Client for the same source and connection or create the smallest authorized Feign interface
  and client-local configuration. Return a change request if the required shared Feign
  capability is missing.
- Persist `details.connection.baseUrl` directly under `details.connection.baseUrlConfigKey`
  in the authorized Spring Boot YAML/properties file as a plain scalar. Reference the exact
  key in `@FeignClient` with `${property.key}`. Never wrap the stored value in
  `${ENV_NAME:default}`, derive an environment variable, or place the URL in Java.
- Keep domain-level shared headers on their existing shared-configuration path;
  do not turn them into additional operation-level mapped parameters.
- Configure confirmed connect/read timeouts through the project's existing client-scoped
  Feign convention. Preserve the declared millisecond value; do not guess another unit.

### Business Operation Method and Transport

- Reuse a compatible method for the same Operation identity or add the concrete method
  to the prepared Client.
- Declare the upstream method/path with the exact Spring MVC mapping annotation. Bind typed
  Path, Query, header, and body values with `@PathVariable`, `@RequestParam`,
  `@RequestHeader`, and `@RequestBody` as required by the confirmed operation.
- Use explicit binding names and preserve required flags. Optional Query/Header
  bindings use `required=false`. Every operation-level Header remains a separate
  typed method parameter even without a value mapping. Optional scalar arguments
  must be able to represent absence. Keep optional targets in the signature and omit them
  from the HTTP request when their value is absent. Preserve supplied false, zero, the
  empty string, and explicit null where the confirmed contract supports it. Required
  targets need confirmed value rules. Never send the literal null string or invent defaults.
- Use complete typed request and response DTOs. Preserve the declared object, array,
  envelope, and pagination structure instead of using untyped maps or `Object`.
- Preserve JSON property spelling with the project's Jackson annotation convention.
- Reuse an existing Feign `ErrorDecoder`/exception convention. Add a client-local decoder
  only when required by confirmed error semantics and authorized by `allowed_paths`.

Preserve confirmed acknowledgement/status semantics without inventing an Entity, PO,
or Converter. When multiple internal Endpoints reference the same external Operation,
reuse one complete Client method and compatible transport DTO set; their different
value mappings never authorize shrinking that shared method's signature.

## Template `common` Infrastructure

Template initialization injects shared response, pagination, business-exception, global
exception-handler, and CORS classes. Locate their real files and packages before writing;
treat them as read-only template dependencies.

- The confirmed internal Endpoint `response_schema_ref` describes the business body type
  `T`, not the HTTP JSON root. Controllers return that DTO through the template
  `common.response.ResponseEntity<T>` via `success()` / `success(body)`. Do not add
  `returnCode`, `errorMsg`, or `body` fields to the business DTO or API Contract, and do not
  accidentally import `org.springframework.http.ResponseEntity` or create another wrapper.
- Use `PageParam` and `PageResult<T>` only when the confirmed internal Endpoint declares
  pagination. Their existence never authorizes new pagination semantics.
- Translate confirmed upstream failures through the existing concrete `IErrorCode` and
  `BizException` flow so `BaseExceptionHandler` owns the public failure response. Do not
  catch `BizException` or call `ResponseEntity.failed` in the Controller, and never invent
  an error code. Follow the shared Endpoint error-handling guide for code authority,
  message generation, parameter formatting, and Service-first exception ownership.
- Reuse existing CORS configuration. Do not add `@CrossOrigin`, another
  `WebMvcConfigurer`, or edits to template `common` files.

The API Contract remains authoritative for the internal business schemas and status
behavior. `ResponseEntity<T>` is the separate fixed transport contract, so wrapping the
confirmed response DTO is not a schema mismatch. If the actual common class cannot carry
the confirmed `T`, do not modify shared template code; return a contract mismatch/change
request when the task cannot satisfy both within scope.

## Mapping and Pagination

Use current `api_design.fieldMappings` as the response assignment authority. Service
applies the confirmed source paths, value rules, and missing policies after the upstream
response is available. Preserve all declared array elements and the complete transport
response structure. Never guess another envelope or array.

Use project-compatible Java 8 types and the existing Jackson convention for declared
types. Implement pagination only when the internal API contract and confirmed mappings
declare it; familiar names such as `total`, `pageSize`, `list`, or `items` do not authorize
pagination by themselves.

## Error and Safety Behavior

- Prefer the Application Service for the final endpoint-facing `BizException`. An upstream
  adapter should preserve a failure in the project's non-public transport form and must not
  expose or turn a raw upstream message into the public `errorMsg`. Throw at the adapter
  boundary only when the mapping is complete there and the existing module follows that
  convention.
- Do not swallow upstream HTTP errors, timeouts, or decode failures; do not return fake
  success or expose credentials/raw sensitive payloads.
- External Operations currently support public/no-auth only. Apply only confirmed
  non-sensitive headers. Do not add authentication, credentials, cookies, tracing,
  arbitrary interceptors, or unconfirmed headers. Return a change request for upstream
  authentication, sensitive headers, missing Base URL/configuration key, missing
  request/response semantics, or an operation not linked to the current Endpoint.
- In a mixed-source task, follow the database reference for the confirmed database branch
  in the current stage. Scope persistence classes, MyBatis Mapper/XML, and Repository files
  to that branch. Do not add datasource configuration, migrations, seed SQL, tests, build
  commands, or verification tasks.
- Return a change request rather than expanding scope when a required capability is absent.

## Java 8 and Project Constraints

- Keep generated code compatible with Java 8 and follow the actual workspace package,
  naming, annotation, and configuration conventions.
- Do not use `record`, `var`, `List.of`, text blocks, `String.isBlank`, or other Java 9+
  syntax or APIs.
- Add Chinese purpose comments to every new or materially changed class and method as
  required by the repository.

## Completion Criteria

- Implement only the current stage for its scoped endpoints and only within `allowed_paths` and
  `change_scope`.
- Leave a fully satisfying target unchanged and return `already_satisfied` with concrete
  evidence.
- Otherwise return the exact structured result required by the outer Build flow.
