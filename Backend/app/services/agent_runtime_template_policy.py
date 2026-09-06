"""平台内置的 Agent Runtime 七模块路径策略。"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


AGENT_RUNTIME_TEMPLATE_POLICY_VERSION = "agent-runtime-template.policy.v1"
AGENT_RUNTIME_MODULES = (
    "prompt",
    "model",
    "memory",
    "tools",
    "skills",
    "knowledge",
    "context",
)

# 路径权限由 XCodeAgent 持有，生成应用无需携带平台 Manifest。
_MODULE_PATH_POLICY: dict[str, dict[str, list[str]]] = {
    "prompt": {
        "readPaths": ["src/app/agent/factory.py", "src/app/agent/context.py"],
        "modifyPaths": ["src/app/agent/factory.py"],
        "addRoots": [],
        "testRoots": ["tests"],
    },
    "model": {
        "readPaths": [
            "src/app/agent/factory.py",
            "src/app/models/factory.py",
            "src/app/settings.py",
        ],
        "modifyPaths": ["src/app/agent/factory.py", "src/app/models/factory.py"],
        "addRoots": [],
        "testRoots": ["tests"],
    },
    "memory": {
        "readPaths": [
            "src/app/agent/factory.py",
            "src/app/persistence/checkpointer.py",
            "src/app/settings.py",
        ],
        "modifyPaths": [
            "src/app/agent/factory.py",
            "src/app/persistence/checkpointer.py",
        ],
        "addRoots": [],
        "testRoots": ["tests"],
    },
    "tools": {
        "readPaths": [
            "src/app/agent/factory.py",
            "src/app/agent/context.py",
            "src/app/settings.py",
            "src/app/tools/__init__.py",
        ],
        "modifyPaths": ["src/app/agent/factory.py", "src/app/tools/__init__.py"],
        "addRoots": ["src/app/tools"],
        "testRoots": ["tests"],
    },
    "skills": {
        "readPaths": ["src/app/agent/factory.py", "src/app/settings.py"],
        "modifyPaths": ["src/app/agent/factory.py"],
        "addRoots": ["src/app/skills"],
        "testRoots": ["tests"],
    },
    "knowledge": {
        "readPaths": ["src/app/agent/factory.py", "src/app/settings.py"],
        "modifyPaths": ["src/app/agent/factory.py"],
        "addRoots": ["src/app/knowledge"],
        "testRoots": ["tests"],
    },
    "context": {
        "readPaths": [
            "src/app/agent/factory.py",
            "src/app/agent/context.py",
            "src/app/interaction/schemas.py",
            "src/app/interaction/service.py",
        ],
        "modifyPaths": [
            "src/app/agent/factory.py",
            "src/app/agent/context.py",
            "src/app/interaction/schemas.py",
            "src/app/interaction/service.py",
        ],
        "addRoots": [],
        "testRoots": ["tests"],
    },
}


class AgentRuntimeTemplatePolicyError(ValueError):
    """表示当前 Runtime 工程不满足平台内置路径策略。"""


def _canonical_sha256(value: Any) -> str:
    """为平台路径策略生成稳定摘要。"""

    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"


def load_agent_runtime_template_policy(runtime_root: str | Path) -> dict[str, Any]:
    """校验策略依赖的模板入口，并返回不可由模板修改的七模块路径合同。"""

    unresolved_root = Path(runtime_root).expanduser()
    if unresolved_root.is_symlink():
        raise AgentRuntimeTemplatePolicyError("Agent Runtime 模板目录不能是符号链接。")
    root = unresolved_root.resolve()
    if not root.is_dir():
        raise AgentRuntimeTemplatePolicyError("Agent Runtime 模板目录不存在或不是普通目录。")
    missing: list[str] = []
    symbolic: list[str] = []
    required_paths = {
        path
        for config in _MODULE_PATH_POLICY.values()
        for field in ("readPaths", "modifyPaths", "testRoots")
        for path in config[field]
    }
    for relative_path in sorted(required_paths):
        target = root / relative_path
        if not target.exists():
            missing.append(relative_path)
        elif target.is_symlink():
            symbolic.append(relative_path)
    if missing:
        raise AgentRuntimeTemplatePolicyError(
            "Agent Runtime 模板缺少平台路径策略依赖：" + "、".join(missing)
        )
    if symbolic:
        raise AgentRuntimeTemplatePolicyError(
            "Agent Runtime 平台路径策略不允许符号链接：" + "、".join(symbolic)
        )
    policy = {
        "policyVersion": AGENT_RUNTIME_TEMPLATE_POLICY_VERSION,
        "modulePaths": deepcopy(_MODULE_PATH_POLICY),
    }
    return {**policy, "policySha256": _canonical_sha256(policy)}


__all__ = [
    "AGENT_RUNTIME_MODULES",
    "AGENT_RUNTIME_TEMPLATE_POLICY_VERSION",
    "AgentRuntimeTemplatePolicyError",
    "load_agent_runtime_template_policy",
]
