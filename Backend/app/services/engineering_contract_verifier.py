from __future__ import annotations

from pathlib import Path
import re
from typing import Any


_METHOD_TOKENS = {
    "GET": ("service.get", "@GetMapping", "RequestMethod.GET"),
    "POST": ("service.post", "@PostMapping", "RequestMethod.POST"),
    "PUT": ("service.put", "@PutMapping", "RequestMethod.PUT"),
    "PATCH": ("service.patch", "@PatchMapping", "RequestMethod.PATCH"),
    "DELETE": ("service.delete", "@DeleteMapping", "RequestMethod.DELETE"),
}

_MAX_BACKEND_MODEL_REFERENCE_DEPTH = 4
_MAX_BACKEND_MODEL_FILES = 64


def verify_contract_binding(
    check: dict[str, Any],
    *,
    kind: str,
    root: Path | None,
) -> tuple[str | None, str]:
    """按前端 API、页面绑定或后端 JSON 模型层级验证接口契约。"""

    if root is None:
        return "契约检查缺少工作区根目录。", "无法读取生成代码。"
    root = root.resolve()
    source, api_source, backend_model_source = _source_text(
        root,
        _string_list(check.get("target_paths")),
    )
    if not source:
        return "契约检查未找到可读取的生成代码。", "任务目标没有可检查的文本源文件。"
    endpoints = _dict_items(_dict_value(check.get("expected")).get("endpoints"))
    errors: list[str] = []
    for endpoint in endpoints:
        method = str(endpoint.get("method") or "").upper()
        path = str(endpoint.get("path") or "")
        if kind == "backend_contract_binding":
            if not _contains_method(source, method, backend=True):
                errors.append(f"缺少 {method} 的 Spring Mapping")
            if path and not _contains_path(source, path):
                errors.append(f"缺少接口路径 {path}")
            fields = _combined_schema_fields(endpoint)
            field_source = backend_model_source or source
            missing_fields = [
                field
                for field in fields
                if not _backend_wire_field_present(root, field_source, source, field)
            ]
        elif api_source:
            if str(endpoint.get("source_type") or "") not in {
                "mock",
                "static",
                "none",
            }:
                if not _contains_method(api_source, method, backend=False):
                    errors.append(f"前端 API 模块缺少 service.{method.lower()}")
                if path and not _contains_path(api_source, path):
                    errors.append(f"前端 API 模块缺少接口路径 {path}")
            fields = _combined_schema_fields(endpoint)
            missing_fields = [
                field for field in fields if not _contains_identifier(api_source, field)
            ]
        else:
            fields = _string_list(endpoint.get("response_binding_fields"))
            missing_fields = [
                field for field in fields if not _contains_identifier(source, field)
            ]
        if missing_fields:
            if kind == "backend_contract_binding":
                errors.append(f"缺少 Schema JSON 映射字段：{', '.join(missing_fields)}")
            elif api_source:
                errors.append(f"前端 API 类型缺少 Schema 字段：{', '.join(missing_fields)}")
            else:
                errors.append(f"页面缺少响应绑定字段：{', '.join(missing_fields)}")
    if errors:
        return "；".join(errors) + "。", "生成代码未完整匹配已确认接口契约。"
    return None, "接口方法、路径及任务使用的 Schema 字段均有静态代码证据。"


def _combined_schema_fields(endpoint: dict[str, Any]) -> list[str]:
    """合并并去重接口请求、响应字段，避免同名字段重复报错。"""

    return _string_list(
        [
            *_string_list(endpoint.get("request_fields")),
            *_string_list(endpoint.get("response_fields")),
        ]
    )


