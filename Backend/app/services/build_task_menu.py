from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any

from app.services.frontend_page_tree import find_frontend_page


logger = logging.getLogger(__name__)

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


def ensure_page_route_registration_task(
    tasks: list[dict[str, Any]],
    *,
    project_plan: dict[str, Any],
    workspace_root: str | Path | None,
    build_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """为模板页面确定性补充菜单登记任务，使自动路由能够解析页面入口。"""

    target = build_context.get("target") if isinstance(build_context.get("target"), dict) else {}
    page_id = str(target.get("id") or "")
    if target.get("type") != "page" or not page_id or not workspace_root:
        return tasks
    menu_file = Path(workspace_root).expanduser() / FRONTEND_MENU_PATH
    if not menu_file.is_file():
        return tasks

    page_unit_id = f"page:{page_id}"
    page_task_ids = [
        str(task.get("id"))
        for task in tasks
        if _task_matches_page_unit(task, page_unit_id) and task.get("id")
    ]
    page_keys = {
        key
        for task in tasks
        if _task_matches_page_unit(task, page_unit_id)
        for path in _task_page_entry_paths(task)
        if (key := _page_key_from_entry_path(str(path)))
    }
    if len(page_keys) != 1:
        # 兜底：模型常漏在 change_scope 声明页面入口（只写 API/组件）。
        # 若 build_context.target.page_key 在磁盘上有对应页面目录，说明脚手架
        # 已建好入口，用 target.page_key 补齐，避免因模型漏声明而阻塞任务拆分。
        fallback_key = _resolve_fallback_page_key(
            target, workspace_root, page_keys
        )
        if not fallback_key:
            raise ValueError(
                f"Page {page_id} must resolve to exactly one frontend page entry before route registration."
            )
        page_key = fallback_key
    else:
        page_key = next(iter(page_keys))
    page = _page_skeleton(project_plan, page_id)
    page_name = str(page.get("name") or _dict_value(build_context.get("page_detail")).get("page_name") or page_id)
    confirmed_path = str(page.get("path") or _dict_value(build_context.get("page_detail")).get("path") or "")
    menu_path = _menu_route_path(confirmed_path, page.get("module_id"), page_key)
    menu_tasks = [
        task for task in tasks if FRONTEND_MENU_PATH in _task_page_entry_paths(task)
    ]
    if _menu_entry_exists(
        menu_file,
        page_key=page_key,
        page_name=page_name,
        menu_path=menu_path,
    ):
        return _mark_existing_menu_tasks_satisfied(
            tasks,
            menu_tasks=menu_tasks,
            page_task_ids=page_task_ids,
            page_unit_id=page_unit_id,
            page_key=page_key,
            page_name=page_name,
            menu_path=menu_path,
        )
    if menu_tasks:
        return _normalize_existing_menu_tasks(
            tasks,
            menu_tasks=menu_tasks,
            page_task_ids=page_task_ids,
            page_unit_id=page_unit_id,
            page_key=page_key,
            page_name=page_name,
            menu_path=menu_path,
        )

    task_id = _unique_task_id(f"page:{page_id}:route-menu-registration", tasks)
    route_payload = _menu_registration_task_payload(
        page_task_ids=page_task_ids,
        page_unit_id=page_unit_id,
        page_key=page_key,
        page_name=page_name,
        menu_path=menu_path,
    )
    route_task = {
        "id": task_id,
        "owner": "frontend",
        "title": f"登记{page_name}菜单与自动路由",
        **route_payload,
        "status": "pending",
        "source_refs": dict(build_context.get("source_refs") or {}),
        "impact_scope": {
            "summary": f"使{page_name}进入模板菜单和自动路由。",
            "affected_modules": [FRONTEND_MENU_PATH],
            "public_contracts": [],
            "risks": ["菜单 key 必须与页面目录 PageKey 完全一致。"],
        },
        "verification_commands": [],
    }
    return [*tasks, route_task]


def _page_key_identity(value: str) -> str:
    """生成页面目录语义键，仅忽略大小写、分隔符和常见 Page 后缀。"""

    normalized = "".join(character.lower() for character in value if character.isalnum())
    return normalized[:-4] if normalized.endswith("page") else normalized


def _task_matches_page_unit(task: dict[str, Any], page_unit_id: str) -> bool:
    """判断任务是否归属给定页面 Unit，容忍模型输出的多种 unit_id 格式与大小写。

    标准 Unit ID 为 ``page:<page_id>``，但模型实际输出格式不稳定，常见三种：
    ``page:<page_id>``、``frontend:page:<page_id>``、以及裸 ``<page_id>``。
    此外 ``reconcile_live_page_paths`` 会按已存在页面目录把 unit_id 规范化为
    canonical key（如 ``project_list_page`` → ``ProjectListPage``），大小写与
    分隔符都会变化。因此这里用 ``_page_key_identity`` 做语义匹配，忽略大小写、
    分隔符和 Page 后缀差异，否则页面入口无法被收集，触发误报。
    """

    unit_id = str(task.get("unit_id") or "")
    if not unit_id or not page_unit_id:
        return False
    if unit_id == page_unit_id or unit_id == f"frontend:{page_unit_id}":
        return True
    page_id = page_unit_id.split(":", 1)[1] if ":" in page_unit_id else page_unit_id
    if unit_id == page_id:
        return True
    # 兼容 reconcile 规范化后的 canonical key（大小写/分隔符不同但语义相同）。
    # 先从 unit_id 去掉可能的 page:/frontend:page: 前缀，再比 _page_key_identity，
    # 否则 page:DashboardPage 的 identity 会带上 "page" 前缀导致与 dashboard_page 不匹配。
    unit_key = unit_id
    for prefix in ("frontend:page:", "page:"):
        if unit_id.startswith(prefix):
            unit_key = unit_id[len(prefix):]
            break
    return _page_key_identity(unit_key) == _page_key_identity(page_id) and bool(
        _page_key_identity(unit_key)
    )


def _page_key_from_entry_path(path: str) -> str:
    """从标准页面入口路径提取 PageKey，非页面入口返回空字符串。"""

    normalized = path.lstrip("/")
    if not normalized.startswith(FRONTEND_PAGE_ENTRY_PREFIX) or not normalized.endswith("/index.tsx"):
        return ""
    return normalized[len(FRONTEND_PAGE_ENTRY_PREFIX) : -len("/index.tsx")]


def _resolve_fallback_page_key(
    target: dict[str, Any],
    workspace_root: str | Path,
    page_keys: set[str],
) -> str:
    """当任务未声明唯一页面入口时，用 target.page_key 兜底。

    模型常漏在 change_scope/target_files 声明页面入口（只写 API 或子组件），
    导致 page_keys 为空。若 build_context.target.page_key 在磁盘上有对应页面
    目录（脚手架已建好入口），则用它兜底，避免因模型漏声明而阻塞任务拆分。

    仅在 page_keys 为空时兜底；若模型声明了多个冲突 key，仍交由上层报错。
    """

    if page_keys:
        return ""
    candidate = str(target.get("page_key") or "").strip()
    if not candidate:
        return ""
    entry = Path(workspace_root).expanduser() / f"{FRONTEND_PAGE_ENTRY_PREFIX}{candidate}/index.tsx"
    return candidate if entry.is_file() else ""


def _replace_task_page_path(
    task: dict[str, Any],
    *,
    planned_path: str,
    canonical_path: str,
    planned_key: str,
    canonical_key: str,
) -> dict[str, Any]:
    """同步替换任务内的页面路径与 PageKey，并把已存在入口操作改为 modify。"""

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
    change_scope = []
    for change in replaced.get("change_scope", []):
        if isinstance(change, dict) and change.get("path") == canonical_path:
            change_scope.append({**change, "operation": "modify"})
        else:
            change_scope.append(change)
    return {
        **replaced,
        "change_scope": change_scope,
        "path_reconciliation": {
            "source": "live_workspace",
            "planned_path": planned_path,
            "canonical_path": canonical_path,
            "reason": "unique semantic page directory already exists",
        },
    }


def _menu_registration_task_payload(
    *,
    page_task_ids: list[str],
    page_unit_id: str,
    page_key: str,
    page_name: str,
    menu_path: str,
) -> dict[str, Any]:
    """生成确定性的模板菜单登记任务字段，避免模型决定菜单 path 形态。"""

    hide_in_menu = _has_react_router_path_param(menu_path)
    menu_item = (
        f"{{ path: '{menu_path}', name: '{page_name}', key: '{page_key}', hideInMenu: true }}"
        if hide_in_menu
        else f"{{ path: '{menu_path}', name: '{page_name}', key: '{page_key}' }}"
    )
    acceptance = [
        f"{FRONTEND_MENU_PATH} 的 BIZ_MENUS 顶层数组包含页面“{page_name}”。",
        f"新增菜单项 key 为 {page_key}，与 {FRONTEND_PAGE_ENTRY_PREFIX}{page_key}/index.tsx 完全一致。",
        f"新增菜单项 path 为 {menu_path}，且不删除或修改任何已有菜单项。",
    ]
    if hide_in_menu:
        acceptance.append(
            "由于新增菜单项 path 包含 React Router 路径参数，必须设置 hideInMenu: true。"
        )
    return {
        "description": (
            f"仅向 BIZ_MENUS 顶层数组追加 {menu_item}，"
            "由模板自动路由加载对应页面入口；不得修改现有菜单项或路由骨架。"
        ),
        "dependencies": page_task_ids,
        "unit_id": page_unit_id,
        "allowed_paths": [FRONTEND_MENU_PATH],
        "target_files": [FRONTEND_MENU_PATH],
        "change_scope": [
            {
                "operation": "modify",
                "path": FRONTEND_MENU_PATH,
                "description": "仅向 BIZ_MENUS 顶层数组追加当前页面菜单项。",
            }
        ],
        "can_run_in_parallel": False,
        "parallel_reason": "菜单是共享的增量文件，必须在页面入口完成后串行追加。",
        "acceptance_criteria": acceptance,
        "engineering_context": {
            "menu_registration": {
                "file": FRONTEND_MENU_PATH,
                "path": menu_path,
                "name": page_name,
                "key": page_key,
                "hide_in_menu": hide_in_menu,
            }
        },
    }


def _has_react_router_path_param(path: str) -> bool:
    """判断菜单 path 是否包含 React Router 动态路径参数。"""

    route_part = str(path or "").split("?", 1)[0].split("#", 1)[0]
    return any(
        re.fullmatch(r":[A-Za-z0-9_][A-Za-z0-9_-]*", segment)
        for segment in route_part.split("/")
    )


def _normalize_existing_menu_tasks(
    tasks: list[dict[str, Any]],
    *,
    menu_tasks: list[dict[str, Any]],
    page_task_ids: list[str],
    page_unit_id: str,
    page_key: str,
    page_name: str,
    menu_path: str,
) -> list[dict[str, Any]]:
    """把模型已生成的菜单任务改写为确定性的顶层 BIZ_MENUS 追加任务。"""

    menu_task_ids = {str(task.get("id") or "") for task in menu_tasks}
    payload = _menu_registration_task_payload(
        page_task_ids=page_task_ids,
        page_unit_id=page_unit_id,
        page_key=page_key,
        page_name=page_name,
        menu_path=menu_path,
    )
    return [
        {
            **task,
            **payload,
            "title": str(task.get("title") or f"登记{page_name}菜单与自动路由"),
            "impact_scope": {
                **_impact_scope(task.get("impact_scope"), payload["description"]),
                "affected_modules": [FRONTEND_MENU_PATH],
            },
            "verification_commands": [],
        }
        if str(task.get("id") or "") in menu_task_ids
        else task
        for task in tasks
    ]


def _menu_entry_exists(
    menu_file: Path,
    *,
    page_key: str,
    page_name: str,
    menu_path: str,
) -> bool:
    """检查模板菜单中是否已存在与页面 key、名称和路径完全一致的条目。"""

    try:
        content = menu_file.read_text(encoding="utf-8")
    except OSError:
        return False
    for match in re.finditer(r"\{(?P<body>[^{}]*)\}", content, flags=re.DOTALL):
        body = match.group("body")
        properties = {
            name: _typescript_string_property(body, name)
            for name in ("path", "name", "key")
        }
        if properties == {"path": menu_path, "name": page_name, "key": page_key}:
            return True
    return False


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


def _mark_existing_menu_tasks_satisfied(
    tasks: list[dict[str, Any]],
    *,
    menu_tasks: list[dict[str, Any]],
    page_task_ids: list[str],
    page_unit_id: str,
    page_key: str,
    page_name: str,
    menu_path: str,
) -> list[dict[str, Any]]:
    """把独立菜单任务标记已满足，并从混合页面任务中移除已满足的菜单写入。"""

    if not menu_tasks:
        return tasks
    payload = _menu_registration_task_payload(
        page_task_ids=page_task_ids,
        page_unit_id=page_unit_id,
        page_key=page_key,
        page_name=page_name,
        menu_path=menu_path,
    )
    matching_ids = {
        str(task.get("id") or "")
        for task in menu_tasks
        if page_key in json.dumps(task, ensure_ascii=False)
    }
    if not matching_ids and len(menu_tasks) == 1:
        matching_ids = {str(menu_tasks[0].get("id") or "")}
    evidence_text = (
        f"脚手架已在 {FRONTEND_MENU_PATH} 注册 "
        f"{{ path: '{menu_path}', name: '{page_name}', key: '{page_key}' }}。"
    )
    normalized: list[dict[str, Any]] = []
    for task in tasks:
        if str(task.get("id") or "") not in matching_ids:
            normalized.append(task)
            continue
        if _task_only_targets_menu(task):
            normalized.append(
                {
                    **task,
                    **payload,
                    "status": "already_satisfied",
                    "last_result_status": "already_satisfied",
                    "satisfaction_evidence": {
                        "target_files": [FRONTEND_MENU_PATH],
                        "acceptance_criteria": [
                            {
                                "criterion_index": index,
                                "status": "passed",
                                "evidence": evidence_text,
                            }
                            for index, _ in enumerate(
                                task.get("acceptance_criteria", [])
                            )
                        ],
                    },
                    "satisfied_by": "frontend-template-page-scaffold",
                }
            )
            continue
        normalized.append(_remove_satisfied_menu_target(task, evidence_text))
    return normalized


def _task_only_targets_menu(task: dict[str, Any]) -> bool:
    """判断任务是否只负责菜单文件，避免把页面与 API 混合任务整体标记为已满足。"""

    targets = {
        path.lstrip("/")
        for path in [
            *_string_list(task.get("target_files")),
            *_string_list(task.get("allowed_paths")),
        ]
        if path
    }
    return bool(targets) and targets == {FRONTEND_MENU_PATH}


def _remove_satisfied_menu_target(
    task: dict[str, Any], evidence_text: str
) -> dict[str, Any]:
    """从混合任务移除已存在的菜单写目标，同时保留其余页面和接口工作。"""

    def remaining_paths(value: Any) -> list[str]:
        """过滤列表中的菜单目标并保持原有路径写法。"""

        return [
            path
            for path in _string_list(value)
            if path.lstrip("/") != FRONTEND_MENU_PATH
        ]

    remaining_scope = [
        change
        for change in task.get("change_scope", [])
        if isinstance(change, dict)
        and str(change.get("path") or "").lstrip("/") != FRONTEND_MENU_PATH
    ]
    return {
        **task,
        "description": (
            f"{str(task.get('description') or '').strip()} "
            f"{evidence_text} 菜单已满足，不得再次修改 {FRONTEND_MENU_PATH}。"
        ).strip(),
        "allowed_paths": remaining_paths(task.get("allowed_paths")),
        "target_files": remaining_paths(task.get("target_files")),
        "change_scope": remaining_scope,
        "pre_satisfied_targets": [
            *[
                item
                for item in task.get("pre_satisfied_targets", [])
                if isinstance(item, dict)
            ],
            {
                "path": FRONTEND_MENU_PATH,
                "satisfied_by": "frontend-template-page-scaffold",
                "evidence": evidence_text,
            },
        ],
    }


def _page_skeleton(project_plan: dict[str, Any], page_id: str) -> dict[str, Any]:
    """从任务准备视图读取当前页面的名称、路径和模块标识。

    优先从 ``application_skeleton.pages`` 读取；当该视图为空（部分工作区未填充
    application_skeleton）时回退到 ``frontend_pages`` 树，否则页面 name/path 取不到，
    会导致菜单重复检测失配、生成错误的菜单登记任务。
    """

    skeleton = project_plan.get("application_skeleton")
    pages = skeleton.get("pages", []) if isinstance(skeleton, dict) else []
    match = next(
        (
            page
            for page in pages
            if isinstance(page, dict) and str(page.get("pageId") or "") == page_id
        ),
        None,
    )
    if match is None:
        match = find_frontend_page(project_plan.get("frontend_pages"), page_id)
    return match if isinstance(match, dict) else {}


def _menu_route_path(confirmed_path: str, module_id: Any, page_key: str) -> str:
    """按前端脚手架规则把确认路径转换为菜单 path。

    脚手架在 menus.ts 中使用页面的完整确认路径（如 ``/page/page/projects``）
    作为菜单项 path，而非仅取末级段。返回完整路径以确保确定性菜单登记检查
    与脚手架生成的菜单条目完全匹配。
    """

    del module_id
    path = str(confirmed_path or "").strip()
    if path:
        return path
    return page_key[:1].lower() + page_key[1:]


def _unique_task_id(base_id: str, tasks: list[dict[str, Any]]) -> str:
    """生成不与模型候选任务冲突的稳定任务 ID。"""

    used = {str(task.get("id") or "") for task in tasks}
    candidate = base_id
    suffix = 2
    while candidate in used:
        candidate = f"{base_id}-{suffix}"
        suffix += 1
    return candidate


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


def _dedupe_normalized_strings(values: list[str]) -> list[str]:
    """按规范化文本去重模型输出列表，避免同一句验收点或路径重复落库。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalized_text_key(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def _normalized_text_key(value: str) -> str:
    """生成文本去重键，忽略大小写、空白和常见中英文标点差异。"""

    text = str(value or "").strip().lower()
    if not text:
        return ""
    punctuation = " \t\r\n。．.，,；;：:、!！?？（）()[]【】{}<>《》\"'`"
    return "".join(char for char in text if char not in punctuation)


def _impact_scope(value: Any, description: str) -> dict[str, Any]:
    """规整菜单任务影响范围。"""

    source = value if isinstance(value, dict) else {}
    return {
        "summary": _text(source.get("summary"), description),
        "affected_modules": _string_list(source.get("affected_modules")),
        "public_contracts": _string_list(source.get("public_contracts")),
        "risks": _string_list(source.get("risks")),
    }


def _text(value: Any, default: str = "") -> str:
    """规整文本输入。"""

    text = str(value or "").strip()
    return text or default


def _dict_value(value: Any) -> dict[str, Any]:
    """将不可信输入规整为字典，便于读取上下文。"""

    return dict(value) if isinstance(value, dict) else {}
