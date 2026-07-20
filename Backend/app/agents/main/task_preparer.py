from __future__ import annotations

from hashlib import sha256
import json
import logging
from typing import Any

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
    frontend_root = f"apps/{app_name}/frontend" if app_name else "apps/<app.name>/frontend"
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
        "Prepare executable tasks only from TaskPreparationContext.executable_details "
        "and TargetBuildContext. TaskPreparationContext.application_skeleton is a "
        "non-executable Unit skeleton: it describes all pages, data sources, public "
        "application units, and API ranges, but it must never cause tasks for pages or "
        "data sources outside TargetBuildContext.required_unit_ids.\n"
        "Do not invent generic paths when an existing project convention is present in "
        "the snapshot. For page tasks, use only confirmed executable_details.page_detail_plans "
        "and preserve page_goal, layout_design, operation_interactions, state_feedback, "
        "api_dependencies, response_bindings, page_navigation, permissions, and "
        "acceptance_criteria. For backend/data tasks, use only executable_details."
        "data_source_detail_plans, executable_details.data_sources, and "
        "executable_details.api_contracts. ProjectPlan/API contracts in executable_details "
        "are the only source of fields; preserve schema_refs, endpoint ids, "
        "request/response schema refs, and page response_bindings in task source references.\n"
        "Split work into independently verifiable tasks. Every task must include:\n"
        "- id: a stable unique task id\n"
        "- unit_id: one of the required Unit IDs from TargetBuildContext\n"
        "- owner: frontend or data_source\n"
        "- title and description\n"
        "- dependencies: ids of prerequisite tasks\n"
        "- change_scope: [{operation: add|modify|delete, path, description}] using exact workspace-relative paths\n"
        "- impact_scope: {summary, affected_modules, public_contracts, risks}\n"
        "- can_run_in_parallel and parallel_reason\n"
        "- acceptance_criteria: concrete, testable completion conditions\n"
        "- verification_commands when known\n"
        "- status: pending for every newly planned task\n"
        "When TargetBuildContext.reusable_tasks_by_unit lists an application Unit, do not "
        "create another task for that Unit; reference its listed task ids as dependencies when needed.\n"
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
    return content if isinstance(content, str) else str(content)


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
