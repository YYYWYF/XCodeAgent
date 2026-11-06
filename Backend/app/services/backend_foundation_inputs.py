"""投影模块基础骨架真正消费的表结构和上游连接，支持跨接口复用。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path

from app.services.planning_frozen import plain_json
from app.services.unit_generation_requirement_targets import backend_foundation_requirement_id
from app.services.unit_task_manifest import backend_module_unit_id


def _snapshots(design: Mapping) -> list[Mapping]:
    """只选择能追溯至正式数据库或外部来源的快照。"""
    return [item for item in design.get("sourceSnapshots", ())
            if item.get("sourceType") in {"database", "external_api"} and item.get("sourceId")]


def foundation_facts(design: Mapping) -> set[tuple[str, ...]]:
    """以物理表和连接配置为身份，记录已有骨架必须能覆盖的精确列定义。"""
    facts = set()
    for snapshot in _snapshots(design):
        details = snapshot.get("details") or {}
        source_id = snapshot["sourceId"]
        if snapshot["sourceType"] == "external_api":
            connection = details.get("connection") or {}
            if connection.get("baseUrlConfigKey"):
                encoded = json.dumps(plain_json(connection), sort_keys=True, ensure_ascii=False)
                facts.add(("external_api", source_id, encoded))
            continue
        if not details.get("table"):
            continue
        key = ("database", source_id, details.get("schema", ""), details.get("managedDatasourceKey", ""),
               details["table"])
        facts.add(key)
        for column in details.get("columns", ()):
            shape = {name: plain_json(value) for name, value in column.items() if name != "description"}
            facts.add((*key, json.dumps(shape, sort_keys=True, ensure_ascii=False)))
    return facts


def foundation_expectations(design: Mapping) -> dict:
    """构造按当前接口验证的骨架类型、表名、列和配置键。"""
    snapshots = _snapshots(design)
    return {
        "source_types": sorted({snapshot["sourceType"] for snapshot in snapshots}),
        "tables": [
            {"name": details.get("table"),
             "columns": [item["name"] for item in details.get("columns", ()) if item.get("name")]}
            for snapshot in snapshots if snapshot["sourceType"] == "database"
            if (details := snapshot.get("details"))
        ],
        "config_keys": sorted({
            key for snapshot in snapshots if snapshot["sourceType"] == "external_api"
            if (key := (snapshot.get("details", {}).get("connection") or {}).get("baseUrlConfigKey"))
        }),
    }


def reuse_foundation_providers(
    tasks: Sequence[Mapping],
    designs: Sequence[Mapping],
    capabilities: dict[str, dict[str, list[str]]],
    workspace_root: str | Path,
) -> None:
    """用已成功任务的正式来源和当前源码补足同模块新接口的基础能力。"""
    from app.services.business_acceptance_verifiers.backend_foundation import verify_foundation_source
    from app.services.business_acceptance_verifiers.common import read_target_files

    required = {}
    for design in designs:
        contract, endpoint = design["apiContractId"], design["endpointId"]
        capability = backend_foundation_requirement_id(contract, endpoint)
        unit_id = backend_module_unit_id(contract)
        shapes = foundation_facts(design)
        if shapes:
            required[(unit_id, capability)] = shapes
    if not required:
        return

    covered: dict[str, list[tuple[str, set[tuple[str, ...]]]]] = {}
    units = {unit_id for unit_id, _ in required}
    for task in tasks:
        unit_id = task["unit_id"]
        if unit_id not in units or task.get("status") not in {"completed", "already_satisfied"}:
            continue
        refs = task.get("source_refs") or {}
        if refs.get("backend_stage") != "foundation":
            continue
        previous = {item["endpointId"]: item for item in refs.get("endpoint_designs", ())}
        for item in task.get("deliverables", ()):
            if item.get("kind") != "backend.foundation" or (design := previous.get(item.get("target_id"))) is None:
                continue
            declared = backend_foundation_requirement_id(design["apiContractId"], design["endpointId"])
            current_providers = capabilities[unit_id].get(declared, [])
            if task["id"] not in current_providers:
                continue
            facts = foundation_facts(design)
            if not any(facts & shapes for (unit, _), shapes in required.items() if unit == unit_id):
                continue
            files, errors = read_target_files({"target_paths": list(item.get("paths", ()))}, workspace_root)
            if not errors and verify_foundation_source(files, foundation_expectations(design))["status"] == "passed":
                covered.setdefault(unit_id, []).append((task["id"], facts))
            elif (unit_id, declared) in required:
                # 已完成记录仍需有当前磁盘证据，缺失时允许重新规划该骨架。
                current_providers.remove(task["id"])
                if not current_providers:
                    capabilities[unit_id].pop(declared, None)

    for (unit_id, capability), missing in required.items():
        if capabilities.get(unit_id, {}).get(capability):
            continue
        providers = set()
        for task_id, facts in covered.get(unit_id, ()):
            if missing & facts:
                providers.add(task_id)
                missing = missing - facts
        if not missing:
            capabilities[unit_id][capability] = sorted(providers)
