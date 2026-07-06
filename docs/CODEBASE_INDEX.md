# XCodeAgent Codebase Index

Last updated: 2026-07-06

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
| `Backend/` | FastAPI backend, agent runtime, planning/orchestration, local workspace tools, bundled docs. | `Backend/app/main.py`, then the relevant module below. |
| `Frontend/` | Electron + React desktop client with Vite, Ant Design, local session/app storage, AG-UI frontend clients. | `Frontend/src/main/index.ts`, `Frontend/src/renderer/src/pages/AppEntryPage.tsx`. |

## Backend

| Path | Responsibility | Read first / common edits |
| --- | --- | --- |
| `Backend/app/main.py` | FastAPI app setup and route registration for `/health`, `/chat`, `/ag-ui`, `/tools/*`, approvals, and git/workspace tools. | Add or change HTTP endpoints here, then update the corresponding runtime/tool module. |
| `Backend/app/config.py` | Environment-backed settings for Anthropic-compatible model calls. | Read when changing `.env` variables or model/client configuration. |
| `Backend/app/llm_client.py` | Anthropic-compatible client factory. | Read when changing provider/auth/base URL behavior. |
| `Backend/app/ag_ui.py` | AG-UI stream adapter, default requirement intake routing, main-agent approval events, and explicit mode routing between chat, planner, and orchestrator behavior. | Read when changing `forwardedProps`, AG-UI event shape, approval payloads, intake-to-agent routing, or frontend/backend agent mode handoff. |
| `Backend/app/agent.py` | Main LangGraph chat/application-development agent, workspace tool prompt, text tool parsing, local tool dispatch, and in-memory sessions. | Read when changing coding-agent behavior, workspace tool use, built-in prompts, or conversation loop behavior. |
| `Backend/app/requirement_intake.py` | Deterministic intake classifier that decides whether a new requirement is simple or complex and identifies the initial frontend/backend/fullstack target. | Read when changing simple-vs-complex routing rules or default main-agent/orchestrator handoff. |
| `Backend/app/development_contract.py` | Shared Development Contract normalization models for SDD, features, API contracts, task graph, and verification plan. | Read when changing planner/orchestrator payload shape or frontend contract typings. |
| `Backend/app/orchestrator.py` | Development orchestrator for clarification, unified planning, task DAG batching, and verification summaries. | Read when changing plan/dispatch/verify behavior. |
| `Backend/app/task_scheduler.py` | Computes task execution modes and serial/parallel batches from target-file isolation and shared-contract rules. | Read when changing subagent direct-write policy or task batching. |
| `Backend/app/run_store.py` | Writes `.xcodeagent/runs/<runId>` artifacts and JSONL run events after a plan is confirmed for dispatch. | Read when changing run artifact lifecycle, retention, or saved contract behavior. |
| `Backend/app/agent_events.py` | Small helpers for append-oriented run event payloads. | Read when changing run event names or JSONL fields. |
| `Backend/app/subagents.py` | Subagent input/result protocol and direct-write guard helpers for scout/build/verifier roles. | Read when wiring real subagent execution or changing subagent payloads. |
| `Backend/app/builtin_skills.py` | Loads bundled backend skill prompt content. | Read when wiring new bundled skills into backend prompts. |
| `Backend/app/tools/requirement_planner.py` | LLM-backed requirement question generation and structured development plan normalization. | Read when changing planner questions, plan schema, or planning prompts. |
| `Backend/app/tools/workspace.py` | Sandboxed workspace/file/search/terminal/git tools, sensitive-file checks, approval gates, and tool capability summaries. | Read when changing local file access, terminal policy, git tools, or workspace tool API contracts. |
| `Backend/app/tools/approvals.py` | In-memory approval grants, one-time approval consumption, reusable same-operation approval rules, and operation fingerprints for protected tool actions. | Read when changing approval lifecycle, approval scopes, or risk gating. |
| `Backend/app/tools/antd_v4_docs.py` | Access to bundled Ant Design v4 docs for prompt/context lookup. | Read when changing AntD docs retrieval. |
| `Backend/app/builtin_skills/react-antd-v4-codegen/` | Bundled React + Ant Design codegen skill and references. | Read `SKILL.md` first; references are task-specific. |
| `Backend/resources/docs/` | Large generated Ant Design docs and JSON catalog. | Avoid broad reads; use `antd_v4_docs.py` or targeted files only. |

