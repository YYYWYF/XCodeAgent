"""后端权限资源常量的完整清单与最近成功应用记录，不使用内容摘要。"""

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from app.services.authorization_constants_projection import AUTH_CONSTANTS_RELATIVE_PATH


BACKEND_AUTH_UNIT = "backend:auth-resources"
BACKEND_AUTH_TASK = "backend-auth-resources"
BACKEND_AUTH_CAPABILITY = "backend.auth.resources"
BACKEND_AUTH_EXECUTOR = "authorization.backend_resources"
BACKEND_AUTH_PATH = AUTH_CONSTANTS_RELATIVE_PATH.as_posix()
BACKEND_AUTH_RECORD = Path(".devagentstudio/runtime/backend-auth-resources.json")


def normalize_backend_constants(value: Any) -> list[dict[str, str]]:
    """按常量名称排序并检查当前格式，使比较不受清单排列顺序影响。"""

    if not isinstance(value, (list, tuple)):
        raise ValueError("后端权限常量清单必须为数组。")
    result = []
    names = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"name", "resourceKey"}:
            raise ValueError("权限常量必须包含 name 和 resourceKey。")
        name, key = item["name"], item["resourceKey"]
        if (not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", key)
                or name != f"{key.upper()}_RESOURCE" or name in names
                or key == "system_authorization_management"):
            raise ValueError("后端权限常量名称、资源键或唯一性不合法。")
        names.add(name)
        result.append({"name": name, "resourceKey": key})
    return sorted(result, key=lambda item: item["name"])


def compile_backend_resource_constants(
    project_plan: Mapping[str, Any], application_config: Mapping[str, Any],
) -> list[dict[str, str]]:
    """由当前应用权限开关和全部正式接口绑定生成去重后的业务资源常量。"""

    authorization = application_config.get("authorization") or {}
    if authorization.get("enabled") is not True:
        return []
    manifest = project_plan.get("authorization_manifest")
    if not isinstance(manifest, Mapping):
        raise ValueError("权限已开启，但正式技术规划缺少 authorization_manifest。")
    endpoint_ids = {endpoint["id"] for contract in project_plan.get("api_contracts", ())
                    for endpoint in contract.get("endpoints", ())}
    keys = set()
    for binding in (manifest.get("bindings") or {}).get("endpoints", ()):
        if binding.get("endpointId") not in endpoint_ids:
            raise ValueError("权限资源绑定引用了不存在的正式接口。")
        keys.update(binding.get("operationResourceKeys") or ())
    keys.discard("system_authorization_management")
    return normalize_backend_constants([
        {"name": f"{key.upper()}_RESOURCE", "resourceKey": key} for key in sorted(keys)
    ])


def backend_resource_record_path(workspace: str | Path) -> Path:
    """定位当前应用的完成记录，拒绝通过符号链接写出工作区。"""

    root = Path(workspace).resolve()
    target = root / BACKEND_AUTH_RECORD
    if not target.resolve().is_relative_to(root):
        raise ValueError("后端权限完成记录超出当前工作区。")
    return target


def backend_resources_applied(workspace: str | Path, constants: Any) -> bool:
    """直接比较最近成功清单；不存在或无效的记录不能抵扣本轮执行。"""

    try:
        record = json.loads(backend_resource_record_path(workspace).read_text(encoding="utf-8"))
        return (record.get("status") == "completed"
                and normalize_backend_constants(record["constants"]) == normalize_backend_constants(constants))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def record_backend_resources(workspace: str | Path, constants: Any) -> None:
    """仅在常量成功写入并校验后原子保存完整清单，失败不覆盖原成功记录。"""

    content = json.dumps({"status": "completed", "constants": normalize_backend_constants(constants)},
                         ensure_ascii=False, indent=2) + "\n"
    target = backend_resource_record_path(workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".backend-auth-resources-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backend_resource_planning_context(
    project_plan: Mapping[str, Any], build_context: Mapping[str, Any], *,
    application_config: Mapping[str, Any], workspace: str | Path,
) -> dict[str, Any]:
    """为后端构建冻结完整资源清单及应用状态，并在 Bootstrap 前登记规划单元。"""

    required = [unit for unit in build_context.get("required_unit_ids", ()) if unit != BACKEND_AUTH_UNIT]
    constants = compile_backend_resource_constants(project_plan, application_config)
    if constants and any(unit.startswith("backend:") for unit in required):
        required.insert(0, BACKEND_AUTH_UNIT)
    return {**build_context, "required_unit_ids": required, "backend_auth_resources": {
        "constants": constants,
        "applied": bool(constants) and backend_resources_applied(workspace, constants),
    }}
