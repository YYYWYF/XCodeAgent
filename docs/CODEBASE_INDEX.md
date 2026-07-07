# XCodeAgent Codebase Index

Last updated: 2026-07-07

Use this file before broad code exploration. It is a routing map, not full documentation: read the matching row, then inspect only the listed entry files and nearby code.

## Update Policy

Update this index in the same change when a code change:

- Adds, removes, renames, or moves a directory or major module.
- Adds a new feature slice that changes ownership boundaries.
- Changes public API routes, Electron IPC channels, storage formats, AG-UI payloads, or workspace tool contracts.
- Changes validation/build expectations for an area.

For small local edits that do not change structure or ownership, leave the index alone and mention that no index update was needed.

## Top-Level Map

| Path | Responsibility | Read first |
| --- | --- | --- |
| `AGENTS.md` | Repository-wide agent instructions, validation rules, and architecture constraints. | Always read before edits. |
| `docs/CODEBASE_INDEX.md` | Directory/function routing map for future agents. | Read before broad search or planning. |
| `scripts/start-backend.sh` | Standard backend dev-server command for agents and humans. | Run this when asked to start the backend service. |
| `scripts/build-backend-mac.sh` | macOS PyInstaller build and staging script for the packaged backend executable plus `.env`. | Run on macOS before `Frontend` mac packaging. |
| `scripts/build-backend-win.ps1` | Windows-only PyInstaller build and staging script for the packaged backend exe plus `.env`. | Run on a Windows CI/VM before `Frontend` Windows packaging. |
| `Backend/` | FastAPI backend, agent runtime, planning/orchestration, local workspace tools, bundled docs. | `Backend/app/main.py`, then the relevant module below. |
| `Frontend/` | Electron + React desktop client with Vite, Ant Design, local session/app storage, AG-UI frontend clients. | `Frontend/src/main/index.ts`, `Frontend/src/renderer/src/pages/AppEntryPage.tsx`. |

## Backend

```text
Backend/app/
├── main.py
├── config.py
├── graph/          # LangGraph main business flows
├── agents/         # First-class Deep Agent runtimes and contracts
├── domain/         # Framework-independent core data models
├── services/       # Deterministic business rules and shared services
├── tools/          # Controlled tools exposed to Deep Agents
├── protocols/      # Protocol integration
├── workspace/      # User-local workspace management
├── middleware/     # Cross-cutting Deep Agent controls
├── persistence/    # Business and runtime persistence
└── observability/  # Runtime events and observability
```

| Path | Responsibility | Read first / common edits |
| --- | --- | --- |
| `Backend/app/main.py` | FastAPI app setup and route registration for `/health`, `/chat`, `/ag-ui`, `/tools/*`, approvals, and git/workspace tools. | Add or change HTTP endpoints here, then update the corresponding runtime/tool module. |
| `Backend/app/config.py` | Environment-backed model Provider, endpoint, credentials, and runtime settings. | Read when changing `.env` variables or model/client configuration. |
| `Backend/app/graph/agent.py` | Main LangGraph chat/application-development flow, workspace tool dispatch, code-change aggregation, and in-memory sessions. | Read when changing coding-agent behavior, tool use, prompts, or conversation flow. |
| `Backend/app/graph/orchestrator.py` | Development orchestration flow for clarification, planning, task DAG batching, and verification. | Read when changing plan/dispatch/verify behavior. |
| `Backend/app/agents/requirement_planner.py` | LLM-backed requirement questions and structured development-plan normalization. | Read when changing planner questions, schema, state, or prompts. |
| `Backend/app/agents/subagents.py` | Subagent input/result contracts and direct-write guards. | Read when wiring scout/build/verifier agents or changing subagent payloads. |
| `Backend/app/domain/development_contract.py` | Framework-independent Development Contract models and normalization. | Read when changing SDD, features, API contracts, task graph, or verification plan. |
| `Backend/app/services/requirement_intake.py` | Deterministic simple/complex requirement classification and target routing. | Read when changing intake rules or agent handoff. |
| `Backend/app/services/task_scheduler.py` | Deterministic serial/parallel task batching and execution assignment. | Read when changing task isolation or scheduling rules. |
| `Backend/app/services/llm_client.py` | Provider-neutral model response contract plus Anthropic and OpenAI-compatible adapters. | Read when changing provider selection, request/response conversion, tools, auth, or base URL behavior. |
| `Backend/app/services/builtin_skills.py` | Loads bundled backend skill prompt content. | Read when wiring bundled skills into prompts. |
| `Backend/app/tools/antd_v4_docs.py` | Access to bundled Ant Design v4 docs for prompt/context lookup. | Read when changing AntD docs retrieval. |
| `Backend/app/protocols/ag_ui.py` | AG-UI stream adapter, agent-mode routing, approval events, and workspace code-change events. | Read when changing `forwardedProps`, AG-UI event shapes, or frontend/backend handoff. |
| `Backend/app/workspace/workspace.py` | Sandboxed workspace/file/search/terminal/Git operations, sensitive-file checks, approval gates, and code-change payloads. | Read when changing workspace management or agent-facing workspace tool contracts. |
| `Backend/app/middleware/approvals.py` | Approval grants, reusable operation rules, and protected-operation fingerprints. | Read when changing approval lifecycle, scopes, or risk gating. |
| `Backend/app/persistence/run_store.py` | Writes `.xcodeagent/runs/<runId>` artifacts and JSONL run events. | Read when changing run artifact lifecycle, formats, or retention. |
| `Backend/app/observability/agent_events.py` | Helpers for append-oriented agent run event payloads. | Read when changing event names or JSONL fields. |
| `Backend/app/builtin_skills/react-antd-v4-codegen/` | Bundled React + Ant Design codegen skill and references. | Read `SKILL.md` first; references are task-specific. |
| `Backend/resources/docs/` | Large generated Ant Design docs and JSON catalog. | Avoid broad reads; use `antd_v4_docs.py` or targeted files only. |

