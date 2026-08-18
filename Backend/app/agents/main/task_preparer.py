from __future__ import annotations

from hashlib import sha256
import json
import logging
from typing import Any

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.build_task_planner import create_build_task_plan
from app.utils.model_output import extract_json_object


def _app_name_from_plan(project_plan: dict[str, Any]) -> str:
    """从任务准备上下文中读取应用名，用于推导用户应用前端根目录。"""

    app = project_plan.get("app") or {}
    if isinstance(app, dict):
        name = app.get("name") or app.get("appName")
        if name:
            return str(name)
    return ""


logger = logging.getLogger(__name__)


def _springboot_mybatis_skill_document() -> str:
    """读取内置 springboot-mybatis-generate 技能的 SKILL.md 全文。

    任务规划模型处于 planning-only 边界，无法调用技能工具读取文件，因此需要把
    SKILL.md 的完整内容直接内联进 prompt 上下文。文件缺失时返回降级提示，
    避免规划静默失去后端架构约束。
    """

    from app.services.builtin_skills import read_builtin_skill_md

    content = read_builtin_skill_md("springboot-mybatis-generate")
    if content:
        return content
    return (
        "(springboot-mybatis-generate SKILL.md 未找到，请仍按 Spring Boot + "
        "MyBatis-Plus 标准分层架构规划后端任务：对象类(Entity/PO/DTO/Converter/Assembler) → "
        "Repository/Mapper → ApplicationService → Controller。)"
    )


