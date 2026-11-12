"""校验 V4 配置片段及其在内存副本中的合并结果。"""
from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken, TagToken

if TYPE_CHECKING:
    from app.services.template_reconcile.executor_v3 import WorkingCopyStoreV3
    from app.services.template_reconcile.protocol_v4 import ExtensionUpdatePackageV4

ENV = "frontend/.env"
MAIN = "backend/src/main/resources/application.yml"
DEV = "backend/src/main/resources/application-dev.yml"
MANAGED_CONFIG_FORMATS = {ENV: "dotenv", MAIN: "yaml", DEV: "yaml"}


class ConfigSurfaceError(ValueError):
    """保留可投影错误码与不含值的受管路径。"""

    def __init__(self, code: str, target: str) -> None:
        """错误中仅携带受管目标文件路径。"""
        self.code = code
        self.target = target
        super().__init__(f"{code}：{target} 配置结构不满足当前合同。")


def yaml_nodes(content: str, target: str) -> tuple[MappingNode, dict[tuple[str | int, ...], Node]]:
    """递归索引完整 YAML 路径，拒绝重复键、锚点、别名、合并键和不安全标签。"""
    try:
        if any(isinstance(token, (AliasToken, AnchorToken, TagToken)) for token in yaml.scan(content)):
            raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
        root = yaml.compose(content, Loader=yaml.SafeLoader)
        if not isinstance(root, MappingNode):
            raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
        nodes: dict[tuple[str | int, ...], Node] = {}

        def visit(node: Node, path: tuple[str | int, ...]) -> None:
            """检查整棵树而不是仅检查本次缺失节点所在分支。"""
            if isinstance(node, MappingNode):
                if node.flow_style:
                    raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
                seen: set[str] = set()
                for key, value in node.value:
                    if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str" or key.value in seen or key.value == "<<":
                        raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
                    seen.add(key.value)
                    child = (*path, key.value)
                    nodes[child] = value
                    visit(value, child)
            elif isinstance(node, SequenceNode):
                for index, value in enumerate(node.value):
                    visit(value, (*path, index))
            elif not isinstance(node, ScalarNode) or node.tag not in {f"tag:yaml.org,2002:{kind}" for kind in ("str", "int", "float", "bool", "null", "timestamp")}:
                raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
        visit(root, ())
        return root, nodes
    except (yaml.YAMLError, UnicodeError, RecursionError) as exc:
        raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target) from exc


def dotenv_entries(content: str, target: str, *, fragment: bool = False) -> dict[str, str]:
    """读取 dotenv 键，拒绝重复和无法安全合并的片段行。"""
    entries: dict[str, str] = {}
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        pattern = r"([A-Za-z_][A-Za-z0-9_]*)=(.*)" if fragment else r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)"
        match = re.fullmatch(pattern, line)
        if not match or match[1] in entries:
            raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
        entries[match[1]] = match[2]
    return entries


@dataclass(frozen=True)
class _ScalarValue:
    """保留 YAML 标量的标签，避免布尔值和数字在比较时等同。"""

    tag: str
    value: Any


def _semantic(node: Node, loader: yaml.SafeLoader) -> Any:
    """保留映射键原文和标量类型，将整个序列作为一个值比较。"""
    if isinstance(node, MappingNode):
        return {key.value: _semantic(value, loader) for key, value in node.value}
    if isinstance(node, SequenceNode):
        return tuple(_semantic(item, loader) for item in node.value)
    value = loader.construct_object(node)
    # NaN 不等于自身，需要稳定的表示才能比较重复声明。
    if isinstance(value, float) and value != value:
        value = "nan"
    return _ScalarValue(node.tag, value)


def _surface_values(content: bytes, target: str, *, fragment: bool = False) -> dict[str, Any]:
    """安全读取配置结构，保留含点号的 YAML 键及 dotenv 原始值。"""
    try:
        decoded = content.decode("utf-8")
        if target == ENV:
            return dotenv_entries(decoded, target, fragment=fragment)
        root, _ = yaml_nodes(decoded, target)
        loader = yaml.SafeLoader("")
        try:
            return _semantic(root, loader)
        finally:
            loader.dispose()
    except ConfigSurfaceError:
        raise
    except (ValueError, OverflowError, yaml.YAMLError, RecursionError) as exc:
        raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target) from exc


