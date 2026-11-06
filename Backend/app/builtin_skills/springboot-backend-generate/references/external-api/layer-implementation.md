# External API Layer Implementation Details

## Execution Scope

Read this reference only for `implementation_contract.kind=endpoint`. Implement only the
current task `stage`; do not perform bootstrap dependency or global activation work here.

## Authoritative Inputs and Scope

- Read `implementation_contract.api_design` for the current confirmed Endpoint design.
  `sourceBinding` identifies the selected Operation; each external `sourceSnapshots`
  entry provides its source identity, `details.directoryId`, `details.connection`,
  and complete `details.operation`. `externalApiBindings` owns request values and
  `fieldMappings` owns application response values. The internal
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

## Required Implementation Sequence

1. Locate the selected external Operation in `api_design.sourceSnapshots` and read
   `details.connection` and the complete `details.operation`.
2. Resolve the Base URL through the exact `connection.baseUrlConfigKey`; for Feign use the
   mechanically equivalent `${property.key}` placeholder rather than a Java constant.
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
8. Return the internal API Contract response through the application service and Controller.

## Client Implementation

### Preferred HTTP Client: Spring Cloud OpenFeign

- Prefer a Java 8-compatible typed `@FeignClient` interface for every newly created external
  Client. Do not reject or rewrite an existing RestTemplate, WebClient, or project-approved
  abstraction solely because it is not Feign when it already satisfies the operation.
- Reuse an existing compatible Feign Client when it represents the same Operation identity. If
  none exists, create the smallest interface and client-local configuration authorized by
  the upstream task. Missing shared Feign capability is a plan mismatch; endpoint tasks
  must not edit Maven dependencies or `@EnableFeignClients`.
- Persist `details.connection.baseUrl` directly under `details.connection.baseUrlConfigKey` in the
  authorized Spring Boot YAML/properties file as a plain scalar. Never wrap the stored value
  in `${ENV_NAME:default}`, derive an environment variable, or place the URL in Java.
- Declare the upstream method/path with the exact Spring MVC mapping annotation. Bind typed
  Path, Query, header, and body values with `@PathVariable`, `@RequestParam`,
  `@RequestHeader`, and `@RequestBody` as required by the confirmed operation.
- Use explicit binding names and preserve required flags. Optional Query/Header
  bindings use `required=false`. Every operation-level Header remains a separate
  typed method parameter even without a value mapping. Optional scalar arguments
  must be able to represent absence. An absent target is omitted from the HTTP request;
  preserve explicitly supplied null only where the confirmed contract supports it,
  and preserve false and zero. Never substitute the literal null string or invent defaults.
- Keep domain-level shared headers on their existing shared-configuration path;
  do not turn them into additional operation-level mapped parameters.
- Apply only confirmed non-sensitive headers. Do not add authentication, credentials,
  cookies, tracing, arbitrary interceptors, or unconfirmed headers.
- Configure confirmed connect/read timeouts through the project's existing client-scoped
  Feign convention. Preserve the declared millisecond value; do not guess another unit.
- Use complete typed request and response DTOs. Preserve the declared object, array,
  envelope, and pagination structure instead of using untyped maps or `Object`.
- Preserve JSON property spelling with the project's Jackson annotation convention.
- Reuse an existing Feign `ErrorDecoder`/exception convention. Add a client-local decoder
  only when required by confirmed error semantics and authorized by `allowed_paths`.

## Layer Responsibilities

1. The upstream stage is one task and one module. It owns transport request/response DTOs,
   envelopes and nested objects; an upstream-local Converter when confirmed transformation
   exists; the preferred `@FeignClient` interface or compatible existing HTTP Client;
   parameter binding, client-scoped timeout, Base URL property, and upstream error
   translation. Implement transport objects first, then Converter and Client. It does not
   own Maven or global Feign activation, and it must not create an empty Converter.
2. Upstream helpers own transport-only adaptation. The service stage owns
   `externalApiBindings`, `fieldMappings`, business conversion, source-path traversal,
   array-element mapping, and final application response composition.
3. The service stage owns endpoint-facing orchestration and business decisions. Prefer
   assigning the module ErrorCode file to this stage and throwing endpoint-facing
   `BizException` here; preserve an explicit existing module convention. The service does
   not issue HTTP calls directly when an upstream adapter exists.
4. The controller stage owns the confirmed internal HTTP method/path, request validation,
   response envelope/status mapping, and delegation to the application service.

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
