# Application Page Planning

## Scope

After a new application directory is created, XCodeAgent runs a standalone page-planning flow:

1. Ask the configured model for focused clarification questions using the application name and scenario.
2. Ask the model for a compact page structure after the user answers those questions.
3. Show the proposed pages and their purposes to the user for review.
4. Let the user submit free-form revision feedback and repeatedly update the current proposal.
5. Update the application workspace's `application.json` with the confirmed menu structure.

This flow does not call, resume, or modify the existing LangGraph workflow. Questions, page proposals, and confirmed persistence all use the dedicated `/application-page-planning/run` AG-UI SSE endpoint and one page-planning thread id; transient review state stays in the creation UI.

## Reference Architecture Mapping

- **learn-coding-agent:** keep the loop narrow and explicit: gather the minimum context (name, scenario, terminal, answers), take one model-backed planning action, then wait for user verification before writing.
- **OpenCode:** treat the review as a human-in-the-loop permission boundary. Model output is untrusted structured input; Pydantic validates and normalizes it before the UI or filesystem consumes it.
- **Deep Agents:** use progressive disclosure and filesystem-backed durable state. Only the confirmed artifact is durable; intermediate prompts and model output stay out of the main workflow graph and session history.

XCodeAgent intentionally uses a small, graph-free AG-UI agent endpoint instead of a new agent graph because this feature has only two bounded reasoning steps and one deterministic write. The same page-planning thread id ties the clarification and proposal runs together without coupling them to the workflow thread or state.

## Context Budget

The model receives only application metadata, at most five short question/answer pairs, and—during revision—the current normalized proposal plus the latest user feedback. Responses are limited to five questions or twelve pages with short purpose and feature fields. No repository tree, workflow history, tool logs, or generated source files are included, keeping the flow safely within the 128k context budget.

## Persistence and Safety

- `application.json` is updated only by the explicit AG-UI `confirm` action. Existing application configuration is preserved; `menus.homeMenuKey` is `default`, same-level page routes become `items`, and shared first-level route paths become a `menu` whose `children` are page entries. Every menu/page object stores its own `purpose` and `keyFeatures`.
- Clarification answers and the intermediate `pagePlan` are never persisted. Confirmation also removes stale `pagePlan`, `clarification`, or `clarifications` fields left by an earlier version.
- Model runs are transported with `@ag-ui/client`, `@ag-ui/core`, AG-UI events, and state snapshots; no handwritten SSE parsing is used.
- The target is always the fixed filename `application.json` directly under a validated workspace directory; callers cannot provide an arbitrary relative file path.
- The backend serializes the validated page-plan model instead of persisting raw model text.
- The write uses a temporary sibling file followed by replacement so readers never observe a partially written JSON document.
