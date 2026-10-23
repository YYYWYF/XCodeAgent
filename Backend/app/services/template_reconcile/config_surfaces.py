"""V4 新装能力配置表面的路径合同与安全解析，不负责创建模板结构。"""
from __future__ import annotations

import re
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode
from yaml.tokens import AliasToken, AnchorToken, TagToken

ENV = "frontend/.env"
MAIN = "backend/src/main/resources/application.yml"
DEV = "backend/src/main/resources/application-dev.yml"
LOGIN_FIELDS = ("client-id", "private-key", "public-key", "center-public-key", "center-uri")
CONTRACT = {
    "login": {
        ENV: {name for name in ("PUBLIC_YHT_CLIENT_ID", "PUBLIC_YHT_AUTHORIZE_URI", "PUBLIC_YHT_END_URL", "PUBLIC_YHT_LOGOUT_SHARE_JS")},
        MAIN: {*(f"bee.auth.{key}" for key in LOGIN_FIELDS), "bee.auth.login.authorize-uri", "bee.auth.login.redirect-uri", "bee.auth.login.filter-patterns"},
        DEV: {*(f"yht.authentication.{key}" for key in LOGIN_FIELDS), "yht.authentication.authorize-uri", "yht.authentication.redirect-uri"},
    },
    "authorization": {MAIN: {"yht.api.user-query-path"}, DEV: {"yht.api.base-url", "yht.api.view-id"}},
}


class ConfigSurfaceError(ValueError):
    """保留可投影错误码与不含值的受管路径。"""

    def __init__(self, code: str, target: str) -> None:
        """错误中仅携带已知文件或字段路径。"""
        self.code = code
        self.target = target
        super().__init__(f"{code}：{target} 配置结构不满足当前合同。")


def yaml_nodes(content: str, target: str) -> tuple[MappingNode, dict[str, Any]]:
    """递归索引完整 YAML 路径，拒绝重复键、锚点、别名、合并键和不安全标签。"""
    try:
        if any(isinstance(token, (AliasToken, AnchorToken, TagToken)) for token in yaml.scan(content)):
            raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
        root = yaml.compose(content, Loader=yaml.SafeLoader)
        if not isinstance(root, MappingNode):
            raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
        nodes: dict[str, Any] = {}

        def visit(node: Any, path: str) -> None:
            """检查整棵树而不是仅检查本次缺失节点所在分支。"""
            if isinstance(node, MappingNode):
                if node.flow_style:
                    raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
                seen: set[str] = set()
                for key, value in node.value:
                    if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str" or key.value in seen or key.value == "<<":
                        raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
                    seen.add(key.value)
                    child = f"{path}.{key.value}" if path else key.value
                    nodes[child] = value
                    visit(value, child)
            elif isinstance(node, SequenceNode):
                for index, value in enumerate(node.value):
                    visit(value, f"{path}[{index}]")
            elif not isinstance(node, ScalarNode) or node.tag not in {f"tag:yaml.org,2002:{kind}" for kind in ("str", "int", "float", "bool", "null", "timestamp")}:
                raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)
        visit(root, "")
        return root, nodes
    except (yaml.YAMLError, UnicodeError) as exc:
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


def surface_values(content: bytes | None, target: str) -> dict[str, Any]:
    """读取配置语义用于完整性及非受管值保护，不持久化任何字段值。"""
    if content is None:
        raise ConfigSurfaceError("CONFIG_STRUCTURE_MISSING", target)
    try:
        text = content.decode("utf-8")
    except UnicodeError as exc:
        raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target) from exc
    if target == ENV:
        return dotenv_entries(text, target)
    _, nodes = yaml_nodes(text, target)
    return nodes


def validate_required(values: dict[str, Any], required: set[str], target: str) -> None:
    """要求全部受管叶子存在且具有模板约定的字符串或过滤规则数组类型。"""
    for path in required:
        node = values.get(path)
        if node is None:
            raise ConfigSurfaceError("CONFIG_STRUCTURE_MISSING", path)
        if target == ENV:
            continue
        valid = isinstance(node, ScalarNode) and node.tag == "tag:yaml.org,2002:str"
        if path == "bee.auth.login.filter-patterns" and isinstance(node, SequenceNode):
            valid = all(isinstance(item, ScalarNode) and item.tag == "tag:yaml.org,2002:str" for item in node.value)
        if not valid:
            raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", path)


def validate_ensure_package(package: Any, payloads: dict[str, bytes], newly_installed: set[str]) -> None:
    """在执行前验证 Capability × 目标文件 × 实际片段路径覆盖且不夹带用户节点。"""
    from app.services.template_reconcile.protocol_v4 import EnsureCapabilityConfigOperationV4
    coverage: dict[tuple[str, str], set[str]] = {}
    for operation in package.operations:
        if not isinstance(operation, EnsureCapabilityConfigOperationV4):
            if operation.target in {ENV, MAIN, DEV}:
                raise ConfigSurfaceError("TEMPLATE_PACKAGE_CONTRACT_INVALID", operation.target)
            continue
        caps = operation.parameters.capabilityIds
        if len(set(caps)) != len(caps) or not set(caps) <= newly_installed:
            raise ConfigSurfaceError("TEMPLATE_PACKAGE_CONTRACT_INVALID", operation.target)
        allowed: set[str] = set()
        for cap in caps:
            paths = CONTRACT[cap].get(operation.target)
            if not paths:
                raise ConfigSurfaceError("TEMPLATE_PACKAGE_CONTRACT_INVALID", operation.target)
            allowed.update(paths)
        values = surface_values(payloads[operation.payloadRef], operation.target)
        leaves = {path for path, node in values.items() if not isinstance(node, MappingNode)}
        if not leaves or not leaves <= allowed:
            raise ConfigSurfaceError("TEMPLATE_PACKAGE_CONTRACT_INVALID", operation.target)
        validate_required(values, leaves, operation.target)
        for cap in caps:
            coverage.setdefault((cap, operation.target), set()).update(leaves & CONTRACT[cap][operation.target])
    for cap in newly_installed & CONTRACT.keys():
        for target, required in CONTRACT[cap].items():
            if not required <= coverage.get((cap, target), set()):
                raise ConfigSurfaceError("CONFIG_STRUCTURE_MISSING", target)


def validate_working_surfaces(store: Any, capabilities: set[str]) -> None:
    """在 TemplateState 写入前复验全部目标及原有节点值，防止半包或用户配置覆盖。"""
    targets = {target for cap in capabilities & CONTRACT.keys() for target in CONTRACT[cap]}
    for target in targets:
        entry = store.entry(target)
        values = surface_values(entry.working, target)
        original = surface_values(entry.original, target)
        for cap in capabilities & CONTRACT.keys():
            validate_required(values, CONTRACT[cap].get(target, set()), target)
        # 原有映射、数组、标量均必须保留；对叶子比较原 AST 对应语义。
        for path, value in original.items():
            if path not in values or _semantic(value) != _semantic(values[path]):
                raise ConfigSurfaceError("MANAGED_SURFACE_CONFLICT", target)


def _semantic(node: Any) -> Any:
    """映射允许补键，原有叶子及数组必须完全相同。"""
    if isinstance(node, MappingNode):
        return "mapping"
    if isinstance(node, SequenceNode):
        return (node.tag, tuple(_semantic(item) if not isinstance(item, MappingNode) else tuple((k.value, _semantic(v)) for k, v in item.value) for item in node.value))
    if isinstance(node, ScalarNode):
        return (node.tag, node.value)
    return node
