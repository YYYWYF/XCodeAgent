"""ProjectPlan frontend_pages 菜单树与页面叶子的统一辅助方法。"""

from __future__ import annotations

import re
from typing import Any, Iterable


def dict_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的对象项，避免后续树遍历受到脏数据影响。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def is_menu_node(node: dict[str, Any]) -> bool:
    """通过 children 且缺少 pageId 的约束识别菜单目录节点。"""

    return isinstance(node.get("children"), list) and not str(
        node.get("pageId") or node.get("id") or ""
    ).strip()


def is_page_leaf(node: dict[str, Any]) -> bool:
    """把包含正式 pageId 的对象识别为可执行的业务页面叶子。"""

    return bool(str(node.get("pageId") or node.get("id") or "").strip())


def flatten_frontend_pages(value: Any) -> list[dict[str, Any]]:
    """递归拍平 frontend_pages，仅返回真正的业务页面叶子。"""

    flattened: list[dict[str, Any]] = []
    for node in dict_items(value):
        if is_page_leaf(node):
            flattened.append(node)
        if isinstance(node.get("children"), list):
            flattened.extend(flatten_frontend_pages(node.get("children")))
    return flattened


def frontend_page_ids(value: Any) -> list[str]:
    """返回树中全部页面叶子的稳定 pageId 列表。"""

    result: list[str] = []
    for page in flatten_frontend_pages(value):
        page_id = str(page.get("pageId") or page.get("id") or "").strip()
        if page_id and page_id not in result:
            result.append(page_id)
    return result


def frontend_page_tree_has_menu_nodes(value: Any) -> bool:
    """判断当前 frontend_pages 是否已经包含菜单目录层级。"""

    for node in dict_items(value):
        if is_menu_node(node):
            return True
        if frontend_page_tree_has_menu_nodes(node.get("children")):
            return True
    return False


def find_frontend_page(value: Any, page_id: str) -> dict[str, Any] | None:
    """按 pageId 从树中查找叶子页面对象。"""

    target_id = str(page_id or "").strip()
    if not target_id:
        return None
    for page in flatten_frontend_pages(value):
        if str(page.get("pageId") or page.get("id") or "").strip() == target_id:
            return page
    return None


