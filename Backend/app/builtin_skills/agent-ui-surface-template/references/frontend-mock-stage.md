# Offline Design Mock Stage

## Scope

Use the installed official Agent UI application layer in the isolated design
project. This reference applies only to design preview.
Formal Build consumes the Runtime contract and follows ag-ui-integration-stage.md.

## Adapter Boundary

- Canonical Runtime remains the sole conversation owner. Use Creator's published
  official HTTP Mock or supported JSONL through the tool/preview owner in T18.
- Do not generate `AgentConversationAdapter`, local message state or fake approval
  callbacks. Missing official support is explicitly unsupported, not a successful fixture.
- Mock data must be realistic, deterministic, and clearly labelled as simulated
  in the rendered UI.
- Context input must contain only the confirmed page ID, action ID, and declared
  context item IDs and values. Do not inspect or serialize the page DOM.

## Prohibitions

Page code must not implement HTTP, `fetch`, `axios`, WebSocket, SSE, or any
handwritten event parser. The official Mock is a separate developer process and
its endpoint is explicitly bound by the preview owner. It must not add secrets,
tokens, persistent conversation storage, another global Provider, or another
copy of the shared chat core.

Do not add user-facing scenario/state/theme demonstration controls. Keep a visible
simulated-source notice during Mock preview; exclude Mock dependencies, recordings
and connection configuration when transferring to a formal production application.
