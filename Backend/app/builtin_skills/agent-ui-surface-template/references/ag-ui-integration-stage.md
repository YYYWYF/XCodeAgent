# AG-UI Integration Stage

## Status

This document defines the future integration stage. It does not authorize real
network implementation during UiDesign or generated-frontend Mock delivery.

## Replacement Boundary

- Replace `MockAgentConversationAdapter` with an adapter implemented using
  `@ag-ui/client` and `@ag-ui/core`.
- Keep the same page components, configuration, Surface ownership, action IDs,
  context whitelist, and view state semantics.
- The browser calls only the Java Agent Gateway. It never connects directly to
  the Python Agent Runtime.
- Preserve the complete AG-UI lifecycle: run start, assistant message, structured
  result or error, state snapshot or delta, and exactly one run finish event.
- Java validates user, tenant, application, Agent, page, action, Contract hash,
  and context schema before forwarding trusted identity to the Runtime.
- Do not implement a custom SSE parser, a parallel thread store, compatibility
  aliases, dual writes, or Mock-and-real fallback behavior.

Integration is complete only after the existing Build, testing, code review,
launch, and acceptance stages hold real runtime evidence.
