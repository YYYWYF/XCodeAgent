# Generated Frontend Mock Stage

## Scope

Use the shared Agent conversation components already present in the generated
application frontend template. Implement page composition and confirmed business
configuration only. The current delivery uses `MockAgentConversationAdapter`.

## Adapter Boundary

- Conversation history, messages, simulated delays, stop, retry, Tool summaries,
  approvals, errors, and success results belong in the Mock adapter.
- Pages consume the stable `AgentConversationAdapter` interface and must not know
  whether a later implementation uses AG-UI.
- Mock data must be realistic, deterministic, and clearly labelled as simulated
  in the rendered UI.
- Context input must contain only the confirmed page ID, action ID, and declared
  context item IDs and values. Do not inspect or serialize the page DOM.

## Prohibitions

Mock delivery must not call HTTP, `fetch`, `axios`, a Java Gateway, a Python
sidecar, WebSocket, SSE, or any handwritten event parser. It must not add secrets,
tokens, persistent conversation storage, another global Provider, or another
copy of the shared chat core.

Remove XCodeAgent-only preview controls when composing production pages, but keep
the visible simulated-runtime notice until real integration replaces the Mock
adapter in an independently authorized batch.
