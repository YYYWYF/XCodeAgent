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
from app.services.template_state import load_template_state, template_revision
from app.services.unit_generation_requirements_contracts import (
    UnitGenerationRequirements,
    fail_requirement_input,
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


def _module_target_files(config: dict[str, Any]) -> list[str]:
    """把模板策略中的明确修改文件编译为新 DAG 可执行目标。"""

    return list(
        dict.fromkeys(
            _prefixed_path(path)
            for path in config.get("modifyPaths", [])
            if str(path).strip()
        )
    )


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
    current_template_revision = template_revision(load_template_state(root))
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
            target_files = _module_target_files(module_paths)
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
                    "template_revision": current_template_revision,
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
                "target_files": target_files,
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


def build_agent_unit_candidate(
    *,
    unit_id: str,
    contracts: list[dict[str, Any]],
    workspace: str | Path,
    generation_requirements: UnitGenerationRequirements,
    retained_task_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """把当前业务 Agent 的缺项职责编译为确定性 Candidate，不调用规划模型。"""

    requirements = UnitGenerationRequirements.model_validate(generation_requirements)
    if not unit_id.startswith("agent:") or unit_id == "agent:runtime":
        fail_requirement_input(
            "AGENT_CANDIDATE_INPUT_INVALID",
            f"Agent deterministic builder 不支持 Unit {unit_id}。",
            unit_ids=[unit_id],
        )
    strategy = requirements.generation_strategy_by_unit.get(unit_id)
    missing = requirements.generation_requirements_by_unit.get(unit_id, ())
    if strategy not in {"deterministic", "reuse_only"}:
        fail_requirement_input(
            "AGENT_CANDIDATE_INPUT_INVALID",
            "业务 Agent Unit 必须有明确的 deterministic 或 reuse_only 职责判定。",
            unit_ids=[unit_id],
        )
    if not missing:
        return None
    if strategy != "deterministic":
        fail_requirement_input(
            "AGENT_CANDIDATE_INPUT_INVALID",
            "存在 Agent 职责缺项时必须生成 deterministic Candidate。",
            unit_ids=[unit_id],
        )

    required_by_capability = {
        requirement.requirement_id: requirement
        for requirement in missing
    }
    compiled = compile_agent_build_tasks(
        contracts,
        unit_ids={unit_id},
        workspace=workspace,
    )
    tasks = [
        task
        for task in compiled
        if set(task.get("provides_capabilities") or ()) & set(required_by_capability)
    ]
    provided = {
        capability
        for task in tasks
        for capability in task.get("provides_capabilities") or ()
        if capability in required_by_capability
    }
    if provided != set(required_by_capability):
        fail_requirement_input(
            "AGENT_CANDIDATE_INPUT_INVALID",
            "Agent 七模块编译结果没有精确覆盖当前 generation requirements。",
            unit_ids=[unit_id],
        )
    selected_task_ids = {str(task.get("id") or "") for task in tasks}
    available_task_ids = selected_task_ids | set(retained_task_ids or ())
    for task in tasks:
        capabilities = set(task.get("provides_capabilities") or ())
        capability = next(iter(capabilities & set(required_by_capability)), "")
        requirement = required_by_capability.get(capability)
        source_refs = requirement.source_refs if requirement is not None else {}
        task_source_refs = task.get("source_refs") or {}
        if (
            source_refs.get("kind") != "agent.runtime"
            or source_refs.get("agent_id") != task_source_refs.get("agent_id")
            or source_refs.get("agent_module") != task_source_refs.get("agent_module")
        ):
            fail_requirement_input(
                "AGENT_CANDIDATE_INPUT_INVALID",
                f"Agent Task {task.get('id') or '<unknown>'} 与正式模块职责不一致。",
                unit_ids=[unit_id],
            )
        # 增量 Candidate 只保留当前候选或正式 retained Task 依赖，不能引用被复用事实
        # 省略且没有任务身份的旧模块。
        task["dependencies"] = [
            dependency
            for dependency in task.get("dependencies") or []
            if dependency in available_task_ids
        ]
    return {"tasks": tasks}


__all__ = ["build_agent_unit_candidate", "compile_agent_build_tasks"]
