# UiDesign Stage

## Scope

Compose the current page from the confirmed ProductPlan facts and the fixed
`@xcodeagent/agent-ui-design` runtime. The output remains one self-contained TSX
page for XCodeAgent's isolated design iframe, but the Agent conversation core is
an imported platform component rather than generated page-local code.

## Required Composition

1. Read the projected Agent Surface before choosing the page composition.
2. For `standalone_page`, render the declared `AgentConversationTemplate` as the
   page body. A separately declared floating Surface may coexist on that page.
3. For `floating_panel`, preserve the complete business page and render exactly
   one `AgentFloatingPanelTemplate` beside that body.
4. Pass every platform-projected `AgentUiTemplateConfig`. Keep Agent ID,
   Surface, the complete actions list, confirmation flags, and context item IDs exact.
5. Keep ProductPlan action and information-item evidence on the business controls
   that implement those facts. Mock preview controls remain explicitly marked as
   preview-only and are not product actions.

## Fixed Boundaries

- Do not implement a second message list, Composer, Tool card, approval card,
  launcher, panel, conversation sidebar, mobile Drawer, drag handler, or theme.
- Do not copy fixed component source into the page.
- Do not add `fetch`, `axios`, effects that call a service, real storage, routing,
  credentials, or Runtime connectivity.
- Use the same fixed component for desktop and mobile. Responsive behavior stays
  inside the component; do not duplicate Agent evidence nodes per breakpoint.

## Completion Evidence

The page must import the exact virtual module, use the expected component for its
Surface, and expose the template version and projected stable bindings required
by the current UiManifest validator. A visually similar custom chat is a failure.
