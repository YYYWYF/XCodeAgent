"""按 YAML 路径补入模板配置片段，同时保留目标文件的原始文本。"""

from __future__ import annotations

from collections import defaultdict

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode
from app.services.template_reconcile.config_surfaces import ConfigSurfaceError, yaml_nodes


class CapabilityYamlMergeError(ValueError):
    """表示模板配置片段不能安全合并到已有 YAML。"""


def merge_capability_yaml(current: str, addition: str) -> str:
    """只补缺失的映射键，不覆盖已有值或重写无关配置。"""

    try:
        # 仅为 AST 补齐 EOF 行边界，原文件无实际插入时仍保持原字节。
        target_text = current if current.endswith("\n") else current + "\n"
        fragment_text = addition if addition.endswith("\n") else addition + "\n"
        target, _ = yaml_nodes(target_text, "application.yml")
        fragment, _ = yaml_nodes(fragment_text, "application.yml")
    except (yaml.YAMLError, ConfigSurfaceError) as exc:
        raise CapabilityYamlMergeError("MANAGED_SURFACE_CONFLICT：YAML 配置无法安全解析。") from exc
    if not isinstance(target, MappingNode) or not isinstance(fragment, MappingNode):
        raise CapabilityYamlMergeError("MANAGED_SURFACE_CONFLICT：YAML 配置根必须是映射。")
    current_lines = current.splitlines(keepends=True)
    fragment_lines = fragment_text.splitlines(keepends=True)
    inserts: dict[int, list[str]] = defaultdict(list)
    _collect_missing(target, fragment, fragment_lines, inserts)
    if not inserts:
        return current
    newline = "\r\n" if "\r\n" in current else "\n"
    # 从文件末尾向前插入，避免新增行使前面计算的节点行号失效。
    for line_index in sorted(inserts, reverse=True):
        blocks = inserts[line_index]
        if line_index > 0 and current_lines[line_index - 1] and not current_lines[line_index - 1].endswith(("\n", "\r")):
            blocks.insert(0, newline)
        current_lines[line_index:line_index] = blocks
    result = "".join(current_lines)
    try:
        merged, _ = yaml_nodes(result, "application.yml")
    except (yaml.YAMLError, ConfigSurfaceError) as exc:
        raise CapabilityYamlMergeError("MANAGED_SURFACE_CONFLICT：合并后的 YAML 无效。") from exc
    if not isinstance(merged, MappingNode):
        raise CapabilityYamlMergeError("MANAGED_SURFACE_CONFLICT：合并后的 YAML 根无效。")
    _mapping_entries(merged)
    return result


def _mapping_entries(node: MappingNode) -> dict[str, tuple[ScalarNode, Node]]:
    """读取映射的直接子键，并拒绝重复键或无法定点合并的流式结构。"""

    if node.flow_style:
        raise CapabilityYamlMergeError("MANAGED_SURFACE_CONFLICT：不支持合并流式 YAML 映射。")
    entries: dict[str, tuple[ScalarNode, Node]] = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str" or key.value in entries:
            raise CapabilityYamlMergeError("MANAGED_SURFACE_CONFLICT：YAML 配置键重复或类型无效。")
        entries[key.value] = (key, value)
    return entries


def _collect_missing(
    target: MappingNode,
    fragment: MappingNode,
    fragment_lines: list[str],
    inserts: dict[int, list[str]],
) -> None:
    """按完整父子路径定位缺失节点，已有叶子保持原值。"""

    existing = _mapping_entries(target)
    proposed = _mapping_entries(fragment)
    fragment_items = list(proposed.items())
    for index, (name, (source_key, source_value)) in enumerate(fragment_items):
        present = existing.get(name)
        if present is not None:
            target_value = present[1]
            if isinstance(source_value, MappingNode) and isinstance(target_value, MappingNode):
                _collect_missing(target_value, source_value, fragment_lines, inserts)
            elif type(source_value) is not type(target_value) or source_value.tag != target_value.tag:
                raise CapabilityYamlMergeError("MANAGED_SURFACE_CONFLICT：YAML 已有节点类型与模板片段冲突。")
            continue
        end_line = fragment_items[index + 1][1][0].start_mark.line if index + 1 < len(fragment_items) else fragment.end_mark.line
        source_block = fragment_lines[source_key.start_mark.line:end_line]
        shift = target.start_mark.column - source_key.start_mark.column
        block = "".join(_reindent_line(line, shift) for line in source_block)
        if block and not block.endswith(("\n", "\r")):
            block += "\n"
        inserts[target.end_mark.line].append(block)


def _reindent_line(line: str, shift: int) -> str:
    """把模板片段缩进映射到目标父节点下，不改变行内配置值。"""

    if not line.strip():
        return line
    leading = len(line) - len(line.lstrip(" "))
    if leading + shift < 0:
        raise CapabilityYamlMergeError("MANAGED_SURFACE_CONFLICT：YAML 片段缩进无效。")
    return " " * (leading + shift) + line[leading:]