## Frontend

| Path | Responsibility | Read first / common edits |
| --- | --- | --- |
| `Frontend/src/main/index.ts` | Electron main process: windows, external/preview browser IPC, app storage, workspace selection/project creation, local session file storage, and historical session workspace listing. | Read when changing desktop IPC, persisted app/session storage, preview windows, or filesystem-backed desktop behavior. |
| `Frontend/src/main/backendService.ts` | Packaged Windows/macOS backend service lifecycle: locating the PyInstaller executable, selecting a local port, health polling, renderer base URL, and process shutdown. | Read when changing bundled backend startup, ports, health checks, or quit cleanup. |
| `Frontend/src/preload/index.ts` and `Frontend/src/preload/index.d.ts` | Context-bridge API exposed as `window.xcodeAgent` plus preload typings, including session workspace history calls. | Update with every new/changed Electron IPC channel. |
| `Frontend/src/renderer/src/window.d.ts` | Renderer-side `window.xcodeAgent` type declarations. | Update with preload API changes. |
| `Frontend/src/renderer/src/main.tsx` | React renderer entry. | Read for app bootstrapping issues. |
| `Frontend/src/renderer/src/pages/AppEntryPage.tsx` | Top-level renderer routing between welcome flow and workbench. | Read when changing app open/create lifecycle. |
| `Frontend/src/renderer/src/pages/WelcomePage.tsx` | Create/open application UI, historical workspace selection, and application metadata collection. | Read when changing app setup, workspace selection, or saved application metadata. |
| `Frontend/src/renderer/src/pages/WorkbenchPage.tsx` | Workbench shell; currently routes the selected app into `LeftPanel`. | Read when changing workbench layout entry. |
| `Frontend/src/renderer/src/components/AiChatPanel/` | Main chat UI, history sidebar, local chat sessions, AG-UI sends, protected-tool approval cards/actions, code-change diff review cards/panels, assistant output publishing, and frontend preview actions. | Read when changing chat, history, AG-UI message sending, approval interactions, diff review UI, or preview controls. |
| `Frontend/src/renderer/src/components/RequirementPlannerPanel/` | Planner UI for collecting answers and rendering structured development plans. | Read when changing requirement planning interactions. |
| `Frontend/src/renderer/src/components/OrchestrationPanel/` | Structured orchestration card for complex requirements, task graph summary, and plan confirmation. | Read when changing development-orchestrator UI or confirmation behavior. |
| `Frontend/src/renderer/src/components/BrowserPreviewPanel/` | Embedded browser preview panel. | Read when changing in-app preview. |
| `Frontend/src/renderer/src/components/ProtectedToolPanel/` | UI for protected tool approvals. | Read when changing approval display/approval actions. |
| `Frontend/src/renderer/src/components/GlobalConfigPanel/` | Global configuration UI. | Read when changing app-wide settings UI. |
| `Frontend/src/renderer/src/components/EditorPanel/` | Displays assistant output bridged into editor-facing panels. | Read when changing editor output presentation. |
| `Frontend/src/renderer/src/components/LeftPanel/` | Left workbench panel composition. | Read when changing panel layout or which assistant panels are mounted. |
| `Frontend/src/renderer/src/components/ActivityBar/`, `ResizeHandle/`, `PlaceholderPanel/`, `MarkdownContent/` | Shared UI pieces for navigation, resizing, empty states, and markdown rendering. | Read only for UI work touching those widgets. |
| `Frontend/src/renderer/src/service/agUiAgent.ts` | Frontend AG-UI client wrappers for chat and requirement planner sessions, including custom approval and workspace code-change event parsing. | Read when changing AG-UI request props, approval decisions, parsing structured payloads, code-change payloads, or thread/session handling. |
| `Frontend/src/renderer/src/service/orchestratorAgent.ts` | Thin re-export service for development orchestrator AG-UI sessions. | Read when changing frontend orchestrator call sites. |
| `Frontend/src/renderer/src/service/chatSessions.ts` | Renderer chat session model, summary/workspace normalization, code-change message persistence, Electron/localStorage persistence fallback. | Read when changing chat history/session behavior or persisted message shape. |
| `Frontend/src/renderer/src/service/applicationStorage.ts` | Stored application list load/save fallback and Electron storage calls. | Read when changing saved applications. |
| `Frontend/src/renderer/src/service/workspaceTools.ts` | Frontend client helpers for backend workspace tools and approval approve/reject calls. | Read when changing workspace tool invocation or approval API usage from the UI. |
| `Frontend/src/renderer/src/context/WorkbenchContext.tsx` | Shared workbench state for assistant messages published to editor panels. | Read when changing cross-panel message flow. |
| `Frontend/src/renderer/src/typings/` | Shared TypeScript domain types for applications, workbench mode, and plans. | Update when payload shape changes. |
| `Frontend/src/renderer/src/constants/` | Static workbench constants. | Read when changing mode/navigation constants. |
| `Frontend/src/renderer/src/utils/` | Class name helper, layout helpers, preview URL/open helpers. | Read for shared renderer utilities. |
| `Frontend/src/renderer/src/styles/global.less` and component `.less` files | Global and component-scoped styling. | Keep style changes near the component unless truly global. |
| `Frontend/scripts/verify-backend-resource.mjs` | Preflight check used by Windows/macOS Electron package scripts to ensure platform-specific `resources/backend/<platform>` contains the staged PyInstaller backend and `.env`. | Read when changing frontend desktop packaging requirements. |

