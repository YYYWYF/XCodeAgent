# Business Error Handling Reference

## Inspect Business Error Codes

Locate the current Endpoint in `api_contract.endpoints` and read its `error_codes`.

Inspect the module's existing ErrorCode enum. Reuse confirmed codes and messages,
adding only missing items required by the task.

The ErrorCode enum implements `IBizErrorCode`.

```java
@Getter
@RequiredArgsConstructor
public enum ProductErrorCode implements IBizErrorCode {

    DUPLICATE_CODE("Product code already exists");

    private final String errorMessage;
}
```

Use the current contract and project implementation for code names and messages.

## Implement Business Failure Branches

Throw `BizException` where the confirmed business outcome can be determined,
usually in the Application Service.

```java
if (productRepository.existsByCode(request.getCode())) {
    throw new BizException(ProductErrorCode.DUPLICATE_CODE);
}
```

A declared error code does not require an additional validation branch. Add a failure
branch only when confirmed business behavior requires it.

## Use Error Messages

Prefer confirmed messages, or derive a concise message from confirmed business
semantics.

For runtime arguments, follow the project's existing `MessageFormat` convention:

```java
throw new BizException(ProductErrorCode.PRODUCT_CONFLICT, action);
```

Match placeholders such as `{0}` to the argument order. Do not expose SQL, stack
traces, credentials, or raw upstream payloads to users.

## Reuse Exception Handling

Use the existing global exception handling.

Preserve existing handling chains for database and external API technical failures.
Do not convert all technical failures into business errors or catch failures to
return fake success.
