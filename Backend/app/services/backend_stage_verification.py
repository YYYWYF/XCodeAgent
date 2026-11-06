"""在模块阶段切换前核对基础骨架和实际路由。"""

from pathlib import Path

from app.services.business_acceptance_verifiers.backend_endpoint import verify_endpoint_source
from app.services.business_acceptance_verifiers.backend_foundation import verify_foundation_source
from app.services.business_acceptance_verifiers.common import read_target_files
from app.services.engineering_acceptance import _backend_authorization_checks
from app.services.engineering_acceptance_verifier import _verify_backend_authorization


def backend_stage_errors(task: dict, workspace: str, added_paths: frozenset[str]) -> list[str]:
    """确认骨架和路由结构，并逐接口验证权限，不执行应用测试或构建。"""
    stage = (task.get("source_refs") or {}).get("backend_stage")
    kind = {"foundation": "backend.foundation_contract", "controller": "backend.endpoint_contract"}.get(stage)
    if not kind:
        return []
    checks = [check for check in task.get("business_acceptance_checks", []) if check.get("kind") == kind]
    if not checks:
        return ["模块公共任务缺少已确认的阶段验收契约。"]
    errors = []
    for check in checks:
        files, read_errors = read_target_files(check, workspace)
        if read_errors:
            errors.extend(read_errors)
            continue
        expected = check["expected"]
        if stage == "foundation":
            result = verify_foundation_source(files, expected, new_paths=added_paths)
        else:
            result = verify_endpoint_source(files, {"endpoints": expected["endpoints"]})
        if result.get("status") != "passed":
            errors.append(result["evidence"])
    if stage == "controller" and not errors:
        for check in _backend_authorization_checks(task):
            error, _ = _verify_backend_authorization(check, root=Path(workspace))
            if error:
                errors.append(error)
    return errors
