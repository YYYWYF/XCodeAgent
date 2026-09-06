# Java Tool adapter

The generated file at `artifacts.toolAdapterPath` exposes
`build_tools(runtime_context) -> list`. Each list item is a LangChain Tool whose stable
name is the Contract `toolId` and whose description preserves the declared usage trigger.

## Transport boundary

- Call only the relative `endpoint.path` and HTTP `endpoint.method` from the Contract.
- Resolve the Java service base URL through `load_settings().require_backend_base_url()`
  and the service-to-service credential through
  `load_settings().require_tool_gateway_token()` at call time. Their environment sources
  are `AGENT_RUNTIME_BACKEND_BASE_URL` and `AGENT_RUNTIME_TOOL_GATEWAY_TOKEN`.
- Reject a missing base URL/token, an absolute Endpoint path, redirects to another host,
  and response bodies that do not match the declared JSON expectation.
- Forward trusted context as `X-Agent-User-Id`, `X-Agent-Tenant-Id`, `X-Agent-Scopes`, and
  `traceparent` when present. Tool arguments must never override these headers.
- Send `Authorization: Bearer <service token>` and JSON content headers. Never expose the
  token in errors, logs, model-visible results, or test snapshots.
- Apply a finite timeout and return a bounded, safe Tool error. Do not retry write
  operations automatically.

## Request and response mapping

Use the resolved API Contract supplied by the execution prompt to implement path/query/
body mapping. Do not infer fields from names alone and do not invent defaults. Preserve
the declared request and response schema shapes. A `requestSchemaRef` of `null` means no
JSON body, not an empty fabricated object.

`accessMode=read` Tools must remain read-only. `accessMode=write` Tools must state that
approval is platform-managed and must not treat model text as approval. The Java Endpoint
remains the enforcement boundary.

Focused tests must replace the HTTP transport, verify the exact method/path/body and
trusted headers, and cover a safe failure response without performing network calls.