def update_frontend_page_leaves(
    value: Any,
    updater_by_page_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 pageId 更新树中的页面叶子，同时原样保留菜单层级。"""

    updated_nodes: list[dict[str, Any]] = []
    for node in dict_items(value):
        updated = dict(node)
        page_id = str(updated.get("pageId") or updated.get("id") or "").strip()
        if page_id and page_id in updater_by_page_id:
            updated = {**updated, **updater_by_page_id[page_id]}
        children = node.get("children")
        if isinstance(children, list):
            updated["children"] = update_frontend_page_leaves(
                children,
                updater_by_page_id,
            )
        updated_nodes.append(updated)
    return updated_nodes


def rebuild_frontend_page_tree(
    tree_source: Any,
    normalized_pages: list[dict[str, Any]],
    *,
    module_names: dict[str, str] | None = None,
    root_route_prefix: str | None = None,
    menu_enabled: bool = False,
) -> list[dict[str, Any]]:
    """优先复用模型给出的目录层级；无目录时按模块为页面生成菜单树。"""

    effective_root_route_prefix = (
        _normalize_route_text(root_route_prefix)
        if root_route_prefix is not None
        else _infer_root_route_prefix(normalized_pages)
    )
    pages_by_id = {
        str(page.get("pageId") or page.get("id") or "").strip(): dict(page)
        for page in normalized_pages
        if str(page.get("pageId") or page.get("id") or "").strip()
    }
    if frontend_page_tree_has_menu_nodes(tree_source):
        preserved = _rebuild_tree_from_source(tree_source, pages_by_id)
        attached_ids = set(frontend_page_ids(preserved))
        remaining_pages = [
            page
            for page_id, page in pages_by_id.items()
            if page_id not in attached_ids
        ]
        if remaining_pages:
            preserved.extend(
                group_pages_into_menu_tree(remaining_pages, module_names=module_names)
            )
        return apply_frontend_page_route_hierarchy(
            preserved,
            root_route_prefix=effective_root_route_prefix,
            menu_enabled=menu_enabled,
        )
    return apply_frontend_page_route_hierarchy(
        group_pages_into_menu_tree(normalized_pages, module_names=module_names),
        root_route_prefix=effective_root_route_prefix,
        menu_enabled=menu_enabled,
    )


def group_pages_into_menu_tree(
    pages: Iterable[dict[str, Any]],
    *,
    module_names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """在缺少显式菜单层级时，按模块为页面生成稳定菜单树。"""

    ordered_pages = [dict(page) for page in pages if isinstance(page, dict)]
    groups: dict[str, list[dict[str, Any]]] = {}
    group_order: list[str] = []
    for page in ordered_pages:
        page_id = str(page.get("pageId") or page.get("id") or "").strip()
        if not page_id:
            continue
        module_id = str(page.get("module_id") or "core").strip() or "core"
        if module_id not in groups:
            groups[module_id] = []
            group_order.append(module_id)
        groups[module_id].append(page)

    used_unique_paths: set[str] = set()
    result: list[dict[str, Any]] = []
    for module_id in group_order:
        children = groups[module_id]
        if not children:
            continue
        if _should_keep_as_root_page(module_id, children):
            result.extend(children)
            continue
        menu_name = (
            (module_names or {}).get(module_id)
            or str(children[0].get("module_name") or "").strip()
            or _module_display_name(module_id)
        )
        unique_path = _unique_menu_path(
            str(children[0].get("unique_path") or "") or _menu_path_from_module(module_id),
            used_unique_paths,
        )
        result.append(
            {
                "name": menu_name,
                "unique_path": unique_path,
                "children": children,
            }
        )
    return result


def frontend_page_menu_paths(value: Any) -> list[str]:
    """收集菜单节点 unique_path，供协议与校验层做唯一性检查。"""

    result: list[str] = []
    for node in dict_items(value):
        unique_path = str(node.get("unique_path") or "").strip()
        if is_menu_node(node) and unique_path and unique_path not in result:
            result.append(unique_path)
        result.extend(
            path
            for path in frontend_page_menu_paths(node.get("children"))
            if path not in result
        )
    return result


def apply_frontend_page_route_hierarchy(
    value: Any,
    *,
    root_route_prefix: str = "",
    menu_enabled: bool = False,
) -> list[dict[str, Any]]:
    """按页面根路由和菜单层级重写菜单与页面路由。"""

    normalized_root = _normalize_route_text(root_route_prefix)
    return _apply_frontend_page_route_hierarchy(
        value,
        root_route_prefix=normalized_root,
        menu_enabled=menu_enabled,
        inherited_menu_path="",
        used_menu_paths=set(),
        used_page_paths=set(),
    )


def _rebuild_tree_from_source(
    tree_source: Any,
    pages_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """按原始树顺序重建菜单节点，并替换为规范化后的页面叶子。"""

    rebuilt: list[dict[str, Any]] = []
    for node in dict_items(tree_source):
        if is_menu_node(node):
            children = _rebuild_tree_from_source(node.get("children"), pages_by_id)
            if not children:
                continue
            rebuilt.append(
                {
                    "name": str(node.get("name") or "未命名菜单").strip() or "未命名菜单",
                    "unique_path": (
                        str(node.get("unique_path")).strip()
                        if "unique_path" in node
                        else _menu_path_from_label(node.get("name") or "menu")
                    ),
                    "children": children,
                }
            )
            continue
        page_id = str(node.get("pageId") or node.get("id") or "").strip()
        page = pages_by_id.pop(page_id, None)
        if page is not None:
            rebuilt.append(page)
    return rebuilt


def _should_keep_as_root_page(module_id: str, pages: list[dict[str, Any]]) -> bool:
    """保留首页/仪表盘类孤立页面为根节点，避免所有页面都被强制包进菜单。"""

    if len(pages) != 1:
        return False
    page = pages[0]
    path = str(page.get("path") or "").strip()
    page_id = str(page.get("pageId") or page.get("id") or "").strip().lower()
    normalized_module = module_id.strip().lower()
    return (
        path == "/"
        or page_id in {"home", "dashboard", "index"}
        or normalized_module in {"core", "home", "dashboard"}
    )


def _module_display_name(module_id: str) -> str:
    """为缺少模块中文名的规划结果生成较可读的菜单名称。"""

    normalized = str(module_id or "core").strip()
    if not normalized:
        return "核心菜单"
    parts = [part for part in re.split(r"[_\-\s]+", normalized) if part]
    if not parts:
        return "核心菜单"
    return " ".join(part.capitalize() for part in parts)


def _path_from_pageId(page_id: str) -> str:
    """根据页面标识生成稳定叶子路由。"""

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(page_id or "page")).strip("-_")
    route = normalized.replace("_", "-").lower() or "page"
    if route.endswith("-page") and route != "dashboard-page":
        route = route[: -len("-page")] or route
    return "/" if route in {"dashboard", "dashboard-page", "home", "index"} else f"/{route}"


def _menu_leaf_path_from_pageId(page_id: str) -> str:
    """启用菜单时，为首页类页面生成带叶子段的稳定路由。"""

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(page_id or "home")).strip("-_")
    route = normalized.replace("_", "-").lower() or "home"
    if route.endswith("-page") and route != "dashboard-page":
        route = route[: -len("-page")] or route
    if route in {"dashboard", "dashboard-page", "home", "index"}:
        route = "home"
    return f"/{route}"


def _menu_path_from_module(module_id: str) -> str:
    """根据模块标识生成稳定菜单 unique_path。"""

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(module_id or "menu")).strip("-_")
    slug = normalized.replace("_", "-").lower() or "menu"
    return f"/{slug}"


def _menu_path_from_label(label: Any) -> str:
    """根据菜单名称兜底生成稳定 unique_path。"""

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(label or "menu")).strip("-_")
    slug = normalized.replace("_", "-").lower() or "menu"
    return f"/{slug}"


def _unique_menu_path(candidate: str, used_paths: set[str]) -> str:
    """确保菜单 unique_path 在当前 frontend_pages 树中稳定唯一。"""

    normalized = candidate.strip() or "/menu"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    normalized = normalized.replace("//", "/")
    if normalized not in used_paths:
        used_paths.add(normalized)
        return normalized
    base = normalized
    suffix = 2
    while f"{base}-{suffix}" in used_paths:
        suffix += 1
    unique_path = f"{base}-{suffix}"
    used_paths.add(unique_path)
    return unique_path


def _apply_frontend_page_route_hierarchy(
    value: Any,
    *,
    root_route_prefix: str,
    menu_enabled: bool,
    inherited_menu_path: str,
    used_menu_paths: set[str],
    used_page_paths: set[str],
) -> list[dict[str, Any]]:
    """递归把菜单路由前缀传播给子菜单和页面叶子。"""

    normalized_nodes: list[dict[str, Any]] = []
    for index, node in enumerate(dict_items(value), start=1):
        if is_menu_node(node):
            normalized_menu_path = _resolve_menu_unique_path(
                node,
                index=index,
                root_route_prefix=root_route_prefix,
                inherited_menu_path=inherited_menu_path,
                used_menu_paths=used_menu_paths,
            )
            effective_menu_path = normalized_menu_path or inherited_menu_path or root_route_prefix
            children = _apply_frontend_page_route_hierarchy(
                node.get("children"),
                root_route_prefix=root_route_prefix,
                menu_enabled=menu_enabled,
                inherited_menu_path=effective_menu_path,
                used_menu_paths=used_menu_paths,
                used_page_paths=used_page_paths,
            )
            if not children:
                continue
            normalized_nodes.append(
                {
                    "name": str(node.get("name") or f"菜单 {index}").strip() or f"菜单 {index}",
                    "unique_path": normalized_menu_path,
                    "children": children,
                }
            )
            continue
        normalized_nodes.append(
            _normalize_page_leaf_route(
                node,
                root_route_prefix=root_route_prefix,
                menu_enabled=menu_enabled,
                inherited_menu_path=inherited_menu_path,
                used_page_paths=used_page_paths,
            )
        )
    return normalized_nodes


def _resolve_menu_unique_path(
    node: dict[str, Any],
    *,
    index: int,
    root_route_prefix: str,
    inherited_menu_path: str,
    used_menu_paths: set[str],
) -> str:
    """把菜单 unique_path 归一到根路由和父菜单路由之下。"""

    if "unique_path" in node:
        raw_unique_path = str(node.get("unique_path") or "").strip()
        if not raw_unique_path:
            return ""
    else:
        raw_unique_path = _menu_path_from_label(node.get("name") or f"menu-{index}")
    base_prefix = inherited_menu_path or root_route_prefix
    candidate = _resolve_child_route(raw_unique_path, base_prefix=base_prefix)
    return _unique_menu_path(candidate, used_menu_paths) if candidate else ""


def _normalize_page_leaf_route(
    node: dict[str, Any],
    *,
    root_route_prefix: str,
    menu_enabled: bool,
    inherited_menu_path: str,
    used_page_paths: set[str],
) -> dict[str, Any]:
    """确保页面叶子路由遵守根路由和父菜单路由前缀。"""

    normalized = dict(node)
    page_id = str(normalized.get("pageId") or normalized.get("id") or "").strip() or "page"
    current_path = _normalize_route_text(normalized.get("path") or _path_from_pageId(page_id))
    if menu_enabled and root_route_prefix and current_path == root_route_prefix:
        current_path = _join_route_paths(root_route_prefix, _menu_leaf_path_from_pageId(page_id))
    if inherited_menu_path:
        if current_path != inherited_menu_path and not current_path.startswith(f"{inherited_menu_path}/"):
            leaf_segment = _page_leaf_segment(
                current_path,
                page_id=page_id,
                root_route_prefix=root_route_prefix,
            )
            current_path = _join_route_paths(inherited_menu_path, leaf_segment)
    elif root_route_prefix and current_path != root_route_prefix and not current_path.startswith(f"{root_route_prefix}/"):
        leaf_segment = _page_leaf_segment(
            current_path,
            page_id=page_id,
            root_route_prefix="",
        )
        current_path = _join_route_paths(root_route_prefix, leaf_segment)
    normalized["path"] = _unique_page_path(current_path, page_id, used_page_paths)
    return normalized


def _infer_root_route_prefix(pages: Iterable[dict[str, Any]]) -> str:
    """从平铺页面路由中推断统一的页面根路由前缀。"""

    path_segments = [
        _route_segments(page.get("path"))
        for page in pages
        if _route_segments(page.get("path"))
    ]
    if not path_segments:
        return ""
    common = list(path_segments[0])
    for segments in path_segments[1:]:
        length = 0
        while length < len(common) and length < len(segments) and common[length] == segments[length]:
            length += 1
        common = common[:length]
        if not common:
            return ""
    return f"/{'/'.join(common)}" if common else ""


def _page_leaf_segment(path: str, *, page_id: str, root_route_prefix: str) -> str:
    """从页面当前路由中提取叶子段，供挂载到菜单路由之后使用。"""

    normalized = _normalize_route_text(path)
    if root_route_prefix and normalized.startswith(f"{root_route_prefix}/"):
        normalized = normalized[len(root_route_prefix) :]
    segments = _route_segments(normalized)
    if segments:
        return segments[-1]
    fallback = _route_segments(_path_from_pageId(page_id))
    return fallback[-1] if fallback else page_id


def _unique_page_path(path: str, page_id: str, used_paths: set[str]) -> str:
    """为页面叶子生成稳定且唯一的最终路由。"""

    normalized = _normalize_route_text(path) or _path_from_pageId(page_id)
    if normalized not in used_paths:
        used_paths.add(normalized)
        return normalized

    candidate = _path_from_pageId(page_id)
    if candidate == "/" or candidate in used_paths:
        base = candidate if candidate != "/" else f"/{page_id.replace('_', '-').lower() or 'page'}"
        suffix = 2
        candidate = base
        while candidate in used_paths:
            candidate = f"{base}-{suffix}"
            suffix += 1
    used_paths.add(candidate)
    return candidate


def _resolve_child_route(path: str, *, base_prefix: str) -> str:
    """把相对子路由解析到父路由前缀之下。"""

    normalized = _normalize_route_text(path)
    if not normalized:
        return ""
    if base_prefix and (
        normalized == base_prefix or normalized.startswith(f"{base_prefix}/")
    ):
        return normalized
    return _join_route_paths(base_prefix, normalized) if base_prefix else normalized


def _join_route_paths(base: str, path: str) -> str:
    """按 URL 规则拼接父子路由，避免重复斜杠。"""

    normalized_base = _normalize_route_text(base)
    normalized_path = _normalize_route_text(path)
    if not normalized_base:
        return normalized_path
    if not normalized_path or normalized_path == "/":
        return normalized_base
    joined = f"{normalized_base.rstrip('/')}/{normalized_path.lstrip('/')}"
    return _normalize_route_text(joined)


def _normalize_route_text(value: Any) -> str:
    """把任意路由文本规范成单前导斜杠的形式。"""

    text = str(value or "").strip()
    if not text:
        return ""
    if text != "/" and not text.startswith("/"):
        text = f"/{text}"
    normalized = re.sub(r"/{2,}", "/", text)
    return normalized.rstrip("/") or "/"


def _route_segments(value: Any) -> list[str]:
    """把路由拆成不含空值的稳定路径段列表。"""

    normalized = _normalize_route_text(value)
    if not normalized or normalized == "/":
        return []
    return [segment for segment in normalized.strip("/").split("/") if segment]
