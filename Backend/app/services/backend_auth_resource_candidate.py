"""后端权限资源任务的确定性候选构造和精确写入资格。"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.services.backend_authorization_resources import (
    BACKEND_AUTH_UNIT, BACKEND_AUTH_TASK, BACKEND_AUTH_CAPABILITY,
    BACKEND_AUTH_EXECUTOR, BACKEND_AUTH_PATH, normalize_backend_constants,
)
from app.services.planning_frozen import plain_json
from app.services.unit_generation_requirements_contracts import UnitGenerationRequirements


def build_backend_auth_resource_candidate(requirements: UnitGenerationRequirements) -> dict[str, Any] | None:
    """把唯一资源清单职责转换为一个平台任务，不调用模型或写入文件。"""

    duties = requirements.generation_requirements_by_unit.get(BACKEND_AUTH_UNIT, ())
    if not duties:
        return None
    if len(duties) != 1 or requirements.generation_strategy_by_unit[BACKEND_AUTH_UNIT] != "deterministic":
        raise ValueError("后端权限资源必须由唯一 deterministic 职责生成。")
    refs = plain_json(duties[0].source_refs)
    if (duties[0].requirement_id != BACKEND_AUTH_CAPABILITY
            or refs.get("kind") != BACKEND_AUTH_CAPABILITY
            or refs.get("capability_id") != BACKEND_AUTH_CAPABILITY
            or refs.get("paths") != [BACKEND_AUTH_PATH]
            or not normalize_backend_constants(refs["constants"])):
        raise ValueError("后端权限资源职责必须绑定完整非空清单与唯一常量文件。")
    return {"tasks": [{
        "id": BACKEND_AUTH_TASK, "title": "生成后端权限资源常量", "unit_id": BACKEND_AUTH_UNIT,
        "owner": "backend", "task_type": "backend.code", "status": "pending",
        "execution_strategy": "deterministic", "platform_executor": BACKEND_AUTH_EXECUTOR,
        "description": "根据全部已确认接口的权限资源点，更新 AuthConstants.java 的业务常量托管区。",
        "dependencies": [], "target_files": [BACKEND_AUTH_PATH], "allowed_paths": [BACKEND_AUTH_PATH],
        "change_scope": [{"path": BACKEND_AUTH_PATH, "operation": "modify",
                          "description": "写入当前完整业务资源常量，保留托管区外的模板内容。"}],
        "provides_capabilities": [BACKEND_AUTH_CAPABILITY], "source_refs": refs,
        "deliverables": [{"id": f"{BACKEND_AUTH_TASK}-constants", "kind": BACKEND_AUTH_CAPABILITY,
                          "target_id": BACKEND_AUTH_CAPABILITY, "paths": [BACKEND_AUTH_PATH],
                          "provides": [BACKEND_AUTH_CAPABILITY]}],
    }]}


def is_backend_auth_resource_task(task: Mapping[str, Any], paths: Sequence[str]) -> bool:
    """仅识别具有固定身份、执行器及唯一目标文件的后端平台权限任务。"""

    refs = task.get("source_refs") or {}
    deliverables = task.get("deliverables") or []
    return (task.get("id") == BACKEND_AUTH_TASK and task.get("unit_id") == BACKEND_AUTH_UNIT
            and task.get("owner") == "backend" and task.get("execution_strategy") == "deterministic"
            and task.get("platform_executor") == BACKEND_AUTH_EXECUTOR
            and set(paths) == {BACKEND_AUTH_PATH}
            and refs.get("kind") == BACKEND_AUTH_CAPABILITY
            and refs.get("capability_id") == BACKEND_AUTH_CAPABILITY
            and len(deliverables) == 1 and deliverables[0].get("kind") == BACKEND_AUTH_CAPABILITY
            and deliverables[0].get("target_id") == BACKEND_AUTH_CAPABILITY
            and deliverables[0].get("paths") == [BACKEND_AUTH_PATH]
            and BACKEND_AUTH_CAPABILITY in task.get("provides_capabilities", ()))
