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

_ENDPOINT_SOURCE_TYPES = frozenset({"database", "external_api", "static"})
_ENDPOINT_BACKEND_SOURCE_TYPES = frozenset({"database", "external_api"})
_EXTERNAL_API_SKILL_NAME = "springboot-external-api-generate"
_STATIC_DATA_SKILL_NAME = "frontend-static-data-generate"


def _springboot_mybatis_skill_document() -> str:
    """读取内置 springboot-mybatis-generate 技能的精简入口文档。

    任务规划模型处于 planning-only 边界，无法调用技能工具读取文件，因此需要把
    SKILL.md 的规划核心直接内联进 prompt 上下文；执行细节由 Skill 引用文件延迟
    加载。文件缺失时返回降级提示，
    避免规划静默失去后端架构约束。
    """

    return _builtin_skill_document(
        "springboot-mybatis-generate",
        "(springboot-mybatis-generate SKILL.md 未找到，请仍按 Spring Boot + "
        "MyBatis-Plus 标准分层架构规划后端任务：对象类(Entity/PO/DTO/Converter/Assembler) → "
        "Repository/Mapper → ApplicationService → Controller。)",
    )


def _builtin_skill_document(skill_name: str, fallback: str) -> str:
    """读取指定内置 Skill 文档，缺失时返回明确的保守降级规则。"""

    from app.services.builtin_skills import read_builtin_skill_md

    content = read_builtin_skill_md(skill_name)
    return content if content else fallback


def _endpoint_source_groups(
    project_plan: dict[str, Any],
    build_context: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """按当前 endpoint 的实体设计归并数据源，拒绝非法来源并避免全局污染。"""

    executable = project_plan.get("executable_details")
    executable = executable if isinstance(executable, dict) else {}
    designs = executable.get("entity_designs")
    if not isinstance(designs, list) or not designs:
        context = build_context if isinstance(build_context, dict) else {}
        designs = context.get("entity_designs")
    groups: dict[str, list[dict[str, Any]]] = {
        source_type: [] for source_type in ("database", "external_api", "static")
    }
    if isinstance(designs, list):
        for item in designs:
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("data_source_type") or "").strip()
            if not source_type:
                if item.get("entity_id") or item.get("entity_name"):
                    raise ValueError("任务准备上下文的实体设计缺少数据源类型。")
                continue
            if source_type not in _ENDPOINT_SOURCE_TYPES:
                raise ValueError(f"任务准备上下文包含非法数据源类型: {source_type}")
            groups[source_type].append(item)
    if not any(groups.values()):
        skeleton = project_plan.get("application_skeleton")
        sources = skeleton.get("data_sources") if isinstance(skeleton, dict) else None
        for source in sources or []:
            if not isinstance(source, dict):
                continue
            source_type = str(source.get("type") or "").strip()
            if not source_type:
                continue
            if source_type not in _ENDPOINT_SOURCE_TYPES:
                raise ValueError(f"任务准备上下文包含非法数据源类型: {source_type}")
            groups[source_type].append(source)
    return {source_type: items for source_type, items in groups.items() if items}


def _endpoint_source_types(
    project_plan: dict[str, Any],
    build_context: dict[str, Any] | None = None,
) -> set[str]:
    """返回当前 endpoint 实际涉及的数据源类型集合。"""

    return set(_endpoint_source_groups(project_plan, build_context))


def _external_api_skill_document() -> str:
    """读取外部 API 后端生成 Skill 文档。"""

    return _builtin_skill_document(
        _EXTERNAL_API_SKILL_NAME,
        "(springboot-external-api-generate SKILL.md 未找到：按 Java 8 Spring Boot 的上游 DTO/HTTP Client/字段映射/ApplicationService/Controller 分层规划，禁止生成 Entity、PO、Mapper、Repository、迁移或数据库配置。)",
    )


def _static_data_skill_document() -> str:
    """读取静态数据前端生成 Skill 文档。"""

    return _builtin_skill_document(
        _STATIC_DATA_SKILL_NAME,
        "(frontend-static-data-generate SKILL.md 未找到：在 /frontend/src/apis/<business>Api.ts 中实现模块级内存数据与异步契约函数，禁止生成后端接口或把业务数组放入页面。)",
    )


