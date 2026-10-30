# AG-UI Integration Stage

## Status

Formal Runtime Build uses the platform-injected official AG-UI adapter. UiDesign
remains offline. These are distinct current stages, with no network-to-Mock fallback.

## Replacement Boundary

- Use the fixed components' default Runtime adapter implemented using
  `@ag-ui/client` and `@ag-ui/core`; preserve config.runtime exactly.
- Keep the same page components, configuration, Surface ownership, action IDs,
  context whitelist, and view state semantics.
- Render the primary page contract with AGENT_UI_CONFIG and every companion
  with AGENT_UI_CONFIG_1, AGENT_UI_CONFIG_2 in order. Each owns an independent
  fixed component and Runtime adapter. Preserve config.actions: question and
  follow-up buttons submit their declared actionId/message in the current thread;
  clear_conversation starts a new client thread, retains server history, and
  honors the declared confirmation. Do not replace those operations with REST APIs.
- Compose mixed business/Agent sequences through the fixed component's
  `actionRequest` and `onActionResult` props. Use a unique requestId and the
  declared actionId/stepId, clear the consumed request and advance only after a
  completed result. A waiting, failed, cancelled or unknown result stops the
  sequence; do not auto-resubmit it or bypass pending interaction handling.
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
