# AG-UI Integration Stage

## Status

Formal Runtime Build uses the platform-injected official AG-UI adapter. UiDesign
remains offline. These are distinct current stages, with no network-to-Mock fallback.

## Replacement Boundary

- Use the fixed components' default Runtime adapter implemented using
  `@ag-ui/client` and `@ag-ui/core`; preserve config.runtime exactly.
- Keep the same page components, configuration, Surface ownership, action IDs,
  context whitelist, and view state semantics.
- The browser calls the confirmed same-origin /agents/{agentId}/run path through
  the template proxy to Python Runtime. Do not introduce a Java Gateway.
- Preserve the complete AG-UI lifecycle: run start, assistant message, structured
  result or error, state snapshot or delta, and exactly one run finish event.
- Runtime supplies trusted anonymous identity through its same-origin Cookie;
  never send a Principal, namespace, credential, host port or model key from the page.
- Submit pendingInteraction responses with the exact interactionId. Confirmation
  requires strict true/false; clarification supplies text and never grants approval.
- Do not implement a custom SSE parser, a parallel thread store, compatibility
  aliases, dual writes, or Mock-and-real fallback behavior.

Integration is complete only after the existing Build, testing, code review,
launch, and acceptance stages hold real runtime evidence.
