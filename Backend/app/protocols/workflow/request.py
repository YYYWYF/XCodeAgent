"""将受支持的 HTTP 和 AG-UI 请求结构归一化为主工作流输入。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.workspace.plan_documents import load_project_plan_json
from app.workspace.spec_documents import load_requirement_spec_json
from app.workspace.task_documents import load_build_task_plan_json
from app.workspace.workspace_snapshot_documents import load_workspace_snapshot_json
from app.services.workspace_inspector import snapshot_hash


MAX_SELECTED_SKILLS = 64
MAX_SELECTED_SKILL_NAME_CHARS = 128


class InvalidSelectedSkillsError(ValueError):
    """表示 Workflow 请求中的技能名称集合格式无效。"""

    code = "invalid_selected_skills"


class SelectedSkillConflictError(ValueError):
    """表示恢复请求试图替换原 Workflow 的技能集合。"""

    code = "selected_skill_conflict"


def workflow_run_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    """应用兼容性回退规则并返回统一的运行时输入。

    显式顶层字段优先于 forwardedProps；只有 `request` 和 `message` 都不存在时，
    才会使用 AG-UI 消息列表中的最后一条用户消息。
    """

    forwarded_props = _optional_dict(payload.get("forwardedProps")) or {}
    application = _optional_dict(forwarded_props.get("application")) or {}
    state = _optional_dict(payload.get("state")) or {}
    clarification_answers = (
        payload.get("clarificationAnswers")
        or forwarded_props.get("clarificationAnswers")
    )
    request = (
        _optional_text(payload.get("request"))
        or _optional_text(payload.get("message"))
        or _last_user_message(payload.get("messages"))
    )
    request = _merge_clarification_answers(
        request=request,
        original_request=(
            _optional_text(payload.get("originalRequest"))
            or _optional_text(forwarded_props.get("originalRequest"))
        ),
        clarification_answers=clarification_answers,
    )
    resume_state = (
        _optional_dict(payload.get("resumeState"))
        or _optional_dict(payload.get("resume_state"))
        or _optional_dict(forwarded_props.get("resumeState"))
        or _optional_dict(forwarded_props.get("resume_state"))
    )
    debug_state = (
        _optional_dict(payload.get("workflowDebug"))
        or _optional_dict(payload.get("debugState"))
        or _optional_dict(forwarded_props.get("workflowDebug"))
        or _optional_dict(forwarded_props.get("debugState"))
        or {}
    )
    workflow_scope = (
        _optional_text(payload.get("workflowScope"))
        or _optional_text(forwarded_props.get("workflowScope"))
    )
    resume_from = _supported_resume_node(
        (
            _resume_from_state(resume_state, workflow_scope=workflow_scope)
            or _optional_text(debug_state.get("resume_from"))
            or _optional_text(debug_state.get("resumeFrom"))
            or _optional_text(payload.get("resume_from"))
            or _optional_text(payload.get("resumeFrom"))
            or _optional_text(forwarded_props.get("resume_from"))
            or _optional_text(forwarded_props.get("resumeFrom"))
        ),
        workflow_scope=workflow_scope,
    )
    if not resume_from and _clarification_answers_to_text(clarification_answers):
        resume_from = (
            "requirements"
            if workflow_scope == "application_planning"
            else "detail_confirmation"
        )
    if not request and resume_from:
        request = f"从 {resume_from} 节点继续执行 workflow 调试。"
    detail_review_submission = _detail_review_submission(clarification_answers)
    selectedPageId = (
        _optional_text(payload.get("selectedPageId"))
        or _optional_text(forwarded_props.get("selectedPageId"))
    )
    workspace = (
        _optional_text(payload.get("workspace"))
        or _optional_text(payload.get("workspaceRoot"))
        or _optional_text(forwarded_props.get("workspaceRoot"))
        or _optional_text(application.get("workspaceRoot"))
    )
    editor_mode = _supported_editor_mode(
        _optional_text(payload.get("editor_mode"))
        or _optional_text(payload.get("editorMode"))
        or _optional_text(forwarded_props.get("editor_mode"))
        or _optional_text(forwarded_props.get("editorMode"))
    )
    selected_skill_names, selected_skills_error = _workflow_selected_skill_names(
        payload,
        forwarded_props=forwarded_props,
        resume_state=resume_state,
    )
    resume_values = {
        **(
            _project_plan_start_values(workspace)
            if workflow_scope != "application_planning"
            else {}
        ),
        **_resume_values(resume_state),
        **_debug_resume_values(debug_state, workspace=workspace),
        "selected_skill_names": list(selected_skill_names),
        **(
            {"detail_review_submission": detail_review_submission}
            if detail_review_submission
            else {}
        ),
        **({"selectedPageId": selectedPageId} if selectedPageId else {}),
    }

    return {
        "cancel_run_id": (
            _optional_text(payload.get("cancelRunId"))
            or _optional_text(payload.get("cancel_run_id"))
            or _optional_text(forwarded_props.get("cancelRunId"))
            or _optional_text(forwarded_props.get("cancel_run_id"))
        ),
        "request": request,
        "resume_from": resume_from,
        "resume_values": resume_values,
        "selected_skill_names": list(selected_skill_names),
        "selected_skills_error": selected_skills_error,
        "project_id": (
            _optional_text(payload.get("project_id"))
            or _optional_text(payload.get("projectId"))
            or _optional_text(state.get("project_id"))
            or _optional_text(state.get("projectId"))
            or _optional_text(application.get("id"))
        ),
        "workspace": workspace,
        "editor_mode": editor_mode,
        "workflow_scope": workflow_scope,
        "thread_id": (
            _optional_text(payload.get("thread_id"))
            or _optional_text(payload.get("threadId"))
        ),
        "run_id": (
            _optional_text(payload.get("run_id"))
            or _optional_text(payload.get("runId"))
        ),
    }


def _workflow_selected_skill_names(
    payload: dict[str, Any],
    *,
    forwarded_props: dict[str, Any],
    resume_state: dict[str, Any] | None,
) -> tuple[tuple[str, ...], ValueError | None]:
    """解析显式选择和恢复状态，并把校验错误延迟到 AG-UI 生命周期内。"""

    try:
        explicit_present, explicit_names = _selected_skill_names_from_sources(
            payload,
            forwarded_props,
        )
        resumed_present, resumed_names = _selected_skill_names_from_resume(resume_state)
        if resumed_present:
            if explicit_present and explicit_names != resumed_names:
                raise SelectedSkillConflictError(
                    "恢复 Workflow 时不能更换最初选择的用户技能。"
                )
            return resumed_names, None
        return explicit_names, None
    except (InvalidSelectedSkillsError, SelectedSkillConflictError) as exc:
        return (), exc


def _selected_skill_names_from_sources(
    *sources: dict[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    """按来源优先级读取 camelCase 或 snake_case 技能字段。"""

    for source in sources:
        for field_name in ("selectedSkillNames", "selected_skill_names"):
            if field_name in source:
                return True, _normalize_selected_skill_names(source[field_name])
    return False, ()


def _selected_skill_names_from_resume(
    resume_state: dict[str, Any] | None,
) -> tuple[bool, tuple[str, ...]]:
    """从公开 state 或 result 中恢复初始技能集合。"""

    if not resume_state:
        return False, ()
    state = _optional_dict(resume_state.get("state")) or {}
    result = _optional_dict(resume_state.get("result")) or {}
    return _selected_skill_names_from_sources(state, result)


def _normalize_selected_skill_names(value: Any) -> tuple[str, ...]:
    """严格校验并生成稳定、去重的技能名称元组。"""

    if not isinstance(value, list):
        raise InvalidSelectedSkillsError("selectedSkillNames 必须是字符串数组。")
    if len(value) > MAX_SELECTED_SKILLS:
        raise InvalidSelectedSkillsError(
            f"一次最多选择 {MAX_SELECTED_SKILLS} 个用户技能。"
        )
    normalized: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise InvalidSelectedSkillsError("selectedSkillNames 只能包含字符串。")
        name = item.strip()
        if not name:
            raise InvalidSelectedSkillsError("selectedSkillNames 不能包含空名称。")
        if len(name) > MAX_SELECTED_SKILL_NAME_CHARS:
            raise InvalidSelectedSkillsError("用户技能名称过长。")
        normalized.add(name)
    return tuple(sorted(normalized, key=lambda name: (name.casefold(), name)))


def _last_user_message(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""

    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            text = _message_content_to_text(message.get("content"))
            if text:
                return text

    return ""


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item.get("text"), str):
                    parts.append(item["text"])
            elif hasattr(item, "text") and isinstance(item.text, str):
                parts.append(item.text)
        return "\n".join(part for part in parts if part).strip()

    return str(content).strip() if content is not None else ""


def _optional_text(value: Any) -> str:
    return str(value).strip() if value is not None and str(value).strip() else ""


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _supported_editor_mode(value: str) -> str:
    return value if value in {"frontend", "backend"} else ""


def _resume_from_state(
    value: dict[str, Any] | None,
    *,
    workflow_scope: str = "",
) -> str:
    """根据当前 Graph 范围从公开状态推断可恢复节点。"""

    if not value:
        return ""

    events = value.get("events")
    if isinstance(events, list):
        for event in reversed(events):
            if not isinstance(event, dict):
                continue
            if event.get("status") != "requires_user_input":
                continue
            node_name = _optional_text(event.get("nodeName"))
            if node_name:
                return _supported_resume_node(
                    node_name,
                    workflow_scope=workflow_scope,
                )
            node = _optional_dict(event.get("node"))
            node_id = _optional_text(node.get("id")) if node else ""
            if node_id:
                return _supported_resume_node(
                    node_id,
                    workflow_scope=workflow_scope,
                )

    state = _optional_dict(value.get("state")) or {}
    summary = _optional_dict(value.get("summary")) or {}
    for source in (state, summary, value):
        if source.get("status") == "requires_user_input":
            phase = _optional_text(source.get("phase"))
            if phase:
                return _supported_resume_node(
                    phase,
                    workflow_scope=workflow_scope,
                )

    return ""


def _supported_resume_node(node_name: str, *, workflow_scope: str = "") -> str:
    """限制独立规划 Graph 与主 Graph 各自可恢复的节点集合。"""

    supported = (
        {"requirements", "project_planning"}
        if workflow_scope == "application_planning"
        else {
            "detail_confirmation",
            "inspect_workspace",
            "prepare_build_tasks",
            "build",
            "integration_test",
            "launch_project",
            "acceptance",
            "finalize_project",
        }
    )
    return node_name if node_name in supported else ""


def _resume_values(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}

    state = _optional_dict(value.get("state")) or {}
    result = _optional_dict(value.get("result")) or {}
    merged = {**state, **result}
    allowed_keys = {
        "project_plan",
        "frontend_pages",
        "pending_project_plan",
        "project_plan_path",
        "project_plan_json_path",
        "detail_selection",
        "selectedPageId",
        "selected_data_source_id",
        "page_spec_draft",
        "data_source_spec_draft",
        "detail_plans",
        "detail_review_submission",
        "workspace_snapshot_summary",
        "workspace_snapshot_path",
        "workspace_snapshot_hash",
        "workspace_revision",
        "requirement_spec",
        "requirement_spec_path",
        "requirement_spec_json_path",
        "build_task_plan",
        "build_task_plan_path",
        "tasks",
        "selected_skill_names",
        "workflow_scope",
    }
    return {
        key: merged[key]
        for key in allowed_keys
        if key in merged and merged[key] is not None
    }


def _project_plan_start_values(workspace: str) -> dict[str, Any]:
    """从工作区正式计划文件加载主 Workflow 的完整计划和页面功能概览。"""

    workspace_root = _workspace_root_path(workspace)
    if workspace_root is None:
        return {}
    for relative_path in (
        Path(".xcodeagent/plans/project-plan.json"),
        Path("plans/project-plan.json"),
    ):
        project_plan_path = workspace_root / relative_path
        if not project_plan_path.is_file():
            continue
        project_plan = load_project_plan_json(
            project_plan_path,
            hydrate_detail_designs=True,
        )
        if not isinstance(project_plan, dict):
            raise ValueError("project-plan.json 的根结构必须是 JSON 对象。")
        frontend_pages = project_plan.get("frontend_pages", [])
        return {
            "project_plan": project_plan,
            "frontend_pages": (
                frontend_pages if isinstance(frontend_pages, list) else []
            ),
            "project_plan_path": _markdown_sibling_path(project_plan_path),
            "project_plan_json_path": str(project_plan_path),
        }
    return {}


def _debug_resume_values(
    debug_state: dict[str, Any],
    *,
    workspace: str = "",
) -> dict[str, Any]:
    if not debug_state or debug_state.get("enabled") is False:
        return {}

    values: dict[str, Any] = {}
    requirement_path = _resolve_debug_json_path(
        debug_state,
        ("requirement_spec_path", "requirementSpecPath", "requirementSpecDirectory"),
        (
            "requirement-spec.json",
            ".xcodeagent/specs/requirement-spec.json",
            "specs/requirement-spec.json",
        ),
        workspace=workspace,
    )
    if requirement_path:
        values["requirement_spec_path"] = _markdown_sibling_path(requirement_path)
        values["requirement_spec_json_path"] = str(requirement_path)
        values["requirement_spec"] = load_requirement_spec_json(requirement_path)

    project_plan_path = _resolve_debug_json_path(
        debug_state,
        ("project_plan_path", "projectPlanPath", "projectPlanDirectory"),
        (
            "project-plan.json",
            ".xcodeagent/plans/project-plan.json",
            "plans/project-plan.json",
        ),
        workspace=workspace,
    )
    if project_plan_path:
        values["project_plan_path"] = _markdown_sibling_path(project_plan_path)
        values["project_plan_json_path"] = str(project_plan_path)
        values["project_plan"] = load_project_plan_json(
            project_plan_path,
            hydrate_detail_designs=True,
        )

    build_task_plan_path = _resolve_debug_json_path(
        debug_state,
        ("build_task_plan_path", "buildTaskPlanPath", "buildTaskPlanDirectory"),
        (
            "build-task-plan.json",
            ".xcodeagent/plans/build-task-plan.json",
            "plans/build-task-plan.json",
        ),
        workspace=workspace,
    )
    if build_task_plan_path:
        build_task_plan = load_build_task_plan_json(build_task_plan_path)
        values["build_task_plan_path"] = str(build_task_plan_path)
        values["build_task_plan"] = build_task_plan
        if isinstance(build_task_plan.get("tasks"), list):
            values["tasks"] = build_task_plan["tasks"]

    workspace_snapshot_path = _resolve_debug_workspace_snapshot_path(
        debug_state,
        workspace=workspace,
    )
    if workspace_snapshot_path:
        workspace_snapshot = load_workspace_snapshot_json(workspace_snapshot_path)
        values["workspace_snapshot_path"] = str(workspace_snapshot_path)
        values["workspace_snapshot_hash"] = snapshot_hash(workspace_snapshot)
        values["workspace_revision"] = str(
            workspace_snapshot.get("workspace_revision") or ""
        )
        values["workspace_snapshot_summary"] = _workspace_snapshot_summary(
            workspace_snapshot
        )

    return values


def _resolve_debug_workspace_snapshot_path(
    debug_state: dict[str, Any],
    *,
    workspace: str = "",
) -> Path | None:
    raw_path = ""
    for field_name in (
        "workspace_snapshot_path",
        "workspaceSnapshotPath",
        "workspaceSnapshotDirectory",
    ):
        raw_path = _optional_text(debug_state.get(field_name))
        if raw_path:
            break
    if not raw_path:
        workspace_root = _workspace_root_path(workspace)
        if not workspace_root:
            return None
        path = workspace_root / ".xcodeagent" / "cache" / "workspace-snapshots"
        if not path.is_dir():
            path = workspace_root / "cache" / "workspace-snapshots"
            if not path.is_dir():
                return None
    else:
        path = Path(raw_path).expanduser()

    if path.is_file() and path.suffix == ".json":
        return path
    if path.is_dir():
        candidates = sorted(
            path.glob("*.json"),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None
    return None


def _workspace_snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    code_graph = snapshot.get("code_graph") if isinstance(snapshot, dict) else {}
    return {
        "schema_version": snapshot.get("schema_version"),
        "workspace_revision": snapshot.get("workspace_revision"),
        "tech_stack": snapshot.get("tech_stack", []),
        "entrypoints": snapshot.get("entrypoints", []),
        "project_roots": snapshot.get("project_roots", []),
        "file_manifest": snapshot.get("file_manifest", {}),
        "code_graph": {
            "provider": (code_graph or {}).get("provider"),
            "available": bool((code_graph or {}).get("available")),
        },
    }


def _resolve_debug_json_path(
    debug_state: dict[str, Any],
    field_names: tuple[str, ...],
    default_files: tuple[str, ...],
    *,
    workspace: str = "",
) -> Path | None:
    raw_path = ""
    for field_name in field_names:
        raw_path = _optional_text(debug_state.get(field_name))
        if raw_path:
            break
    if not raw_path:
        workspace_root = _workspace_root_path(workspace)
        if not workspace_root:
            return None
        candidates = [workspace_root / default_file for default_file in default_files]
        return next((candidate for candidate in candidates if candidate.exists()), None)

    path = Path(raw_path).expanduser()
    if path.is_file():
        if path.suffix == ".json":
            return path
        json_sibling = path.with_suffix(".json")
        return json_sibling if json_sibling.exists() else None

    candidates = [path / default_file for default_file in default_files]
    return next((candidate for candidate in candidates if candidate.exists()), None)


def _workspace_root_path(workspace: str) -> Path | None:
    workspace_text = _optional_text(workspace)
    if not workspace_text:
        return None
    path = Path(workspace_text).expanduser()
    return path if path.is_dir() else None


def _markdown_sibling_path(json_path: Path) -> str:
    markdown_path = json_path.with_suffix(".md")
    return str(markdown_path)


def _merge_clarification_answers(
    *,
    request: str,
    original_request: str,
    clarification_answers: Any,
) -> str:
    answers_text = _clarification_answers_to_text(clarification_answers)
    if not answers_text:
        return request

    base_request = original_request or request
    return "\n".join(
        [
            "请基于原始需求和以下用户补充确认，继续生成需求文档并推进后续 workflow。",
            "",
            "原始需求：",
            base_request,
            "",
            "用户补充确认：",
            answers_text,
        ]
    ).strip()


def _clarification_answers_to_text(value: Any) -> str:
    if isinstance(value, dict):
        lines: list[str] = []
        for key, answer in value.items():
            answer_text = _answer_to_text(answer)
            if answer_text:
                lines.extend([f"- {key}", f"  回答：{answer_text}"])
        return "\n".join(lines)

    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                question = _optional_text(item.get("question")) or _optional_text(
                    item.get("header")
                )
                answer = _answer_to_text(item.get("answer"))
                if question and answer:
                    lines.append(f"- {question}: {answer}")
            else:
                answer = _answer_to_text(item)
                if answer:
                    lines.append(f"- {answer}")
        return "\n".join(lines)

    return _answer_to_text(value)


def _answer_to_text(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        selected = _selected_answer_text(value.get("selected"))
        other = _optional_text(value.get("other"))
        if selected or other:
            parts = []
            if selected:
                parts.append(f"已选：{selected}")
            if other:
                parts.append(f"其他补充：{other}")
            return "；".join(parts)
        return ", ".join(
            f"{key}={answer}" for key, answer in value.items() if str(answer).strip()
        )
    return str(value).strip() if value is not None else ""


def _detail_review_submission(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    submission = value.get("detail_review")
    if not isinstance(submission, dict):
        return None
    if submission.get("review_status") != "confirmed":
        return None
    return submission


def _selected_answer_text(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(
            str(item).strip()
            for item in value
            if str(item).strip() and str(item).strip() != "__other__"
        )
    text = str(value).strip() if value is not None else ""
    return "" if text == "__other__" else text