## Frontend

| Path | Responsibility | Read first / common edits |
| --- | --- | --- |
| `Frontend/src/main/index.ts` | Electron main process: windows, external/preview browser IPC, app storage, workspace selection/project creation, local session file storage. | Read when changing desktop IPC, persisted app/session storage, preview windows, or filesystem-backed desktop behavior. |
| `Frontend/src/preload/index.ts` and `Frontend/src/preload/index.d.ts` | Context-bridge API exposed as `window.xcodeAgent` plus preload typings. | Update with every new/changed Electron IPC channel. |
| `Frontend/src/renderer/src/window.d.ts` | Renderer-side `window.xcodeAgent` type declarations. | Update with preload API changes. |
| `Frontend/src/renderer/src/main.tsx` | React renderer entry. | Read for app bootstrapping issues. |
| `Frontend/src/renderer/src/pages/AppEntryPage.tsx` | Top-level renderer routing between welcome flow and workbench. | Read when changing app open/create lifecycle. |
| `Frontend/src/renderer/src/pages/WelcomePage.tsx` | Create/open application UI and application metadata collection. | Read when changing app setup, workspace selection, or saved application metadata. |
| `Frontend/src/renderer/src/pages/WorkbenchPage.tsx` | Workbench shell; currently routes the selected app into `LeftPanel`. | Read when changing workbench layout entry. |
| `Frontend/src/renderer/src/components/AiChatPanel/` | Main chat UI, history sidebar, local chat sessions, AG-UI sends, protected-tool approval cards/actions, assistant output publishing, and frontend preview actions. | Read when changing chat, history, AG-UI message sending, approval interactions, or preview controls. |
| `Frontend/src/renderer/src/components/RequirementPlannerPanel/` | Planner UI for collecting answers and rendering structured development plans. | Read when changing requirement planning interactions. |
| `Frontend/src/renderer/src/components/OrchestrationPanel/` | Structured orchestration card for complex requirements, task graph summary, and plan confirmation. | Read when changing development-orchestrator UI or confirmation behavior. |
| `Frontend/src/renderer/src/components/BrowserPreviewPanel/` | Embedded browser preview panel. | Read when changing in-app preview. |
| `Frontend/src/renderer/src/components/ProtectedToolPanel/` | UI for protected tool approvals. | Read when changing approval display/approval actions. |
| `Frontend/src/renderer/src/components/GlobalConfigPanel/` | Global configuration UI. | Read when changing app-wide settings UI. |
| `Frontend/src/renderer/src/components/EditorPanel/` | Displays assistant output bridged into editor-facing panels. | Read when changing editor output presentation. |
| `Frontend/src/renderer/src/components/LeftPanel/` | Left workbench panel composition. | Read when changing panel layout or which assistant panels are mounted. |
| `Frontend/src/renderer/src/components/ActivityBar/`, `ResizeHandle/`, `PlaceholderPanel/`, `MarkdownContent/` | Shared UI pieces for navigation, resizing, empty states, and markdown rendering. | Read only for UI work touching those widgets. |
| `Frontend/src/renderer/src/service/agUiAgent.ts` | Frontend AG-UI client wrappers for chat and requirement planner sessions, including custom approval event parsing. | Read when changing AG-UI request props, approval decisions, parsing structured payloads, or thread/session handling. |
| `Frontend/src/renderer/src/service/orchestratorAgent.ts` | Thin re-export service for development orchestrator AG-UI sessions. | Read when changing frontend orchestrator call sites. |
| `Frontend/src/renderer/src/service/chatSessions.ts` | Renderer chat session model, summary normalization, Electron/localStorage persistence fallback. | Read when changing chat history/session behavior. |
| `Frontend/src/renderer/src/service/applicationStorage.ts` | Stored application list load/save fallback and Electron storage calls. | Read when changing saved applications. |
| `Frontend/src/renderer/src/service/workspaceTools.ts` | Frontend client helpers for backend workspace tools and approval approve/reject calls. | Read when changing workspace tool invocation or approval API usage from the UI. |
| `Frontend/src/renderer/src/context/WorkbenchContext.tsx` | Shared workbench state for assistant messages published to editor panels. | Read when changing cross-panel message flow. |
| `Frontend/src/renderer/src/typings/` | Shared TypeScript domain types for applications, workbench mode, and plans. | Update when payload shape changes. |
| `Frontend/src/renderer/src/constants/` | Static workbench constants. | Read when changing mode/navigation constants. |
| `Frontend/src/renderer/src/utils/` | Class name helper, layout helpers, preview URL/open helpers. | Read for shared renderer utilities. |
| `Frontend/src/renderer/src/styles/global.less` and component `.less` files | Global and component-scoped styling. | Keep style changes near the component unless truly global. |

