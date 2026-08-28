from __future__ import annotations

import json
from typing import Any

from app.services.business_acceptance import DELIVERABLE_KINDS


_ENDPOINT_SOURCE_TYPES = frozenset({"database", "external_api", "static"})
_ENDPOINT_BACKEND_SOURCE_TYPES = frozenset({"database", "external_api"})
_EXTERNAL_API_SKILL_NAME = "springboot-external-api-generate"
_STATIC_DATA_SKILL_NAME = "frontend-static-data-generate"


def _builtin_skill_document(skill_name: str, fallback: str) -> str:
    """读取指定内置 Skill 文档，缺失时返回明确的保守降级规则。"""

    from app.services.builtin_skills import read_builtin_skill_md

    content = read_builtin_skill_md(skill_name)
    return content if content else fallback


def _springboot_mybatis_skill_document() -> str:
    """读取数据库来源后端代码生成 Skill 的入口文档。"""

    return _builtin_skill_document(
        "springboot-mybatis-generate",
        "(springboot-mybatis-generate SKILL.md 未找到：仍按 Java 8 Spring Boot + "
        "MyBatis-Plus 的 objects → repository → service → controller 四阶段规划，"
        "禁止生成数据库 DDL、迁移或种子数据。)",
    )


def _external_api_skill_document() -> str:
    """读取外部 API 后端生成 Skill 文档。"""

    return _builtin_skill_document(
        _EXTERNAL_API_SKILL_NAME,
        "(springboot-external-api-generate SKILL.md 未找到：按 Java 8 Spring Boot 的 "
        "upstream → mapping → service → controller 四阶段规划，禁止生成持久化层、"
        "迁移或数据库配置。)",
    )


def _static_data_skill_document() -> str:
    """读取静态数据前端生成 Skill 文档。"""

    return _builtin_skill_document(
        _STATIC_DATA_SKILL_NAME,
        "(frontend-static-data-generate SKILL.md 未找到：在 /frontend/src/apis/"
        "<business>Api.ts 中实现模块级内存数据与异步契约函数，禁止生成后端接口。)",
    )