def _source_text(root: Path, paths: list[str]) -> tuple[str, str, str]:
    """读取任务文本，并有限跟随后端传输模型的工作区内类型引用。"""

    chunks: list[str] = []
    api_chunks: list[str] = []
    backend_model_files: dict[Path, str] = {}
    for raw_path in paths:
        path = raw_path.lstrip("./")
        if any(token in path for token in ("*", "?", "[")):
            continue
        if _resolved_path_error(root, path):
            continue
        resolved = (root / path).resolve()
        if not resolved.is_file() or resolved.stat().st_size > 1_000_000:
            continue
        try:
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        chunks.append(content)
        normalized = "/" + path.replace("\\", "/")
        if "/src/apis/" in normalized:
            api_chunks.append(content)
        if _is_backend_contract_model_path(normalized):
            backend_model_files[resolved] = content
    backend_model_source = _expand_backend_model_source(root, backend_model_files)
    return "\n".join(chunks), "\n".join(api_chunks), backend_model_source


def _expand_backend_model_source(root: Path, seeds: dict[Path, str]) -> str:
    """从任务 DTO 出发有限跟随被引用 DTO，仅扩展静态验收的只读证据。"""

    chunks: list[str] = []
    visited: set[Path] = set()
    pending: list[tuple[Path, str, int]] = [
        (path, content, 0) for path, content in seeds.items()
    ]
    while pending and len(visited) < _MAX_BACKEND_MODEL_FILES:
        path, content, depth = pending.pop(0)
        resolved = path.resolve()
        if resolved in visited or _path_outside_root(root, resolved):
            continue
        visited.add(resolved)
        chunks.append(content)
        if depth >= _MAX_BACKEND_MODEL_REFERENCE_DEPTH:
            continue
        for candidate in _java_model_reference_paths(resolved, content):
            candidate_resolved = candidate.resolve()
            if candidate_resolved in visited or _path_outside_root(
                root,
                candidate_resolved,
            ):
                continue
            relative = candidate_resolved.relative_to(root).as_posix()
            if not _is_backend_contract_model_path(f"/{relative}"):
                continue
            referenced_content = _read_bounded_text(candidate_resolved)
            if referenced_content is not None:
                pending.append((candidate_resolved, referenced_content, depth + 1))
    return "\n".join(chunks)


def _java_model_reference_paths(source_path: Path, source: str) -> list[Path]:
    """解析 Java 模型实际使用的显式导入和同包类型，不进行全仓库模糊扫描。"""

    scan_source = re.sub(r"/\*.*?\*/|//[^\n]*", " ", source, flags=re.DOTALL)
    scan_source = re.sub(
        r"(?m)^\s*import\s+(?:static\s+)?[\w.]+\s*;\s*$",
        " ",
        scan_source,
    )
    referenced_names = set(re.findall(r"\b([A-Z][A-Za-z0-9_]*)\b", scan_source))
    imports = re.findall(
        r"(?m)^\s*import\s+(?!static\s+)([A-Za-z_][A-Za-z0-9_.]*)\s*;",
        source,
    )
    source_root = _java_source_root(source_path)
    candidates: list[Path] = []
    if source_root is not None:
        for qualified_name in imports:
            if qualified_name.rsplit(".", 1)[-1] not in referenced_names:
                continue
            candidates.append(
                source_root.joinpath(*qualified_name.split(".")).with_suffix(".java")
            )
    for type_name in sorted(referenced_names):
        candidates.append(source_path.parent / f"{type_name}.java")
    return list(dict.fromkeys(path for path in candidates if path.is_file()))


def _java_source_root(path: Path) -> Path | None:
    """从 Java 文件路径定位 Maven/Gradle 的 src/main/java 源码根。"""

    for parent in path.parents:
        if parent.as_posix().endswith("/src/main/java"):
            return parent
    return None


def _read_bounded_text(path: Path) -> str | None:
    """读取大小受限的 UTF-8 文本，无法确定时不把文件作为验收证据。"""

    try:
        if not path.is_file() or path.stat().st_size > 1_000_000:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _path_outside_root(root: Path, path: Path) -> bool:
    """判断解析后的只读证据路径是否越过当前工作区。"""

    return path != root and root not in path.parents