def _endpoint_source_prompt_fragments(
    project_plan: dict[str, Any],
    build_context: dict[str, Any] | None = None,
) -> tuple[str, str, set[str]]:
    """根据来源集合生成 endpoint 规则、Skill 注入和路径提示。"""

    groups = _endpoint_source_groups(project_plan, build_context)
    source_types = set(groups)
    fragments: list[str] = []
    skill_fragments: list[str] = []
    if "database" in source_types:
        fragments.append(
            "DATABASE is the entity data-source classification, not a database-schema task. "
            "Create owner=backend code tasks in the required backend:endpoint Unit and use "
            "the injected springboot-mybatis-generate Skill for the four ordered layers "
            "(object classes, repository, application service, controller). Table/schema "
            "work was completed during entity confirmation; never create owner=database "
            "tasks, database:* Units, migrations, seed SQL, or other schema/table operations.\n"
        )
        skill_fragments.append(
            "--- INJECTED springboot-mybatis-generate SKILL.md ---\n"
            + _springboot_mybatis_skill_document()
            + "\n--- END INJECTED springboot-mybatis-generate SKILL.md ---\n"
        )
    if "external_api" in source_types:
        fragments.append(
            "EXTERNAL_API entities: create backend tasks in the required backend:endpoint "
            "Unit for upstream DTO/client or gateway, field mapping, application service, "
            "and internal controller. Do not create persistence classes, Mapper, Repository, "
            "migration, seed SQL, or datasource tasks. Preserve the confirmed upstream "
            "method/path/request/response and mappings.\n"
        )
        skill_fragments.append(
            "--- INJECTED springboot-external-api-generate SKILL.md ---\n"
            + _external_api_skill_document()
            + "\n--- END INJECTED springboot-external-api-generate SKILL.md ---\n"
        )
    if "static" in source_types:
        fragments.append(
            "STATIC entities: create owner=frontend tasks only in the required "
            "frontend:data:<sourceId> Unit. Implement the confirmed contract in a frontend "
            "in-memory API module; never create backend, Spring, MyBatis, database, real HTTP, "
            "or page implementation tasks for these entities.\n"
        )
        skill_fragments.append(
            "--- INJECTED frontend-static-data-generate SKILL.md ---\n"
            + _static_data_skill_document()
            + "\n--- END INJECTED frontend-static-data-generate SKILL.md ---\n"
        )
    if not source_types:
        raise ValueError("任务准备上下文缺少 endpoint 实体数据源类型。")
    mapping = (
        "Source-to-owner mapping for this endpoint:\n"
        "- database and external_api -> backend:endpoint:* / owner=backend\n"
        "- static -> frontend:data:* / owner=frontend\n"
        "Mixed sources must remain partitioned by entity source; do not collapse them into "
        "a single static-or-backend classification.\n"
    )
    return mapping + "".join(fragments), "".join(skill_fragments), source_types


def _task_plan_retry_feedback(errors: list[str] | None) -> str:
    """把平台校验错误转换为下一次任务规划模型的内部重生成指令。"""

    normalized = [str(error).strip() for error in errors or [] if str(error).strip()]
    if not normalized:
        return ""
    return (
        "\n\n--- AUTOMATIC REGENERATION FEEDBACK ---\n"
        "The previous candidate task plan violated platform-owned task boundaries or DAG "
        "constraints. Regenerate the complete JSON task plan now. Do not ask the user to "
        "decide how to split platform tasks, do not preserve the invalid task, and do not "
        "explain the correction outside the JSON output. Fix every issue below:\n"
        + "\n".join(f"- {error}" for error in normalized[:20])
        + "\n--- END AUTOMATIC REGENERATION FEEDBACK ---\n"
    )


def _task_preparation_prompt(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
    build_context: dict[str, Any] | None = None,
    validation_feedback: list[str] | None = None,
) -> str:
    """按本轮实际待生成 Unit 选择渐进式任务规划提示词。"""

    mode = _planning_context_mode(build_context)
    if mode == "page":
        return _page_task_preparation_prompt(
            project_plan,
            workspace_snapshot,
            build_context,
        )
    if mode == "endpoint":
        return _endpoint_task_preparation_prompt(
            project_plan,
            workspace_snapshot,
            build_context,
        )
    return _combined_task_preparation_prompt(
        project_plan,
        workspace_snapshot,
        build_context,
    )


