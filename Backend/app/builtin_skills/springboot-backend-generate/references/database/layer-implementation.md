# MyBatis Layer Implementation Details

## Execution Scope

Read this reference only for `implementation_contract.kind=endpoint`. Implement only the
current task `stage`; do not perform bootstrap dependency, data source, or global MyBatis
configuration work here.

## Authoritative Inputs and Scope

- Treat `implementation_contract.entities[].source_binding`,
  `database_design.bindings`, and `implementation_contract.api_contract` as the business
  authority for entity fields, target tables, column bindings, endpoint behavior, and
  schemas. Do not rely only on matching names.
- Preserve the REST path from the confirmed API Contract. Do not rederive or override it
  from the table name.
- Convert a table such as `product_category` to the class name `ProductCategory` and module
  name `productCategory`; convert a column such as `category_name` to the field name
  `categoryName`.
- Write only `allowed_paths` and `change_scope`. Return a change request instead of
  expanding scope when the confirmed contract cannot fit the current task.

## Required Implementation Sequence

1. Read the confirmed source binding, field bindings, API Contract, current target files,
   and nearest implementation for the current layer.
2. Locate the injected `common` infrastructure and reuse its actual classes and packages.
3. Determine whether the current stage is already complete; leave it unchanged when it
   fully satisfies the contract.
4. Implement only the minimum missing work for the current object, repository, service, or
   Controller stage.
5. Return the exact structured completion, failure, or change-request result required by
   the outer Build flow.

## Persistence Implementation

- Bind the confirmed table and columns with MyBatis-Plus annotations; do not infer a schema
  change or persistence rule that is absent from the design.
- Prefer MyBatis-Plus for basic single-table operations. Use custom Mapper XML only when
  the current endpoint genuinely requires joins, aggregation, dynamic filters, or custom
  SQL.
- Follow existing project and table conventions for primary-key strategy, logical
  deletion, audit fields, and tenant filtering. Do not add them without evidence.

## Layer Responsibilities

### Entity

- Represent domain concepts and business fields. Derive the primary-key type from the
  confirmed field and table design.
- Do not expose persistence-only audit fields by default, but retain business identifiers
  such as `userId` and `orderNo`.
- Follow the project's existing Lombok style. When using MapStruct, provide writable
  properties or constructors.

### PO

- Bind the confirmed target table with `@TableName`, mapping every field to its database
  column.
- Derive the primary-key strategy from the existing table structure and project
  conventions. Do not default to `AUTO` without evidence.
- Use explicit MyBatis-Plus mapping annotations when a database column and Java field have
  different names.

### DTO, Converter, and Assembler

- Include only fields genuinely required by the current API Contract and application
  service.
- Use a Converter for PO/Entity mapping and an Assembler for Entity/DTO mapping. Do not
  duplicate mapping logic in Controllers.
- Prefer the project's existing MapStruct configuration and component model.

### Repository

- Extend `BaseMapper<PO>` from the Mapper and prefer MyBatis-Plus for basic single-table
  operations.
- Use Mapper XML only for joins, aggregation, dynamic filters, or custom SQL genuinely
  required by the current endpoint. Do not create empty XML or duplicate basic CRUD SQL.
- Declare only domain operations required by the current endpoint and existing module in
  the Repository interface. Do not force a generic five-method CRUD surface.
- Inject the Mapper and Converter into `RepositoryImpl`. Query conditions and pagination
  behavior must match the TechnicalPlan Endpoint parameters and schema.
- Follow existing project and table conventions for logical deletion, audit fields, and
  tenant filters. Do not add them without evidence.

### Application Service

- Let `ApplicationService` coordinate DTOs/Entities, the Repository, and business
  decisions. It must not access the Mapper directly.
- For write operations, determine transaction boundaries from Spring conventions and the
  actual persistence boundary.
- Match confirmed endpoint decisions for zero matches, multiple matches, uniqueness
  conflicts, and response status.
- Implement only methods required by the current contract. Make precise incremental
  changes when reusing an existing Service.

### Controller

- Use the template's existing response wrapper, exception handling, validation
  annotations, and authorization conventions. See “Template `common` Infrastructure” for
  the exact response, pagination, and exception reuse rules.
- Follow the current TechnicalPlan API Contract exactly for HTTP method, path, request
  schema, response schema, status code, error codes, and execution semantics.
- Keep Controllers limited to protocol adaptation and parameter validation; put business
  decisions in `ApplicationService`.