def _task_preparation_prompt(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
    build_context: dict[str, Any] | None = None,
) -> str:
    """组合全局计划与定向详情上下文，约束模型仅返回当前 Unit 的任务候选。"""
    if _task_preparation_datasource_type(project_plan) == "static":
        return _static_task_preparation_prompt(
            project_plan,
            workspace_snapshot,
            build_context,
        )
    snapshot_text = json.dumps(
        _compact_workspace_snapshot(workspace_snapshot),
        ensure_ascii=False,
        indent=2,
    )
    app_name = _app_name_from_plan(project_plan)
    # 直接平铺到根目录，不再嵌套 apps/<app_name>/ 前缀
    frontend_root = "frontend"
    backend_root = "backend"
    # 规划模型处于 planning-only 边界，无法读取技能文件，须把 SKILL.md 内联进 prompt。
    backend_skill_document = _springboot_mybatis_skill_document()
    # 从 WorkspaceSnapshot 中提取真实后端目录树，直接注入 prompt。
    compact_snapshot = _compact_workspace_snapshot(workspace_snapshot)
    backend_snapshot = compact_snapshot.get("backend") if compact_snapshot else None
    backend_dir_structure = (
        backend_snapshot.get("dir_structure")
        if isinstance(backend_snapshot, dict) and backend_snapshot.get("dir_structure")
        else None
    )
    return (
        "You are the build-task planning model for an app-generation workflow.\n"
        "This is a planning-only boundary. Do not call tools, do not call subagents, "
        "do not inspect files outside the provided WorkspaceSnapshot, and do not generate "
        "or modify code.\n"
        "Use the deterministic WorkspaceSnapshot as the primary source for the current "
        "source tree, package/framework conventions, route entry, API/data layer, shared "
        "modules, tests, and relevant existing files.\n"
        
        f"Frontend path convention: all generated frontend code MUST live under the "
        f"virtual path `/{frontend_root}/` (resolved from ProjectPlan.app.name). Every "
        f"`src/...` path in the frontend-template-modification-boundary skill is relative "
        f"to `/{frontend_root}/`, so `src/pages/<PageKey>/index.tsx` must be planned as "
        f"`/{frontend_root}/src/pages/<PageKey>/index.tsx` in change_scope paths. Do NOT "
        f"use `Frontend/src/`, bare `src/`, or `/app/frontend/` — those are wrong for a "
        f"user-application workspace.\n"
        
        "Backend path convention: this is a Spring Boot + MyBatis-Plus Maven project "
        f"rooted at `/{backend_root}/` (the directory containing pom.xml). Every backend "
        "file MUST be planned under `/{backend_root}/src/main/java/...` or "
        "`/{backend_root}/src/main/resources/...`; never use a bare `src/`, `app/backend/`, "
        "or any other backend root. Do NOT plan tasks to create pom.xml, the main "
        "application class, or the framework skeleton — those already exist.\n"
        "The current real backend directory tree on disk is below. Directories end with "
        "`/`, files are leaves, and build artifacts (e.g. `target/`) are already excluded "
        "from the snapshot:\n"
        "--- CURRENT BACKEND DIRECTORY STRUCTURE (workspace_snapshot.backend.dir_structure) ---\n"
        f"{backend_dir_structure}\n"
        "--- END BACKEND DIRECTORY STRUCTURE ---\n"
        "Plan every backend task against that tree: reuse existing packages and do not "
        "re-plan files already present. Only add new files under "
        f"`/{backend_root}/src/main/java/...` or `/{backend_root}/src/main/resources/...`.\n"
        "The ONLY authoritative reference for backend code organization, file structure, "
        "naming rules, type mapping, and generation order is the injected "
        "`springboot-mybatis-generate` SKILL.md below. The pre-check can be planned as a "
        "separate backend task (owner: backend) that only checks and fills missing "
        "infrastructure (pom.xml dependencies, application.yml datasource, MyBatisPlusConfig) "
        "and generates no business code. For each backend module (one endpoint/table), plan "
        "FOUR separate backend stage tasks (owner: backend) in the 4-phase order instead of "
        "one monolithic module task: stage 1 object classes (Entity/PO/DTO/Converter/"
        "Assembler), stage 2 repository (Mapper/Mapper.xml/Repository/RepositoryImpl), "
        "stage 3 application service (ApplicationService), stage 4 controller (REST "
        "Controller). Each stage task owns ONLY its own files in change_scope; never merge "
        "stages and never add another stage's files to a task's change_scope. Chain stage "
        "dependencies in order (stage 2 depends on stage 1, stage 3 on stage 2, stage 4 on "
        "stage 3); when the pre-check task exists, stage 1 must list it in dependencies. "
        "Write each stage's expected goal — the exact workspace-relative paths it will "
        "create, each file's responsibility, and the contracts/class names downstream "
        "stages must reference — into the NEXT stage task's description as execution "
        "context, so every stage knows what its predecessor produced and can reference "
        "those artifacts. A deterministic compiler derives engineering acceptance checks "
        "from each stage task's exact change_scope. Follow its naming conversions "
        "(table snake_case → PascalCase class / camelCase module, table → REST path), "
        "and its MySQL-to-Java type mapping.\n"
        
        "--- INJECTED springboot-mybatis-generate SKILL.md (planning model cannot read "
        "skills, content inlined) ---\n"
        + backend_skill_document
        + "\n"
        "--- END INJECTED SKILL.md ---\n"

        "NOTE: any acceptance guidance inside the injected SKILL.md (for example "
        "`acceptance_criteria` covering compilation or REST availability) describes "
        "generated-code content expectations only and is SUPERSEDED for task output: "
        "tasks MUST still return `acceptance_criteria: []` and `acceptance_checks: []`; "
        "never copy SKILL.md acceptance items into task fields.\n"
          
        "前后端任务只生成代码相关的工作，不要生成任何验证工作：不要规划编写或运行测试、"
        "执行构建/类型检查、冒烟验证、接口联通检查等验证类任务，也不要规划测试文件"
        "（*.test.*、*.spec.*、Java 测试类等）。不要把 PageDetail、EndpointDetail 或需求文档中的"
        "业务验收标准复制到任务；acceptance_criteria 和 acceptance_checks 都必须返回空数组（[]），"
        "模型不得输出任何验收检查对象或验收文案，后端会根据 change_scope、"
        "allowed_paths、菜单元数据和正式 API 契约确定性生成纯工程验收点并写入 acceptance_checks。"
        "verification_commands 必须留空；工程验收由确定性 harness 执行，build/lint/typecheck/test "
        "由后续集成测试阶段负责。\n"
          
        "Backend API base address and frontend API call validation:\n"
        "CRITICAL: the Spring Boot backend starts on port 8080, so its base address is "
        "`http://localhost:8080`. This base address is centralized in the frontend "
        "template skeleton's `src/apis/service.ts` axios instance and MUST NOT be "
        "hardcoded, re-configured, or rewritten in any business file (including "
        "`src/apis/<biz>Api.ts`, pages, hooks, or utils). Do not plan any task that "
        "modifies `service.ts`, `.env`, or `vite.config.ts` to change the backend "
        "address.\n"
        "Every frontend page task whose confirmed page_detail_plans declare "
        "api_dependencies MUST also plan the matching business API service file "
        "`src/apis/<biz>Api.ts` (owner: frontend, one per business module) when it does "
        "not already exist in the snapshot. That service file imports the shared axios "
        "instance from `src/apis/service.ts` (base address `http://localhost:8080`) and "
        "exports typed request/response functions for exactly the confirmed endpoints "
        "resolved from executable_details.api_contracts. Page components MUST call "
        "those service functions via `@/apis/<biz>Api` and MUST NOT call axios or raw "
        "HTTP directly, invent URLs, or hardcode `http://localhost:8080` in page code. "
        "Include `src/apis/<biz>Api.ts` in the page task's change_scope when the page "
        "task is its only writer, or plan it as a separate prerequisite frontend task "
        "when multiple pages share the same business module.\n"
        
        "Prepare executable tasks ONLY from TaskPreparationContext.executable_details "
        "and TargetBuildContext. TaskPreparationContext.application_skeleton is a "
        "non-executable Unit skeleton: it describes all pages, data sources, public "
        "application units, and API ranges, but it must never cause tasks for pages or "
        "data sources outside TargetBuildContext.required_unit_ids.\n"
        "Plan tasks per bound entity, never per endpoint-level data source: resolve each "
        "endpoint's bound entities from executable_details.entity_designs (each entry carries "
        "entity_id/entity_name/data_source_type plus the confirmed database/external_api/static "
        "design summary). A database entity produces backend data read-write tasks; an "
        "external_api entity produces backend call "
        "tasks from its confirmed field mappings; a static entity produces "
        "frontend:data:static in-memory mock modules. When an endpoint binds entities with "
        "different data source types, plan separate tasks for each entity source instead of "
        "classifying the endpoint as a single data source. Database table operations are "
        "already executed and confirmed during entity design: in endpoint/page scopes NEVER "
        "create owner=database tasks or database:* Unit tasks. API contracts in "
        "executable_details.api_contracts are only request/response schema references and "
        "must never be used to infer a data source.\n"
        "Do not invent generic paths when an existing project convention is present in "
        "the snapshot. For page tasks, use only confirmed executable_details.page_detail_plans "
        "and use page_goal, layout_design, operation_interactions, state_feedback, "
        "api_dependencies, response_bindings, page_navigation, permissions, and "
        "acceptance_criteria as implementation context only; never copy those business criteria into task output. Frontend page tasks must additionally follow the "
        "frontend-backend API matching rules in this prompt: resolve every api_dependencies "
        "endpoint id to its exact method/path/schemas in executable_details.api_contracts and "
        "never invent endpoints or fields. For backend/data tasks, use only executable_details."
        "endpoint_detail_plans, executable_details.data_sources, and "
        "executable_details.api_contracts. ProjectPlan/API contracts in executable_details "
        "are the only source of fields; preserve schema_refs, endpoint ids, "
        "request/response schema refs, and page response_bindings in task source references.\n"
        
        "When executable_details.database_planning_context.schema_version is "
        "`database-context.v1` and status is completed, `gaps` is the complete, "
        "deterministic list of genuine schema differences. A required field already "
        "satisfied — same name (case-insensitive) and a compatible/semantically "
        "equivalent type — needs no database task. Create a database task ONLY for "
        "`database_change` gaps (missing_database/missing_table/missing_column/"
        "incompatible_column_type/nullable_mismatch); if `gaps` is empty or none is "
        "`database_change`, do NOT create database tasks. `backend_adaptation` belongs in "
        "backend tasks; `needs_confirmation` is never a task. "
        "##TASK GENERATION PRIORITY — plan database tasks first, then backend tasks, "
        "then frontend tasks: generate the full set of database tasks before any backend "
        "task, and generate backend tasks before frontend tasks, so frontend API/page "
        "tasks are only planned after the database and backend tasks they rely on. "
        "##Merge ALL `database_change` gaps on the same table into ONE database task. "
        "Every database task MUST carry a `database_scope` with the validated shape "
        "`{\"gaps\": [full original gap objects]}` and `gap_ids` covering all merged "
        "gaps — downstream reconstructs the target schema from `database_scope.gaps`, so "
        "never drop gap fields, leave it empty, or infer it from ProjectPlan.data_sources. "
        "If database_planning_context is missing or has no `database_change` gap, do not "
        "create database tasks; backend tasks may still reference the existing schema.\n"
        
        "Split work into independently verifiable tasks. Every task must include:\n"
        "- id: a stable unique task id\n"
        "- unit_id: one of the required Unit IDs from TargetBuildContext\n"
        "- owner: database, backend, or frontend\n"
        "- title and description: write both fields in Simplified Chinese for user-facing display\n"
        "- dependencies: ids of prerequisite tasks returned in the same Unit only; never "
        "encode cross-Unit dependencies because the deterministic Unit Graph is their "
        "only source of truth\n"
        "- change_scope: [{operation: add|modify|delete, path, description}] using exact workspace-relative paths\n"
        "- backend stage tasks: split the four phases into separate tasks and embed the previous stage's expected goal (files, responsibilities, contracts) in the next stage's description\n"
        "- impact_scope: {summary, affected_modules, public_contracts, risks}\n"
        "- can_run_in_parallel and parallel_reason\n"
        "- acceptance_criteria: always [] because deterministic engineering acceptance compilation owns this field\n"
        "- acceptance_checks: always [] and never emit check objects; the deterministic compiler derives acceptance_checks from change_scope/allowed_paths/menu/API contract metadata\n"
        "- verification_commands: frontend and backend tasks must leave it empty (verification happens in the integration test phase)\n"
        "- status: pending for every newly planned task\n"
        "Before returning JSON, self-check every task and remove duplicate or semantically "
        "equivalent list items. Keep acceptance_criteria and acceptance_checks both empty ([]) and "
        "never add generic build, test, runtime UI, permission, role, or other business acceptance "
        "statements, and never return acceptance_checks objects.\n"
        "When TargetBuildContext.reusable_tasks_by_unit lists an application Unit, do not "
        "create another task for that Unit and do not copy its task ids into dependencies; "
        "the deterministic Unit Graph will connect that reusable capability.\n"
        "For a page target, the page must also be registered in the template menu so its "
        "automatic route can resolve. Use TargetBuildContext.target.page_key as the "
        "authoritative PageKey for the page directory name and menu key — this is a "
        "PascalCase identifier derived from the page ID (e.g. dashboard_page → "
        "DashboardPage). All page-related paths in target_files, allowed_paths, and "
        "change_scope MUST use this exact PageKey: `frontend/src/pages/<PageKey>/index.tsx`. "
        "Do NOT use the raw page ID (snake_case like dashboard_page) as the directory name. "
        "Menu entries use the exact template shape `{ path, name, key }`; never use "
        "a `label` field. A deterministic compiler step will add the bounded menus.ts task "
        "when the model omits it and will reconcile a planned PageKey with one unique semantically "
        "equivalent live page directory. Never create a second page merely because a stale "
        "WorkspaceSnapshot omitted the live directory.\n"
        "When TargetBuildContext.target.type is `endpoint`, every new task MUST use the exact "
        "`backend:endpoint:<apiContractId>:<endpointId>` Unit from TargetBuildContext.required_unit_ids "
        "or an unprepared prerequisite Unit listed there. Do not create page tasks, do not create "
        "tasks for other endpoints or other entities' data sources, and use the confirmed "
        "executable_details.endpoint_detail_plans[0] as the executable source of truth.\n"
        "## CRITICAL — Reuse existing template scaffold, do NOT rebuild it\n"
        "The WorkspaceSnapshot describes a frontend template project that ALREADY EXISTS "
        f"under `/{frontend_root}/`. Its framework scaffold is complete and MUST NOT be "
        "recreated or modified. Before planning any task, check the snapshot's "
        "frontend.components / frontend.routes / frontend.entrypoints / file_manifest: "
        "any file already listed there is already present on disk.\n"
        "- NEVER plan `operation: add` for a file that already exists in the snapshot. "
        "In particular do NOT create tasks to add: package.json, pnpm-lock.yaml, "
        "vite.config.ts, tsconfig.json, tailwind.config.js, postcss.config.js, index.html, "
        "Dockerfile, src/main.tsx, src/index.tsx, src/App.tsx, src/routes/index.tsx, "
        "src/utils/route.tsx, src/layout/**, src/providers/**, src/components/ErrorBoundary/**, "
        "src/hooks/useGuard.ts, src/apis/service.ts, src/constants/index.ts, "
        "src/constants/routes.ts, src/constants/menus.ts, src/typings/**, src/styles/**. "
        "These are the template skeleton; recreating them wastes effort and breaks the build.\n"
        "- NEVER plan `operation: modify` on the skeleton files above either, except the "
        "single permitted append-only change to `src/constants/menus.ts`: add the current "
        "page as `{ path, name, key }` to the top-level `BIZ_MENUS` array without changing "
        "existing entries or the "
        "file structure. All other skeleton files remain read-only.\n"
        "- When the menu item path contains a React Router dynamic path segment such as "
        "`:id` or `detail/:id`, include `hideInMenu: true` on that new menu item because "
        "parameterized pages are not stable menu entries.\n"
        "- Only plan `operation: add` for NEW business files that do NOT exist in the "
        "snapshot: business page components under src/pages/<PageKey>/index.tsx (replace "
        "the scaffold placeholder content), business API files under src/apis/<biz>Api.ts, "
        "and page-specific types/constants/hooks/utils/components as governed by the "
        "frontend-template-modification-boundary skill. Every page task with confirmed "
        "api_dependencies MUST create the matching src/apis/<biz>Api.ts service file "
        "(or reuse the existing one when already in the snapshot) so the page calls the "
        "backend only through that service.\n"
        "- If a page already has a placeholder index.tsx in the snapshot, plan it as "
        "`operation: modify` on that existing file (replace placeholder with real code), "
        "NOT `operation: add`.\n"
        "Return one JSON object only, without markdown fences or commentary. "
        "The JSON object must include workspace_analysis and tasks. It may include a dag "
        "summary, but dependencies on tasks are the source of truth for DAG edges. "
        "workspace_analysis must summarize the directories, entry files, stack, and "
        "conventions used from the WorkspaceSnapshot.\n\n"
        f"WorkspaceSnapshot (bounded planning projection):\n{snapshot_text}\n\n"
        f"TargetBuildContext:\n{json.dumps(build_context or {}, ensure_ascii=False, indent=2)}\n\n"
        f"TaskPreparationContext:\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}"
    )


