"""审查阶段的代码扫描、受控修复和前后端构建子图。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.agents.code_review_repair import (
    invoke_code_review_repair_agent,
    normalize_code_review_repair_result,
)
from app.agents.code_analyze.scope import is_code_review_change_path
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.state import ProjectState
from app.services.integration_test_runner import run_integration_checks
from app.agents.code_analyze.analyzer import analyze_workspace_code, _safe_review_text
from app.workspace.code_changes import code_change_state_update
from app.workspace.code_review_documents import write_code_review_markdown


CODE_REVIEW_REPAIR_CONFIRMATION_MODE = "code_review_repair_confirmation"
CODE_REVIEW_REPAIR_ACTION = "repair_all"
CODE_REVIEW_REPAIR_EVENT_TYPE = "code_review.repair"
CODE_REVIEW_BUILD_EVENT_TYPE = "code_review.build_checks"
MAX_CODE_REVIEW_REPAIR_ITERATIONS = 3
_CODE_REVIEW_CAPTURE_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".xcodeagent",
    "node_modules",
}
_CODE_REVIEW_CAPTURE_ROOTS = ("frontend", "backend/src/main/java")
_REVIEW_BUILD_CHECKS = (
    ("frontend_install", "前端依赖安装检查", "frontend"),
    ("frontend_build", "前端构建检查", "frontend"),
    ("backend_build", "后端构建检查", "backend"),
)


def _writer() -> Callable[[dict[str, Any]], None]:
    """读取当前 Graph 的瞬态 custom writer，测试环境无 writer 时使用空回调。"""

    try:
        return get_stream_writer()
    except RuntimeError:
        return lambda _event: None


def _issues(state: ProjectState) -> list[dict[str, Any]]:
    """读取有界的扫描问题列表。"""

    value = state.get("code_review_result")
    if not isinstance(value, dict) or not isinstance(value.get("issues"), list):
        return []
    return [dict(item) for item in value["issues"][:100] if isinstance(item, dict)]


def _issue_ids(issues: list[dict[str, Any]]) -> set[str]:
    """提取问题稳定 ID，缺少 ID 的结果不允许进入自动修复。"""

    return {str(item.get("id") or "").strip() for item in issues if str(item.get("id") or "").strip()}


def _max_repair_attempts(state: ProjectState) -> int:
    """读取并钳制修复预算，任何恢复快照都不能把上限扩大到三轮以上。"""

    raw_value = state.get(
        "code_review_max_repair_iterations",
        MAX_CODE_REVIEW_REPAIR_ITERATIONS,
    )
    value = (
        int(raw_value)
        if isinstance(raw_value, int) and not isinstance(raw_value, bool)
        else MAX_CODE_REVIEW_REPAIR_ITERATIONS
    )
    return max(1, min(value, MAX_CODE_REVIEW_REPAIR_ITERATIONS))


def _repair_path(value: Any) -> str:
    """规范化并校验修复 Agent 返回的授权相对路径。"""

    normalized = str(value or "").strip().replace("\\", "/").lstrip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        return ""
    return normalized if is_code_review_change_path(normalized) else ""


def _required_repair_actions(issues: list[dict[str, Any]]) -> set[str]:
    """汇总扫描问题声明的有限修复动作。"""

    return {
        str(action or "").strip()
        for issue in issues
        for action in (
            issue.get("repair_actions")
            if isinstance(issue.get("repair_actions"), list)
            else []
        )
        if str(action or "").strip()
    }


def _failed_build_checks(value: Any) -> list[dict[str, Any]]:
    """裁剪上一轮构建失败证据，避免把完整日志带入 Agent。"""

    if not isinstance(value, list):
        return []
    failures: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or item.get("passed") is not False:
            continue
        execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
        failures.append(
            {
                "id": str(item.get("id") or "")[:120],
                "name": str(item.get("name") or "")[:200],
                "layer": str(item.get("layer") or "")[:40],
                "evidence": str(item.get("evidence") or "")[-2_000:],
                "stdout_tail": str(execution.get("stdout_tail") or "")[-2_000:],
                "stderr_tail": str(execution.get("stderr_tail") or "")[-2_000:],
            }
        )
    return failures[:10]


def _frontend_install_result(value: Any) -> dict[str, Any] | None:
    """把修复 Agent 的成功 pnpm 证据转换为可复用的安装检查结果。"""

    if not isinstance(value, dict) or value.get("status") != "passed":
        return None
    if value.get("exit_code") != 0 or value.get("command") != ["pnpm", "install"]:
        return None
    return {
        "id": "frontend_install",
        "name": "前端依赖安装检查",
        "layer": "frontend",
        "language": "typescript",
        "passed": True,
        "required": True,
        "command": ["pnpm", "install"],
        "evidence": "修复 Agent 已按前端 Skill 执行 pnpm install 并成功更新依赖。",
        "failure_category": None,
        "execution": {
            "tool": "pnpm_install_frontend",
            "argv": ["pnpm", "install"],
            "cwd": "frontend",
            "returncode": 0,
            "timed_out": False,
            "stdout_log": value.get("stdout_log"),
            "stderr_log": value.get("stderr_log"),
            "stdout_tail": str(value.get("stdout_tail") or "")[-4_000:],
            "stderr_tail": str(value.get("stderr_tail") or "")[-4_000:],
        },
    }


def _repair_confirmation_payload(
    issues: list[dict[str, Any]], *, truncated: bool = False
) -> dict[str, Any]:
    """生成代码审查问题的一键修复确认载荷。"""

    count = len(issues)
    return {
        "mode": CODE_REVIEW_REPAIR_CONFIRMATION_MODE,
        "status": "requires_user_input",
        "message": "代码审查发现问题，请在上方执行一键修复。",
        "issueCount": count,
        "truncated": truncated,
        "questions": [],
    }


def _route_review_start(state: ProjectState) -> str:
    """首次进入扫描，确认一键修复后直接进入修复，避免重复扫描。"""

    submission = state.get("code_review_repair_confirmation")
    if (
        isinstance(submission, dict)
        and submission.get("action") == CODE_REVIEW_REPAIR_ACTION
        and _issue_ids(_issues(state))
    ):
        return "code_review_repair"
    return "code_scan"


def code_scan(state: ProjectState) -> dict[str, Any]:
    """调用只读审查 Agent，并在发现问题时暂停等待一键修复。"""

    workspace = workspace_from_state(state)
    writer = _writer()
    writer({"type": "code_review.scan", "status": "running"})
    try:
        result = analyze_workspace_code(state, workspace)
        report_path = write_code_review_markdown(state, result)
    except Exception as exc:  # noqa: BLE001 - 子图边界统一转换为失败状态
        return {
            "phase": "code_review",
            "status": "failed",
            "message": "前后端代码审查失败。",
            "error": _safe_review_text(f"{type(exc).__name__}: {exc}", workspace),
            "code_review_result": {},
            "code_review_report_path": "",
            "code_review_repair_status": "failed",
            "code_review_next_action": "handle_failure",
            "code_review_events": ["code_scan"],
            "timeline": ["code_review", "code_scan"],
        }

    issues = [item for item in result.get("issues", []) if isinstance(item, dict)]
    if issues:
        return {
            "phase": "code_review",
            "status": "requires_user_input",
            "message": result.get("summary") or "代码审查发现需要处理的问题。",
            "clarification": _repair_confirmation_payload(
                issues, truncated=bool(result.get("truncated"))
            ),
            "code_review_result": result,
            "code_review_report_path": report_path,
            "code_review_repair_status": "awaiting_user",
            "code_review_repair_result": {
                "status": "awaiting_user",
                "iteration": 0,
                "max_iterations": MAX_CODE_REVIEW_REPAIR_ITERATIONS,
                "requested_issue_count": len(issues),
                "attempted_issue_ids": [],
                "summary": "等待用户确认一键修复。",
                "changed_files": [],
                "build_checks": [],
            },
            "code_review_build_results": [],
            "code_review_repair_iteration": 0,
            "code_review_max_repair_iterations": MAX_CODE_REVIEW_REPAIR_ITERATIONS,
            "code_review_next_action": "await_user_input",
            "code_review_events": ["code_scan"],
            "timeline": ["code_review", "code_scan"],
        }
    return {
        "phase": "code_review",
        "status": "completed",
        "message": result.get("summary") or "前后端代码审查完成，未发现需要处理的问题。",
        "clarification": {},
        "code_review_result": result,
        "code_review_report_path": report_path,
        "code_review_repair_status": "not_required",
        "code_review_repair_result": {
            "status": "not_required",
            "iteration": 0,
            "max_iterations": MAX_CODE_REVIEW_REPAIR_ITERATIONS,
            "requested_issue_count": 0,
            "attempted_issue_ids": [],
            "summary": "未发现需要修复的问题。",
            "changed_files": [],
            "build_checks": [],
        },
        "code_review_build_results": [],
        "code_review_repair_iteration": 0,
        "code_review_max_repair_iterations": MAX_CODE_REVIEW_REPAIR_ITERATIONS,
        "code_review_next_action": "acceptance_phase_confirmation",
        "code_review_events": ["code_scan"],
        "timeline": ["code_review", "code_scan"],
    }


def code_review_repair(state: ProjectState) -> dict[str, Any]:
    """调用修复 Agent 处理扫描问题，并校验真实源码 Diff。"""

    issues = _issues(state)
    issue_ids = _issue_ids(issues)
    if not issues or len(issue_ids) != len(issues):
        return _repair_failure(state, "审查问题缺少稳定 ID，无法安全执行一键修复。")
    attempt = max(0, int(state.get("code_review_repair_iteration", 0) or 0)) + 1
    max_attempts = _max_repair_attempts(state)
    if attempt > max_attempts:
        return _repair_failure(
            state,
            f"代码审查修复已达到最大 {max_attempts} 轮，项目不会启动。",
            max_attempts,
        )
    writer = _writer()
    writer(
        {
            "type": CODE_REVIEW_REPAIR_EVENT_TYPE,
            "status": "running",
            "attempt": attempt,
            "message": f"正在修复第 {attempt}/{max_attempts} 轮代码审查问题。",
        }
    )
    workspace = workspace_from_state(state)
    try:
        captured = capture_agent_file_changes(
            workspace=workspace,
            source_tool="code_review_repair.deep_agent",
            action=lambda: invoke_code_review_repair_agent(
                issues=issues,
                build_failures=_failed_build_checks(state.get("code_review_build_results")),
                attempt=attempt,
                max_attempts=max_attempts,
                workspace=workspace,
            ),
            ignored_dirs=_CODE_REVIEW_CAPTURE_IGNORED_DIRS,
            included_roots=_CODE_REVIEW_CAPTURE_ROOTS,
        )
        repair_result = normalize_code_review_repair_result(captured.value)
    except Exception as exc:  # noqa: BLE001 - 修复 Agent 错误统一阻断项目启动
        return _repair_failure(state, _safe_review_text(f"{type(exc).__name__}: {exc}", workspace), attempt)

    attempted_ids = set(repair_result.get("attempted_issue_ids", []))
    if repair_result.get("status") != "completed":
        return _repair_failure(
            state,
            repair_result.get("failure_reason") or "CodeReviewRepairAgent 未完成问题修复。",
            attempt,
        )
    if attempted_ids != issue_ids:
        return _repair_failure(state, "CodeReviewRepairAgent 未覆盖当前展示的全部问题。", attempt)
    required_actions = _required_repair_actions(issues)
    pnpm_evidence = repair_result.get("pnpm_install")
    pnpm_call_count = int(repair_result.get("pnpm_install_call_count") or 0)
    pnpm_succeeded = (
        pnpm_call_count == 1
        and repair_result.get("pnpm_install_called") is True
        and repair_result.get("pnpm_install_completed") is True
        and repair_result.get("pnpm_install_failed") is not True
        and isinstance(pnpm_evidence, dict)
        and pnpm_evidence.get("status") == "passed"
        and pnpm_evidence.get("exit_code") == 0
    )
    if "pnpm_install" in required_actions and not pnpm_succeeded:
        return _repair_failure(
            state,
            "前端 Skill 要求恰好执行一次 pnpm install，但未取得唯一的成功执行证据。",
            attempt,
        )
    if "pnpm_install" not in required_actions and pnpm_call_count:
        return _repair_failure(state, "CodeReviewRepairAgent 在未授权问题中执行了 pnpm install。", attempt)
    if "pnpm_install" in required_actions:
        lockfile = Path(workspace or "") / "frontend/pnpm-lock.yaml"
        if not workspace or not lockfile.is_file():
            return _repair_failure(state, "pnpm install 未生成 frontend/pnpm-lock.yaml。", attempt)
    captured_paths = _captured_change_paths(captured.code_change_set)
    if "pnpm_install" in required_actions and not {
        "frontend/package.json",
        "frontend/pnpm-lock.yaml",
    } <= captured_paths:
        return _repair_failure(
            state,
            "前端依赖修复未同时产生 package.json 与 pnpm-lock.yaml 的真实 Diff。",
            attempt,
        )
    changed_paths = {path for path in captured_paths if _repair_path(path)}
    reported_raw_paths = {
        str(item or "").strip().replace("\\", "/").lstrip("/")
        for item in repair_result.get("changed_files", [])
        if str(item or "").strip()
    }
    reported_paths = {path for path in reported_raw_paths if _repair_path(path)}
    if any(not _repair_path(path) for path in captured_paths):
        return _repair_failure(state, "CodeReviewRepairAgent 产生了越界项目变更。", attempt)
    if any(not _repair_path(path) for path in reported_raw_paths):
        return _repair_failure(state, "CodeReviewRepairAgent 返回了越界项目路径。", attempt)
    if "frontend/pnpm-lock.yaml" in captured_paths and not pnpm_succeeded:
        return _repair_failure(state, "pnpm-lock.yaml 只能由成功的专用 pnpm 工具生成。", attempt)
    if not changed_paths:
        return _repair_failure(state, "CodeReviewRepairAgent 未产生授权项目 Diff。", attempt)
    if reported_paths and not reported_paths <= changed_paths:
        return _repair_failure(state, "CodeReviewRepairAgent 返回了未实际变更的源码路径。", attempt)

    repair_state = {
        # RepairAgent 已完成文件修改后仍需通过确定性构建门禁；在构建结果返回前
        # 公开状态必须保持 building，避免前端提前显示“修复完成”。
        "status": "building",
        "attempt": attempt,
        "iteration": attempt,
        "max_iterations": max_attempts,
        "requested_issue_count": len(issues),
        "attempted_issue_ids": sorted(issue_ids),
        "summary": _safe_review_text(
            repair_result.get("summary") or f"已完成第 {attempt} 轮代码问题修复。",
            workspace,
        ),
        "changed_files": sorted(changed_paths),
        "package_install": pnpm_evidence if pnpm_succeeded else None,
        "failure": None,
    }
    return {
        **code_change_state_update(captured.code_change_set),
        "phase": "code_review",
        "status": "in_progress",
        "message": repair_state["summary"],
        "clarification": {},
        "code_review_repair_status": "building",
        "code_review_repair_result": repair_state,
        "code_review_repair_iteration": attempt,
        "code_review_max_repair_iterations": max_attempts,
        "code_review_next_action": "review_build_checks",
        "code_review_events": ["code_review_repair"],
        "timeline": ["code_review", "code_review_repair"],
    }


def review_build_checks(state: ProjectState) -> dict[str, Any]:
    """独立执行审查修复后的前后端依赖和构建检查。"""

    writer = _writer()
    workspace = workspace_from_state(state)

    def report(event: dict[str, Any]) -> None:
        """把构建检查进度投影为审查子图事件。"""

        check = event.get("check")
        if not isinstance(check, dict):
            return
        writer(
            {
                "type": CODE_REVIEW_BUILD_EVENT_TYPE,
                "status": event.get("status"),
                "attempt": int(state.get("code_review_repair_iteration", 0) or 0),
                "checks": [check],
            }
        )

    try:
        previous_repair = state.get("code_review_repair_result")
        previous_repair = previous_repair if isinstance(previous_repair, dict) else {}
        frontend_install_result = _frontend_install_result(
            previous_repair.get("package_install")
        )
        result = run_integration_checks(
            state,
            on_progress=report,
            phase="build",
            artifact_namespace="code-review",
            frontend_install_result=frontend_install_result,
        )
    except Exception as exc:  # noqa: BLE001 - 构建执行异常进入有限修复环
        results = [
            {
                "id": "code_review_build_runner",
                "name": "审查构建执行",
                "layer": "fullstack",
                "passed": False,
                "required": True,
                "evidence": str(exc)[:2_000],
            }
        ]
    else:
        results = [dict(item) for item in result.get("test_results", []) if isinstance(item, dict)]

    passed = not any(
        item.get("passed") is False and bool(item.get("required", item.get("blocking", True)))
        for item in results
    )
    attempt = int(state.get("code_review_repair_iteration", 0) or 0)
    max_attempts = _max_repair_attempts(state)
    if passed:
        previous_repair = state.get("code_review_repair_result")
        previous_repair = previous_repair if isinstance(previous_repair, dict) else {}
        return {
            "phase": "code_review",
            "status": "completed",
            "message": "代码修复完成，前后端构建检查通过。",
            "clarification": {},
            "code_review_repair_status": "completed",
            "code_review_repair_result": {
                **previous_repair,
                "status": "completed",
                "iteration": attempt,
                "max_iterations": max_attempts,
                "requested_issue_count": len(_issues(state)),
                "summary": "修复已执行，前后端构建检查通过。",
                "build_checks": _public_build_checks(results, workspace=workspace),
                "failure": None,
            },
            "code_review_build_results": results,
            "code_review_next_action": "acceptance_phase_confirmation",
            "code_review_events": ["review_build_checks"],
            "timeline": ["code_review", "code_review_repair", "review_build_checks"],
        }
    if attempt < max_attempts:
        previous_repair = state.get("code_review_repair_result")
        previous_repair = previous_repair if isinstance(previous_repair, dict) else {}
        return {
            "phase": "code_review",
            "status": "in_progress",
            "message": f"前后端构建未通过，将进入第 {attempt + 1}/{max_attempts} 轮修复。",
            "clarification": {},
            "code_review_repair_status": "repairing",
            "code_review_repair_result": {
                **previous_repair,
                "status": "repairing",
                "iteration": attempt,
                "max_iterations": max_attempts,
                "requested_issue_count": len(_issues(state)),
                "build_checks": _public_build_checks(results, workspace=workspace),
                "summary": f"构建未通过，准备第 {attempt + 1}/{max_attempts} 轮修复。",
            },
            "code_review_build_results": results,
            "code_review_next_action": "code_review_repair",
            "code_review_events": ["review_build_checks"],
            "timeline": ["code_review", "review_build_checks"],
        }
    return {
        "phase": "code_review",
        "status": "failed",
        "message": "代码修复后前后端构建仍未通过。",
        "error": _build_failure_summary(results, workspace=workspace),
        "clarification": {},
        "code_review_repair_status": "failed",
        "code_review_repair_result": {
            "status": "failed",
            "iteration": attempt,
            "max_iterations": max_attempts,
            "requested_issue_count": len(_issues(state)),
            "attempted_issue_ids": sorted(
                str(item.get("id") or "") for item in _issues(state) if str(item.get("id") or "")
            ),
            "summary": "代码修复后前后端构建仍未通过。",
            "build_checks": _public_build_checks(results, workspace=workspace),
            "failure": _build_failure_summary(results, workspace=workspace),
        },
        "code_review_build_results": results,
        "code_review_next_action": "handle_failure",
        "code_review_events": ["review_build_checks"],
        "timeline": ["code_review", "review_build_checks"],
    }


def _route_after_repair(state: ProjectState) -> str:
    """修复完成后统一进入独立审查构建节点。"""

    return "review_build_checks" if state.get("status") != "failed" else END


def _route_after_build(state: ProjectState) -> str:
    """构建失败且预算未耗尽时回到修复节点，否则结束子图。"""

    if state.get("status") == "in_progress" and state.get("code_review_next_action") == "code_review_repair":
        return "code_review_repair"
    return END


def _repair_failure(state: ProjectState, message: str, attempt: int | None = None) -> dict[str, Any]:
    """构造修复失败结果，确保外层主图进入统一失败处理。"""

    workspace = workspace_from_state(state)
    safe_message = _safe_review_text(message, workspace)[:2_000]
    return {
        "phase": "code_review",
        "status": "failed",
        "message": "前后端代码修复失败。",
        "error": safe_message,
        "clarification": {},
        "code_review_repair_status": "failed",
        "code_review_repair_result": {
            "status": "failed",
            "attempt": attempt or int(state.get("code_review_repair_iteration", 0) or 0),
            "iteration": attempt or int(state.get("code_review_repair_iteration", 0) or 0),
            "max_iterations": _max_repair_attempts(state),
            "requested_issue_count": len(_issues(state)),
            "attempted_issue_ids": [],
            "summary": safe_message,
            "changed_files": [],
            "build_checks": [],
            "failure": safe_message,
        },
        "code_review_next_action": "handle_failure",
        "code_review_events": ["code_review_repair"],
        "timeline": ["code_review", "code_review_repair"],
    }


def _captured_change_paths(change_set: Any) -> set[str]:
    """从真实变更集合提取所有相对路径，供调用方执行边界校验。"""

    if not isinstance(change_set, dict) or not isinstance(change_set.get("files"), list):
        return set()
    return {
        str(item.get("path") or "").strip().replace("\\", "/").lstrip("/")
        for item in change_set["files"]
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    }


def _build_failure_summary(
    results: list[dict[str, Any]], *, workspace: str | None = None
) -> str:
    """把构建失败转换为不含绝对路径的可读摘要。"""

    failures = [
        f"{str(item.get('name') or item.get('id') or '构建检查')}: "
        f"{_safe_review_text(item.get('evidence') or '未提供失败证据', workspace)[:500]}"
        for item in results
        if item.get("passed") is False
    ]
    return "；".join(failures)[:2_000] or "前后端构建检查未通过。"


def _public_build_checks(
    results: list[dict[str, Any]], *, workspace: str | None = None
) -> list[dict[str, Any]]:
    """裁剪构建结果为 CodeReviewCard 可展示的检查状态。"""

    by_id = {
        str(item.get("id") or "").strip(): item
        for item in results
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    checks: list[dict[str, Any]] = []
    for check_id, check_name, layer in _REVIEW_BUILD_CHECKS:
        item = by_id.get(check_id)
        if item is None:
            # 前置检查失败或项目类型不适用时，仍保留固定行，避免 UI 缺少第三个步骤。
            checks.append(
                {
                    "id": check_id,
                    "name": check_name,
                    "layer": layer,
                    "status": "skipped",
                    "evidence": "本轮未执行该检查。",
                }
            )
            continue
        # 缺少工程配置时检查器会返回 skipped=true、passed=true；这里必须保留
        # skipped 语义，避免前端把“未执行”误显示成“已通过”。
        checks.append(
            {
                "id": check_id,
                "name": str(item.get("name") or check_name)[:200],
                "layer": str(item.get("layer") or layer)[:40],
                "status": (
                    "skipped"
                    if item.get("skipped") is True
                    else "passed"
                    if item.get("passed") is True
                    else "failed"
                ),
                "evidence": _safe_review_text(item.get("evidence") or "", workspace)[:800]
                or None,
            }
        )
    return checks


def build_code_review_subgraph():
    """构建代码审查子图，首次扫描后等待一键修复并循环构建验证。"""

    builder = StateGraph(ProjectState)
    builder.add_node("code_scan", code_scan)
    builder.add_node("code_review_repair", code_review_repair)
    builder.add_node("review_build_checks", review_build_checks)
    builder.add_conditional_edges(
        START,
        _route_review_start,
        {"code_scan": "code_scan", "code_review_repair": "code_review_repair"},
    )
    builder.add_edge("code_scan", END)
    builder.add_conditional_edges(
        "code_review_repair",
        _route_after_repair,
        {"review_build_checks": "review_build_checks", END: END},
    )
    builder.add_conditional_edges(
        "review_build_checks",
        _route_after_build,
        {"code_review_repair": "code_review_repair", END: END},
    )
    return builder.compile()


_code_review_subgraph = build_code_review_subgraph()


def run_code_review_subgraph(
    state: ProjectState, config: Any | None = None
) -> dict[str, Any]:
    """执行代码审查子图并返回外层主图所需的有限状态更新。"""

    return _code_review_subgraph.invoke(dict(state), config=config or {})
