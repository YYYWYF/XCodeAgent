# External API Business Reference

## Confirm the External Operation and Bindings

Locate the current external API source in `api_design.sources` and read:

- `connection`: the base address, configuration key, and confirmed connection settings.
- `operation`: HTTP method, path, parameters, and request/response structures.

Read `api_design.externalApiBindings` for the current Operation's Path, Query, Header,
and Body parameter values.

Each binding has a `target` such as `external_api_1.query.offset` and a `value`
such as `{ "fixed": 0 }`. External response references include their section,
for example `external_api_1.response_body.data.id`.

Apply the entrypoint's `request`, `source`, `fixed`, and `business` value forms.
Use `fieldMappings` for the internal response values.

## Prepare Transport Objects

Inspect and complete request, response, and nested transport DTOs using the
Operation's full `requestStructure` and `responseStructure`.

Preserve field names, types, object nesting, and arrays, including fields unused by
the current response mappings.

```java
@Data
public class ProductApiResponse {

    // Declare the complete response structure from the Operation.
}
```

Keep external transport DTOs separate from internal API DTOs. Reuse existing
transport objects for the same Operation.

## Implement the Client

Inspect the Foundation Client and existing project HTTP abstractions. Reuse the
implementation matching the current connection.

Declare parameters from the complete Operation:

- `@PathVariable` for Path parameters.
- `@RequestParam` for Query parameters.
- `@RequestHeader` for operation-level Headers.
- `@RequestBody` for a declared request body.

For example:

```java
@FeignClient(
    name = "productClient",
    url = "${external.product.base-url}"
)
public interface ProductClient {

    @GetMapping("/products/{id}")
    ProductApiResponse getProduct(
        @PathVariable("id") Long id,
        @RequestParam(value = "locale", required = false) String locale
    );
}
```

This example shows declaration structure only. Use the Operation's actual method,
path, and parameters.

`externalApiBindings` determines call-site values; it does not shrink the Client
signature or transport DTOs.

Keep declared optional parameters without mappings in the signature, and omit them
from the actual request when no value is provided.

Use the confirmed configuration key for the base address and reuse existing
connection configuration.

## Prepare Internal Requests, Responses, and Conversions

Inspect internal request DTOs, response DTOs, and the Assembler. Implement conversions
using the API schemas and `fieldMappings`.

```java
@Component
public class ProductAssembler {

    public ProductDetailResponse toResponse(ProductApiResponse response) {
        // Map the external response to the internal response.
    }
}
```

Read the confirmed external response paths and preserve nesting and array-element
correspondence.

## Implement the Application Service

Inspect the task's Service. Resolve parameters from `externalApiBindings`, invoke
the Client, and compose the internal response using `fieldMappings`.

```java
@Service
@RequiredArgsConstructor
public class ProductApplicationService {

    private final ProductClient productClient;
    private final ProductAssembler productAssembler;

    public ProductDetailResponse getProduct(Long id) {
        // Prepare external request parameters.
        // Invoke the Client.
        // Compose the internal response.
    }
}
```

Invoke the confirmed external Operation even when it has no request bindings.
Follow existing project handling for external call failures.
