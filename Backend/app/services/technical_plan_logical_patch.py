"""在内存工作副本上执行稳定身份路径的通用 JSON 操作。"""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any

from app.domain.technical_plan_changeset import LogicalOperation

_IDENTITY_KEYS = ("id", "pageId", "actionId", "stepId", "itemId", "ruleId", "endpoint_id", "endpointId", "resourceKey", "name", "key", "label")


def canonical_json(value: Any) -> str:
    """按 JSON 语义比较值，忽略对象键顺序，拒绝非 JSON 数值。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def path_segments(path: str) -> list[str]:
    """解析严格 JSON Pointer 风格路径，不允许根替换、空段或非法转义。"""

    if not isinstance(path, str) or not path.startswith("/") or len(path) <= 1:
        raise ValueError(f"修改路径必须指向根对象内的具体节点：{path!r}。")
    segments = path[1:].split("/")
    if any(not segment or re.search(r"~(?![01])", segment) for segment in segments):
        raise ValueError(f"修改路径含空段或非法转义：{path}。")
    return [segment.replace("~1", "/").replace("~0", "~") for segment in segments]


def item_identity(value: Any) -> str:
    """提取对象稳定身份，具备位置和名称的参数使用复合身份。"""

    if not isinstance(value, dict):
        return ""
    if value.get("in") and value.get("name"):
        return f"{value['in']}.{value['name']}"
    for key in _IDENTITY_KEYS:
        identity = value.get(key)
        if isinstance(identity, str) and identity.strip():
            return identity
    return ""


def _list_index(items: list[Any], segment: str, path: str) -> int | None:
    """只按稳定身份寻址数组，拒绝数字下标、键名前缀和歧义，不以展示墓碑充当存活目标。"""

    bracketed = len(segment) > 1 and segment.startswith("[") and segment.endswith("]")
    normalized = segment[1:-1] if bracketed else segment
    for identity_key in (*_IDENTITY_KEYS, "in"):
        prefix = f"{identity_key}="
        if normalized.startswith(prefix):
            identity = normalized[len(prefix):]
            raise ValueError(
                f"修改数组路径的身份段只写身份值本身，不能写 {segment}；请改为 {identity}：{path}。"
            )
    if normalized.isdigit():
        raise ValueError(f"修改数组必须使用稳定身份，不能使用数字下标或方括号下标：{path}。")
    matches = [i for i, item in enumerate(items) if item_identity(item) == segment]
    if len(matches) > 1:
        raise ValueError(f"修改路径匹配到多个同身份节点：{path}。")
    if not matches:
        parameter_identities = [
            item_identity(item)
            for item in items
            if isinstance(item, dict) and item.get("in") and item.get("name") == segment
        ]
        if parameter_identities:
            raise ValueError(
                f"参数路径必须使用 in.name 复合身份，不能只写参数名 {segment}；"
                f"请改用 {'、'.join(parameter_identities)}：{path}。"
            )
    return matches[0] if matches else None


def _resolve(document: Any, path: str, *, adding: bool = False) -> tuple[Any, Any, bool, Any]:
    """沿已存在的中间节点定位叶子，区分缺失键与存在且值为 null 的键。"""

    segments = path_segments(path)
    parent = document
    for segment in segments[:-1]:
        if isinstance(parent, dict) and segment in parent:
            parent = parent[segment]
        elif isinstance(parent, list):
            index = _list_index(parent, segment, path)
            if index is None:
                raise ValueError(f"修改路径的中间节点不存在：{path}。")
            parent = parent[index]
        else:
            raise ValueError(f"修改路径的中间节点不存在或不可展开：{path}。")
    leaf = segments[-1]
    if isinstance(parent, dict):
        return parent, leaf, leaf in parent, parent.get(leaf)
    if isinstance(parent, list):
        if leaf == "-":
            if adding:
                return parent, leaf, False, None
            raise ValueError(f"只有新增操作可使用数组末尾路径：{path}。")
        index = _list_index(parent, leaf, path)
        if index is not None:
            return parent, index, True, parent[index]
        if adding:
            return parent, leaf, False, None
    raise ValueError(f"修改目标不存在或父节点不可展开：{path}。")


def read_logical_value(document: dict[str, Any], path: str) -> tuple[bool, Any]:
    """读取操作后置值，目标已删除时明确返回不存在，不把 null 当作缺失。"""

    try:
        _parent, _key, exists, value = _resolve(document, path)
        return exists, value
    except ValueError:
        return False, None


def _add(parent: Any, key: Any, value: Any, path: str) -> None:
    """只新增不存在的对象键或数组身份，禁止通过新增覆盖其他节点。"""

    if isinstance(parent, dict):
        parent[key] = deepcopy(value)
    elif key == "-":
        identity = item_identity(value)
        if not identity or any(item_identity(item) == identity for item in parent):
            raise ValueError(f"追加数组项必须具有唯一稳定身份：{path}。")
        parent.append(deepcopy(value))
    else:
        if item_identity(value) != key:
            raise ValueError(f"新增列表项的稳定身份必须与路径末段一致：{path}。")
        parent.append(deepcopy(value))


def _apply(document: dict[str, Any], operation: LogicalOperation) -> None:
    """在隔离副本中完成前置核对和写入，错误由外层丢弃整个副本。"""

    source = operation.from_path if operation.op == "move" else operation.path
    parent, key, exists, current = _resolve(document, source, adding=operation.op == "add")
    if operation.op == "add":
        if exists:
            raise ValueError(f"add 目标已经存在，请使用 replace：{source}。")
        _add(parent, key, operation.value, source)
        return
    if not exists:
        raise ValueError(f"修改目标不存在：{source}。")
    expected = operation.value if operation.op == "test" else operation.expected_before
    if canonical_json(expected) != canonical_json(current):
        raise ValueError(f"{operation.op} 的前置值与当前内容不一致：{source}。")
    if operation.op == "test":
        return
    if operation.op == "replace":
        if isinstance(parent, list) and item_identity(current) != item_identity(operation.value):
            raise ValueError(f"替换数组项不能隐式改写稳定身份，请显式修改身份字段：{source}。")
        parent[key] = deepcopy(operation.value)
        return
    if operation.op == "move":
        target_segments, source_segments = path_segments(operation.path), path_segments(source)
        if target_segments[:len(source_segments)] == source_segments:
            raise ValueError(f"move 操作不能移动到自身或自身内部：{source}。")
        target_parent, target_key, target_exists, _value = _resolve(document, operation.path, adding=True)
        if target_exists:
            raise ValueError(f"move 目标已经存在：{operation.path}。")
        # 先移除再追加；即便追加失败，调用层也不会把本副本暴露给原文档。
        value = deepcopy(current)
        if isinstance(parent, dict):
            del parent[key]
        else:
            parent.pop(key)
        _add(target_parent, target_key, value, operation.path)
        return
    if isinstance(parent, dict):
        del parent[key]
    else:
        parent.pop(key)


def apply_logical_operation(document: dict[str, Any], operation: LogicalOperation) -> None:
    """原子执行单个操作，任何定位或写入失败都不修改传入文档。"""

    working = deepcopy(document)
    _apply(working, operation)
    document.clear()
    document.update(working)