## Generated Or External-Like Areas

| Path | Notes |
| --- | --- |
| `Frontend/node_modules/`, `Frontend/out/`, `Frontend/dist/` | Generated/dependency output; do not inspect for normal feature work. |
| `Backend/.venv/`, `Backend/.pycache/` | Environment/cache output; do not inspect for normal feature work. |
| `.pnpm-store/` | Package store; do not inspect. |
| `Backend/resources/docs/antd-v4*` | Large bundled docs; prefer targeted lookup through backend docs tooling. |

## Common Change Routes

| Task | Start here | Usually also touch |
| --- | --- | --- |
| Add or change backend API route | `Backend/app/main.py` | Runtime/tool module, frontend service caller, validation docs if behavior changes. |
| Add or change workspace/file/git/terminal tool | `Backend/app/tools/workspace.py` | `Backend/app/main.py`, `Backend/app/agent.py`, frontend tool UI/service if exposed. |
| Change AG-UI chat behavior | `Frontend/src/renderer/src/service/agUiAgent.ts` | `Backend/app/ag_ui.py`, `Backend/app/agent.py`, `AiChatPanel`. |
| Change default requirement intake/routing | `Backend/app/requirement_intake.py` | `Backend/app/ag_ui.py`, `Backend/app/tools/requirement_planner.py`, shared typings if payload shape changes. |
| Change requirement planning | `Backend/app/tools/requirement_planner.py` | `RequirementPlannerPanel`, `agUiAgent.ts`, shared typings. |
| Change orchestration/verification | `Backend/app/orchestrator.py` | `development_contract.py`, `task_scheduler.py`, `run_store.py`, `Backend/app/main.py`, planner types/UI if payload changes. |
| Change Development Contract payload | `Backend/app/development_contract.py` | `Frontend/src/renderer/src/typings/developmentContract.ts`, `requirement_planner.py`, `orchestrator.py`, `OrchestrationPanel`. |
| Change local chat history | `Frontend/src/main/index.ts` | `preload` typings, `chatSessions.ts`, `AiChatPanel`. |
| Change saved applications/workspace open flow | `WelcomePage.tsx` | `applicationStorage.ts`, Electron workspace/app storage IPC, application typings. |
| Change frontend preview | `AiChatPanel` preview actions | `BrowserPreviewPanel`, `utils/previewUrl.ts`, Electron browser IPC. |
| Change shared domain types | `Frontend/src/renderer/src/typings/` | All service/UI/backend payload producers that consume the shape. |

## Validation Routes

| Change type | Expected validation |
| --- | --- |
| Documentation/instruction/index only | No backend health, Vite check, build, or compile required unless explicitly requested. |
| Frontend TypeScript/UI/Electron behavior | `pnpm build` from `Frontend`; if a dev server is running, check the active Vite URL. |
| Backend Python behavior | Focused `python3 -m py_compile` or narrower test for changed Python files, plus `/health`. |
| Backend API/agent behavior | `/health`; add focused endpoint/manual checks when behavior changes. |