def _planning_context_mode(build_context: dict[str, Any] | None) -> str:
    """根据实际会被模型替换的 Unit 判断 endpoint/page 上下文范围。"""

    context = build_context if isinstance(build_context, dict) else {}
    explicit_mode = str(context.get("planning_context_mode") or "").strip()
    if explicit_mode in {"page", "endpoint", "combined"}:
        return explicit_mode
    target = context.get("target") if isinstance(context.get("target"), dict) else {}
    pending_units = {
        str(unit_id)
        for unit_id in context.get("planning_unit_ids")
        or context.get("required_unit_ids")
        or []
        if str(unit_id).strip()
    }
    target_type = str(target.get("type") or "").strip()
    needs_page = any(unit_id.startswith("page:") for unit_id in pending_units)
    needs_endpoint = target_type == "endpoint" or any(
        unit_id.startswith("backend:") or unit_id.startswith("frontend:data:")
        for unit_id in pending_units
    )
    if needs_page and needs_endpoint:
        return "combined"
    if needs_page:
        return "page"
    if needs_endpoint:
        return "endpoint"
    # 没有显式 Unit 时保守沿用完整提示词，兼容独立调用方。
    return "combined"


def _combined_task_preparation_prompt(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
    build_context: dict[str, Any] | None = None,
) -> str:
    """组合全局计划与定向详情上下文，约束模型仅返回当前 Unit 的任务候选。"""
    source_types = _endpoint_source_types(project_plan, build_context)
    if source_types and source_types <= {"static"}:
        return _static_task_preparation_prompt(
            project_plan,
            workspace_snapshot,
            build_context,
            validation_feedback,
        )
    endpoint_source_rules = ""
    endpoint_skill_documents = ""
    if source_types:
        endpoint_source_rules, endpoint_skill_documents, _ = _endpoint_source_prompt_fragments(
            project_plan,
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
        
        "Backend path convention: this is a Spring Boot Maven project "
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
        "The source-specific Skill sections below are the only authoritative reference for "
        "backend and static data generation. Database and external API entities have "
        "different implementation layers; follow only the section matching each entity. "
        "For database entities, plan four ordered backend stages (object classes, repository, "
        "application service, controller). For external API entities, plan upstream DTO/client, "
        "mapping, application service, and internal controller stages without persistence. "
        "For static entities, plan frontend:data Unit tasks only. Each task owns ONLY its own "
        "files in change_scope and same-Unit dependencies; a deterministic compiler owns "
        "cross-Unit edges.\n"
        + endpoint_source_rules
        + endpoint_skill_documents
        + "NOTE: any acceptance guidance inside the injected SKILL.md (for example "
        "`acceptance_criteria` covering compilation or REST availability) describes "
        "generated-code content expectations only and is SUPERSEDED for task output: "
        "tasks MUST still return `acceptance_criteria: []` and `acceptance_checks: []`; "
        "never copy SKILL.md acceptance items into task fields.\n"
          
        "前后端任务只生成代码相关的工作，不要生成任何验证工作：不要规划编写或运行测试、"
        "执行构建/类型检查、冒烟验证、接口联通检查等验证类任务，也不要规划测试文件"
        "（*.test.*、*.spec.*、Java 测试类等）。不要把 PageDetail、EndpointDetail 或需求文档中的"
        "业务验收标准复制到任务；acceptance_criteria 和 acceptance_checks 都必须返回空数组（[]），"
        "模型不得输出任何验收检查对象或验收文案，后端会根据 change_scope、"
        "allowed_paths 和正式 API 契约确定性生成纯工程验收点并写入 acceptance_checks。"
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
        "Every frontend page task whose confirmed page_implementation_contracts declare "
        "requiredEndpointIds MUST also plan the matching business API service file "
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
        "already executed and confirmed during entity design: in every Build scope NEVER "
        "create owner=database tasks or database:* Unit tasks. API contracts in "
        "executable_details.api_contracts are only request/response schema references and "
        "must never be used to infer a data source.\n"
        "Do not invent generic paths when an existing project convention is present in "
        "the snapshot. For page tasks, use only executable_details.page_implementation_contracts "
        "and the referenced confirmed React UI file when one is present. If the UI design stage "
        "was explicitly skipped, use ProductPlan, TechnicalPlan, and the code-block-template skill "
        "as the page implementation source. Treat actionBindings, requiredEndpointIds, "
        "responseBindings, permissionBindings, navigationBindings, productAcceptance, and "
        "engineeringAcceptance as implementation context only; never copy business criteria into task output. "
        "actionBindings are the deterministic merge of ProductPlan behavior, UiManifest effects, and "
        "TechnicalPlan endpoint implementations. Implement the compiled endpoint, navigation, local, external, "
        "and ordered sequence behavior exactly as declared, and never infer a different endpoint from button "
        "text or treat non-endpoint behavior as a new technical decision. Frontend page tasks must additionally follow the "
        "frontend-backend API matching rules in this prompt: resolve every api_dependencies "
        "endpoint id to its exact method/path/schemas in executable_details.api_contracts and "
        "never invent endpoints or fields. For backend/data tasks, use only executable_details."
        "endpoint_detail_plans, executable_details.entity_designs, and "
        "executable_details.api_contracts. TechnicalPlan entities and API contracts in executable_details "
        "are the only source of fields; preserve entity_ids, endpoint ids, "
        "request/response schema refs, and page response_bindings in task source references.\n"
        
        "Split work into independently verifiable tasks. Every task must include:\n"
        "- id: a stable unique task id\n"
        "- unit_id: one of the required Unit IDs from TargetBuildContext\n"
        "- owner: backend or frontend\n"
        "- title and description: write both fields in Simplified Chinese for user-facing display\n"
        "- dependencies: ids of prerequisite tasks returned in the same Unit only; never "
        "encode cross-Unit dependencies because the deterministic Unit Graph is their "
        "only source of truth\n"
        "- change_scope: [{operation: add|modify|delete, path, description}] using exact workspace-relative paths\n"
        "- backend stage tasks: split the four phases into separate tasks and embed the previous stage's expected goal (files, responsibilities, contracts) in the next stage's description\n"
        "- impact_scope: {summary, affected_modules, public_contracts, risks}\n"
        "- can_run_in_parallel and parallel_reason\n"
        "- acceptance_criteria: always [] because deterministic engineering acceptance compilation owns this field\n"
        "- acceptance_checks: always [] and never emit check objects; the deterministic compiler derives acceptance_checks from change_scope/allowed_paths/API contract metadata\n"
        "- verification_commands: frontend and backend tasks must leave it empty (verification happens in the integration test phase)\n"
        "- status: pending for every newly planned task\n"
        "Before returning JSON, self-check every task and remove duplicate or semantically "
        "equivalent list items. Keep acceptance_criteria and acceptance_checks both empty ([]) and "
        "never add generic build, test, runtime UI, permission, role, or other business acceptance "
        "statements, and never return acceptance_checks objects.\n"
        "When TargetBuildContext.reusable_tasks_by_unit lists an application Unit, do not "
        "create another task for that Unit and do not copy its task ids into dependencies; "
        "the deterministic Unit Graph will connect that reusable capability.\n"
        "For a page target, the page entry, route and menu are already initialized by the "
        "template lifecycle and are read-only during DAG planning. Use TargetBuildContext.target.page_key as the "
        "authoritative PageKey for the page directory name and existing entry lookup — this is a "
        "PascalCase identifier derived from the page ID (e.g. dashboard_page → "
        "DashboardPage). All page-related paths in target_files, allowed_paths, and "
        "change_scope MUST use this exact PageKey: `frontend/src/pages/<PageKey>/index.tsx`. "
        "Do NOT use the raw page ID (snake_case like dashboard_page) as the directory name. "
        "The deterministic compiler only verifies the already-existing page entry and never "
        "creates a menu, route or placeholder task. Never create a second page "
        "merely because a stale WorkspaceSnapshot omitted the live directory.\n"
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
        "- NEVER plan `operation: modify` on the skeleton files above. Menus, routes and "
        "shared registration files are initialized upstream and remain read-only in this DAG.\n"
        "- Do not return menu or route metadata in task output. Menu and route registration "
        "are completed by the template lifecycle and are not Build task scope.\n"
        "- Only plan `operation: add` for NEW business files that do NOT exist in the "
        "snapshot. The existing page entry src/pages/<PageKey>/index.tsx is a template "
        "file and may only be modified for business content; add business API files under "
        "src/apis/<biz>Api.ts, "
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
        f"{_task_plan_retry_feedback(validation_feedback)}"
        f"TargetBuildContext:\n{json.dumps(build_context or {}, ensure_ascii=False, indent=2)}\n\n"
        f"TaskPreparationContext:\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}"
    )


