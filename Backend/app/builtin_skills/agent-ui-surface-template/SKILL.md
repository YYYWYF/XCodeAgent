---
name: agent-ui-surface-template
description: >-
  Compose confirmed Agent surfaces using installed public Agent UI packages for
  multi-file UiDesign. Route formal generated-frontend Build separately to its
  current integration contract.
---

# Agent UI Surface Template

Use this Skill whenever the confirmed ProductPlan assigns an Agent Surface to
the current page. It supplements the normal UI or frontend implementation Skill;
it does not replace ProductPlan, UiDesign, the frontend template boundary, or the
existing Build and acceptance flow.

## Core Invariants

- In UiDesign, `standalone_page` contains one package-consumer conversation and
  retains confirmed navigation or other non-Agent page controls beside it.
- `floating_panel` is an enhancement mounted beside the complete host page,
  including a declared standalone conversation owned by another Agent. It must
  preserve the host body. The current UiDesign pilot supports one Agent and one
  Surface per page; reject unsupported combinations explicitly.
- Both design surfaces share one mounted official Agent and canonical Runtime;
  closing the panel or selecting a page must not create a second conversation owner.
- UiDesign uses the installed public production packages and the project application
  layer created through Creator's public initialization SDK. Do not use the old
  virtual-module templates, internal package imports, test factories or copied framework code.
- Initial generation uses the initialized official application layer and preserves
  accepted shared customizations. Explicit design revisions delegate composition
  and presentation changes to Creator. Do not automatically start a Creator task
  during initial generation or add demonstration controls.
- Only configure the confirmed Agent identity, responsibility, capabilities,
  suggested questions, action binding, context whitelist, declared feature
  switches, and realistic Mock examples.
- Use stable Agent, page, action, and information-item IDs. Never infer bindings
  from labels, routes, component names, or the DOM.
- UiDesign connection is supplied explicitly by its controlled preview owner.
  Missing connection is a pending state, never an assumed endpoint or fake success.
  Mock integration is a separate task; do not implement a page-local Mock adapter.
- Formal Build consumes the current `agent-ui-shared-build.v1` contract from
  confirmed design and reviewed T21/T23 transfer evidence. Pages use only
  `SharedAgentPage` / `useSharedAgentPanel` from the supplied public module.
  The unique Host owns the installed public Agent and Runtime. Page code never
  creates transport, credentials, identity, another Provider or an adapter.
- Formal connectivity remains `blocked_T27` until public package admission and
  X Runtime mapping are verified. Keep sending disabled; never inject Mock into
  the formal Host or infer an endpoint from the page.
- Mock or design evidence never proves AG-UI, Runtime, Launch, or
  Acceptance completion.

## Stage Routing

- For XCodeAgent UiDesign composition, read
  `references/ui-design-stage.md` before writing or adjusting the page.
- For generated application Build, read `references/ag-ui-integration-stage.md`.
- `references/frontend-mock-stage.md` describes offline design fixtures only;
  it cannot override a formal Runtime Build contract.

Reject unknown Surfaces, multiple design Agents and undeclared context. Do not
change Host routing, dependencies, formal Runtime contracts or acceptance gates
while generating a design prototype.
