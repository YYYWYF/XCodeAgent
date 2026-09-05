"""为 V2 Strategy 提供基于 Tree-sitter 的受限结构定位。"""

from __future__ import annotations

from typing import Any

from tree_sitter_language_pack import get_parser


class StrategyAstV2Error(ValueError):
    """表示结构化 Strategy 无法唯一、安全地定位 AST 目标。"""


def insert_after_last_import(source_path: str, content: str, insertion: str) -> str:
    """在 TypeScript/Java AST 的最后一个 import 声明后插入内容。"""

    language, root, raw = _parse(source_path, content)
    # Java 与 TypeScript 系列的 Tree-sitter import 节点名称不同，必须按实际语法识别。
    import_node_types = {"import_declaration"} if language == "java" else {"import_statement"}
    imports = [node for node in _walk(root) if node.type in import_node_types]
    if not imports:
        raise StrategyAstV2Error("AST_IMPORT_TARGET_MISSING：目标缺少 import 声明。")
    position = max(node.end_byte for node in imports)
    return _splice(raw, position, "\n" + insertion).decode("utf-8")


def insert_at_selector(source_path: str, content: str, selector: dict[str, Any], insertion: str) -> str:
    """在唯一 AST selector 的开头、结尾或容器闭合前插入结构化代码。"""

    node_type = selector.get("nodeType")
    position = selector.get("position")
    name = selector.get("name")
    if not isinstance(node_type, str) or not node_type or position not in {"before", "after", "beforeEnd"}:
        raise StrategyAstV2Error("AST_SELECTOR_INVALID：selector 必须含 nodeType 和受限 position。")
    if name is not None and (not isinstance(name, str) or not name):
        raise StrategyAstV2Error("AST_SELECTOR_INVALID：selector.name 必须是非空字符串。")
    _, root, raw = _parse(source_path, content)
    matches = [node for node in _walk(root) if node.type == node_type and _matches_name(node, raw, name)]
    if len(matches) != 1:
        raise StrategyAstV2Error("AST_SELECTOR_AMBIGUOUS：Strategy 必须命中唯一 AST 节点。")
    target = matches[0]
    if position == "before":
        offset = target.start_byte
    elif position == "after":
        offset = target.end_byte
    else:
        if target.end_byte <= target.start_byte + 1:
            raise StrategyAstV2Error("AST_SELECTOR_INVALID：目标节点不能作为 beforeEnd 容器。")
        offset = target.end_byte - 1
    return _splice(raw, offset, insertion).decode("utf-8")


def _parse(source_path: str, content: str):
    """按扩展名解析 Java、TypeScript 或 TSX，并拒绝含语法错误的源文件。"""

    lower = source_path.casefold()
    language = "java" if lower.endswith(".java") else "tsx" if lower.endswith(".tsx") else "typescript"
    raw = content.encode("utf-8")
    root = get_parser(language).parse(raw).root_node
    if root.has_error:
        raise StrategyAstV2Error("AST_PARSE_FAILED：目标文件存在语法错误。")
    return language, root, raw


def _walk(node: Any):
    """深度优先遍历 AST 的具名节点。"""

    yield node
    for child in node.named_children:
        yield from _walk(child)


def _matches_name(node: Any, source: bytes, expected: str | None) -> bool:
    """按 AST name 字段过滤候选节点，未指定名称时保留候选。"""

    if expected is None:
        return True
    name = node.child_by_field_name("name")
    return name is not None and source[name.start_byte:name.end_byte].decode("utf-8") == expected


def _splice(source: bytes, offset: int, insertion: str) -> bytes:
    """在 Tree-sitter 返回的字节边界拼接 UTF-8 插入内容。"""

    return source[:offset] + insertion.encode("utf-8") + source[offset:]
