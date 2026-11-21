"""从共享前端/后端文件中精确移除单个接口实现，保留同文件其它接口。"""

from __future__ import annotations

import re
from typing import Any

from tree_sitter_language_pack import get_parser


def _matching_delimiter(source: str, start: int, opening: str, closing: str) -> int | None:
    """返回配对分隔符位置，忽略字符串、模板字符串和注释中的分隔符。"""

    depth = 0
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    index = start
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and following == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and following == "*":
            block_comment = True
            index += 2
            continue
        if char in {'"', "'", "`"}:
            quote = char
            index += 1
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _trim_removal_span(content: str, start: int, end: int) -> tuple[int, int]:
    """把紧邻的 JSDoc/JavaDoc、行首缩进和尾部空行纳入删除范围。"""

    line_start = content.rfind("\n", 0, start) + 1
    comment_start = content.rfind("/**", 0, line_start)
    comment_end = content.rfind("*/", 0, line_start)
    if (
        comment_start >= 0
        and comment_end > comment_start
        and not content[comment_end + 2:line_start].strip()
    ):
        start = comment_start
    else:
        start = line_start
    cursor = end
    while cursor < len(content) and content[cursor] in " \t":
        cursor += 1
    if cursor < len(content) and content[cursor] == "\r":
        cursor += 1
    if cursor < len(content) and content[cursor] == "\n":
        cursor += 1
    # 再多吸收一个空行，避免删除后留下连续大段空白。
    blank_end = cursor
    while blank_end < len(content) and content[blank_end] in " \t":
        blank_end += 1
    if blank_end < len(content) and content[blank_end] in "\r\n":
        cursor = blank_end + 1
        if blank_end < len(content) and content[blank_end] == "\r" and cursor < len(content) and content[cursor] == "\n":
            cursor += 1
    return start, cursor


def _typescript_function_span(content: str, function_name: str) -> tuple[int, int] | None:
    """定位一个导出的 TypeScript 函数及其注释，无法安全解析时返回 None。"""

    matches = list(re.finditer(
        rf"\bexport\s+(?:async\s+)?function\s+{re.escape(function_name)}\s*\(",
        content,
    ))
    if len(matches) != 1:
        return None
    declaration = matches[0]
    parameters_open = content.find("(", declaration.start(), declaration.end())
    if parameters_open < 0:
        return None
    parameters_close = _matching_delimiter(content, parameters_open, "(", ")")
    if parameters_close is None:
        return None
    body_open = -1
    angle_depth = 0
    for index in range(parameters_close + 1, len(content)):
        char = content[index]
        if char == "<":
            angle_depth += 1
        elif char == ">" and angle_depth > 0:
            angle_depth -= 1
        elif char == "{" and angle_depth == 0:
            body_open = index
            break
        elif char == ";":
            return None
    if body_open < 0:
        return None
    body_close = _matching_delimiter(content, body_open, "{", "}")
    if body_close is None:
        return None
    return _trim_removal_span(content, declaration.start(), body_close + 1)


def remove_typescript_function(content: str, function_name: str) -> str | None:
    """移除一个 TypeScript 请求函数；未找到唯一且完整的函数时返回 None。"""

    span = _typescript_function_span(content, function_name)
    if span is None:
        return None
    start, end = span
    return content[:start] + content[end:]


def _java_method_span(content: str, function_name: str) -> tuple[int, int] | None:
    """用 Java AST 定位唯一同名方法，包含注解与紧邻 JavaDoc。"""

    source = content.encode("utf-8")
    root = get_parser("java").parse(source).root_node
    if root.has_error:
        return None
    matches: list[Any] = []

    def walk(node: Any) -> None:
        """递归查找目标方法声明。"""

        if node.type == "method_declaration":
            name = node.child_by_field_name("name")
            if name is not None and source[name.start_byte:name.end_byte].decode("utf-8") == function_name:
                matches.append(node)
        for child in node.children:
            walk(child)

    walk(root)
    if len(matches) != 1:
        return None
    method = matches[0]
    # tree-sitter 返回 UTF-8 字节偏移，Python 字符串切片使用字符索引；
    # 中文注释或文案存在时必须转换，否则会误删相邻方法。
    start = len(source[:method.start_byte].decode("utf-8"))
    end = len(source[:method.end_byte].decode("utf-8"))
    return _trim_removal_span(content, start, end)


def remove_java_function(content: str, function_name: str) -> str | None:
    """移除一个 Java 方法及其注解和注释；无法安全解析时返回 None。"""

    span = _java_method_span(content, function_name)
    if span is None:
        return None
    start, end = span
    return content[:start] + content[end:]
