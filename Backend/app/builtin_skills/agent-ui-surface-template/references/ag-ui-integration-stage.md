# AG-UI Integration Stage

## Current Build Contract

Formal Build consumes `agent-ui-shared-build.v1`, `mode=shared_host`.
The platform rechecks confirmed formal documents, shared source, target ownership,
actual installed package/catalog/types/lock identity and Host wiring from disk.
Missing or changed evidence blocks Build before scheduling; it never initializes,
installs, copies fixed assets, confirms documents or falls back to old templates.

## Page Boundary

- Import only `SharedAgentPage` and `useSharedAgentPanel` from the exact module
  supplied in the current contract. Preserve the exact pageId.
- Standalone pages render the shared placeholder. Floating pages retain their
  business body and use the shared Host visibility hook for user open/close actions.
- For `standalone_page`, the conversation is the page, not a card embedded in a
  second page. Use the existing application navigation and the official thread
  sidebar. Do not enclose the slot in ProCard/Card/PageContainer, a centered
  max-width frame, rounded/shadowed panels, duplicate hero text or a technical footer.
  Keep confirmed non-Agent actions in a compact toolbar. Fill the actual Shell
  content area with a flat full-width flex layout.
  Remove the Shell content padding and contrasting outer background only for this
  mounted standalone page, using a page-scoped parent selector such as
  `.ant-pro-layout-content:has(> .your-page-class)`. Do not edit the global Shell
  or remove spacing from ordinary business pages.
  Give the remaining-space region min-width/min-height zero and its direct `.shared-agent-page-slot` height 100%
  in page-scoped CSS. Measure or inherit actual available height instead of nesting
  a 100vh page or hard-coding a large minimum height. Use inherited theme tokens.
  This overrides generic page-card advice only around a standalone conversation;
  it does not flatten ordinary business cards or change floating-panel pages.
- The Host is mounted once inside the template identity boundary, outside routing.
  Page changes and visibility changes preserve the same Agent, thread and draft;
  logout or a user change destroys the old session.
- Do not modify the shared application, Provider registration, sourceRoot, BASE,
  package declarations or wiring from a page task. Changes require a new reviewed
  design/transfer through their owning flows.
- Do not create a page-local Agent, Runtime, Adapter, transport, credentials or
  conversation store. Old fixed components, virtual modules and AGENT_UI_CONFIG
  contracts are not supported.
- `blocked_T27` means the formal Host has no usable endpoint and sending remains
  disabled. A declared same-origin Agent route is not public package admission.
  Never connect it through an old Adapter or inject design Mock as a substitute.
- Ordinary business AG-UI flows keep their own confirmed static business URLs
  and official SDK. They cannot create a second Agent session.

## Evidence

Shared static source checks do not prove runtime behavior, real connectivity or
business success. Mock and Electron design evidence remain separate. Preserve all
formal confirmation gates; a revised design needs confirmation and reviewed transfer
again. Declared real Runtime capabilities require T27 and subsequent validation.
