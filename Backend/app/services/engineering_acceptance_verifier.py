from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
import re
from typing import Any

from app.services.engineering_acceptance import ensure_engineering_acceptance
from app.services.business_acceptance_verifiers.common import strip_comments
from app.services.business_acceptance_verifiers.java_inspection_support import (
    _find_controller_method,
    _inspect_or_block,
    _type_has_suffix,
)


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

    # 验收入口重新归一化任务，确保工程检查只由当前任务元数据产生。
    task = ensure_engineering_acceptance(task)
    checks = _dict_items(task.get("acceptance_checks"))
    if not checks:
        return [], ["任务缺少 acceptance_checks，请重新执行 prepare_build_tasks。"]
    root = Path(workspace_root).expanduser().resolve() if workspace_root else None
    changes = {
        _normalize_path(item.get("path")): item
        for item in _change_items(code_change_set)
        if _normalize_path(item.get("path"))
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
                "error": error,
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
    if kind == "page_entry":
        return _verify_page_entry(check, root=root)
    if kind == "page_default_export":
        return _verify_page_default_export(check, root=root)
    if kind == "page_placeholder":
        return _verify_page_placeholder(check, root=root)
    if kind == "page_component_reachability":
        return _verify_page_component_reachability(check, root=root)
    if kind == "frontend_api_boundary":
        return _verify_frontend_api_boundary(check, root=root)
    if kind == "frontend_authorization":
        return _verify_frontend_authorization(check, root=root)
    if kind == "backend_authorization":
        return _verify_backend_authorization(check, root=root)
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
    path = _normalize_path(paths[0]) if paths else ""
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
            and expected_change_type == "added"
            and _task_has_retry_attempt(task)
        ):
            # 原始 add 任务重试时面对的是已经被失败尝试部分创建的工作区；
            # 本轮按 attempt 基线允许 modified，但不改写规划 operation。
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
        target = _workspace_target(root, path)
        if target.exists():
            return f"已满足检查失败：{path} 仍然存在。", "删除目标仍存在。"
    else:
        target = _workspace_target(root, path)
        if not target.is_file():
            return f"已满足检查失败：{path} 不存在。", "目标文件不存在。"
    return None, f"{path} 的 {operation} 工程状态已由工作区证据确认。"


