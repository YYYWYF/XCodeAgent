"""保留原文件结构的 dotenv 和 YAML 受管字段编辑器。"""

from __future__ import annotations

import json
import re
from typing import Mapping

import yaml
from yaml.nodes import ScalarNode
from app.services.template_reconcile.config_surfaces import ConfigSurfaceError, yaml_nodes, dotenv_entries


class ParameterUpdateError(ValueError):
    """表示目标文件与受管字段合同冲突；异常不携带配置值。"""

    def __init__(self, message: str, *, target: str | None = None) -> None:
        """保存安全错误码和显式目标路径，供阶段诊断使用。"""
        super().__init__(message)
        self.code = message.split("：", 1)[0]
        self.target = target


_AUTH_BEGIN = "# BEGIN DEVAGENTSTUDIO MANAGED: login-auth"
_AUTH_END = "# END DEVAGENTSTUDIO MANAGED: login-auth"
_AUTH_FIELDS = ("client-id", "private-key", "public-key", "center-public-key")


def _newline(content: str) -> str:
    """按既有文件的换行样式生成新增行。"""

    return "\r\n" if "\r\n" in content else "\n"


def _quoted(value: str) -> str:
    """用 JSON 双引号转义安全表示 dotenv 与 YAML 字符串。"""

    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ParameterUpdateError("CONFIG_VALUE_INVALID：配置值必须为不含控制字符的字符串。")
    return json.dumps(value, ensure_ascii=False)


def edit_dotenv(original: bytes, values: Mapping[str, str]) -> bytes:
    """只替换精确的 PUBLIC Key 行，并保留其余字节与换行。"""

    try:
        content = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：frontend/.env 不是 UTF-8 文件。") from exc
    try:
        dotenv_entries(content, "frontend/.env")
    except ConfigSurfaceError as exc:
        raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：frontend/.env 存在重复键或非法行。", target="frontend/.env") from exc
    lines = content.splitlines(keepends=True)
    found: set[str] = set()
    updated: list[str] = []
    for line in lines:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*?)(\r?\n)?$", line)
        if match and match.group(1) in values:
            key = match.group(1)
            if key in found:
                raise ParameterUpdateError(f"CONFIG_TARGET_CONFLICT：frontend/.env 中 {key} 重复。")
            found.add(key)
            updated.append(f"{key}={_quoted(values[key])}{match.group(3) or ''}")
        else:
            updated.append(line)
    missing = set(values) - found
    if missing:
        raise ParameterUpdateError("CONFIG_STRUCTURE_MISSING：frontend/.env 缺少受管键。", target="frontend/.env")
    return "".join(updated).encode("utf-8")


def edit_yht_yaml(original: bytes, values: Mapping[str, str]) -> bytes:
    """只替换 application-dev.yml 已有的 yht 受管叶子，不创建任何节点。"""

    target = "backend/src/main/resources/application-dev.yml"
    try:
        content = original.decode("utf-8")
        _, nodes = yaml_nodes(content, target)
    except (UnicodeError, ConfigSurfaceError) as exc:
        raise ParameterUpdateError(f"CONFIG_TARGET_CONFLICT：{target} 无法安全定位 YAML。") from exc
    replacements: list[tuple[int, int, str]] = []
    for path, value in values.items():
        node = nodes.get(path)
        if node is None:
            raise ParameterUpdateError(f"CONFIG_STRUCTURE_MISSING：{path} 缺少受管配置结构。", target=path)
        # 仅允许唯一块式映射下的单行字符串标量，禁止多行、对象及猜测定位。
        if not isinstance(node, ScalarNode) or node.tag != "tag:yaml.org,2002:str" or node.style in {"|", ">"} or node.start_mark.line != node.end_mark.line:
            raise ParameterUpdateError(f"CONFIG_TARGET_CONFLICT：{path} 无法安全定位标量。", target=path)
        quoted = _quoted(value)
        if node.value != value:
            replacements.append((node.start_mark.index, node.end_mark.index, quoted))
    replacements.sort()
    if any(left[1] > right[0] for left, right in zip(replacements, replacements[1:])):
        raise ParameterUpdateError(f"CONFIG_TARGET_CONFLICT：{target} 文本区间重叠。")
    result = content
    # 仅替换标量 token，保留键、空格、CRLF 和行尾注释，倒序避免偏移失效。
    for start, end, replacement in reversed(replacements):
        result = result[:start] + replacement + result[end:]
    try:
        _, projected = yaml_nodes(result, target)
    except ConfigSurfaceError as exc:
        raise ParameterUpdateError(f"CONFIG_TARGET_CONFLICT：{target} 更新后结构无效。") from exc
    for path, value in values.items():
        node = projected.get(path)
        if not isinstance(node, ScalarNode) or node.tag != "tag:yaml.org,2002:str" or node.value != value:
            raise ParameterUpdateError(f"CONFIG_TARGET_CONFLICT：{path} 更新后校验失败。")
    return result.encode("utf-8")