## Generated Or External-Like Areas

| Path | Notes |
| --- | --- |
| `Frontend/node_modules/`, `Frontend/out/`, `Frontend/dist/` | Generated/dependency output; do not inspect for normal feature work. |
| `Frontend/resources/backend/` | Generated platform backend staging output from `scripts/build-backend-win.ps1` and `scripts/build-backend-mac.sh`; contains PyInstaller support files and copied `.env`. Do not commit or inspect secrets. |
| `Backend/build/`, `Backend/dist/` | Generated PyInstaller build output. |
| `Backend/.venv/`, `Backend/.pycache/` | Environment/cache output; do not inspect for normal feature work. |
| `.pnpm-store/` | Package store; do not inspect. |
| `Backend/resources/docs/antd-v4*` | Large bundled docs; prefer targeted lookup through backend docs tooling. |

## Common Change Routes

| Task | Start here | Usually also touch |
| --- | --- | --- |
| Add or change backend API route | `Backend/app/main.py` | Runtime/tool module, frontend service caller, validation docs if behavior changes. |
| Add or change workspace/file/git/terminal tool | `Backend/app/workspace/workspace.py` | `Backend/app/main.py`, `Backend/app/graph/agent.py`, frontend tool UI/service if exposed. |
| Change AG-UI chat behavior | `Frontend/src/renderer/src/service/agUiAgent.ts` | `Backend/app/protocols/ag_ui.py`, `Backend/app/graph/agent.py`, `AiChatPanel`. |
| Change default requirement intake/routing | `Backend/app/services/requirement_intake.py` | `Backend/app/protocols/ag_ui.py`, `Backend/app/agents/requirement_planner.py`, shared typings if payload shape changes. |
| Change requirement planning | `Backend/app/agents/requirement_planner.py` | `RequirementPlannerPanel`, `agUiAgent.ts`, shared typings. |
| Change orchestration/verification | `Backend/app/graph/orchestrator.py` | `domain/development_contract.py`, `services/task_scheduler.py`, `persistence/run_store.py`, `Backend/app/main.py`, planner types/UI if payload changes. |
| Change Development Contract payload | `Backend/app/domain/development_contract.py` | `Frontend/src/renderer/src/typings/developmentContract.ts`, `agents/requirement_planner.py`, `graph/orchestrator.py`, `OrchestrationPanel`. |
| Change local chat history | `Frontend/src/main/index.ts` | `preload` typings, `chatSessions.ts`, `AiChatPanel`. |
| Change saved applications/workspace open flow | `WelcomePage.tsx` | `applicationStorage.ts`, Electron workspace/app storage IPC, application typings. |
| Change frontend preview | `AiChatPanel` preview actions | `BrowserPreviewPanel`, `utils/previewUrl.ts`, Electron browser IPC. |
| Change shared domain types | `Frontend/src/renderer/src/typings/` | All service/UI/backend payload producers that consume the shape. |

## Validation Routes

| Change type | Expected validation |
| --- | --- |
| Documentation/instruction/index only | No backend health, Vite check, build, or compile required unless explicitly requested. |
| Frontend TypeScript/UI/Electron behavior | `pnpm build` from `Frontend`; if a dev server is running, check the active Vite URL. |
| Windows Electron package with bundled backend | Run `scripts/build-backend-win.ps1` on Windows first, then `pnpm build:win:dev` from `Frontend`. |
| macOS Electron package with bundled backend | Run `scripts/build-backend-mac.sh` on macOS first, then `pnpm build:mac:dev` from `Frontend`. |
| Backend Python behavior | Focused `python3 -m py_compile` or narrower test for changed Python files, plus `/health`. |
| Backend API/agent behavior | `/health`; add focused endpoint/manual checks when behavior changes. |
