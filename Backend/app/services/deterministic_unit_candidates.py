"""平台确定性 Unit 候选正文构造，不调用模型、不分配 Attempt、不读写工作区。"""

from typing import Any

from app.services.authorization_resource_catalog import (
    ResourceCatalog, compile_frontend_resource_catalog, resource_catalog_fingerprint,
)
from app.services.planning_frozen import plain_json
from app.services.unit_generation_requirements_contracts import (
    UnitGenerationRequirements, fail_requirement_input,
)


AUTH_GUARD_UNIT_ID = "frontend:auth-guard"
AUTH_RESOURCES_PATH = "frontend/src/constants/resources.ts"


def _invalid_input(message: str) -> None:
    """将确定性构造的输入冲突作为不可模型重试的前置问题返回。"""

    fail_requirement_input("AUTH_CANDIDATE_INPUT_INVALID", message, unit_ids=[AUTH_GUARD_UNIT_ID])


def build_auth_guard_candidate(
    *, unit_id: str, resource_catalog: ResourceCatalog | None, fingerprint: str | None,
    generation_requirements: UnitGenerationRequirements,
) -> dict[str, Any] | None:
    """仅为 T2.3 判定的当前 auth 缺项构造单 Task 候选；无缺项返回 None。

    generation_requirements 必须来自消费 T2.5B ReuseFacts 的职责判定服务，
    本层不重复推断 reuse。source refs 来自该结果中的唯一 GenerationRequirement。
    输出只含 tasks 正文，不冒充已验证 CandidateAttempt，不进入模型调度或 Local 计数。
    """

    if unit_id != AUTH_GUARD_UNIT_ID:
        _invalid_input("确定性 auth builder 仅支持 frontend:auth-guard。")
    requirements = UnitGenerationRequirements.model_validate(generation_requirements)
    strategy = requirements.generation_strategy_by_unit.get(unit_id)
    if strategy not in {"deterministic", "reuse_only"}:
        _invalid_input("auth-guard 必须有明确的 deterministic 或 reuse_only 职责判定。")
    missing = requirements.generation_requirements_by_unit[unit_id]
    if not missing:
        return None
    if strategy != "deterministic" or len(missing) != 1:
        _invalid_input("auth-guard Candidate 必须恰好对应一条当前资源目录缺项。")
    if not isinstance(resource_catalog, ResourceCatalog):
        _invalid_input("auth-guard 缺项必须提供当前完整 ResourceCatalog。")
    # 重用 canonical 编译规则，拒绝手工构造的重复/冲突目录，不自行修复资源身份。
    try:
        catalog = compile_frontend_resource_catalog({
            "enabled": True,
            "resources": [{
                "resourceKey": item.resource_key, "type": item.resource_type,
                "targetResourceRef": item.target_resource_ref,
            } for item in resource_catalog.resources],
        })
    except ValueError as exc:
        _invalid_input(str(exc))
    current_fingerprint = resource_catalog_fingerprint(catalog)
    if fingerprint != current_fingerprint:
        _invalid_input("输入 fingerprint 与当前完整 resource catalog 不一致。")
    capability = f"frontend.auth.resources:{current_fingerprint}"
    requirement = missing[0]
    refs = plain_json(requirement.source_refs)
    if requirement.requirement_id != capability or any(
        refs.get(key) != value for key, value in {
            "artifact": "technical-plan", "kind": "frontend.auth.resources",
            "capability_id": capability, "resource_catalog_fingerprint": current_fingerprint,
            "paths": [AUTH_RESOURCES_PATH],
        }.items()
    ):
        _invalid_input("generation requirement 及 source refs 必须精确指向当前 R 和唯一 resources.ts。")

    # 使用完整摘要而非截断值，Task 身份不依赖 Run、Attempt、描述、时钟或随机数。
    task_id = f"frontend-auth-resources-{current_fingerprint}"
    return {"tasks": [{
        "id": task_id,
        "unit_id": unit_id,
        "owner": "frontend",
        "task_type": "frontend.code",
        "execution_strategy": "deterministic",
        "platform_executor": "authorization.frontend_resources",
        "description": "将当前已确认的完整权限资源目录物化到 resources.ts。",
        "dependencies": [],
        "target_files": [AUTH_RESOURCES_PATH],
        "allowed_paths": [AUTH_RESOURCES_PATH],
        "provides_capabilities": [capability],
        "source_refs": refs,
        "deliverables": [{
            "id": f"{task_id}-resources",
            "kind": "frontend.shared_capability",
            "target_id": capability,
            "paths": [AUTH_RESOURCES_PATH],
            "provides": [capability],
        }],
    }]}
