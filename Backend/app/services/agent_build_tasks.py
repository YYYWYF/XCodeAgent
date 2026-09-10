"""从正式 Agent Contract 编译七模块 Build 任务。"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from app.services.agent_runtime_template_policy import (
    AGENT_RUNTIME_MODULES,
    load_agent_runtime_template_policy,
)


_MODULE_LABELS = {
    "prompt": "Prompt",
    "model": "Model",
    "memory": "Memory",
    "tools": "Tools",
    "skills": "Skills",
    "knowledge": "Knowledge",
    "context": "Context",
}
_DISABLEABLE_MODULES = {"tools", "skills", "knowledge"}


def _canonical_sha256(value: Any) -> str:
    """为正式契约或模块配置生成稳定摘要。"""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"sha256:{sha256(payload.encode('utf-8')).hexdigest()}"


def _dict_value(value: Any) -> dict[str, Any]:
    """把不可信输入收窄为字典。"""

    return value if isinstance(value, dict) else {}


def _prefixed_path(path: str) -> str:
    """把 Runtime 内相对路径映射到生成应用的仓库相对路径。"""

    return f"agent-runtime/{path.lstrip('/')}"


def _module_allowed_paths(config: dict[str, Any]) -> list[str]:
    """从平台路径策略编译当前模块允许修改或新增的路径集合。"""

    paths = [
        *[_prefixed_path(path) for path in config.get("modifyPaths", [])],
        *[
            f"{_prefixed_path(path).rstrip('/')}/**"
            for path in config.get("addRoots", [])
        ],
        *[
            f"{_prefixed_path(path).rstrip('/')}/**"
            for path in config.get("testRoots", [])
        ],
    ]
    return list(dict.fromkeys(paths))


def _template_commit(workspace: Path) -> str:
    """从已确认模板生成 manifest 读取固定 Agent Runtime commit。"""

    manifest_path = workspace / ".xcodeagent" / "template-generation-manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("无法读取 Agent Runtime 模板生成 manifest。") from exc
    steps = _dict_value(payload.get("steps"))
    download = _dict_value(steps.get("download"))
    targets = _dict_value(download.get("targets"))
    target = _dict_value(targets.get("agentRuntime"))
    commit = str(target.get("commitSha") or "").strip()
    if target.get("required") is not True or target.get("status") != "succeeded" or not commit:
        raise ValueError("Agent Runtime 模板生成 manifest 尚未提供有效 commit。")
    return commit


def _module_is_disabled(module_name: str, config: dict[str, Any]) -> bool:
    """只对契约允许关闭的模块识别显式 disabled。"""

    return module_name in _DISABLEABLE_MODULES and config.get("enabled") is False


def compile_agent_build_tasks(
    contracts: list[dict[str, Any]],
    *,
    unit_ids: set[str],
    workspace: str | Path,
) -> list[dict[str, Any]]:
    """为当前 Agent Unit 编译固定顺序的七个模板感知任务。"""

    root = Path(workspace).expanduser().resolve()
    runtime_root = root / "agent-runtime"
    template_policy = load_agent_runtime_template_policy(runtime_root)
    template_commit = _template_commit(root)
    tasks: list[dict[str, Any]] = []
    for contract in contracts:
        agent_id = str(contract.get("agentId") or "").strip()
        unit_id = f"agent:{agent_id}"
        if not agent_id or unit_id not in unit_ids:
            continue
        settings = _dict_value(contract.get("agentSettings"))
        missing_modules = [
            module_name
            for module_name in AGENT_RUNTIME_MODULES
            if module_name not in settings
        ]
        if missing_modules:
            raise ValueError(
                f"Agent Contract {agent_id} 缺少配置模块："
                + "、".join(missing_modules)
                + "。"
            )
        previous_task_id = ""
        for module_index, module_name in enumerate(AGENT_RUNTIME_MODULES, start=1):
            module_config = deepcopy(settings[module_name])
            module_paths = deepcopy(template_policy["modulePaths"][module_name])
            allowed_paths = _module_allowed_paths(module_paths)
            task_id = f"agent:{agent_id}::{module_name}"
            capability_id = f"agent.{agent_id}.{module_name}"
            disabled = _module_is_disabled(module_name, _dict_value(module_config))
            status = "already_satisfied" if disabled else "pending"
            skip_reason = "正式 Agent Contract 未启用该模块。" if disabled else None
            dependencies = [previous_task_id] if previous_task_id else []
            required_capabilities = (
                [f"agent.{agent_id}.{AGENT_RUNTIME_MODULES[module_index - 2]}"]
                if module_index > 1
                else ["agent-runtime.template.ready"]
            )
            task = {
                "id": task_id,
                "unit_id": unit_id,
                "owner": "agent",
                "task_type": "agent.code",
                "title": f"实现 {agent_id} {_MODULE_LABELS[module_name]} 模块",
                "description": (
                    f"依据正式 Agent Contract 和平台模板路径策略检查并实现 {_MODULE_LABELS[module_name]} 模块。"
                ),
                "dependencies": dependencies,
                "status": status,
                "source_refs": {
                    "agent_id": agent_id,
                    "agent_module": module_name,
                    "agent_contract_sha256": _canonical_sha256(contract),
                    "module_config_sha256": _canonical_sha256(module_config),
                    "template_commit": template_commit,
                    "template_policy_sha256": template_policy["policySha256"],
                    "agent_contracts": [deepcopy(contract)],
                },
                "deliverables": [
                    {
                        "id": f"agent:{agent_id}:{module_name}",
                        "kind": "agent.runtime",
                        "target_id": agent_id,
                        "paths": allowed_paths,
                        "provides": [capability_id],
                    }
                ],
                "requires_capabilities": required_capabilities,
                "provides_capabilities": [capability_id],
                "database_scope": {},
                "risk": "medium" if module_name in {"tools", "knowledge"} else "low",
                "approval": {},
                "allowed_paths": allowed_paths,
                "target_files": [],
                "change_scope": [],
                "lock_scope": allowed_paths,
                "impact_scope": {"modules": ["agent-runtime", module_name]},
                "can_run_in_parallel": False,
                "parallel_reason": "七模块第一版串行执行，避免共享组合入口写冲突。",
                "engineering_context": {
                    "generation_mode": "template_aware",
                    "agent_module": module_name,
                    "module_order": module_index,
                    "allowed_actions": ["skip", "reuse", "modify", "add"],
                    "read_paths": [
                        _prefixed_path(path)
                        for path in module_paths.get("readPaths", [])
                    ],
                    "modify_paths": [
                        _prefixed_path(path)
                        for path in module_paths.get("modifyPaths", [])
                    ],
                    "add_roots": [
                        _prefixed_path(path)
                        for path in module_paths.get("addRoots", [])
                    ],
                    "test_roots": [
                        _prefixed_path(path)
                        for path in module_paths.get("testRoots", [])
                    ],
                    **({"skip_reason": skip_reason} if skip_reason else {}),
                },
                **(
                    {
                        "satisfied_by": "agent-contract-disabled-module",
                        "satisfaction_evidence": {
                            "implementation_action": "skip",
                            "reason": skip_reason,
                            "module_config_sha256": _canonical_sha256(module_config),
                        },
                    }
                    if disabled
                    else {}
                ),
            }
            tasks.append(task)
            previous_task_id = task_id
    return tasks


__all__ = ["compile_agent_build_tasks"]
