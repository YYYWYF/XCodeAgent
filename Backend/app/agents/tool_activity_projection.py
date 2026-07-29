from __future__ import annotations

import json
from typing import Any

from app.workspace.workspace import SENSITIVE_FILE_NAMES


_VISIBLE_TOOL_CATEGORIES = {
    "ls": "browse",
    "read_file": "read",
    "glob": "search",
    "grep": "search",
    "write_file": "write",
    "edit_file": "write",
    "delete_file": "delete",
    "execute": "execute",
    "task": "agent",
    "write_todos": "plan",
}


def normalized_tool_activity(
    *,
    call_id: str,
    tool_name: str,
    args: Any,
    workspace: str | None,
) -> dict[str, Any] | None:
    """把允许展示的工具调用转换为不含文件内容和宿主机路径的一行活动。"""

    category = _VISIBLE_TOOL_CATEGORIES.get(tool_name)
    if category is None:
        return None
    payload = args if isinstance(args, dict) else _json_object(args)
    path = _tool_display_path(tool_name, payload, workspace=workspace)
    message = _tool_activity_message(tool_name, payload, path=path)
    return {
        "callId": call_id,
        "tool": tool_name,
        "category": category,
        "status": "running",
        "message": message[:320],
        **({"path": path} if path else {}),
    }


def completed_activity_message(message: str) -> str:
    """把运行中文案转换为稳定的完成态文案。"""

    return message.replace("正在", "已完成", 1) if message else "工具操作已完成"


def failed_activity_message(message: str) -> str:
    """把运行中文案转换为不包含底层错误内容的失败提示。"""

    detail = message.removeprefix("正在")
    return f"工具操作失败：{detail}" if detail else "工具操作失败"


def _tool_display_path(
    tool_name: str,
    payload: dict[str, Any],
    *,
    workspace: str | None,
) -> str:
    """从不同工具参数中提取安全的虚拟路径或查找模式。"""

    if tool_name == "glob":
        return _safe_virtual_path(payload.get("pattern"), workspace=workspace)
    file_tools = {"read_file", "write_file", "edit_file", "delete_file"}
    key = "file_path" if tool_name in file_tools else "path"
    return _safe_virtual_path(payload.get(key), workspace=workspace)


def _tool_activity_message(
    tool_name: str,
    payload: dict[str, Any],
    *,
    path: str,
) -> str:
    """按工具语义生成稳定中文文案，并限制搜索词长度。"""

    target = path or "工作区"
    if tool_name == "ls":
        return f"正在浏览目录：{target}"
    if tool_name == "read_file":
        return f"正在读取文件：{target}"
    if tool_name == "glob":
        return f"正在查找文件：{target}"
    if tool_name == "grep":
        pattern = _safe_pattern(payload.get("pattern"))
        suffix = f" · {target}" if path else ""
        return f"正在搜索代码：{pattern or '工作区内容'}{suffix}"
    if tool_name == "write_file":
        return f"正在写入文件：{target}"
    if tool_name == "edit_file":
        return f"正在编辑文件：{target}"
    if tool_name == "execute":
        return "正在执行项目验证命令"
    if tool_name == "task":
        return "正在委派 Agent 子任务"
    if tool_name == "write_todos":
        return "正在更新任务清单"
    return f"正在删除文件：{target}"


def _safe_virtual_path(value: Any, *, workspace: str | None) -> str:
    """把工具路径限制为虚拟工作区形式，并拒绝敏感文件与外部宿主机路径。"""

    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    workspace_text = str(workspace or "").strip().replace("\\", "/").rstrip("/")
    if workspace_text and (
        text == workspace_text or text.startswith(f"{workspace_text}/")
    ):
        text = f"/{text[len(workspace_text):].lstrip('/')}"
    is_windows_host_path = len(text) > 2 and text[1:3] == ":/"
    host_roots = ("/Users/", "/home/", "/private/", "/var/")
    if is_windows_host_path or (
        text.startswith("/") and workspace_text and text.startswith(host_roots)
    ):
        return "工作区路径"
    if not text.startswith("/"):
        text = f"/{text.lstrip('./')}"
    parts = [part for part in text.split("/") if part]
    if any(part in {"..", "~"} for part in parts):
        return "工作区路径"
    if any(part in SENSITIVE_FILE_NAMES for part in parts):
        return "受保护文件"
    return "/" + "/".join(parts)[:260]


def _safe_pattern(value: Any) -> str:
    """压缩搜索模式为单行短文本，避免把大段参数投射到 AG-UI。"""

    return " ".join(str(value or "").split())[:120]


def _json_object(value: Any) -> dict[str, Any]:
    """仅在完整 JSON 对象可解析时返回工具参数。"""

    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