def _page_task_preparation_prompt(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
    build_context: dict[str, Any] | None,
) -> str:
    """只为页面 Unit 注入前端实现、页面契约和前端工作区上下文。"""

    prompt_context = _scoped_prompt_build_context(build_context, "page")
    snapshot_text = json.dumps(
        _compact_workspace_snapshot(workspace_snapshot, scope="page"),
        ensure_ascii=False,
        indent=2,
    )
    return (
        _common_task_preparation_rules()
        + "You are planning frontend page tasks only. Do not create backend, "
        "EndpointDetail, entity persistence, Spring, MyBatis, database, or endpoint "
        "implementation tasks. Use only the current page implementation contract, its "
        "referenced API Contract schemas, and the existing frontend WorkspaceSnapshot.\n"
        "All generated frontend paths are under /frontend/. Use the authoritative "
        "TargetBuildContext.target.page_key for src/pages/<PageKey>/index.tsx and menu "
        "registration. Reuse the existing React scaffold; only add or replace business "
        "page files and append the current menu item when required.\n"
        "Every page API dependency must resolve to the exact method, path, request schema, "
        "and response schema in executable_details.api_contracts. Call the shared API "
        "service through the existing business API module; never invent URLs, fields, or "
        "raw axios calls. Treat actionBindings, responseBindings, navigationBindings, "
        "permissionBindings, and UI references as implementation context only.\n"
        "Do not plan tests, builds, lint, type checks, verification, or business acceptance. "
        "Return acceptance_criteria=[], acceptance_checks=[], verification_commands=[].\n\n"
        f"WorkspaceSnapshot (frontend-scoped):\n{snapshot_text}\n\n"
        f"TargetBuildContext:\n{json.dumps(prompt_context, ensure_ascii=False, indent=2)}\n\n"
        f"TaskPreparationContext (page-scoped):\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}"
    )


