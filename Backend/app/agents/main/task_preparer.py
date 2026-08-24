from __future__ import annotations

from hashlib import sha256
import logging
from typing import Any

from app.agents.main.task_preparer_prompt import build_task_preparation_prompt
from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.build_task_planner import (
    build_task_candidate_contract_errors,
    create_build_task_plan,
)
from app.utils.model_output import extract_json_object


logger = logging.getLogger(__name__)


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
    prompt = build_task_preparation_prompt(
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
        _log_task_model_response_diagnostics(agent_note, agent_plan)

        raw_contract_errors = build_task_candidate_contract_errors(agent_plan)
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
            last_errors = list(dict.fromkeys([*raw_contract_errors, str(exc)]))
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
                    + "；".join(last_errors)
                ) from exc
            feedback = last_errors
            continue

        last_errors = _merge_candidate_validation_errors(
            raw_contract_errors,
            _build_task_plan_validation_errors(build_task_plan),
        )
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
        execution.get("blocked_batches") if isinstance(execution, dict) else []
    )
    for batch in blocked_batches if isinstance(blocked_batches, list) else []:
        if not isinstance(batch, dict):
            continue
        reason = str(batch.get("reason") or "任务执行批次被阻断。").strip()
        if reason and reason not in errors:
            errors.append(reason)
    return errors


def _merge_candidate_validation_errors(
    raw_contract_errors: list[str],
    compiled_errors: list[str],
) -> list[str]:
    """合并原始与编译错误，并用精确字段错误替代交付物缺失的泛化提示。"""

    precise_task_prefixes = {
        error.split(" deliverables", 1)[0]
        for error in raw_contract_errors
        if " deliverables" in error
    }
    generic_suffix = " must declare at least one deliverable."
    filtered_compiled_errors = [
        error
        for error in compiled_errors
        if not (
            error.endswith(generic_suffix)
            and error[: -len(generic_suffix)] in precise_task_prefixes
        )
    ]
    return list(dict.fromkeys([*raw_contract_errors, *filtered_compiled_errors]))


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
    logger.info(
        "build_task_model_call model=%s prompt_chars=%s prompt_utf8_bytes=%s "
        "input_tokens=%s output_tokens=%s finish_reason=%s configured_max_tokens=%s",
        model_name,
        len(prompt),
        len(prompt.encode("utf-8")),
        usage.get("input_tokens"),
        usage.get("output_tokens"),
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
