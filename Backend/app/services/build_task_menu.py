from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

FRONTEND_PAGE_ENTRY_PREFIX = "frontend/src/pages/"
FRONTEND_MENU_PATH = "frontend/src/constants/menus.ts"


def reconcile_live_page_paths(
    tasks: list[dict[str, Any]],
    *,
    workspace_root: str | Path | None,
    build_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """用实时页面目录纠正唯一同义目标，避免陈旧快照产生重复页面入口。"""

    target = build_context.get("target") if isinstance(build_context.get("target"), dict) else {}
    if target.get("type") != "page" or not workspace_root:
        return tasks
    pages_root = Path(workspace_root).expanduser() / "frontend/src/pages"
    if not pages_root.is_dir():
        return tasks
    existing_keys = [
        path.name
        for path in pages_root.iterdir()
        if path.is_dir() and (path / "index.tsx").is_file()
    ]
    keys_by_identity: dict[str, list[str]] = {}
    for key in existing_keys:
        keys_by_identity.setdefault(_page_key_identity(key), []).append(key)

    replacements: list[tuple[str, str, str, str]] = []
    for task in tasks:
        for path in _task_page_entry_paths(task):
            planned_path = str(path).lstrip("./")
            planned_key = _page_key_from_entry_path(planned_path)
            if not planned_key or (Path(workspace_root) / planned_path).is_file():
                continue
            candidates = keys_by_identity.get(_page_key_identity(planned_key), [])
            if len(candidates) != 1:
                continue
            canonical_key = candidates[0]
            canonical_path = f"{FRONTEND_PAGE_ENTRY_PREFIX}{canonical_key}/index.tsx"
            replacement = (planned_path, canonical_path, planned_key, canonical_key)
            if replacement not in replacements:
                replacements.append(replacement)

    reconciled: list[dict[str, Any]] = []
    for task in tasks:
        next_task = task
        task_text = json.dumps(task, ensure_ascii=False)
        for planned_path, canonical_path, planned_key, canonical_key in replacements:
            if planned_path not in task_text and planned_key not in task_text:
                continue
            next_task = _replace_task_page_path(
                next_task,
                planned_path=planned_path,
                canonical_path=canonical_path,
                planned_key=planned_key,
                canonical_key=canonical_key,
            )
        reconciled.append(next_task)
    return reconciled


def _page_key_identity(value: str) -> str:
    """生成页面目录语义键，仅忽略大小写、分隔符和常见 Page 后缀。"""

    normalized = "".join(character.lower() for character in value if character.isalnum())
    return normalized[:-4] if normalized.endswith("page") else normalized


def _page_key_from_entry_path(path: str) -> str:
    """从标准页面入口路径提取 PageKey，非页面入口返回空字符串。"""

    normalized = path.lstrip("/")
    if not normalized.startswith(FRONTEND_PAGE_ENTRY_PREFIX) or not normalized.endswith("/index.tsx"):
        return ""
    return normalized[len(FRONTEND_PAGE_ENTRY_PREFIX) : -len("/index.tsx")]


def _replace_task_page_path(
    task: dict[str, Any],
    *,
    planned_path: str,
    canonical_path: str,
    planned_key: str,
    canonical_key: str,
) -> dict[str, Any]:
    """同步替换任务内的页面路径与 PageKey，但保留模型声明的操作意图。"""

    # PageKey 替换限定在标识符边界内，避免 planned_key 是 canonical_key 子串时
    # 误伤（如 planned_key=ProjectList、canonical_key=ProjectListPage 时，
    # 朴素 .replace 会把 ProjectListPage 里的 ProjectList 也替换成 ProjectListPagePage）。
    key_pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(planned_key)}(?![A-Za-z0-9])")

    def replace_value(value: Any) -> Any:
        """递归替换任务结构中的精确路径和目录键。"""

        if isinstance(value, str):
            replaced = (
                value.replace(f"/{planned_path}", canonical_path)
                .replace(planned_path, canonical_path)
            )
            if planned_key != canonical_key:
                replaced = key_pattern.sub(canonical_key, replaced)
            return replaced
        if isinstance(value, list):
            return [replace_value(item) for item in value]
        if isinstance(value, dict):
            return {key: replace_value(item) for key, item in value.items()}
        return value

    replaced = replace_value(task)
    return {
        **replaced,
        "path_reconciliation": {
            "source": "live_workspace",
            "planned_path": planned_path,
            "canonical_path": canonical_path,
            "reason": "unique semantic page directory already exists",
        },
    }


def menu_registration_matches(
    workspace_root: str | Path,
    registration: dict[str, Any],
) -> bool:
    """复用菜单解析规则核对确定性工程验收中的完整菜单登记。"""

    file_path = str(registration.get("file") or FRONTEND_MENU_PATH).lstrip("./")
    menu_file = Path(workspace_root).expanduser().resolve() / file_path
    try:
        content = menu_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    expected = {
        "path": str(registration.get("path") or ""),
        "name": str(registration.get("name") or ""),
        "key": str(registration.get("key") or ""),
    }
    expected_hidden = bool(registration.get("hide_in_menu"))
    for match in re.finditer(r"\{(?P<body>[^{}]*)\}", content, flags=re.DOTALL):
        body = match.group("body")
        properties = {
            name: _typescript_string_property(body, name)
            for name in ("path", "name", "key")
        }
        if properties != expected:
            continue
        hidden = bool(re.search(r"\bhideInMenu\s*:\s*true\b", body))
        return hidden is expected_hidden
    return False


def _typescript_string_property(body: str, name: str) -> str:
    """从简单 TypeScript 对象文本中读取一个字符串属性。"""

    match = re.search(
        rf"\b{re.escape(name)}\s*:\s*(['\"])(?P<value>[^'\"]*)\1",
        body,
    )
    return match.group("value") if match else ""


def _task_target_files(task: dict[str, Any]) -> list[str]:
    """读取当前 DAG v3 任务的目标文件。"""

    return _string_list(task.get("target_files"))


def _task_page_entry_paths(task: dict[str, Any]) -> list[str]:
    """收集任务中所有可能携带页面 PageKey 的文件路径。

    同时读 target_files 与 change_scope：模型常把页面入口
    （frontend/src/pages/<Key>/index.tsx）只放在 change_scope 而漏进
    target_files，单读 target_files 会使页面入口缺失。这里合并两个字段
    的路径，再由调用方用 _page_key_from_entry_path 过滤出页面入口。
    """

    paths = list(_task_target_files(task))
    change_scope = task.get("change_scope")
    if isinstance(change_scope, list):
        for item in change_scope:
            if isinstance(item, dict):
                path = str(item.get("path") or "").strip()
            else:
                path = str(item).strip()
            if path:
                paths.append(path)
    return paths


def _string_list(value: Any) -> list[str]:
    """将列表输入规整为去空字符串列表。"""

    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
