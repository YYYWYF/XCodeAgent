"""Unit 规划提示词与实体绑定测试共用的上下文夹具。

这几个构造函数原本定义在被删除的 `tests/test_unit_task_prompt.py` 中，
但下游用例仍在使用，因此单独保留在此，避免重复定义。
"""

from __future__ import annotations

from typing import Any

from app.services.unit_generation_contracts import UnitGenerationContext
from tests.test_unit_generation_contracts import _context_payload


def _requirement(kind: str, suffix: str, **source_refs: Any) -> dict:
    """构造能被 Prompt 规则清单精确消费的增量职责。"""

    requirement_prefix = "backend.endpoint.objects" if kind == "backend.objects" else kind
    requirement_id = f"{requirement_prefix}:{suffix}"
    artifact = (
        "endpoint-api-design"
        if kind in {
            "backend.objects",
            "backend.repository",
            "backend.upstream",
            "backend.application_service",
            "backend.endpoint_controller",
        }
        else "technical-plan"
    )
    return {
        "requirement_id": requirement_id,
        "description": f"实现 {requirement_id}",
        "source_refs": {
            "artifact": artifact,
            "capability_id": requirement_id,
            "kind": kind,
            **source_refs,
        },
    }


def _unit_context(
    unit_id: str,
    unit_kind: str,
    requirements: list[dict],
    *,
    retained_task_summaries: list[dict] | None = None,
) -> UnitGenerationContext:
    """基于公共冻结夹具构造不同类型的模型 Unit Context。"""

    payload = _context_payload()
    payload.update({
        "unit_id": unit_id,
        "unit_kind": unit_kind,
        "generation_requirements": requirements,
        "constraints": {
            "owner": "frontend" if unit_kind in {"page", "frontend"} else unit_kind,
            "managed_files": [],
            "strong_rules": ["exact_unit_owner"],
        },
        "dependency_context": {
            "dependency_unit_ids": [],
            "retained_task_summaries": retained_task_summaries or [],
            "retained_owner_constraints": [],
        },
    })
    return UnitGenerationContext(**payload)


def _backend_endpoint_context(*source_types: str) -> UnitGenerationContext:
    """按 Endpoint 来源集合构造当前五阶段职责的精确子集。"""

    kinds = ["backend.objects"]
    if "database" in source_types:
        kinds.append("backend.repository")
    if "external_api" in source_types:
        kinds.append("backend.upstream")
    kinds.extend(("backend.application_service", "backend.endpoint_controller"))
    # 后端 Unit 已按接口分组收敛：身份是 `backend:module:<contract>`，不再带 endpoint。
    return _unit_context(
        "backend:module:orders-api",
        "backend",
        [
            _requirement(
                kind,
                "orders-api:orders.list",
                api_contract_id="orders-api",
                endpoint_id="orders.list",
                target_id="orders.list",
                data_source_types=list(source_types),
            )
            for kind in kinds
        ],
    )