- When the business module already has a Controller, add a method there instead of
  creating a separate Controller for every endpoint.

## Template `common` Infrastructure

Template initialization injects shared response, pagination, exception handling, and web
configuration into every project. Before writing business code, locate and read these
existing classes in the actual workspace. Follow their real files and package names
instead of hard-coding the reference template's root package. The current template
normally provides these stable responsibilities:

- `ResponseEntity<T>`: the unified response wrapper for internal APIs, with `success()`
  and `success(body)` for successful results.
- `PageParam` and `PageResult<T>`: shared pagination input normalization, total-page
  calculation, and list conversion.
- `BizException`, `IErrorCode`, and `IBizErrorCode`: business exceptions and error-code
  boundaries.
- `BaseExceptionHandler`: centralized conversion of business exceptions, Bean Validation
  failures, and request-body parsing failures into unified error responses.
- A `WebMvcConfigurer`-based CORS configuration: the template-level cross-origin policy.

These files are read-only infrastructure owned by the template. Endpoint object,
repository, service, and Controller tasks must not copy, rename, or modify them, and must
not create a second implementation with the same responsibility:

- The confirmed Endpoint `response_schema_ref` describes the business body type `T`, not
  the HTTP JSON root. The Controller must return that DTO through the template's
  `common.response.ResponseEntity<T>` by calling `success()` or `success(body)`. Do not add
  `returnCode`, `errorMsg`, or `body` fields to the business DTO or API Contract. Do not
  accidentally import `org.springframework.http.ResponseEntity`, and do not create
  `Result`, `ApiResponse`, or another wrapper.
- Use the project's existing Bean Validation conventions so validation failures flow into
  `BaseExceptionHandler`. Controllers must not catch `BizException` or manually call
  `ResponseEntity.failed` to imitate global exception handling.
- For confirmed business failures in a Service, throw `BizException` with an existing
  concrete error-code type. Do not invent error codes. If no confirmed error-code
  implementation exists and the current `allowed_paths` cannot add one, return a change
  request.
- Reuse the existing CORS configuration. Do not add `@CrossOrigin` to a Controller or
  create or modify another `WebMvcConfigurer`.

The API Contract remains authoritative for interface methods, status behavior, and the
business request/response schemas. `ResponseEntity<T>` is the separate fixed transport
contract, so wrapping the confirmed response DTO is not a schema mismatch. If the actual
template common class cannot carry the confirmed `T`, do not modify `common`; return a
contract mismatch or change request when the current write scope cannot implement both.

## Mapping and Pagination

- Map common MySQL types as follows: `tinyint/smallint/int` → `Integer`, `bigint` →
  `Long`, `varchar/char/text` → `String`, `decimal/numeric` → `BigDecimal`, `date` →
  `LocalDate`, `datetime/timestamp` → `LocalDateTime`, `time` → `LocalTime`, `float` →
  `Float`, `double` → `Double`, and `boolean/bit(1)` → `Boolean`.
- When the confirmed interface declares pagination, prefer accepting or composing the
  existing `PageParam` and build results with `PageResult.of` or `PageResult.convert`.
- Do not add pagination merely because the template provides pagination classes. Query
  conditions and page behavior must match the confirmed Endpoint parameters and schema.

## Error and Safety Behavior

- Do not swallow persistence or mapping failures, return fake success, or expose database
  credentials in logs or responses.
- Do not invent error codes, response wrappers, schema fields, CRUD operations, or
  persistence behavior that is absent from the confirmed contract.
- Do not modify database schemas, create migrations or seed data, or edit injected
  template `common` infrastructure.
- Return a change request rather than expanding scope when a required dependency,
  configuration, contract decision, or writable path is absent.

## Java 8 and Project Constraints

- Use Java 8 collection APIs such as `Arrays.asList` and `Collections.singletonList` for
  collection constants.
- Check blank strings with `value != null && !value.trim().isEmpty()`.
- Prefer `java.time` types for time values and `BigDecimal` for monetary values.
- Do not use `record`, `var`, `List.of`, text blocks, `String.isBlank`, or other Java 9+
  syntax or APIs.
- Follow the actual workspace package structure and naming conventions. Add Chinese
  purpose comments to every new or materially changed class and method as required by the
  repository.

## Completion Criteria

- Implement only the current endpoint stage and only within `allowed_paths` and
  `change_scope`.
- Leave a fully satisfying target unchanged and return `already_satisfied` with concrete
  evidence.
- Otherwise return the exact structured result required by the outer Build flow.