def _is_backend_contract_model_path(path: str) -> bool:
    """识别标准生成模板中的请求、响应和 DTO 模型文件。"""

    lowered = path.lower()
    filename = lowered.rsplit("/", 1)[-1]
    return (
        "/dto/" in lowered
        or filename.endswith("request.java")
        or filename.endswith("response.java")
        or filename.endswith("dto.java")
        or filename.endswith("vo.java")
    )


def _backend_wire_field_present(
    root: Path,
    model_source: str,
    all_source: str,
    field: str,
) -> bool:
    """验证 Java 模型可通过显式或全局命名策略暴露契约字段。"""

    if _contains_identifier(model_source, field):
        return True
    camel_field = _snake_to_camel(field)
    return (
        camel_field != field
        and _contains_identifier(model_source, camel_field)
        and _uses_snake_case_json_mapping(root, model_source, all_source)
    )


def _snake_to_camel(value: str) -> str:
    """把契约 snake_case 字段转换为 Java/TypeScript 常用 camelCase。"""

    head, *tail = str(value).split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _uses_snake_case_json_mapping(
    root: Path,
    model_source: str,
    all_source: str,
) -> bool:
    """识别 DTO 注解、ObjectMapper 配置或 Spring Jackson 全局 snake_case 策略。"""

    combined = f"{model_source}\n{all_source}"
    source_patterns = (
        r"@JsonNaming\s*\([^)]*SnakeCaseStrategy",
        r"PropertyNamingStrategies\.SNAKE_CASE",
        r"PropertyNamingStrategy\.SNAKE_CASE",
        r"setPropertyNamingStrategy\s*\([^)]*SNAKE_CASE",
    )
    if any(re.search(pattern, combined) for pattern in source_patterns):
        return True
    config_paths = (
        "backend/src/main/resources/application.yml",
        "backend/src/main/resources/application.yaml",
        "backend/src/main/resources/application.properties",
    )
    for path in config_paths:
        resolved = (root / path).resolve()
        if not resolved.is_file() or _resolved_path_error(root, path):
            continue
        try:
            content = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if re.search(
            r"property[-_.]naming[-_.]strategy\s*[:=]\s*['\"]?(?:SNAKE_CASE|snake_case)",
            content,
            flags=re.IGNORECASE,
        ):
            return True
    return False


def _contains_method(source: str, method: str, *, backend: bool) -> bool:
    """检查前端 service 调用或 Spring Mapping 是否包含目标 HTTP 方法。"""

    tokens = _METHOD_TOKENS.get(method, ())
    candidates = tokens[1:] if backend else tokens[:1]
    return any(token in source for token in candidates)


def _contains_path(source: str, path: str) -> bool:
    """允许路径参数由模板表达式拼接，但要求所有静态路径段出现。"""

    if path in source:
        return True
    static_segments = [
        segment
        for segment in path.strip("/").split("/")
        if segment and not (segment.startswith("{") and segment.endswith("}"))
    ]
    return bool(static_segments) and all(segment in source for segment in static_segments)


def _contains_identifier(source: str, field: str) -> bool:
    """按标识符边界检查 Schema 字段，避免普通子串误判。"""

    return re.search(rf"(?<![A-Za-z0-9_$]){re.escape(field)}(?![A-Za-z0-9_$])", source) is not None


def _resolved_path_error(root: Path, path: str) -> str | None:
    """阻止契约验收读取越过工作区的路径。"""

    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        return f"目标路径越过工作区：{path}。"
    return None


def _string_list(value: Any) -> list[str]:
    """把不可信列表规整为非空且去重的字符串列表。"""

    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip())) if isinstance(value, list) else []


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """把不可信列表规整为字典列表。"""

    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict_value(value: Any) -> dict[str, Any]:
    """把不可信对象规整为字典。"""

    return dict(value) if isinstance(value, dict) else {}