def edit_auth_yaml(original: bytes, values: Mapping[str, str]) -> bytes:
    """仅更新唯一 root auth 中四个叶子，保留其他 YAML 原文。"""

    try:
        content = original.decode("utf-8")
        parsed = yaml.safe_load(content)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：application.yml 无法安全解析。") from exc
    if parsed is not None and not isinstance(parsed, dict):
        raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：application.yml 根必须是映射。")
    roots = list(re.finditer(r"(?m)^auth\s*:\s*(?:#.*)?(?:\r?\n|$)", content))
    if len(roots) > 1 or len(re.findall(r"(?m)^auth\s*:", content)) > 1:
        raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：application.yml 含重复 root auth。")
    if content.count(_AUTH_BEGIN) != content.count(_AUTH_END) or content.count(_AUTH_BEGIN) > 1:
        raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：login-auth 受管标记无效。")
    if _AUTH_BEGIN in content:
        start = content.index(_AUTH_BEGIN)
        end = content.index(_AUTH_END) + len(_AUTH_END)
        block = content[start:end]
        if len(roots) != 1 or not start <= roots[0].start() < end:
            raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：受管标记未包围唯一 auth 根。")
        replaced = _replace_auth_leaves(block, values)
        result = content[:start] + replaced + content[end:]
    elif roots:
        start = roots[0].start()
        next_root = re.search(r"(?m)^[A-Za-z_][A-Za-z0-9_-]*\s*:", content[roots[0].end():])
        end = roots[0].end() + next_root.start() if next_root else len(content)
        block = content[start:end]
        # 旧模板迁移只接管完全匹配的四个 auth 叶子，避免接管用户自定义项。
        keys = re.findall(r"(?m)^  ([A-Za-z][A-Za-z0-9-]*)\s*:", block)
        if len(keys) != 4 or set(keys) != set(_AUTH_FIELDS):
            raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：无受管标记的 auth 含未知或重复字段。")
        replaced = _replace_auth_leaves(block, values)
        newline = _newline(content)
        result = content[:start] + _AUTH_BEGIN + newline + replaced.rstrip("\r\n") + newline + _AUTH_END + newline + content[end:]
    else:
        if parsed and "auth" in parsed:
            raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：auth 根位置无法确认。")
        newline = _newline(content)
        block = _AUTH_BEGIN + newline + "auth:" + newline
        for key in _AUTH_FIELDS:
            block += f"  {key}: {_quoted(values[key])}{newline}"
        block += _AUTH_END + newline
        result = content + (newline if content and not content.endswith(("\n", "\r")) else "") + block
    try:
        projected = yaml.safe_load(result)
    except yaml.YAMLError as exc:
        raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：更新后 application.yml 无效。") from exc
    if not isinstance(projected, dict) or not isinstance(projected.get("auth"), dict):
        raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：更新后 auth 结构无效。")
    if any(projected["auth"].get(key) != values[key] for key in _AUTH_FIELDS):
        raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：auth 字段校验失败。")
    return result.encode("utf-8")


def _replace_auth_leaves(block: str, values: Mapping[str, str]) -> str:
    """在已界定的 auth 块内精确替换四个叶子。"""

    result = block
    for key in _AUTH_FIELDS:
        matches = list(re.finditer(rf"(?m)^(  {re.escape(key)}\s*:\s*).*?(\r?\n|$)", result))
        if len(matches) != 1:
            raise ParameterUpdateError(f"CONFIG_TARGET_CONFLICT：auth.{key} 缺失或重复。")
        match = matches[0]
        result = result[:match.start()] + f"  {key}: {_quoted(values[key])}{match.group(2)}" + result[match.end():]
    return result
