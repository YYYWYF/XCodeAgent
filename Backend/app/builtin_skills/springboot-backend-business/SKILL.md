---
name: springboot-backend-business
description: >-
  Implement Business-stage endpoint behavior, including database operations,
  external API calls, and response composition.
---

# springboot-backend-business

## Prerequisite Checks Are Complete

**Dependencies and shared runtime configuration have already been checked by
preceding tasks. Do not repeat these checks.**

Do not read or search `pom.xml`, `application*.yml`, `application*.properties`, or
infrastructure configuration classes to recheck dependencies or runtime setup.
Reuse the completed setup and focus on the current task's implementation.

## Implement Endpoint Behavior

Read `implementation_contract.api_contract` for the current Endpoint's request,
response, and business error definitions.

Read the shared information in `implementation_contract.api_design`:

- `sources`: source information indexed by alias.
- `fieldMappings`: response fields and their values.
- `implementationDescription`: business implementation guidance.

Reuse Foundation structures and existing project implementations to complete the
DTOs, conversions, source access, and Service required by the current task.

Read task targets first. Read related implementations only to resolve unclear
dependencies or behavior. Finish when all required checks have a definite result.

Follow the API schema's declared formats and nullability. Required field presence
does not itself mean a non-null value; database column nullability does not define
response nullability. Use only confirmed conversion rules.

## Understand Field Values

The keys of `fieldMappings` are response field paths. Each value describes how to
obtain the corresponding field.

### Request Field: request

Read a request field at the specified location and path.

```json
{
  "fieldMappings": {
    "name": { "request": "request_body.name" }
  }
}
```

Assign the request body's `name` to the response's `name` field.

### Source Field: source

Read a field under the specified source alias.

```json
{
  "fieldMappings": {
    "id": { "source": "database_1.id" }
  }
}
```

Assign the database record's `id` from `database_1` to the response's `id` field.
Source aliases are defined in `sources`, not Java class names or configuration names.
For a source with `sourceType: "static"`, resolve the path against that source's
`data` JSON in the current Endpoint. Preserve literal values and their types,
including empty strings, zero, and false. The `[]` marker denotes traversal by
array element, including a root array such as `static_1[].name`. Static data
requires no database or HTTP call.

### Fixed Value: fixed

Use the specified fixed value, preserving its original type.

```json
{
  "fieldMappings": {
    "count": { "fixed": 0 }
  }
}
```

Set the response's `count` field to the number `0`.

### Business Rule: business

Implement the business guidance using the inputs declared in `dependencies`.

```json
{
  "fieldMappings": {
    "amount": {
      "business": "Multiply the product unit price by the purchase quantity",
      "dependencies": {
        "requests": ["request_body.quantity"],
        "sources": ["database_1.price"]
      }
    }
  }
}
```

Calculate the response's `amount` from the requested quantity and the database unit price.

- `dependencies.requests`: request fields required by the business rule.
- `dependencies.sources`: source fields required by the business rule.

Implement only explicitly required field and business validation; do not add extra
field-value checks. Preserve array-element correspondence.

## Source References

Read the references for the current task's sources:

- Database: [Database Business Reference](references/business-database.md).
- External API: [External API Business Reference](references/business-external-api.md).

For tasks involving both types, read both references and coordinate calls in the
Service according to confirmed data dependencies and business guidance.

For business errors, read
[Business Error Handling Reference](references/business-error-handling.md).
