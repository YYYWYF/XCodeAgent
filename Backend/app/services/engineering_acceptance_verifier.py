from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from app.services.build_task_menu import menu_registration_matches
from app.services.engineering_acceptance import ensure_engineering_acceptance
from app.services.engineering_contract_verifier import verify_contract_binding


def unauthorized_batch_paths(
    code_change_set: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
) -> list[str]:
    """找出 owner 批次中不属于任一任务授权范围的实际变更。"""

    allowed = [path for task in tasks for path in _allowed_paths(task)]
    return [
        str(item.get("path") or "")
        for item in _change_items(code_change_set)
        if item.get("path") and not _path_matches_any(str(item["path"]), allowed)
    ]


def verify_engineering_acceptance(
    *,
    task: dict[str, Any],
    status: str,
    code_change_set: dict[str, Any] | None,
    workspace_root: str | None,
    batch_unauthorized_paths: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """确定性执行任务的全部工程检查并返回证据与错误。"""

    # 验收入口也要重新归一化任务，防止历史 DAG 绕过 scheduler 后把契约检查误挂到配置任务。
    task = ensure_engineering_acceptance(task)
    checks = _dict_items(task.get("acceptance_checks"))
    if not checks:
        return [], ["任务缺少 acceptance_checks，请重新执行 prepare_build_tasks。"]
    root = Path(workspace_root).expanduser().resolve() if workspace_root else None
    changes = {
        str(item.get("path") or "").lstrip("./"): item
        for item in _change_items(code_change_set)
        if item.get("path")
    }
    evidence: list[dict[str, Any]] = []
    errors: list[str] = []
    for check in checks:
        error, detail = _verify_check(
            check,
            task=task,
            status=status,
            changes=changes,
            root=root,
            batch_unauthorized_paths=batch_unauthorized_paths or [],
        )
        evidence.append(
            {
                "check_id": check.get("id"),
                "kind": check.get("kind"),
                "status": "failed" if error else "passed",
                "evidence": detail,
            }
        )
        if error:
            errors.append(error)
    return evidence, errors


def apply_batch_scope_violation(
    results: list[dict[str, Any]],
    paths: list[str],
) -> list[dict[str, Any]]:
    """把全批次越权文件变更应用到所有成功结果，避免并发快照交叉误判。"""

    if not paths:
        return results
    reason = "检测到未授权文件变更：" + "、".join(sorted(set(paths))) + "。"
    return [
        {
            **result,
            "status": "failed",
            "failure_category": "acceptance_verification_failed",
            "failure_reason": reason,
            "scheduler_decision": {
                "action": "repair",
                "reason": "acceptance_verification_failed",
            },
            "agent_note": (
                f"{result.get('agent_note')}\n\nVERIFICATION FAILED: {reason}"
                if result.get("agent_note")
                else f"VERIFICATION FAILED: {reason}"
            ),
        }
        if result.get("status") in {"completed", "already_satisfied"}
        else result
        for result in results
    ]


def _verify_check(
    check: dict[str, Any],
    *,
    task: dict[str, Any],
    status: str,
    changes: dict[str, dict[str, Any]],
    root: Path | None,
    batch_unauthorized_paths: list[str],
) -> tuple[str | None, str]:
    """按检查类型分派单项工程验收验证。"""

    kind = str(check.get("kind") or "")
    if kind == "file_operation":
        return _verify_file_operation(
            check,
            task=task,
            status=status,
            changes=changes,
            root=root,
        )
    if kind == "repair_change":
        return _verify_repair_change(check, status=status, changes=changes, root=root)
    if kind == "scope_boundary":
        return _verify_scope_boundary(task, batch_unauthorized_paths)
    if kind == "menu_registration":
        return _verify_menu_registration(check, root=root)
    if kind in {"frontend_contract_binding", "backend_contract_binding"}:
        return verify_contract_binding(check, kind=kind, root=root)
    return f"不支持的工程验收检查类型：{kind or '<empty>'}", "检查类型无法执行。"


def _verify_file_operation(
    check: dict[str, Any],
    *,
    task: dict[str, Any] | None = None,
    status: str,
    changes: dict[str, dict[str, Any]],
    root: Path | None,
) -> tuple[str | None, str]:
    """核对精确目标路径的工作区差异类型或已满足磁盘状态。"""

    paths = _string_list(check.get("target_paths"))
    path = paths[0].lstrip("./") if paths else ""
    expected = _dict_value(check.get("expected"))
    operation = str(expected.get("operation") or "modify")
    expected_change_type = str(expected.get("change_type") or "modified")
    if not path:
        return "文件操作检查缺少目标路径。", "无法解析目标文件。"
    if root is not None:
        path_error = _resolved_path_error(root, path)
        if path_error:
            return path_error, path_error
    if status == "completed":
        actual = changes.get(path)
        actual_type = str(actual.get("changeType") or "") if actual else ""
        accepted_change_types = {expected_change_type}
        if (
            isinstance(task, dict)
            and task.get("kind") == "repair"
            and expected_change_type == "added"
        ):
            # 修复任务面对的是当前工作区；原任务声明 add，但文件可能已被失败尝试创建，
            # 此时本轮正确行为是原地修改，不能再把 modified 判成验收失败。
            accepted_change_types.add("modified")
        if actual_type not in accepted_change_types:
            accepted_text = " 或 ".join(sorted(accepted_change_types))
            return (
                f"{path} 预期差异类型 {accepted_text}，实际为 {actual_type or 'none'}。",
                f"工作区差异未覆盖预期 {operation} 操作。",
            )
    elif root is None:
        return "已满足检查缺少工作区根目录。", "无法核对当前磁盘状态。"
    elif operation == "delete":
        target = (root / path).resolve()
        if target.exists():
            return f"已满足检查失败：{path} 仍然存在。", "删除目标仍存在。"
    else:
        target = (root / path).resolve()
        if not target.is_file():
            return f"已满足检查失败：{path} 不存在。", "目标文件不存在。"
    return None, f"{path} 的 {operation} 工程状态已由工作区证据确认。"


def _verify_repair_change(
    check: dict[str, Any],
    *,
    status: str,
    changes: dict[str, dict[str, Any]],
    root: Path | None,
) -> tuple[str | None, str]:
    """验证修复至少修改一个授权文件，或以当前磁盘状态证明原目标已满足。"""

    expected = _dict_value(check.get("expected"))
    allowed_paths = _string_list(expected.get("allowed_paths"))
    if status == "completed":
        changed = [path for path in changes if _path_matches_any(path, allowed_paths)]
        if not changed:
            return "修复任务未产生授权范围内的文件变更。", "未找到可归属到修复任务的工作区差异。"
        return None, f"修复已变更授权文件：{', '.join(changed)}。"
    target_states = _dict_items(expected.get("target_states"))
    if not target_states:
        return "修复已满足检查缺少原目标状态。", "无法证明修复目标已在磁盘上成立。"
    state_errors = [
        error
        for target_check in target_states
        for error, _ in [
            _verify_file_operation(
                target_check,
                status="already_satisfied",
                changes=changes,
                root=root,
            )
        ]
        if error
    ]
    if state_errors:
        return "；".join(state_errors), "至少一个原任务目标状态尚未满足。"
    return None, "原任务全部精确文件状态已由当前工作区确认。"


def _verify_scope_boundary(
    task: dict[str, Any], unauthorized_paths: list[str]
) -> tuple[str | None, str]:
    """核对批次没有产生任一任务范围之外的文件变更。"""

    if unauthorized_paths:
        joined = "、".join(sorted(set(unauthorized_paths)))
        return f"检测到未授权文件变更：{joined}。", f"批次越过任务 {task.get('id')} 的授权并集。"
    return None, "工作区差异全部位于本批次任务授权范围内。"


def _verify_menu_registration(
    check: dict[str, Any], *, root: Path | None
) -> tuple[str | None, str]:
    """复用菜单解析器核对 path、name、key 与动态路由隐藏标记。"""

    expected = _dict_value(check.get("expected"))
    if root is None or not menu_registration_matches(root, expected):
        return "菜单或自动路由登记与确定性工程契约不一致。", "未找到完全匹配的菜单项。"
    return None, "菜单 path、name、key 与隐藏标记均匹配。"


def _resolved_path_error(root: Path, path: str) -> str | None:
    """阻止工程验收读取越过工作区的路径。"""

    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        return f"目标路径越过工作区：{path}。"
    return None


def _allowed_paths(task: dict[str, Any]) -> list[str]:
    """汇总任务授权路径供批次越权检测。"""

    paths = _string_list(task.get("allowed_paths")) + _string_list(task.get("target_files"))
    paths.extend(
        str(item.get("path") or "")
        for item in _dict_items(task.get("change_scope"))
    )
    return list(dict.fromkeys(path.lstrip("./") for path in paths if path))


def _path_matches_any(path: str, patterns: list[str]) -> bool:
    """判断实际文件是否命中任一精确或通配授权路径。"""

    normalized = path.lstrip("./")
    for pattern in patterns:
        candidate = pattern.lstrip("./")
        if candidate.endswith("/**") and normalized.startswith(candidate[:-3].rstrip("/") + "/"):
            return True
        if fnmatch(normalized, candidate):
            return True
    return False


def _change_items(value: dict[str, Any] | None) -> list[dict[str, Any]]:
    """读取工作区差异中的文件条目。"""

    files = value.get("files") if isinstance(value, dict) else None
    return [dict(item) for item in files if isinstance(item, dict)] if isinstance(files, list) else []


def _string_list(value: Any) -> list[str]:
    """把不可信列表规整为非空字符串列表。"""

    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip())) if isinstance(value, list) else []


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """把不可信列表规整为字典列表。"""

    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict_value(value: Any) -> dict[str, Any]:
    """把不可信对象规整为字典。"""

    return dict(value) if isinstance(value, dict) else {}
