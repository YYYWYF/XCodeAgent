---
name: springboot-backend-controller
description: >-
  Implement Controller-stage HTTP adaptation, BeeResponseEntity response wrapping,
  and endpoint authorization annotations.
---

# springboot-backend-controller

## Prerequisite Checks Are Complete

**Dependencies and shared runtime configuration have already been checked by
preceding tasks. Do not repeat these checks.**

Do not read or search `pom.xml`, `application*.yml`, `application*.properties`, or
infrastructure configuration classes to recheck dependencies or runtime setup.
Reuse the completed setup and focus on the current task's implementation.

## Implement HTTP Adaptation

Read `implementation_contract.api_contract` for the current endpoint's HTTP method,
path, parameters, request structure, and response structure.

Reuse the module Controller, completed Application Service, and internal DTOs.
Keep business processing in the Service.

Read the Controller, delegated Service method signature, and relevant DTOs first.
Read other supporting types only to resolve unclear usage, locating their source
files from the workspace tree and imports. Finish when all required checks have a
definite result.

## Check Endpoint Routes

Match endpoints by HTTP method and full path.

The combined class-level `@RequestMapping` and method-level mapping must match the
contract's full path. Do not repeat the class-level prefix in the method mapping.
For example, `@RequestMapping("/api/products")` with `@GetMapping("/{id}")` handles
`GET /api/products/{id}`.

Reuse the matching handler and preserve other existing routes.

## Bind Request Parameters

Read `parameters` and the request structure referenced by `request_schema_ref`.
Bind parameters according to their declared locations:

- Request body: `@RequestBody`.
- Path parameters: `@PathVariable`.
- Query parameters: `@RequestParam`.
- Request headers: `@RequestHeader`.

Use the completed request DTOs and apply `@Valid` according to the project's
existing validation conventions.

Follow the HTTP contract for parameter names, required flags, and default values.
Do not add extra field-value validation.

## Delegate to the Service and Wrap Responses

Read the actual Application Service method signature and pass the bound parameters
to the corresponding method.

All endpoint responses must use
`com.cmb.bee.web.entity.BeeResponseEntity<T>`.
Return successful results through `BeeResponseEntity.success(body)`;
when there is no business body, use `BeeResponseEntity.success()`.

`response_schema_ref` describes the business body `T` inside the wrapper.

```java
/**
 * 创建商品，委派应用服务并返回统一响应。
 */
@PostMapping
public BeeResponseEntity<ProductDetailResponse> createProduct(
        @Valid @RequestBody CreateProductRequest request) {
    return BeeResponseEntity.success(
            productApplicationService.createProduct(request));
}
```

This example handles product creation under the class-level `/api/products` route.
Use the routes, DTOs, and Service corresponding to the current task.

Apply the HTTP response status when explicitly specified by the contract.
Let the existing global exception handler process validation failures and business
exceptions.

## Resource Authorization

Check the entry for the current endpoint in
`implementation_contract.authorization_constraints`.

When `operationResourceKeys` is non-empty, read the
[Controller Resource Authorization Reference](references/controller-resource-authorization.md)
and add the authorization annotation.

When no resource authorization is declared, do not add `@RequireAnyResource`.
`authentication.required` alone does not determine resource authorization.