def _endpoint_task_preparation_prompt(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
    build_context: dict[str, Any] | None,
) -> str:
    """只为 endpoint/data Unit 注入当前数据源所需的契约、Skill 和工作区上下文。"""

    prompt_context = _scoped_prompt_build_context(build_context, "endpoint")
    source_rules, skill_documents, source_types = _endpoint_source_prompt_fragments(
        project_plan,
        build_context,
    )
    snapshot_scope = (
        "frontend"
        if source_types == {"static"}
        else "endpoint_combined"
        if "static" in source_types and source_types & _ENDPOINT_BACKEND_SOURCE_TYPES
        else "endpoint"
    )
    snapshot_text = json.dumps(
        _compact_workspace_snapshot(workspace_snapshot, scope=snapshot_scope),
        ensure_ascii=False,
        indent=2,
    )
    path_rules = (
        "Backend source tasks use /backend/src/main/java/... or "
        "/backend/src/main/resources/...; frontend static data tasks use "
        "/frontend/src/apis/...; do not recreate either framework skeleton.\n"
    )
    snapshot_label = (
        "frontend-scoped"
        if snapshot_scope == "frontend"
        else "combined endpoint-scoped"
        if snapshot_scope == "endpoint_combined"
        else "backend-scoped"
    )
    return (
        _common_task_preparation_rules()
        + "You are planning endpoint/data tasks only. Do not create page, route, menu, or "
        "page implementation tasks. Use the exact endpoint contract and confirmed entity "
        "designs in the endpoint-scoped context; do not infer data sources from API names.\n"
        + source_rules
        + skill_documents
        + path_rules
        + "Do not "
        "plan tests, builds, lint, type checks, verification, or business acceptance. "
        "Return acceptance_criteria=[], acceptance_checks=[], verification_commands=[].\n\n"
        f"WorkspaceSnapshot ({snapshot_label}):\n{snapshot_text}\n\n"
        f"TargetBuildContext:\n{json.dumps(prompt_context, ensure_ascii=False, indent=2)}\n\n"
        f"TaskPreparationContext (endpoint-scoped):\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}"
    )