def _compact_workspace_snapshot(
    workspace_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """裁剪规划 Prompt 的工作区快照，只保留导航所需的有限事实。"""

    if not isinstance(workspace_snapshot, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in (
        "schema_version",
        "workspace_revision",
        "project_roots",
        "tech_stack",
        "entrypoints",
        "build_commands",
        "test_commands",
        "file_manifest",
        "shared_contracts",
        "high_value_files",
        "code_graph",
    ):
        if key in workspace_snapshot:
            compact[key] = _bounded_prompt_value(workspace_snapshot[key], limit=80)
    for key in ("backend", "frontend"):
        value = workspace_snapshot.get(key)
        if not isinstance(value, dict):
            continue
        bounded = {
            item_key: _bounded_prompt_value(item_value, limit=80)
            for item_key, item_value in value.items()
            if item_key
            in {
                "api_routes",
                "models",
                "workflow_nodes",
                "agent_factories",
                "components",
                "pages",
                "api_clients",
                "ipc_calls",
                "ag_ui_usage",
                "dir_structure",
            }
        }
        compact[key] = bounded
    return compact


def _bounded_prompt_value(value: Any, *, limit: int) -> Any:
    """限制规划上下文中的列表、字符串和嵌套对象，避免模型看到无界快照。"""

    if isinstance(value, str):
        return value[:12_000]
    if isinstance(value, list):
        return [_bounded_prompt_value(item, limit=limit) for item in value[:limit]]
    if isinstance(value, dict):
        return {
            str(key)[:120]: _bounded_prompt_value(item, limit=limit)
            for key, item in list(value.items())[:limit]
        }
    return value


def _task_preparation_datasource_type(project_plan: dict[str, Any]) -> str:
    """从任务准备投影读取数据源类型集合：全 static 走前端分支，其余走后端分支。"""

    skeleton = project_plan.get("application_skeleton")
    sources = skeleton.get("data_sources") if isinstance(skeleton, dict) else None
    source_types = {
        str(source.get("type") or "")
        for source in sources or []
        if isinstance(source, dict)
    }
    if not source_types:
        raise ValueError("任务准备上下文缺少数据源类型。")
    if not source_types <= {"database", "static", "external_api"}:
        raise ValueError("任务准备上下文包含非法数据源类型。")
    if source_types <= {"static"}:
        return "static"
    return "database"


def _static_task_preparation_prompt(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
    build_context: dict[str, Any] | None,
) -> str:
    """构造 Static 专用任务提示，不注入 Spring、MyBatis 或数据库生成要求。"""

    snapshot_text = json.dumps(
        _compact_workspace_snapshot(workspace_snapshot), ensure_ascii=False, indent=2
    )
    return (
        "You are the build-task planning model for a STATIC frontend-only application.\n"
        "This is planning-only: do not call tools, inspect extra files, or generate code.\n"
        "ProjectPlan data source type is immutable static and its runtime implementation is "
        "effective_source=frontend_mock. Create tasks only for required frontend:data:<sourceId> "
        "and page:<pageId> Units listed in TargetBuildContext.required_unit_ids. Never create "
        "database, backend, backend:bootstrap, Controller, Mapper, PO, Spring Boot, MyBatis, "
        "migration, datasource, or real HTTP endpoint work.\n"
        "For each frontend:data Unit, plan src/apis/<business>Api.ts as the sole owner of a "
        "module-level in-memory record collection and async list/create/update/delete functions "
        "strictly bounded by executable_details.api_contracts. Page components must import that "
        "module and must not contain business-data arrays or call a backend service.\n"
        "All files live under /frontend/. Reuse the existing scaffold. Do not modify package.json, "
        "vite.config.ts, src/apis/service.ts, framework entry files, or dependencies. The only "
        "permitted scaffold edit is appending the current page to src/constants/menus.ts.\n"
        "Return one JSON object with workspace_analysis and tasks. Every task must use an allowed "
        "Unit ID, owner=frontend, Simplified Chinese title/description, same-Unit dependencies only, "
        "exact change_scope paths, impact_scope, can_run_in_parallel, parallel_reason, status=pending, "
        "acceptance_criteria=[], acceptance_checks=[] (the deterministic compiler owns both), "
        "and verification_commands=[]. Do not plan tests or verification.\n\n"
        f"WorkspaceSnapshot:\n{snapshot_text}\n\n"
        f"TargetBuildContext:\n{json.dumps(build_context or {}, ensure_ascii=False, indent=2)}\n\n"
        f"TaskPreparationContext:\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}"
    )


def _invoke_live_main_agent(
    project_plan: dict[str, Any],
    *,
    workspace: str | None = None,
    workspace_snapshot: dict[str, Any] | None = None,
    build_context: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> str:
    """调用无工具 ChatModel 执行只读的构建任务候选规划。"""
    del workspace
    active_settings = settings or Settings.from_env()
    prompt = _task_preparation_prompt(
        project_plan,
        workspace_snapshot,
        build_context,
    )
    result = create_chat_model(active_settings).bind(
        max_tokens=active_settings.default_max_tokens
    ).invoke(prompt)
    _log_task_model_call_diagnostics(
        prompt=prompt,
        result=result,
        model_name=active_settings.model_api_name,
        configured_max_tokens=active_settings.default_max_tokens,
    )
    content = getattr(result, "content", "")
    return _coerce_content_text(content) or ""


def _reset_model_acceptance_fields(agent_plan: dict[str, Any] | None) -> None:
    """在模型输出边界强制清空候选任务的验收字段。

    模型即使收到提示词约束也可能生成不准确的验收内容，这里把解析出的
    tasks / dag.tasks 中的 acceptance_criteria 与 acceptance_checks 统一重置为
    空数组，确保下游只依赖确定性编译器生成的验收点，防止模型生成的验收
    文案或检查对象进入 Build Task Plan。
    TODO(验收措施): 后续需要设计更完善的验收验证措施，当前工程验收由
    确定性编译器依据 change_scope 等元数据生成。
    """

    if not isinstance(agent_plan, dict):
        return
    raw_tasks = agent_plan.get("tasks")
    if isinstance(raw_tasks, list):
        _reset_task_acceptance_fields(raw_tasks)
        return
    dag = agent_plan.get("dag")
    if isinstance(dag, dict):
        for key in ("tasks", "nodes"):
            value = dag.get(key)
            if isinstance(value, list):
                _reset_task_acceptance_fields(value)
                return


def _reset_task_acceptance_fields(tasks: list[Any]) -> None:
    """将候选任务列表中的验收字段统一重置为空数组。"""

    for task in tasks:
        if isinstance(task, dict):
            task["acceptance_criteria"] = []
            task["acceptance_checks"] = []


def prepare_build_tasks_with_main_agent(
    project_plan: dict[str, Any],
    *,
    workspace: str | None = None,
    workspace_snapshot: dict[str, Any] | None = None,
    build_context: dict[str, Any] | None = None,
    build_task_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """通过直接模型边界生成当前范围的可执行 Build DAG 候选任务。"""

    settings = Settings.from_env()
    agent_note = _invoke_live_main_agent(
        project_plan,
        workspace=workspace,
        workspace_snapshot=workspace_snapshot,
        build_context=build_context,
        settings=settings,
    )
    preparation_source = "direct_chat_model"
    agent_plan = extract_json_object(agent_note)
    # 模型输出的验收字段不可信，在解析边界统一强制重置为空数组，
    # 防止模型生成不准确的 acceptance_criteria / acceptance_checks 进入下游；
    # 真正的工程验收由确定性编译器基于 change_scope 等元数据生成。
    # TODO(验收措施): 后续需要设计更完善的验收验证措施。
    _reset_model_acceptance_fields(agent_plan)
    _log_task_model_response_diagnostics(agent_note, agent_plan)

    try:
        build_task_plan = create_build_task_plan(
            project_plan,
            agent_note=agent_note,
            agent_plan=agent_plan,
            workspace_snapshot=workspace_snapshot,
            base_build_task_plan=build_task_plan,
            build_context=build_context,
            workspace_root=workspace,
        )
    except ValueError as exc:
        logger.warning(
            "build_task_plan_compile_failed response_sha256=%s parsed_keys=%s error=%s",
            _response_fingerprint(agent_note),
            _parsed_keys(agent_plan),
            str(exc),
        )
        raise
    build_task_plan["prepared_by"] = {
        "agent": "chat-model",
        "mode": "direct",
        "model": settings.model_name,
        "source": preparation_source,
    }
    build_task_plan["preparation_source"] = preparation_source
    return build_task_plan


def _log_task_model_response_diagnostics(
    agent_note: str,
    agent_plan: dict[str, Any] | None,
) -> None:
    """记录模型响应的脱敏解析摘要，用于定位模型输出或 JSON 解析故障。"""

    tasks = agent_plan.get("tasks") if isinstance(agent_plan, dict) else None
    dag = agent_plan.get("dag") if isinstance(agent_plan, dict) else None
    dag_tasks = (dag.get("tasks") or dag.get("nodes")) if isinstance(dag, dict) else None
    candidates = tasks if isinstance(tasks, list) else dag_tasks
    logger.info(
        "build_task_model_response response_chars=%s response_sha256=%s parsed_keys=%s "
        "tasks_type=%s tasks_count=%s",
        len(agent_note),
        _response_fingerprint(agent_note),
        _parsed_keys(agent_plan),
        type(candidates).__name__ if candidates is not None else "missing",
        len(candidates) if isinstance(candidates, list) else 0,
    )


def _response_fingerprint(agent_note: str) -> str:
    """为原始模型响应生成短哈希，便于关联日志而不记录正文。"""

    return sha256(agent_note.encode("utf-8")).hexdigest()[:16]


def _parsed_keys(agent_plan: dict[str, Any] | None) -> list[str]:
    """返回已解析模型对象的顶层键，解析失败时返回空数组。"""

    return sorted(str(key) for key in agent_plan) if isinstance(agent_plan, dict) else []


def _log_task_model_call_diagnostics(
    *,
    prompt: str,
    result: Any,
    model_name: str,
    configured_max_tokens: int,
) -> None:
    """记录任务模型输入长度、Provider token 用量和结束原因，诊断上下文或输出截断。"""

    usage = _model_usage(result)
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    logger.info(
        "build_task_model_call model=%s prompt_chars=%s prompt_utf8_bytes=%s "
        "input_tokens=%s output_tokens=%s finish_reason=%s configured_max_tokens=%s",
        model_name,
        len(prompt),
        len(prompt.encode("utf-8")),
        input_tokens,
        output_tokens,
        _finish_reason(result),
        configured_max_tokens,
    )


def _model_usage(result: Any) -> dict[str, int | None]:
    """兼容 LangChain 的 usage_metadata 与 response_metadata.token_usage 两种用量结构。"""

    usage = getattr(result, "usage_metadata", None)
    metadata = getattr(result, "response_metadata", None)
    token_usage = metadata.get("token_usage") if isinstance(metadata, dict) else {}
    source = (
        usage
        if isinstance(usage, dict)
        else token_usage
        if isinstance(token_usage, dict)
        else {}
    )
    return {
        "input_tokens": _integer(source.get("input_tokens") or source.get("prompt_tokens")),
        "output_tokens": _integer(source.get("output_tokens") or source.get("completion_tokens")),
    }


def _finish_reason(result: Any) -> str | None:
    """提取 Provider 的完成原因，用于确认 length 截断或其他结束类型。"""

    metadata = getattr(result, "response_metadata", None)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("finish_reason") or metadata.get("finishReason")
    return str(value) if value else None


def _integer(value: Any) -> int | None:
    """仅接受非布尔整数 token 统计，避免日志将异常元数据误当成用量。"""

    return value if isinstance(value, int) and not isinstance(value, bool) else None
