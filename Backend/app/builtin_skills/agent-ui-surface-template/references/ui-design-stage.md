# UiDesign Stage

## Scope

Compose the current confirmed RequirementSpec/ProductPlan into a new isolated
React project. The public SDK initializes the real package application layer,
including AppUIModel, plugin registration, props/renderer and locale resources.
The existing UI design model generates business TSX. Initial generation uses the
initialized official Agent and preserves accepted shared customizations without
automatically starting an autonomous Creator task. Explicit design revisions use
the Creator flow below.

## Required Composition

1. Read the projected Agent Surface before choosing the page composition.
2. For `standalone_page`, the platform mounts one official initialized Agent.
   Generate page controls for non-Agent actions, including navigation back to
   another page. The page's message
   list, input, send control and state information are implemented by that Agent;
   they are not business information items to render a second time. Preserve
   non-Agent actions and their formal IDs. Navigation buttons use the Host's typed
   `onNavigatePage('exact targetPageId')` callback; they never implement routing.
3. For `floating_panel`, preserve the complete business body. The Host supplies
   the normal open/close interaction while keeping the same Agent mounted. When
   the confirmed business body declares panel open/close interface actions, bind
   their buttons directly to the typed `onOpenAgentPanel` / `onCloseAgentPanel`
   props and use `agentPanelOpen` for visibility or disabled state. Preserve the
   declared action IDs and effects. Never create page-local panel state. An
   explicit business launcher replaces the Host's default launcher; other
   interface actions must not use these panel callbacks.
4. Keep stable page/Agent/action/context identities from confirmed ProductPlan.
   Do not invent backend addresses, ports, credentials or advanced capabilities.
5. Keep ProductPlan action and information-item evidence on the business controls
   that implement those facts. The platform owns declared Agent entry actions.

## Fixed Boundaries

- Do not implement a second message list, Composer, Tool card, approval card,
  conversation sidebar or theme in business TSX. Shared Agent layout, plugins and
  presentation use the initialized application layer; explicit changes use Creator.
- JSON AppUIModel expresses layout/composition only. Keep business logic in TSX.
- Do not add `fetch`, `axios`, effects that call a service, real storage, routing,
  credentials, or Runtime connectivity.
- Use the same fixed component for desktop and mobile. Responsive behavior stays
  inside the component; do not duplicate Agent evidence nodes per breakpoint.

## Design Revisions

The existing `adjust_pages` flow delegates Agent UI revisions to the installed
Creator public SDK. Forward the original request and the current target page's
application layer, including AppUIModel, public Plugin adapters, styles and locale.
Do not implement request-specific CSS substitutions or use the business TSX model
as a fallback. Creator chooses composition JSON versus public component props or
application code; AppUIModel never contains React props.

Run Creator in an independently initialized working copy so its project config and
source-lock provenance stay valid. Publish only allowed target-page file changes
after the official composition check and final lint/typecheck/build. Preserve
non-target pages, accepted earlier revisions, one canonical Runtime, and the old
preview on failure/cancel. A reply without actual file receipts is not success.
New product/backend capabilities, package takeover and dependency changes require
their own existing authorization flow; pending Creator questions do not confirm
UiDesign or authorize publication of a partial draft.

## Completion Evidence

The complete project must pass source-boundary validation, lint, typecheck and
build. Record actual package/environment identity, formal facts digest, current
revision, file hashes and build hashes. Failed or cancelled generation cannot
reuse an old preview as new evidence. Generated state still waits for explicit
formal UiDesign confirmation. Mock, Electron canvas and confirmation are T18–T20;
standalone generator success does not claim those stages are complete.