def _common_task_preparation_rules() -> str:
    """返回 endpoint/page 共用的规划边界，避免专属上下文互相污染。"""

    return (
        "You are the build-task planning model for an app-generation workflow.\n"
        "This is a planning-only boundary: do not call tools, subagents, or inspect files "
        "outside the provided scoped WorkspaceSnapshot; do not generate or modify code.\n"
        "Create tasks only for Unit IDs in TargetBuildContext.required_unit_ids and only "
        "for the current planning scope. Dependencies may reference tasks in the same Unit "
        "only; never copy reusable task IDs into dependencies because the deterministic Unit "
        "Graph owns cross-Unit edges.\n"
        "Each task must include a stable id, unit_id, owner, Simplified Chinese title and "
        "description, exact change_scope paths, impact_scope, can_run_in_parallel, "
        "parallel_reason, dependencies, status=pending, acceptance_criteria=[], acceptance_checks=[], "
        "and verification_commands=[]. Return one JSON object with workspace_analysis and "
        "tasks, without markdown fences or commentary. workspace_analysis must summarize "
        "only the directories, entrypoints, and reuse conventions actually present in the "
        "scoped WorkspaceSnapshot; do not infer omitted stack or verification facts.\n"
        "The `dependencies` field is the execution prerequisite list: it must be a JSON "
        "array of task IDs from this response and the same Unit, meaning those tasks must "
        "complete before the current task starts. Use `dependencies: []` for a root task. "
        "Reference the exact stable `id` values, never task titles, Unit IDs, or prose. "
        "Do not reference tasks from another Unit or reusable tasks; the deterministic Unit "
        "Graph owns all cross-Unit edges.\n"
    )


def _scoped_prompt_build_context(
    build_context: dict[str, Any] | None,
    mode: str,
) -> dict[str, Any]:
    """从目标上下文中移除另一侧的详情正文，保留当前模式的引用索引。"""

    context = dict(build_context) if isinstance(build_context, dict) else {}
    planning_unit_ids = context.get("planning_unit_ids")
    if isinstance(planning_unit_ids, list) and planning_unit_ids:
        context["required_unit_ids"] = list(planning_unit_ids)
    source_refs = context.get("source_refs")
    if isinstance(source_refs, dict):
        source_refs = dict(source_refs)
        if mode == "page":
            source_refs.pop("endpoint_details", None)
        elif mode == "endpoint":
            source_refs.pop("page_detail", None)
            source_refs.pop("page_implementation_contract", None)
        context["source_refs"] = source_refs
    if mode == "page":
        for key in ("endpoint_detail", "direct_endpoint_details", "entity_designs", "entity_ids"):
            context.pop(key, None)
    elif mode == "endpoint":
        for key in (
            "page_detail",
            "page_implementation_contract",
            "endpoint_detail",
            "direct_endpoint_details",
            "entity_designs",
            "required_endpoint_ids",
            "planning_unit_ids",
            "planning_context_mode",
        ):
            context.pop(key, None)
    if mode == "endpoint":
        allowed_keys = {
            "target",
            "endpoint_ids",
            "entity_ids",
            "required_unit_ids",
            "source_refs",
            "reusable_tasks_by_unit",
        }
        return {key: value for key, value in context.items() if key in allowed_keys}
    return context


def _compact_workspace_snapshot(
    workspace_snapshot: dict[str, Any] | None,
    *,
    scope: str = "combined",
) -> dict[str, Any]:
    """裁剪规划 Prompt 的工作区快照，并按 endpoint/page 过滤另一侧事实。"""

    if not isinstance(workspace_snapshot, dict):
        return {}
    if scope in {"endpoint", "backend"}:
        return _compact_endpoint_workspace_snapshot(
            workspace_snapshot,
            roots=("backend",),
        )
    if scope == "frontend":
        return _compact_endpoint_workspace_snapshot(
            workspace_snapshot,
            roots=("frontend",),
        )
    if scope == "endpoint_combined":
        return _compact_endpoint_workspace_snapshot(
            workspace_snapshot,
            roots=("backend", "frontend"),
        )
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
    if scope in {"page", "endpoint", "frontend", "backend"}:
        selected = "frontend" if scope in {"page", "frontend"} else "backend"
        compact.pop("backend" if selected == "frontend" else "frontend", None)
        for key in ("entrypoints", "file_manifest", "high_value_files"):
            if key in compact:
                compact[key] = _filter_workspace_paths(compact[key], selected)
    return compact


