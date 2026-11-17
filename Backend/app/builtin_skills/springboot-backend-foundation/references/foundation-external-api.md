# External API Foundation Reference

## Prepare Connection Configuration

Find the current external API source in `implementation_contract.source_snapshots`
and read its `details.connection`:

- `baseUrl`: the external API's base address, used as the connection configuration value.
- `baseUrlConfigKey`: the full configuration key storing the base address, used for
  the Client's property reference.

Persist the confirmed `baseUrl` as a plain YAML or properties scalar under the exact
`baseUrlConfigKey`. For example, when `baseUrl` is `http://api.example.com` and
`baseUrlConfigKey` is `external.product.base-url`:

```yaml
external:
  product:
    base-url: http://api.example.com
```

## Prepare the Client Skeleton

Inspect the task's Client file, nearby Client packages and existing HTTP abstractions.

Prefer a Java 8-compatible typed `@FeignClient` for a new Client. Reuse an existing
compatible RestTemplate, WebClient, or project HTTP abstraction when it already
provides the required shared connection foundation.

Use the connection configuration prepared above in the Client. For example:

```java
@FeignClient(
    name = "productClient",
    url = "${external.product.base-url}"
)
public interface ProductClient {
}
```

- `name`: use a unique compatible client name in the project and avoid a duplicate
  Client for the same confirmed connection.
- `url`: reference the confirmed `baseUrlConfigKey`.

For a new Client, prepare only the skeleton. Foundation adds no concrete Operation
methods or request/response transport DTOs; preserve any that already exist.
