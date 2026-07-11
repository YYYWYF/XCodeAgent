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

- Backend code lives under `Backend/app` and is a FastAPI application. Keep API behavior explicit, typed with Pydantic where appropriate, and compatible with the existing `/health`, `/chat`, `/ag-ui`, and `/tools/*` endpoints.
- Frontend code lives under `Frontend/src` and uses React 18, Vite, Electron, TypeScript, LESS, and Ant Design v4.24.16.
- **All newly developed or materially changed frontend/backend product APIs must use AG-UI end to end. This is a mandatory repository rule, not an agent-only recommendation.** It applies to model calls, deterministic business actions, clarification and confirmation, approvals, progress updates, persistence triggers, success results, and failures. Do not introduce a plain JSON/REST product endpoint, a handwritten `fetch` contract, custom SSE transport, or manual event-shape parser for a new feature flow.
- Every workflow node that generates or updates a formal artifact consumed by later nodes, including RequirementSpec and ProjectPlan documents, must stop for explicit user confirmation before downstream logic runs. Clarification answers only supply missing information and never count as confirmation of the resulting document. Regenerating or revising an artifact requires confirmation of that new version again; node-internal optimizations must not bypass this gate.
- User-facing workflow artifacts are Markdown documents. Treat their JSON counterparts as internal workflow state: do not present JSON files as editable user artifacts. At RequirementSpec or ProjectPlan confirmation, detect user edits to the Markdown, synchronize those edits into the internal JSON while preserving hidden structured details, and only then mark the artifact confirmed.
- Backend feature endpoints must emit AG-UI-compatible event streams with explicit run lifecycle, messages, state snapshots or deltas, and structured results/errors. Frontend callers must use `@ag-ui/client` and `@ag-ui/core` rather than hand-rolled request, SSE, or event parsing logic. Keep independent product flows on separate AG-UI endpoints/thread IDs when they must not enter the existing `/workflow/run` LangGraph workflow.
- Existing infrastructure endpoints such as `/health`, documentation lookup, and low-level workspace/tool routes may remain on their established contracts, but they must not be copied as the transport pattern for new product features. If an external standard makes AG-UI technically impossible for a new product API, stop before implementation, document the conflict, and obtain explicit user approval for the exception.
- For frontend UI work, follow the existing Ant Design v4 patterns and local styles. Do not add another UI framework or a large dependency unless there is a clear need.
- Every new or materially changed UI feature must support both light and dark themes. Define and verify readable colors, borders, backgrounds, hover/focus states, overlays, and empty/loading/error states in both themes.
- Use `pnpm` for frontend package scripts. The expected Node version is `20.19.0`.
- Keep secrets out of the repository. Do not print, copy, or commit values from `.env`; update `.env.example` when documenting configuration.

## Agent Feature Architecture

When developing XCodeAgent agent features, use `learn-coding-agent` and OpenCode as source-level reference architectures, not as vague inspiration. This applies to the runtime loop, tools, filesystem access, memory, context management, planning, subagents, permissions, hooks, checkpoints, session storage, and verification workflows.

- Before designing or changing agent functionality, inspect the relevant source or docs from these reference repositories:
  - `https://github.com/YYYWYF/learn-coding-agent` as the primary learning-oriented coding-agent source reference. Use it to understand compact agent loops, codebase navigation, editing flow, terminal/tool integration, and minimal implementation boundaries.
  - `https://github.com/anomalyco/opencode` as the primary production-grade open-source coding-agent reference. Use it for build/plan agents, general subagents, tool orchestration, permissions, session behavior, TUI/desktop patterns, model-provider abstraction, and repository-scale architecture.
- If an agent design decision differs from both `learn-coding-agent` and OpenCode, document why XCodeAgent is intentionally different.
- Treat the model context window as 128k tokens. Do not design features that rely on the model seeing an entire repository, full session history, or unbounded tool output.
- Follow the coding-agent loop shown by the reference repositories: gather context, take action, verify results, and repeat until the task is done or blocked.
- Follow Deep Agents for the default harness shape: planning, tool use, filesystem-backed work, subagents, context compression, long-term memory, and human-in-the-loop control.
- Keep context small by default. Load instructions, skills, docs, memories, and tool results with progressive disclosure; retrieve only what is relevant to the current step.
- Offload large tool outputs, logs, search results, and file snapshots to durable storage. Return concise summaries, stable file references, and metadata to the main agent context.
- Use subagents for high-volume exploration, research, log analysis, broad code search, and specialized work that would otherwise pollute the main conversation. Subagents should run with isolated context, narrow tools, explicit task prompts, and concise final summaries.
- Preserve important state outside the prompt: user requirements, accepted plans, architectural decisions, open questions, file changes, command results, approvals, and verification history.
- Design compaction before it is needed. Summaries must preserve user intent, current plan, changed files, unresolved risks, and next actions; assume older conversation detail can disappear.
- Enforce safety in tools and sandboxes, not just in prompts. Treat LLM output as untrusted. Reads, writes, shell commands, network access, Git operations, and sensitive files need explicit policy boundaries.
- Mirror the reference repositories' permission model where practical: read-only actions can be low friction, file writes and shell commands need auditable approval paths, and destructive or secret-touching operations must be denied or require explicit user approval.
- Make sessions resumable and inspectable. Prefer append-oriented event logs, checkpoints before edits, and enough metadata to fork, replay, debug, or audit an agent run.
- Add hooks or middleware for repeatable lifecycle behavior such as validation after edits, policy checks before tools, telemetry, and automatic context cleanup.
- Before implementing or changing an agent capability, write down the intended mapping to `learn-coding-agent`, OpenCode, and Deep Agents patterns, plus how the design stays within the 128k context budget.

## Validation

- Documentation-only, instruction-only, comment-only, or other non-code changes do not require backend health checks, Vite checks, builds, or Python compile checks unless the user explicitly asks for verification.
- After every code change, check that the backend health endpoint is still healthy:
  `curl -sS http://127.0.0.1:8000/health`
- When a frontend development server is running, also check the active Vite URL after code changes. The default is `http://127.0.0.1:5173`; if Vite reports a different port, use that port instead.
- For frontend changes, run `pnpm build` from `Frontend` when the change can affect TypeScript, bundling, routing, or UI behavior.
- For backend Python changes, run a focused Python syntax/import check, such as `python3 -m py_compile`, on the changed files when no narrower test exists.
- If any health check, build, or focused verification fails, investigate and fix it before reporting the change as done. If a failure cannot be fixed in the current turn, report the exact failing command and why it remains unresolved.

## Reporting

- In the final response, summarize what changed, which files were touched, and which validation commands passed or failed.
- Mention any skipped verification explicitly, including the reason.