def _compact_endpoint_workspace_snapshot(
    workspace_snapshot: dict[str, Any],
    *,
    roots: tuple[str, ...],
) -> dict[str, Any]:
    """仅保留 endpoint 任务选择路径和复用现有代码所需的工作区事实。"""

    compact: dict[str, Any] = {}
    for key in ("entrypoints", "high_value_files"):
        scoped_paths = _filter_workspace_paths_for_roots(
            workspace_snapshot.get(key),
            roots,
        )
        if scoped_paths:
            compact[key] = _bounded_prompt_value(scoped_paths, limit=80)

    allowed_sections = {
        "backend": {"api_routes", "models", "dir_structure"},
        "frontend": {"api_clients", "dir_structure"},
    }
    for root in roots:
        value = workspace_snapshot.get(root)
        if not isinstance(value, dict):
            continue
        allowed_keys = allowed_sections[root]
        bounded = {
            key: _bounded_prompt_value(item, limit=80)
            for key, item in value.items()
            if key in allowed_keys
        }
        if bounded:
            compact[root] = bounded
    return compact


def _filter_workspace_paths_for_roots(
    value: Any,
    roots: tuple[str, ...],
) -> list[Any]:
    """按一个或多个工程根过滤路径条目，并丢弃不含路径的全局统计。"""

    if not isinstance(value, list):
        return []
    prefixes = tuple(f"{root.lower()}/" for root in roots)
    result: list[Any] = []
    for item in value:
        path = item if isinstance(item, str) else _workspace_item_path(item)
        if not path:
            continue
        normalized = str(path).replace("\\", "/").lstrip("/").lower()
        if normalized.startswith(prefixes):
            result.append(item)
    return result


def _filter_workspace_paths(value: Any, selected_root: str) -> Any:
    """过滤快照中的路径集合，只保留指定前后端根目录事实。"""

    if not isinstance(value, list):
        return value
    selected_prefix = f"{selected_root.lower()}/"
    result = []
    for item in value:
        path = item if isinstance(item, str) else _workspace_item_path(item)
        if not path:
            result.append(item)
            continue
        normalized = str(path).replace("\\", "/").lstrip("/").lower()
        if normalized.startswith(selected_prefix) or "/" not in normalized:
            result.append(item)
    return result


def _workspace_item_path(item: Any) -> str:
    """从快照条目中提取可过滤的相对路径字段。"""

    if not isinstance(item, dict):
        return ""
    for key in ("path", "file", "relative_path", "workspace_path"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


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


def _task_preparation_datasource_types(project_plan: dict[str, Any]) -> set[str]:
    """从任务准备投影读取并校验数据源类型集合，不再折叠为二元分类。"""

    skeleton = project_plan.get("application_skeleton")
    sources = skeleton.get("data_sources") if isinstance(skeleton, dict) else None
    source_types = {
        str(source.get("type") or "")
        for source in sources or []
        if isinstance(source, dict)
    }
    if not source_types:
        raise ValueError("任务准备上下文缺少数据源类型。")
    if not source_types <= _ENDPOINT_SOURCE_TYPES:
        invalid = sorted(source_types - _ENDPOINT_SOURCE_TYPES)
        raise ValueError(f"任务准备上下文包含非法数据源类型: {', '.join(invalid)}")
    return source_types


def _static_task_preparation_prompt(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
    build_context: dict[str, Any] | None,
    validation_feedback: list[str] | None = None,
) -> str:
    """构造 Static 专用任务提示，不注入 Spring、MyBatis 或数据库生成要求。"""

    snapshot_text = json.dumps(
        _compact_workspace_snapshot(workspace_snapshot), ensure_ascii=False, indent=2
    )
    static_skill = _static_data_skill_document()
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
        "vite.config.ts, src/apis/service.ts, framework entry files, dependencies, menus, or routes.\n"
        "Return one JSON object with workspace_analysis and tasks. Every task must use an allowed "
        "Unit ID, owner=frontend, Simplified Chinese title/description, same-Unit dependencies only, "
        "exact change_scope paths, impact_scope, can_run_in_parallel, parallel_reason, status=pending, "
        "acceptance_criteria=[], acceptance_checks=[] (the deterministic compiler owns both), "
        "and verification_commands=[]. Do not plan tests or verification. Menu and route files are read-only.\n\n"
        "--- INJECTED frontend-static-data-generate SKILL.md ---\n"
        + static_skill
        + "\n--- END INJECTED frontend-static-data-generate SKILL.md ---\n\n"
        f"WorkspaceSnapshot:\n{snapshot_text}\n\n"
        f"{_task_plan_retry_feedback(validation_feedback)}"
        f"TargetBuildContext:\n{json.dumps(build_context or {}, ensure_ascii=False, indent=2)}\n\n"
        f"TaskPreparationContext:\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}"
    )