def endpoint_source_groups(
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


def endpoint_source_types(
    project_plan: dict[str, Any],
    build_context: dict[str, Any] | None = None,
) -> set[str]:
    """返回当前 endpoint 实际涉及的数据源类型集合。"""

    return set(endpoint_source_groups(project_plan, build_context))


def planning_context_mode(build_context: dict[str, Any] | None) -> str:
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
    return "combined"


def build_task_preparation_prompt(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
    build_context: dict[str, Any] | None = None,
    validation_feedback: list[str] | None = None,
) -> str:
    """按本轮实际待生成 Unit 组装统一八段式任务规划 Prompt。"""

    mode = planning_context_mode(build_context)
    source_groups = (
        {}
        if mode == "page"
        else endpoint_source_groups(project_plan, build_context)
    )
    source_types = set(source_groups)
    if mode == "combined" and source_types and source_types <= {"static"}:
        mode = "static"
    prompt_context = scoped_prompt_build_context(build_context, mode)
    snapshot_scope = _snapshot_scope(mode, source_types)
    snapshot = compact_workspace_snapshot(workspace_snapshot, scope=snapshot_scope)
    sections = [
        _role_boundary_section(mode),
        _output_contract_section(),
        _planning_algorithm_section(
            mode,
            source_groups,
            prompt_context,
            validation_feedback,
        ),
        _task_rules_section(mode, source_types),
        _dependency_rules_section(source_types),
        _skill_injection_section(source_types),
        _forbidden_output_section(mode, source_types),
        _workspace_context_section(snapshot, prompt_context, project_plan),
    ]
    return "\n\n".join(sections)


def _deliverable_kind_contract_prompt() -> str:
    """生成所有任务规划模式共用的交付物结构和类型唯一契约。"""

    allowed_kinds = "\n".join(f"- `{kind}`" for kind in DELIVERABLE_KINDS)
    return (
        "AUTHORITATIVE DELIVERABLE KIND CONTRACT:\n"
        "The `kind` field of every item in `deliverables` MUST be exactly one of the "
        "following values:\n"
        f"{allowed_kinds}\n"
        "This is the complete allowlist, not an example list. Do not invent, extend, "
        "translate, or substitute any other value. Any value outside this list will be "
        "rejected by the platform. `kind` describes the semantic responsibility of the "
        "deliverable; do not encode an implementation language or framework as `kind`. "
        "Business acceptance checks are platform-owned and must not be returned in the "
        "task plan.\n"
        "Every executable frontend or backend task MUST declare a non-empty `deliverables` "
        "array. Every deliverable MUST use exactly this JSON shape: "
        "{\"id\": \"stable unique id\", \"kind\": \"one allowed value above\", "
        "\"target_id\": \"formal page, endpoint, entity, or capability id\", "
        "\"paths\": [\"workspace-relative/path\"], "
        "\"provides\": [\"semantic.capability\"]}. "
        "The fields `id`, `kind`, and `target_id` are required non-empty strings. "
        "The fields `paths` and `provides` are required non-empty string arrays. Every "
        "deliverable path must also belong to the task's change_scope. Singular `path`, "
        "free-form `description`, or any other substitute field does not satisfy this "
        "contract and will be rejected."
    )


def _role_boundary_section(mode: str) -> str:
    """生成统一的角色、权限和当前规划模式说明。"""

    scope_text = {
        "page": "frontend page tasks only",
        "endpoint": "endpoint/data tasks only",
        "static": "STATIC frontend data and page tasks only",
        "combined": "the current mixed page and endpoint/data scope only",
    }[mode]
    return (
        "## 1. Role & Boundary\n"
        "You are the build-task planning model for an app-generation workflow. "
        f"Plan {scope_text}. This is a planning-only boundary: do not call tools or "
        "subagents, inspect files outside the provided WorkspaceSnapshot, or generate or "
        "modify code. Create tasks only for the effective planning Units: use "
        "TargetBuildContext.planning_unit_ids when non-empty, otherwise use "
        "TargetBuildContext.required_unit_ids. Formal contracts and confirmed entity "
        "designs are authoritative; WorkspaceSnapshot is authoritative for existing paths."
    )


def _output_contract_section() -> str:
    """生成唯一 JSON 顶层形态和任务字段契约。"""

    return (
        "## 2. Output Contract\n"
        "Return exactly one JSON object without markdown fences or commentary. The object "
        "must contain exactly two top-level keys: `workspace_analysis` and `tasks`; do not "
        "return `dag` or any other top-level key. `workspace_analysis` summarizes only "
        "directories, entrypoints, stack, and reuse conventions visible in the scoped "
        "WorkspaceSnapshot. Every task must include: `id`, `unit_id`, `owner`, Simplified "
        "Chinese `title` and `description`, `dependencies`, exact `change_scope`, "
        "`deliverables`, `impact_scope`, `can_run_in_parallel`, `parallel_reason`, and "
        "`status: \"pending\"`. Do not return platform-owned acceptance, evidence, summary, "
        "or verification-command fields.\n"
        + _deliverable_kind_contract_prompt()
    )


def _planning_algorithm_section(
    mode: str,
    source_groups: dict[str, list[dict[str, Any]]],
    build_context: dict[str, Any],
    validation_feedback: list[str] | None,
) -> str:
    """生成按 Unit 和数据源执行的确定性候选任务规划步骤。"""

    rules = ["Read the effective planning Unit IDs and process each Unit exactly once."]
    planning_units = {
        str(unit_id)
        for unit_id in build_context.get("planning_unit_ids")
        or build_context.get("required_unit_ids")
        or []
        if str(unit_id).strip()
    }
    if "database" in source_groups:
        if "backend:bootstrap" in planning_units:
            rules.append(
                "Emit exactly one backend:bootstrap root task with id "
                "`backend:bootstrap::bootstrap`, unit_id `backend:bootstrap`, owner "
                "`backend`, and dependencies `[]`. Keep it even when execution may report "
                "`already_satisfied`."
            )
        rules.append(
            "For every confirmed database entity in each backend:endpoint:* Unit, emit "
            "exactly four structural tasks in this order: `objects`, `repository`, "
            "`service`, `controller`. Use IDs "
            "`<endpointUnitId>::<entityId>::<stage>`. Existing files do not remove a stage: "
            "use `modify` for paths present in WorkspaceSnapshot and `add` for missing "
            "business paths; execution may prove the stage `already_satisfied`."
        )
    if "external_api" in source_groups:
        rules.append(
            "For every confirmed external_api entity in each backend:endpoint:* Unit, "
            "emit `upstream`, `mapping`, `service`, and `controller` tasks with IDs "
            "`<endpointUnitId>::<entityId>::<stage>`; do not add persistence stages."
        )
    if "static" in source_groups or mode == "static":
        rules.append(
            "For each frontend:data:* Unit, emit one data-module task with id "
            "`<frontendDataUnitId>::data-module`; it owns the business API module and its "
            "module-local types/constants for all confirmed static entities in that Unit."
        )
    if mode in {"page", "combined", "static"}:
        rules.append(
            "Plan page Units from the current PageImplementationContract. Page task "
            "count remains contract-driven; reuse the existing page entry and add only "
            "page-owned business modules required by the contract."
        )
    if set(source_groups) & _ENDPOINT_BACKEND_SOURCE_TYPES:
        rules.append(
            "Resolve backend naming in this exact priority: reuse an existing package and "
            "module for the same entity or API Contract; otherwise derive basePackage from "
            "the parent path of the existing Application.java or existing business classes; "
            "otherwise do not create a second package root. When no module exists, convert "
            "the exact entity_id mechanically to lowerCamelCase without translation. For "
            "example ProductCategory becomes productCategory. Never invent semantic names "
            "such as catalog, product, commerce, or management."
        )
    rules.append(
        "Fill each task with exact business semantics and file paths, self-check for "
        "duplicate tasks, then return the JSON object."
    )
    rules.append(
        "Across the complete candidate, each frontend endpoint identified by "
        "api_contract_id + endpoint_id must have exactly one implementation-owner task "
        "and one business API module path. A second page that consumes the same endpoint "
        "must reuse that owner task and module through Unit dependencies."
    )
    feedback = _task_plan_retry_feedback(validation_feedback)
    if feedback:
        rules.append(feedback)
    numbered_rules = [
        rule if rule.startswith("--- AUTOMATIC") else f"{index}. {rule}"
        for index, rule in enumerate(rules, start=1)
    ]
    return "## 3. Planning Algorithm\n" + "\n".join(numbered_rules)


def _task_rules_section(mode: str, source_types: set[str]) -> str:
    """生成变更范围语义和路径边界规则。"""

    fragments = [
        "## 4. Task Rules",
        "`change_scope` is the planned file-operation intent, not a pure permission list. "
        "Each item must use `{operation: add|modify|delete, path, description}` with an "
        "exact workspace-relative path. `allowed_paths` remains the execution authorization "
        "boundary. A task must own only its layer's files. Existing equivalent code should "
        "be reused or precisely modified, never recreated under a parallel path. Every "
        "deliverable path must be an exact workspace-relative path owned by the same task "
        "and must also appear in that task's change_scope.",
        "For every frontend or backend change_scope path, the corresponding "
        "WorkspaceSnapshot.<layer>.existing_files is the deterministic file-existence "
        "source: emit operation=modify when the exact path is listed and operation=add "
        "when it is absent. Apply this rule even when choosing an explicit operation; "
        "do not delegate this decision to an execution Agent. The platform will "
        "deterministically normalize add/modify against the live workspace; delete keeps "
        "its separate deletion meaning.",
    ]
    if mode in {"page", "combined", "static"} or "static" in source_types:
        fragments.append("All frontend paths are under `/frontend/`.")
    if mode in {"page", "combined", "static"}:
        fragments.append(
            "The existing page entry uses the exact TargetBuildContext.target.page_key at "
            "`frontend/src/pages/<PageKey>/index.tsx`."
        )
    if source_types & _ENDPOINT_BACKEND_SOURCE_TYPES:
        fragments.append(
            "All backend business source paths are under `/backend/src/main/java/` or "
            "`/backend/src/main/resources/`."
        )
        fragments.append(
            "For every owner=backend task, write `description` as a Simplified Chinese "
            "newline-separated ordered execution list using the exact `1. ...\\n2. ...` "
            "style. Each numbered item must name the exact target path or business "
            "responsibility and must be directly executable, not generic background text. "
            "For a missing path, "
            "the numbered description must directly instruct creation from the confirmed "
            "contract. For an existing path, it must directly instruct reading the existing "
            "file and comparing its behavior with the confirmed business requirements: leave "
            "a fully satisfying file unchanged, and make only the minimum additions or "
            "corrections when it is partially satisfying. When one task owns multiple paths, "
            "state each path's snapshot status and planned action separately. Apply this "
            "description contract to backend:bootstrap, database, and external_api tasks."
        )
        fragments.append(
            "When TargetBuildContext.authorization_constraints contains a non-empty endpoint "
            "operationResourceKeys binding, the matching backend:endpoint task may modify only "
            "the real Controller endpoint: add exactly one @RequireAnyResource annotation before "
            "business logic, reference the platform-generated AuthConstants symbols, and preserve "
            "ANY-OF by passing all keys to that one annotation. Never create or modify AuthConstants, "
            "authorization services, repositories, request-derived permission checks, or data rules."
        )
    return "\n".join(fragments)


def _skill_injection_section(source_types: set[str]) -> str:
    """按数据库、外部 API、静态数据的固定顺序生成独立 Skill 注入段。"""

    fragments = ["## 6. Skill Injection"]
    if "database" in source_types:
        fragments.extend(
            [
                "--- INJECTED springboot-mybatis-generate SKILL.md ---",
                _springboot_mybatis_skill_document(),
                "--- END INJECTED springboot-mybatis-generate SKILL.md ---",
            ]
        )
    if "external_api" in source_types:
        fragments.extend(
            [
                "--- INJECTED springboot-external-api-generate SKILL.md ---",
                _external_api_skill_document(),
                "--- END INJECTED springboot-external-api-generate SKILL.md ---",
            ]
        )
    if "static" in source_types:
        fragments.extend(
            [
                "--- INJECTED frontend-static-data-generate SKILL.md ---",
                _static_data_skill_document(),
                "--- END INJECTED frontend-static-data-generate SKILL.md ---",
            ]
        )
    if len(fragments) == 1:
        fragments.append(
            "No source-specific Skill is required for the current planning scope."
        )
    else:
        fragments.append(
            "Any acceptance or verification guidance inside an injected Skill is subordinate "
            "to the Output Contract. Do not copy acceptance content or platform-owned "
            "verification fields into task output."
        )
    return "\n".join(fragments)


def _dependency_rules_section(source_types: set[str]) -> str:
    """生成同 Unit 依赖和固定阶段链规则。"""

    rules = (
        "## 5. Dependency Rules\n"
        "`dependencies` is a JSON array of prerequisite task IDs from this response and the "
        "same Unit only. Use `[]` for a root. Never reference task titles, Unit IDs, reusable "
        "task IDs, or tasks from another Unit; the deterministic Unit Graph owns all "
        "cross-Unit edges."
    )
    if "database" in source_types:
        rules += (
            " Database chains are objects → repository → service → controller."
        )
    if "external_api" in source_types:
        rules += (
            " External API chains are upstream → mapping → service → controller."
        )
    if source_types & _ENDPOINT_BACKEND_SOURCE_TYPES:
        rules += (
            " The first stage of each entity is a same-Unit root, different entities do not "
            "depend on one another, and each later stage depends only on the immediately "
            "previous stage."
        )
    return rules + (
        " When "
        "TargetBuildContext.reusable_tasks_by_unit lists a Unit, do not recreate its tasks "
        "and do not copy its task ids into dependencies."
    )


def _forbidden_output_section(mode: str, source_types: set[str]) -> str:
    """在所有 Skill 注入后集中声明平台级和来源级禁止项。"""

    rules = [
        "## 7. Forbidden Output",
        "Never create a menu or route registration task, page placeholder, hidden route, or "
        "shared registration task. Never emit menu or route metadata and never modify shared "
        "menu, route, router, registration, framework entry, template dependency, lockfile, "
        "global style, or scaffold files.",
        "Never plan tests, test files, builds, lint, type checks, verification, smoke checks, "
        "runtime availability checks, or business acceptance tasks.",
        "Never return platform-owned acceptance criteria, acceptance checks, business checks, "
        "evidence, summaries, or verification commands; the platform compiles engineering "
        "and business checks deterministically from change_scope, allowed_paths, deliverables, "
        "and formal contracts.",
        "Never output authorization, authorization_constraints, or source_refs.authorization; "
        "the platform injects immutable authorization slices after Unit selection.",
        "Never create, modify, or list AuthConstants in change_scope, allowed_paths, or deliverables; "
        "the platform writes business operation constants into the auth template managed region after DAG confirmation.",
        "Never create owner=database tasks, database:* Units, DDL, schema/table changes, "
        "migrations, or seed SQL. Never add CRUD operations, endpoints, fields, credentials, "
        "URLs, headers, or configuration outside confirmed contracts.",
        "Only backend:bootstrap may plan modifications to the existing backend/pom.xml and "
        "exact datasource/MyBatis configuration paths; ordinary endpoint tasks must not "
        "modify global configuration.",
    ]
    if mode == "page":
        rules.append(
            "Page-only scope must not create backend, database, Spring, MyBatis, endpoint "
            "implementation, or entity-persistence tasks."
        )
    if source_types == {"static"} or mode == "static":
        rules.append(
            "Never create database, backend, Controller, Mapper, PO, Spring Boot, MyBatis, "
            "datasource, real HTTP endpoint, proxy, or mock-plugin work for static entities."
        )
    if "external_api" in source_types:
        rules.append(
            "External API entities must not create Entity/PO, Mapper, Repository, datasource, "
            "migration, or seed work."
        )
    return "\n".join(rules)


def _workspace_context_section(
    workspace_snapshot: dict[str, Any],
    build_context: dict[str, Any],
    project_plan: dict[str, Any],
) -> str:
    """把有界工作区、目标和正式计划上下文统一放在 Prompt 末尾。"""

    return (
        "## 8. Workspace Context\n"
        "### WorkspaceSnapshot\n"
        + json.dumps(workspace_snapshot, ensure_ascii=False, indent=2)
        + "\n\n### TargetBuildContext\n"
        + json.dumps(build_context, ensure_ascii=False, indent=2)
        + "\n\n### TaskPreparationContext\n"
        + json.dumps(project_plan, ensure_ascii=False, indent=2)
    )


def _task_plan_retry_feedback(errors: list[str] | None) -> str:
    """把平台校验错误转换为下一次任务规划模型的内部重生成指令。"""

    normalized = [str(error).strip() for error in errors or [] if str(error).strip()]
    if not normalized:
        return ""
    return (
        "--- AUTOMATIC REGENERATION FEEDBACK ---\n"
        "The previous candidate violated platform-owned boundaries or DAG constraints. "
        "Regenerate the complete JSON object, fix every issue, and do not ask the user to "
        "split platform tasks:\n"
        + "\n".join(f"- {error}" for error in normalized[:20])
        + "\n--- END AUTOMATIC REGENERATION FEEDBACK ---"
    )


def scoped_prompt_build_context(
    build_context: dict[str, Any] | None,
    mode: str,
) -> dict[str, Any]:
    """从目标上下文中移除另一侧详情正文，保留当前模式的引用索引。"""

    context = dict(build_context) if isinstance(build_context, dict) else {}
    planning_unit_ids = context.get("planning_unit_ids")
    if isinstance(planning_unit_ids, list) and planning_unit_ids:
        context["required_unit_ids"] = list(planning_unit_ids)
    source_refs = context.get("source_refs")
    if isinstance(source_refs, dict):
        source_refs = dict(source_refs)
        if mode == "page":
            source_refs.pop("technical_plan_endpoints", None)
        elif mode == "endpoint":
            source_refs.pop("page_implementation_contract", None)
        context["source_refs"] = source_refs
    if mode == "page":
        for key in ("endpoint_contract", "direct_endpoint_contracts", "entity_designs", "entity_ids"):
            context.pop(key, None)
    elif mode == "endpoint":
        for key in (
            "page_implementation_contract",
            "endpoint_contract",
            "direct_endpoint_contracts",
            "entity_designs",
            "required_endpoint_ids",
            "planning_unit_ids",
            "planning_context_mode",
        ):
            context.pop(key, None)
        allowed_keys = {
            "target",
            "endpoint_ids",
            "entity_ids",
            "required_unit_ids",
            "source_refs",
            "reusable_tasks_by_unit",
            "authorization_constraints",
        }
        return {key: value for key, value in context.items() if key in allowed_keys}
    return context


def _snapshot_scope(mode: str, source_types: set[str]) -> str:
    """根据规划模式和实体来源选择最小工作区快照范围。"""

    if mode == "page":
        return "page"
    if mode == "static" or source_types == {"static"}:
        return "frontend"
    if mode == "endpoint":
        if "static" in source_types and source_types & _ENDPOINT_BACKEND_SOURCE_TYPES:
            return "endpoint_combined"
        return "endpoint"
    return "combined"


def compact_workspace_snapshot(
    workspace_snapshot: dict[str, Any] | None,
    *,
    scope: str = "combined",
) -> dict[str, Any]:
    """裁剪规划 Prompt 的工作区快照，并按 endpoint/page 过滤另一侧事实。"""

    if not isinstance(workspace_snapshot, dict):
        return {}
    if scope in {"endpoint", "backend"}:
        return _compact_endpoint_workspace_snapshot(workspace_snapshot, roots=("backend",))
    if scope == "frontend":
        return _compact_endpoint_workspace_snapshot(workspace_snapshot, roots=("frontend",))
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
        compact[key] = {
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
        if key in {"backend", "frontend"}:
            compact[key]["existing_files"] = _existing_files_from_dir_structure(
                value.get("dir_structure")
            )
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
        scoped_paths = _filter_workspace_paths_for_roots(workspace_snapshot.get(key), roots)
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
        bounded = {
            key: _bounded_prompt_value(item, limit=80)
            for key, item in value.items()
            if key in allowed_sections[root]
        }
        if root in {"backend", "frontend"}:
            bounded["existing_files"] = _existing_files_from_dir_structure(
                value.get("dir_structure")
            )
        if bounded:
            compact[root] = bounded
    return compact


def _existing_files_from_dir_structure(value: Any) -> list[str]:
    """将工作区树形目录转换为供规划 Prompt 使用的精确文件路径列表。"""

    if not isinstance(value, str):
        return []
    directory_stack: list[str] = []
    files: list[str] = []
    for raw_line in value.splitlines():
        connector_positions = [
            position
            for marker in ("├── ", "└── ")
            if (position := raw_line.find(marker)) >= 0
        ]
        if not connector_positions:
            continue
        connector_position = min(connector_positions)
        # workspace_inspector 每一层固定使用四个字符缩进；异常行不参与投影。
        if connector_position % 4 != 0:
            continue
        depth = connector_position // 4
        label = raw_line[connector_position + 4 :].strip()
        is_directory = label.endswith("/")
        component = label[:-1] if is_directory else label
        if (
            not component
            or component in {".", ".."}
            or "/" in component
            or "\\" in component
        ):
            continue
        directory_stack = directory_stack[:depth]
        if is_directory:
            directory_stack.append(component)
            continue
        files.append("/".join([*directory_stack, component]))
    return files


def _filter_workspace_paths_for_roots(value: Any, roots: tuple[str, ...]) -> list[Any]:
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


def task_preparation_datasource_types(project_plan: dict[str, Any]) -> set[str]:
    """从任务准备投影读取并校验数据源类型集合。"""

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
