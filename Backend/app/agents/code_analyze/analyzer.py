"""代码审查 Agent 调用、结果校验和安全归一化。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any

from app.agents.tool_activity_stream import ToolActivityCallback, invoke_agent_with_tool_activity
from app.agents.code_analyze.scope import is_code_analyze_read_path
from app.services.builtin_skills import FRONTEND_CODE_SCAN_SKILL_NAME, resolve_builtin_skills_root
from app.utils.model_output import extract_json_object


logger = logging.getLogger(__name__)

MAX_REVIEW_ISSUES = 100
REQUIRED_SKILLS = {
    "/.xcodeagent/builtin-skills/frontend-code-scan/SKILL.md": "frontend-code-scan",
    "/.xcodeagent/builtin-skills/backend-code-scan/SKILL.md": "backend-code-scan",
}
REQUIRED_SKILL_PATHS = {
    *REQUIRED_SKILLS,
    "/.xcodeagent/builtin-skills/backend-code-scan/references/rules-reference.md",
}


def analyze_workspace_code(
    state: dict[str, Any],
    workspace: str | None,
    *,
    on_tool_activity: ToolActivityCallback | None = None,
) -> dict[str, Any]:
    """调用 CodeAnalyze Agent 并返回可投影的安全审查结果。"""

    if not workspace:
        raise ValueError("代码审查需要显式用户 workspaceRoot。")
    from app.agents import create_agent_bundle

    prompt = _build_prompt(state)
    last_error: Exception | None = None
    raw: Any = None
    observed_skill_reads: set[str] = set()
    for attempt in range(2):
        current_skill_reads: set[str] = set()

        def observe(activity: dict[str, Any]) -> None:
            """记录本次尝试的必需 Skill 读取，并转发非敏感工具活动。"""

            if (
                activity.get("tool") == "read_file"
                and activity.get("status") == "completed"
                and str(activity.get("path") or "") in REQUIRED_SKILL_PATHS
            ):
                current_skill_reads.add(str(activity["path"]))
            if on_tool_activity:
                on_tool_activity(activity)

        try:
            raw = invoke_agent_with_tool_activity(
                create_agent_bundle(workspace).code_analyze,
                {"messages": [{"role": "user", "content": prompt}]},
                workspace=workspace,
                on_tool_activity=observe,
            )
            observed_skill_reads = current_skill_reads
            break
        except Exception as exc:  # noqa: BLE001 - 仅 Agent 执行异常允许一次受控重试
            last_error = exc
            if attempt == 1:
                raise ValueError(f"CodeAnalyze Agent 审查失败：{last_error}") from last_error

    # 已完成扫描后的协议校验不得重新调用 Agent，避免因可归一化的结果格式造成整轮重复扫描。
    try:
        if not REQUIRED_SKILL_PATHS <= observed_skill_reads:
            raise ValueError("CodeAnalyze Agent 未读取完整的前后端扫描 Skill 和规则引用。")
        payload = extract_json_object(raw if isinstance(raw, str) else "")
        if not isinstance(payload, dict):
            raise ValueError("CodeAnalyze Agent 返回的审查结果不是合法 JSON。")
        return normalize_code_review_result(payload, workspace=workspace)
    except Exception as exc:  # noqa: BLE001 - 节点统一投影安全错误摘要
        raise ValueError(f"CodeAnalyze Agent 审查失败：{exc}") from exc


def normalize_code_review_result(
    payload: dict[str, Any], *, workspace: str | None = None
) -> dict[str, Any]:
    """校验 Agent 输出、裁剪敏感字段并限制问题数量。"""

    reported_status = str(payload.get("status") or "").strip().lower()
    frontend_scan_warning = _frontend_scan_warning()
    issues: list[dict[str, Any]] = []
    normalized_issue_count = 0
    seen: set[tuple[str, str, str, int, str]] = set()
    seen_ids: set[str] = set()
    raw_issues = payload.get("issues", payload.get("findings"))
    if not isinstance(raw_issues, list):
        raw_issues = []
    for raw in raw_issues:
        if not isinstance(raw, dict):
            raise ValueError("审查问题必须是结构化对象。")
        side = str(raw.get("side") or "").strip().lower()
        # 前端 Skill 只有占位内容时不存在可执行规则，模型生成的前端问题属于无依据结果，必须丢弃。
        if side == "frontend" and frontend_scan_warning:
            continue
        file_path = _normalize_review_path(
            raw.get("file", raw.get("filePath", raw.get("path"))),
            workspace=workspace,
        )
        if side not in {"frontend", "backend"} or not is_code_analyze_read_path(file_path):
            raise ValueError("审查结果包含越界源码路径。")
        expected_prefix = "frontend/src/" if side == "frontend" else "backend/src/main/java/"
        if not file_path.startswith(expected_prefix):
            raise ValueError("审查问题的端类型与文件路径不匹配。")
        severity = str(raw.get("severity") or "medium").strip().lower()
        if severity not in {"critical", "high", "medium", "low"}:
            severity = "medium"
        line = raw.get("line")
        line_number = (
            int(line)
            if isinstance(line, int) and not isinstance(line, bool) and line > 0
            else None
        )
        normalized = {
            "id": str(raw.get("id", raw.get("issue_id")) or "").strip()[:120],
            "side": side,
            "rule_id": str(raw.get("rule_id", raw.get("ruleId")) or "").strip()[:80] or None,
            "severity": severity,
            "title": _safe_review_text(raw.get("title") or "未命名问题", workspace)[:240],
            "summary": _safe_review_text(
                raw.get("summary") or "未提供问题说明。", workspace
            )[:800],
            "file": file_path,
            "line": line_number,
        }
        identity = (
            side,
            normalized["rule_id"] or "",
            file_path,
            line_number or 0,
            normalized["title"],
        )
        if identity in seen:
            continue
        seen.add(identity)
        normalized["id"] = normalized["id"] or _issue_id(identity)
        if normalized["id"] in seen_ids:
            normalized["id"] = _issue_id(identity)
        seen_ids.add(normalized["id"])
        normalized_issue_count += 1
        if len(issues) < MAX_REVIEW_ISSUES:
            issues.append(normalized)

    # 模型可能把“发现规范问题”表达为 failed/issues_found；只要至少一个问题已通过
    # 目录、端类型和字段校验，扫描本身就属于成功完成，不能阻断后续问题列表投影。
    if reported_status != "completed":
        if normalized_issue_count == 0:
            raise ValueError("审查结果 status 必须为 completed。")
        logger.warning(
            "CodeAnalyze Agent 返回非 completed 状态 %r，但包含 %d 个有效问题；已归一为 completed。",
            reported_status,
            normalized_issue_count,
        )

    targets = _normalize_targets(
        payload.get("targets", payload.get("scanTargets")),
        workspace=workspace,
        frontend_scan_warning=frontend_scan_warning,
    )
    loaded = _normalize_loaded_skills(
        payload.get("loaded_skills", payload.get("loadedSkills"))
    )
    if set(loaded) != {"frontend-code-scan", "backend-code-scan"}:
        raise ValueError("审查结果缺少前后端扫描 Skill。")
    summary = _safe_review_text(
        payload.get("summary") or "前后端代码审查完成。", workspace
    )[:2_000]
    if frontend_scan_warning:
        summary = (
            f"代码审查完成，共发现 {normalized_issue_count} 个后端问题；"
            f"{frontend_scan_warning}"
            if normalized_issue_count
            else f"代码审查完成，未发现需要处理的问题；{frontend_scan_warning}"
        )
    return {
        "status": "completed",
        "summary": summary,
        "issue_count": normalized_issue_count,
        "truncated": bool(payload.get("truncated", payload.get("isTruncated")))
        or normalized_issue_count > MAX_REVIEW_ISSUES,
        "loaded_skills": loaded,
        "targets": targets,
        "issues": issues,
    }


def _build_prompt(state: dict[str, Any]) -> str:
    """构造只包含目标和当前工作区边界的审查提示。"""

    target = state.get("build_execution_scope")
    target = target if isinstance(target, dict) else {}
    return (
        "开始审查前后端代码。严格先读取两个扫描 Skill 和后端规则引用。\n"
        "允许的最大范围为 frontend/src/** 与 backend/src/main/java/**；其他用户目录禁止读取。\n"
        "执行后端规则扫描；仅当前端 Skill 存在具体规则时才读取并扫描 frontend/src。\n"
        f"当前构建目标：{json.dumps(target, ensure_ascii=False)}\n"
        "前端 Skill 当前可能没有具体规则；没有具体规则时不得生成任何前端问题，"
        "不得读取 frontend/src 源码，只报告扫描目标、0 个扫描文件和规则未配置 warning。\n"
        "只要扫描执行完成，status 必须始终为 completed；发现规范问题只写入 issues，"
        "不得把 status 写成 failed、issues_found 或 non_compliant。\n"
        "targets 必须为数组，每项使用 side、root、status、scanned_file_count 和可选 warning。\n"
        "只返回约定 JSON，不修改任何文件。"
    )


def _normalize_targets(
    value: Any,
    *,
    workspace: str | None = None,
    frontend_scan_warning: str | None = None,
) -> list[dict[str, Any]]:
    """归一化前后端扫描目标并拒绝第三方目录。"""

    raw_targets: dict[str, dict[str, Any]] = {}
    target_items = _target_items(value)
    if target_items:
        for raw in target_items:
            if not isinstance(raw, dict):
                continue
            side = str(raw.get("side") or "").strip().lower()
            expected = {
                "frontend": "frontend/src",
                "backend": "backend/src/main/java",
            }.get(side)
            root = _normalize_target_root(raw, expected=expected, workspace=workspace)
            if not expected or root != expected:
                raise ValueError("审查目标包含未授权目录。")
            status = str(raw.get("status") or "completed").strip().lower()
            if status not in {"completed", "skipped"}:
                status = "completed"
            count = raw.get(
                "scanned_file_count",
                raw.get(
                    "scannedFileCount",
                    raw.get("files_scanned", raw.get("file_count", 0)),
                ),
            )
            if side in raw_targets:
                raise ValueError("审查结果重复声明扫描目标。")
            raw_targets[side] = {
                "side": side,
                "root": root,
                "status": status,
                "scanned_file_count": (
                    max(0, int(count))
                    if isinstance(count, int) and not isinstance(count, bool)
                    else 0
                ),
                "warning": _safe_review_text(raw.get("warning") or "", workspace)[:500]
                or None,
            }

    workspace_path = Path(workspace).resolve() if workspace else None
    targets: list[dict[str, Any]] = []
    for side, root in (("frontend", "frontend/src"), ("backend", "backend/src/main/java")):
        target = raw_targets.get(
            side,
            {
                "side": side,
                "root": root,
                "status": "completed",
                "scanned_file_count": 0,
                "warning": None,
            },
        )
        root_exists = bool(workspace_path and (workspace_path / root).is_dir())
        if not root_exists:
            target = {
                **target,
                "status": "skipped",
                "scanned_file_count": 0,
                "warning": "扫描目录不存在，已跳过。",
            }
        elif side == "frontend" and frontend_scan_warning:
            # 占位 Skill 的确定性 warning 优先于模型文案，避免展示不存在的前端规则或问题。
            target = {**target, "warning": frontend_scan_warning}
        targets.append(target)
    return targets


def _normalize_target_root(
    value: dict[str, Any],
    *,
    expected: str | None,
    workspace: str | None,
) -> str:
    """归一化模型常见的目标根目录字段；未声明时使用端类型对应的固定安全根。"""

    declared_root = value.get("root")
    if declared_root is None:
        declared_root = value.get("scan_root", value.get("scanRoot"))
    if declared_root is None:
        return expected or ""
    normalized = _normalize_review_path(declared_root, workspace=workspace)
    # 模型有时会把固定根目录写成根下 glob/子路径；将其收敛到端类型的
    # canonical root 仍不扩大读取范围，同时避免把安全目录误报为越权目标。
    if expected and (normalized == expected or normalized.startswith(f"{expected}/")):
        return expected
    return normalized


def _target_items(value: Any) -> list[dict[str, Any]]:
    """兼容模型常见的目标数组和按端分组对象，并裁掉构建目标等非扫描字段。"""

    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []

    items: list[dict[str, Any]] = []
    for side, root in (("frontend", "frontend/src"), ("backend", "backend/src/main/java")):
        side_value = value.get(side)
        if isinstance(side_value, dict):
            items.append({"root": root, **side_value, "side": side})
            continue
        count_key = f"{side}_files_scanned"
        if count_key in value:
            items.append(
                {
                    "side": side,
                    "root": root,
                    "scanned_file_count": value.get(count_key),
                }
            )
    return items


def _normalize_loaded_skills(value: Any) -> list[str]:
    """把 Skill 名称、Skill 文件对象和规则引用路径归一为两个授权 Skill 名称。"""

    if not isinstance(value, list):
        return []
    loaded: set[str] = set()
    for item in value:
        declared_name = ""
        declared_path = ""
        if isinstance(item, str):
            declared_path = item.strip()
        elif isinstance(item, dict):
            declared_name = str(item.get("name") or "").strip()
            declared_path = str(item.get("path") or "").strip()
            _validate_declared_rule_references(item.get("references"))
        else:
            raise ValueError("审查结果包含未授权的扫描 Skill。")

        name_from_path = _skill_name_from_declaration(declared_path) if declared_path else None
        if declared_name in {"backend-code-scan/rules-reference", "rules-reference"}:
            if not declared_path or name_from_path is not None:
                raise ValueError("审查结果包含未授权的扫描 Skill。")
            continue
        if (
            not declared_name
            and declared_path
            and _is_authorized_rules_reference_alias(declared_path)
        ):
            # 模型有时把规则引用直接放在 loaded_skills 数组中；它不是第三个 Skill，
            # 但只允许精确的后端规则文件别名，不能放宽其它路径。
            continue
        if declared_name and declared_name not in {"frontend-code-scan", "backend-code-scan"}:
            raise ValueError("审查结果包含未授权的扫描 Skill。")
        if declared_path and name_from_path is False:
            raise ValueError("审查结果包含未授权的扫描 Skill。")
        if declared_name and isinstance(name_from_path, str) and declared_name != name_from_path:
            raise ValueError("审查结果中的 Skill 名称与路径不匹配。")

        effective_name = declared_name or (name_from_path if isinstance(name_from_path, str) else "")
        if effective_name:
            loaded.add(effective_name)
        elif declared_path and name_from_path is None:
            # 后端规则引用是授权必需文件，但不是第三个 Skill。
            continue
        else:
            raise ValueError("审查结果包含未授权的扫描 Skill。")
    return sorted(loaded)


def _validate_declared_rule_references(value: Any) -> None:
    """校验 Skill 对象中的 references 只能声明唯一授权的后端规则文件。"""

    if value is None:
        return
    references = value if isinstance(value, list) else [value]
    if not references:
        return
    for reference in references:
        if not isinstance(reference, str) or not _is_authorized_rules_reference_alias(reference):
            raise ValueError("审查结果包含未授权的扫描 Skill 规则引用。")


def _skill_name_from_declaration(value: str) -> str | bool | None:
    """解析模型声明的内置 Skill 相对路径；False 表示越权，None 表示授权规则引用。"""

    path = value.strip().replace("\\", "/").lstrip("/")
    builtin_prefix = ".xcodeagent/builtin-skills/"
    if path.startswith(builtin_prefix):
        path = path[len(builtin_prefix) :]
    declarations: dict[str, str | None] = {
        "frontend-code-scan": "frontend-code-scan",
        "backend-code-scan": "backend-code-scan",
        "frontend-code-scan/SKILL.md": "frontend-code-scan",
        "backend-code-scan/SKILL.md": "backend-code-scan",
        "backend-code-scan/references/rules-reference.md": None,
    }
    return declarations[path] if path in declarations else False


def _is_authorized_rules_reference_alias(value: str) -> bool:
    """判断模型输出是否只是唯一授权的后端规则引用别名。"""

    path = value.strip().replace("\\", "/").lstrip("/")
    return path in {
        "rules-reference.md",
        "rules-reference",
        "references/rules-reference.md",
        "backend-code-scan/rules-reference",
        "backend-code-scan/references/rules-reference.md",
        ".xcodeagent/builtin-skills/backend-code-scan/references/rules-reference.md",
    }


def _normalize_review_path(value: Any, *, workspace: str | None) -> str:
    """把安全的工作区路径表示归一为相对路径，并拒绝真实越界与目录穿越。"""

    raw_path = str(value or "").strip().replace("\\", "/")
    if len(raw_path) >= 2 and raw_path[0] == raw_path[-1] == "`":
        raw_path = raw_path[1:-1].strip()
    if not raw_path or (len(raw_path) > 1 and raw_path[1] == ":"):
        return ""

    # DeepAgent 文件工具使用 `/frontend/...` 形式的虚拟路径；结果中安全地转为相对路径。
    virtual_roots = ("frontend/src", "backend/src/main/java")
    if any(
        raw_path == f"/{root}" or raw_path.startswith(f"/{root}/")
        for root in virtual_roots
    ):
        raw_path = raw_path[1:]
    elif raw_path.startswith("/"):
        if not workspace:
            return ""
        try:
            workspace_path = Path(workspace).resolve()
            raw_path = Path(raw_path).resolve().relative_to(workspace_path).as_posix()
        except (OSError, RuntimeError, ValueError):
            return ""

    path = PurePosixPath(raw_path)
    normalized = str(path)
    if normalized in {"", "."} or ".." in path.parts:
        return ""
    return normalized


def _frontend_scan_warning() -> str | None:
    """根据前端内置 Skill 当前内容确定性标记“规则尚未配置”提示。"""

    try:
        skill_path = resolve_builtin_skills_root() / FRONTEND_CODE_SCAN_SKILL_NAME / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "前端扫描规则文件不可读。"
    body = content.split("---", 2)[-1].strip()
    if not body or "后续补充具体扫描规则" in body:
        return "当前未配置扫描规则。"
    return None


def _safe_review_text(value: Any, workspace: str | None) -> str:
    """裁剪审查文案中的宿主机路径，避免摘要和说明泄露绝对路径。"""

    text = str(value or "").strip()
    if workspace:
        try:
            text = text.replace(str(Path(workspace).resolve()), "[workspace]")
        except (OSError, RuntimeError):
            pass
    return re.sub(
        r"(?<![\w])(?:[A-Za-z]:[\\/]|/(?!/))[^\s,;()]+",
        "[path]",
        text,
    )


def _issue_id(identity: tuple[str, str, str, int, str]) -> str:
    """生成不依赖宿主路径的稳定问题标识。"""

    import hashlib

    return hashlib.sha1("|".join(map(str, identity)).encode("utf-8")).hexdigest()[:16]
