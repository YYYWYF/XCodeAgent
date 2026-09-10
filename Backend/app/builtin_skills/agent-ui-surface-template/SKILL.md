---
name: agent-ui-surface-template
description: >-
  Compose business Agent conversation surfaces from XCodeAgent-owned fixed UI
  components and a constrained configuration. Use for standalone Agent pages,
  floating Agent panels on business pages, generated-frontend Mock delivery,
  and the later AG-UI adapter replacement.
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
- Design and Mock delivery are offline. They must not call HTTP, a Java Gateway,
  a Python sidecar, or a handwritten event stream.
- Mock or design evidence never proves AG-UI, Gateway, Runtime, Launch, or
  Acceptance completion.

## Stage Routing

- For XCodeAgent UiDesign composition, read
  `references/ui-design-stage.md` before writing or adjusting the page.
- For generated-frontend Mock implementation, read
  `references/frontend-mock-stage.md` before changing generated application code.
- For the future real transport replacement, read
  `references/ag-ui-integration-stage.md`. This reference is informational until
  the Gateway and Runtime delivery batch is explicitly authorized.

Stop and request a contract decision if a page has an unknown Surface, multiple
floating Agents, undeclared context, or would require changing the host route,
global Provider, request layer, dependencies, or real acceptance gates.
