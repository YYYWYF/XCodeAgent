---
name: agent-ui-surface-template
description: >-
  Compose business Agent conversation surfaces from XCodeAgent-owned fixed UI
  components and a constrained configuration. Use for standalone Agent pages,
  floating Agent panels on business pages, offline UiDesign, and confirmed
  generated-frontend Runtime integration.
---

# Agent UI Surface Template

Use this Skill whenever the confirmed ProductPlan assigns an Agent Surface to
the current page. It supplements the normal UI or frontend implementation Skill;
it does not replace ProductPlan, UiDesign, the frontend template boundary, or the
existing Build and acceptance flow.

## Core Invariants

- `standalone_page` is a dedicated conversation-page template.
- `floating_panel` is an enhancement mounted on a complete business page. It is
  not a second page template and must not replace the business page body.
- Both surfaces share the fixed message, run-status, Tool, approval, error, and
  Composer semantics supplied by XCodeAgent-owned components.
- In UiDesign, import the fixed components only from
  `@xcodeagent/agent-ui-design`; never recreate the chat DOM or styles locally.
- Only configure the confirmed Agent identity, responsibility, capabilities,
  suggested questions, action binding, context whitelist, declared feature
  switches, and realistic Mock examples.
- Use stable Agent, page, action, and information-item IDs. Never infer bindings
  from labels, routes, component names, or the DOM.
- UiDesign is offline. Formal Build uses the platform's fixed official AG-UI
  adapter and exact config.runtime same-origin /agents/{agentId}/run binding.
  Page code never creates transport, credentials, identity or another adapter.
- Mock or design evidence never proves AG-UI, Runtime, Launch, or
  Acceptance completion.

## Stage Routing

- For XCodeAgent UiDesign composition, read
  `references/ui-design-stage.md` before writing or adjusting the page.
- For generated application Build, read `references/ag-ui-integration-stage.md`.
- `references/frontend-mock-stage.md` describes offline design fixtures only;
  it cannot override a formal Runtime Build contract.

Stop and request a contract decision if a page has an unknown Surface, multiple
floating Agents, undeclared context, or would require changing the host route,
global Provider, request layer, dependencies, or real acceptance gates.
