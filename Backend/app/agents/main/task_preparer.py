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

    try:
        from app.services.builtin_skills import resolve_builtin_skills_root

        skill_file = (
            resolve_builtin_skills_root()
            / "springboot-mybatis-generate"
            / "SKILL.md"
        )
        return skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
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
    snapshot_text = (
        json.dumps(workspace_snapshot, ensure_ascii=False, indent=2)
        if workspace_snapshot
        else "{}"
    )
    app_name = _app_name_from_plan(project_plan)
    # 直接平铺到根目录，不再嵌套 apps/<app_name>/ 前缀
    frontend_root = "frontend"
    backend_root = "backend"
    # 规划模型处于 planning-only 边界，无法读取技能文件，须把 SKILL.md 内联进 prompt。
    backend_skill_document = _springboot_mybatis_skill_document()
    # 从 WorkspaceSnapshot 中提取真实后端目录树，直接注入 prompt。
    backend_snapshot = workspace_snapshot.get("backend") if workspace_snapshot else None
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
        
        "如果页面有依赖的接口设计，构建接口的任务规划，并生成任务实现"
        
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
        "`springboot-mybatis-generate` SKILL.md below. Plan every backend task strictly "
        "following its 4-phase generation order (object classes Entity/PO/DTO/Converter/Assembler → "
        "Repository/Mapper → ApplicationService → Controller), its naming conversions "
        "(table snake_case → PascalCase class / camelCase module, table → REST path), "
        "and its MySQL-to-Java type mapping. Use the generated file list and the 4-phase "
        "generation order as the basis for task split, dependencies, and "
        "acceptance_criteria.\n"
        "Backend server port: the Spring Boot backend starts on port 8080. The frontend "
        "must call the backend API through the corresponding base address "
        "`http://localhost:8080` (e.g. a backend endpoint `/api/v1/product-category` is "
        "called by the frontend as `http://localhost:8080/api/v1/product-category`). Do "
        "not hardcode a different port or host in frontend API calls.\n"
        "--- INJECTED springboot-mybatis-generate SKILL.md (planning model cannot read "
        "skills, content inlined) ---\n"
        + backend_skill_document
        + "\n"
        "--- END INJECTED SKILL.md ---\n"
        
        "Prepare executable tasks ONLY from TaskPreparationContext.executable_details "
        "and TargetBuildContext. TaskPreparationContext.application_skeleton is a "
        "non-executable Unit skeleton: it describes all pages, data sources, public "
        "application units, and API ranges, but it must never cause tasks for pages or "
        "data sources outside TargetBuildContext.required_unit_ids.\n"
        "Do not invent generic paths when an existing project convention is present in "
        "the snapshot. For page tasks, use only confirmed executable_details.page_detail_plans "
        "and preserve page_goal, layout_design, operation_interactions, state_feedback, "
        "api_dependencies, response_bindings, page_navigation, permissions, and "
        "acceptance_criteria. For backend/data tasks, use only executable_details."
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
        "##Database tasks run BEFORE backend tasks. "
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
        "- impact_scope: {summary, affected_modules, public_contracts, risks}\n"
        "- can_run_in_parallel and parallel_reason\n"
        "- acceptance_criteria: concrete, testable completion conditions written in Simplified Chinese\n"
        "- verification_commands when known\n"
        "- status: pending for every newly planned task\n"
        "Before returning JSON, self-check every task and remove duplicate or semantically "
        "equivalent list items. In particular, acceptance_criteria must contain unique, "
        "specific checks; do not repeat generic items such as '完成页面功能实现，并通过相关验证。'.\n"
        "When TargetBuildContext.reusable_tasks_by_unit lists an application Unit, do not "
        "create another task for that Unit and do not copy its task ids into dependencies; "
        "the deterministic Unit Graph will connect that reusable capability.\n"
        "For a page target, the page must also be registered in the template menu so its "
        "automatic route can resolve. Use one consistent PageKey for the page directory and "
        "menu key. Menu entries use the exact template shape `{ path, name, key }`; never use "
        "a `label` field. A deterministic compiler step will add the bounded menus.ts task "
        "when the model omits it and will reconcile a planned PageKey with one unique semantically "
        "equivalent live page directory. Never create a second page merely because a stale "
        "WorkspaceSnapshot omitted the live directory.\n"
        "When TargetBuildContext.target.type is `endpoint`, every new task MUST use the exact "
        "`backend:endpoint:<apiContractId>:<endpointId>` Unit from TargetBuildContext.required_unit_ids "
        "or an unprepared prerequisite Unit listed there. Do not create page tasks, do not create "
        "tasks for other endpoints in the same data source, and use the confirmed "
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
        "frontend-template-modification-boundary skill.\n"
        "- If a page already has a placeholder index.tsx in the snapshot, plan it as "
        "`operation: modify` on that existing file (replace placeholder with real code), "
        "NOT `operation: add`.\n"
        "Return one JSON object only, without markdown fences or commentary. "
        "The JSON object must include workspace_analysis and tasks. It may include a dag "
        "summary, but dependencies on tasks are the source of truth for DAG edges. "
        "workspace_analysis must summarize the directories, entry files, stack, and "
        "conventions used from the WorkspaceSnapshot.\n\n"
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