def _invoke_live_main_agent(
    project_plan: dict[str, Any],
    *,
    workspace: str | None = None,
    workspace_snapshot: dict[str, Any] | None = None,
    build_context: dict[str, Any] | None = None,
    validation_feedback: list[str] | None = None,
    settings: Settings | None = None,
) -> str:
    """调用无工具 ChatModel 执行只读的构建任务候选规划。"""
    del workspace
    active_settings = settings or Settings.from_env()
    prompt = _task_preparation_prompt(
        project_plan,
        workspace_snapshot,
        build_context,
        validation_feedback,
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
    build_execution_scope: dict[str, Any] | None = None,
    validation_feedback: list[str] | None = None,
) -> dict[str, Any]:
    """通过直接模型边界生成并自动修复当前范围的 Build DAG 候选任务。"""

    settings = Settings.from_env()
    max_retries = max(0, int(getattr(settings, "build_task_plan_max_retries", 2)))
    feedback = list(validation_feedback or [])
    last_errors: list[str] = []
    for attempt in range(max_retries + 1):
        agent_note = _invoke_live_main_agent(
            project_plan,
            workspace=workspace,
            workspace_snapshot=workspace_snapshot,
            build_context=build_context,
            validation_feedback=feedback,
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
                build_execution_scope=build_execution_scope,
                workspace_root=workspace,
            )
        except ValueError as exc:
            last_errors = [str(exc)]
            logger.warning(
                "build_task_plan_compile_failed attempt=%s/%s response_sha256=%s "
                "parsed_keys=%s error=%s",
                attempt + 1,
                max_retries + 1,
                _response_fingerprint(agent_note),
                _parsed_keys(agent_plan),
                str(exc),
            )
            if attempt >= max_retries:
                raise ValueError(
                    "Build DAG 自动重生成耗尽，最后一次任务候选无法编译："
                    + str(exc)
                ) from exc
            feedback = last_errors
            continue

        last_errors = _build_task_plan_validation_errors(build_task_plan)
        if last_errors:
            logger.warning(
                "build_task_plan_validation_retry attempt=%s/%s response_sha256=%s "
                "errors=%s",
                attempt + 1,
                max_retries + 1,
                _response_fingerprint(agent_note),
                last_errors,
            )
            if attempt >= max_retries:
                raise ValueError(
                    "Build DAG 自动重生成耗尽，最后一次任务候选仍未通过校验："
                    + "；".join(last_errors)
                )
            feedback = last_errors
            continue

        build_task_plan["prepared_by"] = {
            "agent": "chat-model",
            "mode": "direct",
            "model": settings.model_name,
            "source": preparation_source,
            "generation_attempt": attempt + 1,
        }
        build_task_plan["preparation_source"] = preparation_source
        return build_task_plan

    raise ValueError(
        "Build DAG 自动重生成未返回可执行任务：" + "；".join(last_errors)
    )


def _build_task_plan_validation_errors(build_task_plan: dict[str, Any]) -> list[str]:
    """读取候选 Build DAG 的确定性校验错误，供平台自动重生成使用。"""

    graph = build_task_plan.get("task_graph")
    validation = graph.get("validation") if isinstance(graph, dict) else None
    errors = [
        str(error)
        for error in (validation.get("errors") if isinstance(validation, dict) else [])
        if str(error).strip()
    ]
    execution = build_task_plan.get("execution")
    blocked_batches = (
        execution.get("blocked_batches")
        if isinstance(execution, dict)
        else []
    )
    for batch in blocked_batches if isinstance(blocked_batches, list) else []:
        if not isinstance(batch, dict):
            continue
        reason = str(batch.get("reason") or "任务执行批次被阻断。").strip()
        if reason and reason not in errors:
            errors.append(reason)
    return errors


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