def _task_has_retry_attempt(task: dict[str, Any]) -> bool:
    """判断任务是否存在重试计数或明确的前次失败证据。"""

    if str(task.get("kind") or "").strip().lower() == "repair":
        return True
    try:
        if int(task.get("retry_count", 0) or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    scheduler = task.get("scheduler")
    if isinstance(scheduler, dict):
        try:
            if int(scheduler.get("retry_count", 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return bool(
        task.get("last_result_status") == "failed"
        or task.get("failure_category")
        or task.get("failure_reason")
    )


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


def _verify_page_entry(
    check: dict[str, Any],
    *,
    root: Path | None,
) -> tuple[str | None, str]:
    """确认页面入口文件存在且位于当前工作区内。"""

    path = _check_path(check, "entry_path")
    target, error = _read_workspace_file(root, path)
    if error:
        return error, error
    del target
    return None, f"页面入口 {path} 已存在。"


def _verify_page_default_export(
    check: dict[str, Any],
    *,
    root: Path | None,
) -> tuple[str | None, str]:
    """确认页面入口提供 default export。"""

    path = _check_path(check, "entry_path")
    source, error = _read_workspace_file(root, path)
    if error:
        return error, error
    if not re.search(r"\bexport\s+default\b", strip_comments(source or "")):
        return f"页面入口 {path} 缺少 export default。", "未发现页面 default export。"
    return None, f"页面入口 {path} 已提供 export default。"


def _verify_page_placeholder(
    check: dict[str, Any],
    *,
    root: Path | None,
) -> tuple[str | None, str]:
    """确认页面入口不再保留固定模板占位内容。"""

    path = _check_path(check, "entry_path")
    source, error = _read_workspace_file(root, path)
    if error:
        return error, error
    clean = strip_comments(source or "").casefold()
    markers = _string_list(_dict_value(check.get("expected")).get("forbidden_markers"))
    matched = [marker for marker in markers if marker.casefold() in clean]
    if matched:
        return (
            f"页面入口 {path} 仍包含占位内容：{'、'.join(matched)}。",
            "页面占位内容尚未替换。",
        )
    return None, f"页面入口 {path} 未发现受控占位内容。"


def _verify_page_component_reachability(
    check: dict[str, Any],
    *,
    root: Path | None,
) -> tuple[str | None, str]:
    """确认任务内新增组件导出且由页面入口引用。"""

    expected = _dict_value(check.get("expected"))
    entry_path = _normalize_path(expected.get("entry_path"))
    component_path = _normalize_path(expected.get("component_path"))
    symbol = str(expected.get("component_symbol") or "").strip()
    entry_source, entry_error = _read_workspace_file(root, entry_path)
    if entry_error:
        return entry_error, entry_error
    component_source, component_error = _read_workspace_file(root, component_path)
    if component_error:
        return component_error, component_error
    component_clean = strip_comments(component_source or "")
    if not re.search(r"\bexport\s+(?:default\s+)?(?:function|const|class)\b|\bexport\s+default\b", component_clean):
        return f"组件 {component_path} 未发现导出。", "任务内组件缺少 export。"
    entry_clean = strip_comments(entry_source or "")
    component_name = component_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    import_reference = re.search(
        rf"\bfrom\s+[\"'][^\"']*{re.escape(component_name)}(?:\.[^\"']+)?[\"']",
        entry_clean,
    )
    symbol_reference = symbol and re.search(rf"\b{re.escape(symbol)}\b", entry_clean)
    if not import_reference and not symbol_reference:
        return (
            f"页面入口 {entry_path} 未引用组件 {component_path}。",
            "页面入口未发现任务内组件的可达引用。",
        )
    return None, f"组件 {component_path} 已导出并由页面入口引用。"


def _verify_frontend_authorization(
    check: dict[str, Any],
    *,
    root: Path | None,
) -> tuple[str | None, str]:
    """验证页面受控操作的 Permission 接入及页面层 HTTP 边界。"""

    expected = _dict_value(check.get("expected"))
    actions = [
        {
            "actionId": str(item.get("actionId") or "").strip(),
            "resourceKey": str(item.get("resourceKey") or "").strip(),
            "mode": str(item.get("mode") or "hidden").strip() or "hidden",
            "resourceConstant": _dict_value(item.get("resourceConstant")),
        }
        for item in _dict_items(expected.get("controlledActions"))
        if str(item.get("actionId") or "").strip()
        and str(item.get("resourceKey") or "").strip()
    ]
    paths = _string_list(check.get("target_paths"))
    sources: list[str] = []
    for path in paths:
        source, error = _read_workspace_file(root, _normalize_path(path))
        if error:
            return error, error
        sources.append(strip_comments(source or ""))
    merged = "\n".join(sources)
    if not re.search(
        r'import\s*\{[^}]*\bPermission\b[^}]*\}\s*from\s*["\'][^"\']*authorization[^"\']*["\']',
        merged,
    ):
        return "页面受控操作未从模板 authorization 模块导入 Permission。", "未发现 Permission 导入。"
    if not re.search(
        r'import\s*\{[^}]*\bRESOURCES\b[^}]*\}\s*from\s*["\']@/constants/resources["\']',
        merged,
    ):
        return "页面受控操作未从唯一资源目录导入 RESOURCES。", "未发现 RESOURCES 导入。"
    blocks = list(
        re.finditer(
            r"<Permission\b(?P<attrs>[^>]*)>(?P<body>[\s\S]*?)</Permission\s*>",
            merged,
        )
    )
    for action in actions:
        matched_blocks = [
            block
            for block in blocks
            if _permission_block_matches(block.group("attrs") or "", action)
        ]
        if not matched_blocks:
            return (
                f"受控 Action {action['actionId']} 必须由 Permission 使用精确 RESOURCES 常量和 mode={action['mode']} 包装。",
                "Permission 包装与平台权限约束不一致。",
            )
    # Permission 只允许使用当前任务声明的资源常量和展示模式；不依赖 UI 标记推断动作身份。
    for block in blocks:
        if not any(_permission_block_matches(block.group("attrs") or "", action) for action in actions):
            return (
                "Permission 使用了当前任务未声明的 RESOURCES 常量或展示模式。",
                "权限包装范围超出平台资源约束。",
            )
    return None, f"已验证 {len(actions)} 个受控 Action 的 Permission 资源接入。"


def _verify_frontend_api_boundary(
    check: dict[str, Any],
    *,
    root: Path | None,
) -> tuple[str | None, str]:
    """验证页面及其任务内组件未绕过领域 API 层直接访问 HTTP 客户端。"""

    for path in _string_list(check.get("target_paths")):
        source, error = _read_workspace_file(root, _normalize_path(path))
        if error:
            return error, error
        if re.search(r"\bfetch\s*\(|\baxios\s*\.|\bservice\s*\.", strip_comments(source or "")):
            return (
                "页面或组件直接调用 fetch、axios 或 service；业务接口必须经 src/apis/ 与 useRequest。",
                f"在 {path} 发现页面层直连 HTTP 客户端。",
            )
    return None, "页面及任务内组件未发现直连 HTTP 客户端。"


def _verify_backend_authorization(
    check: dict[str, Any],
    *,
    root: Path | None,
) -> tuple[str | None, str]:
    """验证目标 Controller Method 的唯一 RequireAnyResource 与 ANY-OF 常量集合。"""

    expected = _dict_value(check.get("expected"))
    identity = _dict_value(expected.get("endpointIdentity"))
    method = str(identity.get("httpMethod") or "").upper()
    path = str(identity.get("path") or "")
    resource_keys = _string_list(expected.get("operationResourceKeys"))
    constants = [
        str(item.get("name") or "").strip()
        for item in _dict_items(expected.get("authConstants"))
        if str(item.get("name") or "").strip()
    ]
    sources: dict[str, str] = {}
    for target_path in _string_list(check.get("target_paths")):
        source, error = _read_workspace_file(root, _normalize_path(target_path))
        if error:
            return error, error
        sources[_normalize_path(target_path)] = source or ""
    model = _inspect_or_block(sources)
    if isinstance(model, dict):
        return str(model.get("reason") or "Controller 权限验收无法安全解析源码。"), "Java AST 无法解析 Controller 源码。"
    controllers = [item for item in model.types if _type_has_suffix(item, "Controller", "Resource", "Endpoint", "Handler")]
    matched = _find_controller_method(controllers, method, path)
    if matched is None:
        return f"无法唯一定位 Controller Endpoint：{method} {path}。", "未发现与平台 Endpoint 身份匹配的 Controller Method。"
    _controller, handler = matched
    annotations = [item for item in handler.annotations if item.name == "RequireAnyResource"]
    if not resource_keys:
        if annotations:
            return f"未受控 Endpoint {method} {path} 不得新增 RequireAnyResource。", "空资源集合存在权限注解。"
        return None, f"未受控 Endpoint {method} {path} 未发现 RequireAnyResource。"
    if len(annotations) != 1:
        return f"受控 Endpoint {method} {path} 必须恰好存在一个 RequireAnyResource，实际为 {len(annotations)} 个。", "权限注解数量不符合唯一性约束。"
    annotation_text = annotations[0].text
    actual_constants = set(re.findall(r"(?:AuthConstants\s*\.\s*)?([A-Z][A-Z0-9_]*_RESOURCE)\b", annotation_text))
    if actual_constants != set(constants):
        return (
            f"Endpoint {method} {path} 的 RequireAnyResource 常量集合不匹配：期望 {', '.join(constants)}，实际 {', '.join(sorted(actual_constants)) or '<empty>'}。",
            "权限常量集合未精确匹配平台 Contract。",
        )
    if any("@RequireAnyResource" in text for source_path, text in sources.items() if source_path != _controller.source_path):
        return "Controller 之外的任务目标文件出现 RequireAnyResource。", "资源权限判断越过 Controller 边界。"
    return None, f"已验证 {method} {path} 的唯一 RequireAnyResource，包含 {len(resource_keys)} 个 ANY-OF 常量。"


def _permission_block_matches(attributes: str, action: dict[str, Any]) -> bool:
    """判断 Permission 是否使用平台指定 RESOURCES 常量及确认后的展示模式。"""

    constant = _dict_value(action.get("resourceConstant"))
    group = re.escape(str(constant.get("group") or ""))
    name = re.escape(str(constant.get("name") or ""))
    mode = re.escape(str(action.get("mode") or "hidden"))
    resource_pattern = rf"\bresourceKey\s*=\s*\{{\s*RESOURCES\.{group}\.{name}\s*\}}"
    mode_pattern = rf"\bmode\s*=\s*(?:[\"']{mode}[\"']|\{{\s*[\"']{mode}[\"']\s*\}})"
    return bool(re.search(resource_pattern, attributes) and re.search(mode_pattern, attributes))


def _check_path(check: dict[str, Any], expected_key: str) -> str:
    """从工程检查中读取并归一化一个目标路径。"""

    expected = _dict_value(check.get("expected"))
    value = expected.get(expected_key)
    if not value:
        paths = _string_list(check.get("target_paths"))
        value = paths[0] if paths else ""
    return _normalize_path(value)


def _read_workspace_file(root: Path | None, path: str) -> tuple[str | None, str | None]:
    """在工作区边界内读取小型源码文件。"""

    if root is None:
        return None, "工程页面检查缺少工作区根目录。"
    if not path:
        return None, "工程页面检查缺少目标路径。"
    path_error = _resolved_path_error(root, path)
    if path_error:
        return None, path_error
    target = _workspace_target(root, path)
    if not target.is_file():
        return None, f"页面工程检查目标文件不存在：{path}。"
    try:
        if target.stat().st_size > 512_000:
            return None, f"页面工程检查目标文件过大：{path}。"
        return target.read_text(encoding="utf-8"), None
    except (OSError, UnicodeError) as exc:
        return None, f"页面工程检查读取失败 {path}：{type(exc).__name__}。"


def _workspace_target(root: Path, path: str) -> Path:
    """按文件系统实际名称解析相对路径，兼容大小写敏感和不敏感系统。"""

    direct = (root / path).resolve()
    if direct.exists():
        return direct
    current = root
    for part in _normalize_path(path).split("/"):
        if not part:
            continue
        try:
            matches = [
                child
                for child in current.iterdir()
                if child.name.casefold() == part.casefold()
            ]
        except OSError:
            return direct
        if len(matches) != 1:
            return direct
        current = matches[0]
    return current.resolve()


def _resolved_path_error(root: Path, path: str) -> str | None:
    """阻止工程验收读取越过工作区的路径。"""

    normalized = _normalize_path(path)
    if _is_absolute_path(path) or ".." in normalized.split("/"):
        return f"目标路径不安全：{path}。"
    resolved = (root / normalized).resolve()
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
    return list(dict.fromkeys(_normalize_path(path) for path in paths if _normalize_path(path)))


def _path_matches_any(path: str, patterns: list[str]) -> bool:
    """判断实际文件是否命中任一精确、目录或通配授权路径。"""

    normalized = path.replace("\\", "/").lstrip("./").rstrip("/").casefold()
    for pattern in patterns:
        candidate = (
            pattern.replace("\\", "/").lstrip("./").rstrip("/").casefold()
        )
        if not candidate:
            continue
        if candidate.endswith("/**") and normalized.startswith(
            candidate[:-3].rstrip("/") + "/"
        ):
            return True
        if normalized.startswith(candidate + "/") or fnmatch(normalized, candidate):
            return True
    return False


def _normalize_path(value: Any) -> str:
    """统一 Windows、macOS 和 Linux 工程路径。"""

    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return "/".join(part for part in text.split("/") if part not in {"", "."})


def _is_absolute_path(value: Any) -> bool:
    """识别 POSIX、UNC 和 Windows drive 绝对路径。"""

    text = str(value or "").strip().replace("\\", "/")
    return text.startswith("/") or bool(re.match(r"^[A-Za-z]:/", text)) or text.startswith("//")


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