def _ensure_values(
    current: dict[str, Any], fragment: dict[str, Any], target: str, *, declarations: bool,
) -> None:
    """推演缺失键补齐；聚合片段时拒绝歧义，应用时保留用户已有值。"""
    for key, proposed in fragment.items():
        if key not in current:
            current[key] = deepcopy(proposed) if isinstance(proposed, dict) else proposed
            continue
        existing = current[key]
        if isinstance(existing, dict) and isinstance(proposed, dict):
            _ensure_values(existing, proposed, target, declarations=declarations)
        elif type(existing) is not type(proposed) or (
            declarations and existing != proposed
        ):
            code = "TEMPLATE_PACKAGE_CONTRACT_INVALID" if declarations else "MANAGED_SURFACE_CONFLICT"
            raise ConfigSurfaceError(code, target)


def validate_ensure_package(
    package: ExtensionUpdatePackageV4, payloads: dict[str, bytes], newly_installed: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """预检受管操作及片段，按目标保留已验证的操作顺序供后验使用。"""
    from app.services.template_reconcile.protocol_v4 import EnsureCapabilityConfigOperationV4

    fragments: dict[str, list[dict[str, Any]]] = {}
    declarations: dict[str, dict[str, Any]] = {}
    for operation in package.operations:
        target = operation.target
        if not isinstance(operation, EnsureCapabilityConfigOperationV4):
            # 折叠路径别名，避免在大小写不敏感的文件系统上绕开受管目标。
            normalized = "/".join(part.rstrip(". ").casefold() for part in PurePosixPath(target).parts)
            if normalized in MANAGED_CONFIG_FORMATS:
                raise ConfigSurfaceError("TEMPLATE_PACKAGE_CONTRACT_INVALID", normalized)
            continue

        caps = operation.parameters.capabilityIds
        if (
            not caps or len(set(caps)) != len(caps) or not set(caps) <= newly_installed
            or MANAGED_CONFIG_FORMATS.get(target) != operation.parameters.format
            or operation.payloadRef not in payloads
        ):
            raise ConfigSurfaceError("TEMPLATE_PACKAGE_CONTRACT_INVALID", target)
        fragment = _surface_values(payloads[operation.payloadRef], target, fragment=True)
        if not fragment:
            raise ConfigSurfaceError("TEMPLATE_PACKAGE_CONTRACT_INVALID", target)
        # 独立聚合声明以检查包内冲突。
        _ensure_values(declarations.setdefault(target, {}), fragment, target, declarations=True)
        fragments.setdefault(target, []).append(fragment)
    return fragments


def _preserves_original_lines(original: bytes, working: bytes) -> bool:
    """确认旧行字节及顺序不变，允许为原 EOF 行补换行以插入新配置。"""
    remaining = iter(working.splitlines(keepends=True))
    for line in original.splitlines(keepends=True):
        if not any(
            candidate == line or (
                not line.endswith((b"\r", b"\n")) and candidate.rstrip(b"\r\n") == line
            )
            for candidate in remaining
        ):
            return False
    return True


def validate_working_surfaces(
    store: WorkingCopyStoreV3, fragments: dict[str, list[dict[str, Any]]],
) -> None:
    """独立推演每个目标的补齐结果，提交前拒绝漏写、改写和额外节点。"""
    for target, additions in fragments.items():
        entry = store.entry(target)
        original, working = entry.original, entry.working
        if original is None or working is None:
            raise ConfigSurfaceError("CONFIG_STRUCTURE_MISSING", target)
        original_values = _surface_values(original, target)
        expected = deepcopy(original_values)
        for fragment in additions:
            _ensure_values(expected, fragment, target, declarations=False)
        if (
            _surface_values(working, target) != expected
            or (expected == original_values and working != original)
            or not _preserves_original_lines(original, working)
        ):
            raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
