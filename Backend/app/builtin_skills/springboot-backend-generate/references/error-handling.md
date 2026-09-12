# Endpoint Error Handling

## Authority and Error-Code Definition

- Treat `implementation_contract.api_contract.error_codes` as the complete allowlist of
  endpoint-facing business error-code constants. Reusing or defining those exact constants
  is not inventing an error code; adding any other public code is.
- Every generated concrete ErrorCode type must be an enum that implements `IBizErrorCode`.
  Do not generate a plain enum, implement only `IErrorCode`, or represent a confirmed
  business error as a free-form string constant.
- Prefer letting the `service` task create or maintain the module error-code enum because it
  owns the business failure branches that consume those codes. Reuse the enum when it
  already contains the required constants; otherwise create or extend the single
  module-level `domain/exception/<Module>ErrorCode.java` implementing `IBizErrorCode` when
  that exact path is present in the current task's `allowed_paths` and `change_scope`. Do
  not create one enum per Controller method.
- This is a recommended ownership pattern, not an exclusive constraint. Follow an explicit
  existing task/module convention when another stage owns the ErrorCode file. If the
  required file is not writable by the current task, return `plan_mismatch` with
  `change_request`; do not write a reference to a missing type, substitute another code,
  use a raw string, or edit the template `common` classes.

Follow the existing module's enum and Lombok conventions. With the template defaults, the
enum constant name becomes `returnCode` through `IErrorCode.getErrorCodeStr()`, while the
enum's `errorMessage` becomes the public `errorMsg` through `ResponseEntity.failed`.

## Error Type and Propagation Contract

- Define the stable code and message on an `IBizErrorCode` enum constant.
- Propagate a confirmed business failure by constructing `BizException` with that enum
  constant: `throw new BizException(ModuleErrorCode.SOME_ERROR)`.
- When the enum message contains `MessageFormat` placeholders, pass only the matching safe
  formatting arguments after the enum constant:
  `throw new BizException(ModuleErrorCode.SOME_ERROR, argument)`.
- Do not throw an ErrorCode enum directly, throw `RuntimeException` with the public message,
  return `ResponseEntity.failed(...)` from business code, or duplicate the enum message in
  the exception branch. The global exception handler is responsible for translating
  `BizException` and its `IBizErrorCode` into the response envelope.

## Generating the Error Message

Choose the message deterministically from the first available source:

1. An explicit, confirmed user-facing error message attached to the current error code or
   endpoint semantics.
2. A precise failure statement in the confirmed Endpoint summary, business description,
   or implementation description.
3. A concise user-facing sentence derived from the error-code name and the endpoint's
   confirmed business object/action.

For the fallback in step 3, translate only semantics clearly present in the code name. For
example, `*_NOT_FOUND` describes the confirmed business object as not found,
`*_ALREADY_EXISTS` as already existing, `*_CONFLICT` as a business conflict,
`*_QUERY_FAILED` as a failed query, and `*_CREATE_FAILED`, `*_UPDATE_FAILED`, or
`*_DELETE_FAILED` as the corresponding failed action. For a generic code whose precise
reason is not confirmed, use the endpoint's business action plus a neutral failure phrase;
do not guess a more specific cause.

Messages must:

- use the same user-facing language and terminology as the confirmed Endpoint artifacts;
- describe what the user can understand, not Java classes, SQL, tables, URLs, status-line
  dumps, stack traces, credentials, or raw upstream response bodies;
- be stable templates stored in the error-code enum, not strings assembled ad hoc in a
  Controller or Service;
- avoid claiming that data is missing, duplicated, invalid, unauthorized, or unavailable
  unless the contract and the actual branch prove that condition;
- stay concise and actionable without exposing implementation details.

Use `MessageFormat` placeholders such as `{0}` only when a safe runtime value materially
helps the user understand the failure. Placeholder indexes must be contiguous and the
`BizException` arguments must match their order. Never use SLF4J `{}` or `String.format`
`%s` tokens in an `IErrorCode` message. Do not insert credentials, personal data, complete
request bodies, raw upstream payloads, or exception messages as formatting arguments.

```java
@Getter
@RequiredArgsConstructor
public enum ProductErrorCode implements IBizErrorCode {
    PRODUCT_NOT_FOUND("Product not found"),
    PRODUCT_STATUS_CONFLICT("Product status does not allow {0}");

    private final String errorMessage;
}
```

The strings above illustrate structure only. Replace their language and business terms
with those used by the confirmed Endpoint artifacts.

## Where to Raise Exceptions

- Prefer raising endpoint-facing `BizException` from `ApplicationService`, where the full
  business outcome is known. This includes not-found results, uniqueness conflicts,
  invalid state transitions, forbidden business operations, and confirmed failures that
  depend on repository or upstream results.
- Let Repository methods return their domain result, empty result, affected-row count, or
  infrastructure exception. A Repository must not choose a public business message merely
  from a low-level SQL exception.
- Let an external Client or adapter preserve an upstream HTTP status, timeout, decode
  failure, or declared `error_message_path` in the project's non-public transport failure
  form. Prefer performing the confirmed mapping and throwing the final endpoint-facing
  `BizException` in ApplicationService. A lower layer should do so only when classification
  is complete at that boundary and the existing module already follows that convention.
- Controllers must use Bean Validation for request-shape constraints, delegate to the
  Service, and return only success responses. They must not catch `BizException`, call
  `ResponseEntity.failed`, or construct public error messages.
- Bean Validation and unreadable request-body failures use the template handler's fixed
  `validation_failed` response. Do not create an Endpoint business error code or custom
  Controller message for a constraint that belongs to request-shape validation.
- Do not blanket-catch `Exception` and convert every technical fault into a business error.
  Translate only a failure whose contract code is known, preserve the original cause with
  the matching `BizException` constructor when useful for diagnostics, and let unexpected
  faults follow the project's existing global handling.

```java
Product product = productRepository.findById(productId);
if (product == null) {
    throw new BizException(ProductErrorCode.PRODUCT_NOT_FOUND);
}
if (!product.canPerform(action)) {
    throw new BizException(ProductErrorCode.PRODUCT_STATUS_CONFLICT, action);
}
```

Do not throw merely because a query returns an empty list when an empty list is a valid
confirmed response. Every exception branch must correspond to the Endpoint contract and
the real business condition.
