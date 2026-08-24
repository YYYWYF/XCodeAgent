"""业务验收检查器共享的安全文件读取和结果工具。"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from app.services.business_acceptance import normalize_repo_path


MAX_FILE_BYTES = 512_000
MAX_FILES = 40


def verification_result(
    status: str,
    evidence: str,
    *,
    facts: dict[str, Any] | None = None,
    observations: list[str] | None = None,
) -> dict[str, Any]:
    """构造统一的 passed、failed 或 blocked 机器可读结果。"""

    safe_status = status if status in {"passed", "failed", "blocked"} else "blocked"
    return {
        "status": safe_status,
        "evidence": str(evidence or "")[:4_000],
        "facts": facts if isinstance(facts, dict) else {},
        "observations": [str(item)[:1_000] for item in observations or [] if str(item).strip()][:20],
    }


def read_target_files(
    check: dict[str, Any],
    workspace_root: str | Path | None,
) -> tuple[dict[str, str], list[str]]:
    """只读取当前业务检查声明的目标文件，并阻止路径越过工作区。"""

    if not workspace_root:
        return {}, ["缺少工作区根目录，无法执行业务检查。"]
    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        return {}, [f"工作区根目录不存在：{root}"]
    files: dict[str, str] = {}
    errors: list[str] = []
    target_paths = check.get("target_paths") if isinstance(check.get("target_paths"), list) else []
    for raw_path in target_paths[:MAX_FILES]:
        path_text = normalize_repo_path(raw_path)
        if not path_text or _unsafe_relative_path(raw_path):
            errors.append(f"业务检查目标路径不安全：{raw_path}")
            continue
        candidates = _expand_path(root, path_text)
        if not candidates:
            errors.append(f"业务检查目标文件不存在：{path_text}")
            continue
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved != root and root not in resolved.parents:
                errors.append(f"业务检查目标路径越过工作区：{path_text}")
                continue
            if not resolved.is_file():
                errors.append(f"业务检查目标不是文件：{path_text}")
                continue
            relative = normalize_repo_path(resolved.relative_to(root).as_posix())
            try:
                if resolved.stat().st_size > MAX_FILE_BYTES:
                    errors.append(f"业务检查文件超过大小限制：{relative}")
                    continue
                files[relative] = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                errors.append(f"业务检查读取文件失败 {relative}：{type(exc).__name__}")
    if len(files) > MAX_FILES:
        errors.append(f"业务检查读取文件数超过限制：{MAX_FILES}")
    return files, errors


def strip_comments(source: str) -> str:
    """移除 Java/TypeScript 注释并保留字符串，避免注释文本造成假阳性。"""

    result: list[str] = []
    index = 0
    state = "code"
    quote = ""
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if state == "line_comment":
            if char in "\r\n":
                result.append(char)
                state = "code"
            index += 1
            continue
        if state == "block_comment":
            if char == "*" and next_char == "/":
                result.extend("  ")
                index += 2
                state = "code"
            else:
                result.append("\n" if char in "\r\n" else " ")
                index += 1
            continue
        if state == "string":
            result.append(char)
            if char == "\\" and next_char:
                result.append(next_char)
                index += 2
                continue
            if char == quote:
                state = "code"
            index += 1
            continue
        if char in {'"', "'", "`"}:
            state = "string"
            quote = char
            result.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            result.extend("  ")
            index += 2
            state = "line_comment"
            continue
        if char == "/" and next_char == "*":
            result.extend("  ")
            index += 2
            state = "block_comment"
            continue
        result.append(char)
        index += 1
    return "".join(result)


def balanced_delimiters(source: str) -> bool:
    """检查源码括号是否基本平衡，作为结构化读取的最低安全门槛。"""

    cleaned = strip_comments(source)
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{",
    }
    for char in cleaned:
        if char in "([{":
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return False
    return not stack


def find_type_shape(source: str, type_name: str) -> dict[str, Any] | None:
    """从有限 TypeScript interface/type 声明中提取嵌套字段结构，排除注释命中。"""

    if not type_name:
        return None
    clean = strip_comments(source)
    declared = _find_type_shape(clean, type_name, set())
    if declared is not None:
        return declared
    expression = str(type_name).strip()
    if (
        expression.endswith("[]")
        or re.fullmatch(r"(?:Array|ReadonlyArray)\s*<.+>", expression, re.DOTALL)
        or expression.startswith("{")
        or normalize_type_name(expression)
        in {"string", "number", "boolean", "null", "undefined", "union", "object", "array"}
    ):
        return _type_shape_from_expression(clean, expression, set())
    return None


def _find_type_shape(source: str, type_name: str, visited: set[str]) -> dict[str, Any] | None:
    """递归解析 TypeScript 类型并限制循环引用深度。"""

    name = str(type_name or "").strip()
    if not name or name in visited or len(visited) > 8:
        return None
    enum_declaration = re.search(
        rf"\benum\s+{re.escape(name)}\b\s*\{{(?P<body>[^}}]*)\}}",
        source,
        re.DOTALL,
    )
    if enum_declaration:
        body = enum_declaration.group("body")
        values = re.findall(r"=\s*['\"]([^'\"]+)['\"]", body)
        if not values:
            values = [
                token
                for token in re.findall(r"\b[A-Za-z_$][\w$]*\b", body)
                if token not in {"true", "false"}
            ]
        return {"type": "union", "enum": list(dict.fromkeys(values))}
    declaration = re.search(
        rf"\b(?:interface|type)\s+{re.escape(name)}\b\s*(?:=\s*)?",
        source,
    )
    if not declaration:
        return None
    start = declaration.end()
    semicolon = source.find(";", start)
    next_brace = source.find("{", start)
    brace_start = (
        next_brace
        if next_brace >= 0 and (semicolon < 0 or next_brace < semicolon)
        else -1
    )
    if brace_start >= 0:
        body = _balanced_block(source, brace_start)
        if body is not None:
            properties: dict[str, Any] = {}
            next_visited = {*visited, name}
            for field_name, optional, raw_type in _object_fields(body):
                shape = _type_shape_from_expression(source, raw_type, next_visited)
                shape["required"] = not optional
                properties[field_name] = shape
            return {"type": "object", "properties": properties}
    alias_match = re.match(r"([^;\n]+)", source[start:])
    if not alias_match:
        return None
    return _type_shape_from_expression(source, alias_match.group(1).strip(), {*visited, name})


def _type_shape_from_expression(
    source: str,
    raw_type: str,
    visited: set[str],
) -> dict[str, Any]:
    """把 TypeScript 类型表达式投射为可比较的对象、数组、枚举或标量结构。"""

    text = str(raw_type or "").strip()
    if text.endswith("[]"):
        return {
            "type": "array",
            "items": _type_shape_from_expression(source, text[:-2], visited),
        }
    array_match = re.fullmatch(r"(?:Array|ReadonlyArray)\s*<(.+)>", text, re.DOTALL)
    if array_match:
        return {
            "type": "array",
            "items": _type_shape_from_expression(source, array_match.group(1), visited),
        }
    if text.startswith("{") and text.endswith("}"):
        properties: dict[str, Any] = {}
        for field_name, optional, field_type in _object_fields(text[1:-1]):
            shape = _type_shape_from_expression(source, field_type, visited)
            shape["required"] = not optional
            properties[field_name] = shape
        return {"type": "object", "properties": properties}
    enum_values = re.findall(r"['\"]([^'\"]+)['\"]", text)
    if enum_values and all(token.strip(" '\"") in enum_values for token in text.split("|")):
        return {"type": "union", "enum": enum_values}
    primitive = normalize_type_name(text)
    if primitive in {"string", "number", "boolean", "null", "undefined", "union", "object", "array"}:
        return {"type": primitive}
    resolved = _find_type_shape(source, text, visited)
    if resolved is not None:
        return resolved
    return {"type": primitive}


def _balanced_block(source: str, opening_index: int) -> str | None:
    """读取从指定大括号开始的平衡代码块正文。"""

    depth = 0
    for index in range(opening_index, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[opening_index + 1 : index]
    return None


def _object_fields(body: str) -> list[tuple[str, bool, str]]:
    """按顶层分隔符拆解接口或对象类型字段。"""

    chunks: list[str] = []
    start = 0
    brace_depth = bracket_depth = paren_depth = angle_depth = 0
    for index, char in enumerate(body):
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(brace_depth - 1, 0)
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(bracket_depth - 1, 0)
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(paren_depth - 1, 0)
        elif char == "<":
            angle_depth += 1
        elif char == ">":
            angle_depth = max(angle_depth - 1, 0)
        if char in {";", ",", "\n"} and not any(
            (brace_depth, bracket_depth, paren_depth, angle_depth)
        ):
            chunks.append(body[start:index])
            start = index + 1
    chunks.append(body[start:])
    result: list[tuple[str, bool, str]] = []
    for chunk in chunks:
        match = re.match(
            r"\s*([A-Za-z_$][\w$]*)\s*(\?)?\s*:\s*(.*?)\s*$",
            chunk,
            re.DOTALL,
        )
        if match:
            result.append((match.group(1), match.group(2) == "?", match.group(3)))
    return result


def normalize_type_name(value: Any) -> str:
    """把 TypeScript 类型表达式归一为可比较的结构类型。"""

    text = str(value or "").strip()
    if text.endswith("[]") or text.startswith("Array<"):
        return "array"
    if text in {"string", "String"}:
        return "string"
    if text in {"number", "Number", "bigint"}:
        return "number"
    if text in {"boolean", "Boolean"}:
        return "boolean"
    if text in {"null", "undefined"}:
        return text
    if "|" in text:
        return "union"
    if text.startswith("{"):
        return "object"
    return text


def shape_matches(expected: Any, actual: Any) -> bool:
    """比较字段结构的关键语义，允许正式类型名和实现类型别名不同。"""

    if not isinstance(expected, dict):
        return True
    if not isinstance(actual, dict):
        return False
    expected_type = expected.get("type")
    actual_type = actual.get("type")
    if expected_type and actual_type and not _shape_type_matches(expected_type, actual_type):
        return False
    expected_properties = expected.get("properties") if isinstance(expected.get("properties"), dict) else {}
    actual_properties = actual.get("properties") if isinstance(actual.get("properties"), dict) else {}
    for name, expected_field in expected_properties.items():
        actual_field = actual_properties.get(name)
        if not isinstance(actual_field, dict):
            return False
        if "required" in expected_field and actual_field.get("required") != expected_field.get("required"):
            return False
        if (
            expected_field.get("type")
            and not _shape_type_matches(expected_field.get("type"), actual_field.get("type"))
            and not (
                isinstance(expected_field.get("enum"), list)
                and actual_field.get("type") == "union"
            )
        ):
            return False
        if isinstance(expected_field.get("enum"), list):
            if set(actual_field.get("enum") or []) != set(expected_field.get("enum") or []):
                return False
        if expected_field.get("properties") and not shape_matches(expected_field, actual_field):
            return False
        if expected_field.get("items") and not shape_matches(
            {"type": "object", **expected_field.get("items", {})}
            if isinstance(expected_field.get("items"), dict)
            else expected_field.get("items"),
            actual_field.get("items"),
        ):
            return False
    return True


def _shape_type_matches(expected: Any, actual: Any) -> bool:
    """归一 JSON Schema 与 TypeScript 的等价标量类型。"""

    aliases = {"integer": "number"}
    expected_name = aliases.get(str(expected or ""), str(expected or ""))
    actual_name = aliases.get(str(actual or ""), str(actual or ""))
    return expected_name == actual_name


def _expand_path(root: Path, path_text: str) -> list[Path]:
    """展开精确或受限通配路径，避免递归读取任务范围之外的文件。"""

    if any(token in path_text for token in "*?["):
        try:
            return list(root.glob(path_text))[:MAX_FILES]
        except (OSError, ValueError):
            return []
    exact = root / path_text
    if exact.exists():
        return [exact]
    # 任务路径来自跨平台 DAG，目录大小写在 Windows/macOS/Linux 工作区上可能不同；
    # 仅按每一级目录的大小写不敏感名称回溯，不放宽到工作区之外。
    resolved = _resolve_case_insensitive(root, path_text)
    return [resolved] if resolved is not None else []


def _resolve_case_insensitive(root: Path, path_text: str) -> Path | None:
    """按目录层级大小写不敏感解析相对路径，兼容不同文件系统大小写策略。"""

    current = root
    for part in path_text.replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        direct = current / part
        if direct.exists():
            current = direct
            continue
        try:
            matches = [
                child
                for child in current.iterdir()
                if child.name.casefold() == part.casefold()
            ]
        except OSError:
            return None
        if len(matches) != 1:
            return None
        current = matches[0]
    return current if current.exists() else None


def _unsafe_relative_path(value: Any) -> bool:
    """识别绝对路径、drive 路径和 parent traversal。"""

    text = str(value or "").strip().replace("\\", "/")
    return (
        text.startswith("/")
        or text.startswith("//")
        or bool(re.match(r"^[A-Za-z]:/", text))
        or ".." in normalize_repo_path(text).split("/")
    )
