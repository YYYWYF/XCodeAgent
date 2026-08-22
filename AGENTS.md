# Agent Development Habits

These instructions apply to the whole XCodeAgent repository. Every Codex agent should read and follow this file before changing code, docs, scripts, or configuration in this project.

## Working Loop

- Start by checking the current workspace state with `git status --short` and by reading the relevant files before editing.
- Before broad exploration, read `docs/CODEBASE_INDEX.md` and use it to choose the smallest relevant file set. Do not scan the whole repository for simple tasks when the index can route you directly.
- When a substantial code change changes directory ownership, public APIs, Electron IPC, storage formats, AG-UI payloads, workspace tool contracts, or feature boundaries, update `docs/CODEBASE_INDEX.md` in the same patch.
- Prefer small, focused patches that match the existing project structure. Do not rewrite unrelated code, rename files, or introduce new architecture unless the task clearly requires it.
- Use `rg` or `rg --files` for search whenever possible.
- Use precise patches for edits. Avoid whole-file replacement when a targeted change is enough.
- Keep source files focused. When an active source or style file grows beyond roughly 350 lines, or starts mixing unrelated responsibilities, first consider splitting it by feature, hook/service, UI section, or style partial before adding more code. Do not churn deprecated/generated files just to satisfy a line count.
- Treat user changes as intentional. Never revert or overwrite work you did not make unless the user explicitly asks for it.
- Do not run destructive commands such as `rm`, `git reset`, `git clean`, or `git checkout --` without explicit user approval.

## Project Conventions

- Backend code lives under `Backend/app` and is a FastAPI application. Keep API behavior explicit and validate external input with Pydantic at the route boundary or immediately in its protocol adapter. The current public contract includes `/health`; primary AG-UI workflow endpoint `/workflow/run`; independent AG-UI action endpoints `/application-page-planning/run`, `/application-lifecycle/run`, `/skills/run`, and `/agent-files/run`; and the controlled infrastructure endpoints under `/tools/*`.
- Keep backend interfaces explicit: publish current protocol metadata through `/health`; keep current endpoint, payload, event-name, and state-snapshot contracts consistent; and make every AG-UI action flow emit a complete lifecycle (run start, assistant message, custom result/error event, state snapshot, and run finish), including handled business failures.
- Frontend code lives under `Frontend/src` and uses React 18, Vite, Electron, TypeScript, LESS, and Ant Design v4.24.16.
- Every function or method must have a Chinese comment explaining its purpose. Complex logic must also include Chinese comments that explain the key steps, decisions, or non-obvious behavior.
- **All newly developed or materially changed frontend/backend product APIs must use AG-UI end to end. This is a mandatory repository rule, not an agent-only recommendation.** It applies to model calls, deterministic business actions, clarification and confirmation, approvals, progress updates, persistence triggers, success results, and failures. Do not introduce a plain JSON/REST product endpoint, a handwritten `fetch` contract, custom SSE transport, or manual event-shape parser for a new feature flow.
- Every workflow node that generates or updates a formal artifact consumed by later nodes, including RequirementSpec, ProductPlan, UiDesign, and TechnicalPlan documents, must stop for explicit user confirmation before downstream logic runs. Clarification answers only supply missing information and never count as confirmation of the resulting document. Regenerating or revising an artifact requires confirmation of that new version again; node-internal optimizations must not bypass this gate.
- User-facing workflow artifacts are Markdown documents. Treat their JSON counterparts as internal workflow state: do not present JSON files as editable user artifacts. At RequirementSpec, ProductPlan, UiDesign, or TechnicalPlan confirmation, detect user edits to the Markdown, synchronize those edits into the internal JSON while preserving hidden structured details, and only then mark the artifact confirmed.
- Backend feature endpoints must emit AG-UI-compatible event streams with explicit run lifecycle, messages, state snapshots or deltas, and structured results/errors. Frontend callers must use `@ag-ui/client` and `@ag-ui/core` rather than hand-rolled request, SSE, or event parsing logic. Keep independent product flows on separate AG-UI endpoints/thread IDs when they must not enter the existing `/workflow/run` LangGraph workflow.
- Existing infrastructure endpoints such as `/health`, documentation lookup, and low-level workspace/tool routes may remain on their established contracts, but they must not be copied as the transport pattern for new product features. If an external standard makes AG-UI technically impossible for a new product API, stop before implementation, document the conflict, and obtain explicit user approval for the exception.
- For frontend UI work, follow the existing Ant Design v4 patterns and local styles. Do not add another UI framework or a large dependency unless there is a clear need.
- Every new or materially changed UI feature must support both light and dark themes. Define and verify readable colors, borders, backgrounds, hover/focus states, overlays, and empty/loading/error states in both themes. UI changes must inherit the existing theme tokens or add matched light/dark overrides; do not introduce standalone hard-coded visual colors (especially default blue) that diverge from the current purple theme.
- Use `pnpm` for frontend package scripts. The expected Node version is `20.19.0`.
- Keep secrets out of the repository. Do not print, copy, or commit values from `.env`; update `.env.example` when documenting configuration.

## Current-Contract-Only Rule

- This project does not support historical data. During development, implement only the current contract and current storage shape.
- Do not add version probing, migration code, legacy readers, fallback aliases, historical checkpoint recovery, compatibility branches, or dual-write logic unless the user explicitly requests that behavior in the same task.
- When a contract changes, update the current producers, consumers, tests, and documentation together; do not preserve old fields or old file names for compatibility.

## Validation

- Documentation-only, instruction-only, comment-only, or other non-code changes do not require backend health checks, Vite checks, builds, or Python compile checks unless the user explicitly asks for verification.
- After every code change, check that the backend health endpoint is still healthy:
  `curl -sS http://127.0.0.1:8000/health`
- For frontend UI or runtime validation, test the already-running Electron application directly. Do not open or test the Vite-rendered page in a web browser as a substitute for Electron testing. The Vite URL may be checked only as a development-server health signal and does not count as frontend UI validation.
- For frontend changes, run `pnpm build` from `Frontend` when the change can affect TypeScript, bundling, routing, or UI behavior.
- For backend Python changes, run a focused Python syntax/import check, such as `python3 -m py_compile`, on the changed files when no narrower test exists.
- If any health check, build, or focused verification fails, investigate and fix it before reporting the change as done. If a failure cannot be fixed in the current turn, report the exact failing command and why it remains unresolved.

## Reporting

- In the final response, summarize what changed, which files were touched, and which validation commands passed or failed.
- Mention any skipped verification explicitly, including the reason.
