"""将当前 Endpoint 业务职责绑定到显式 Python Runtime，而非旧 Direct SQL 合同。"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any


def runtime_business_architecture() -> dict[str, str]:
    """按显式 Runtime 选择投射技术架构，避免沿 Java 默认栈生成或校验。"""

    return {
        "frontend": "React 前端通过业务接口和 AG-UI 与 Python Runtime 通信。",
        "backend": "Python 3.12 + FastAPI + DeepAgents Runtime 承载业务接口、应用服务及智能体。",
        "data": "业务数据使用 MySQL8，沿当前项目数据源与已确认实体表接入；Agent checkpoint 独立持久化。",
    }


def serves_python_business(plan: Mapping[str, Any]) -> bool:
    """只识别正式服务图，不按目录、Agent 名称或模型输出猜测架构。"""

    topology = plan.get("topology")
    if not isinstance(topology, Mapping):
        return False
    if topology.get("type") != "agent_runtime_direct":
        return False
    if (list(topology.get("serviceIds") or ()) != ["agent-runtime"]
            or topology.get("publicEdgeServiceId") != "agent-runtime"
            or topology.get("authenticationTermination") != "anonymous-session"):
        raise ValueError("Runtime 业务服务图无效，不能转交 Java 执行器。")
    return True


def backend_generation_constraints(plan: Mapping[str, Any]) -> dict[str, str]:
    """冻结实现根，保留新版 backend Unit 的业务身份、职责和依赖图。"""

    return {"backend_implementation": "python_runtime", "backend_root": "agent-runtime"} if serves_python_business(plan) else {}


def python_backend_context(constraints: Mapping[str, Any]) -> bool:
    """拒绝实现类型与工程根不一致的冻结输入，不允许模型自行切换写入根。"""

    implementation = constraints.get("backend_implementation")
    root = constraints.get("backend_root")
    if implementation is None and root is None:
        return False
    if implementation != "python_runtime" or root != "agent-runtime":
        raise ValueError("Backend 冻结实现合同无效。")
    return True


def snapshot_prior_runtime_endpoint_operations(
    workspace: str | Path | None,
    plan: Mapping[str, Any],
    registry: Mapping[str, Any],
    required_unit_ids: list[str] | tuple[str, ...],
) -> dict[str, dict[str, str]]:
    """只查本轮 Python Endpoint 原交付路径的实际文件状态，保留路径但不沿用旧操作。"""

    implementation = backend_generation_constraints(plan)
    if not workspace or not implementation:
        return {}
    root = Path(workspace).resolve()
    if not root.is_dir():
        return {}
    required = set(required_unit_ids)
    operations: dict[str, dict[str, str]] = {}
    for task in registry.values():
        unit_id = str(task.get("unit_id") or "")
        if unit_id not in required or not unit_id.startswith("backend:endpoint:") or task.get("owner") != "backend":
            continue
        refs = task.get("source_refs") or {}
        if any(refs.get(key) != value for key, value in implementation.items()):
            continue
        for deliverable in task.get("deliverables", ()):
            for path in deliverable.get("paths", ()):
                if (not path.startswith("agent-runtime/src/app/") or not path.endswith(".py")
                        or any(part in {"", ".", ".."} for part in path.split("/"))):
                    continue
                target = root / path
                # 不跟随符号链接，也不把已有目录当作可以新增的源文件。
                try:
                    if target.resolve() != target:
                        continue
                    is_file = target.is_file()
                    if not is_file and target.exists():
                        continue
                except (OSError, RuntimeError):
                    continue
                operations.setdefault(unit_id, {})[path] = "modify" if is_file else "add"
    return operations


def runtime_endpoint_request_fields(plan: dict[str, Any], unit_id: str) -> list[dict[str, Any]]:
    """提取本 Python Endpoint 正式请求字段，保留每个同名字段各自的位置和必填性。"""

    parts = unit_id.split(":", 3)
    if len(parts) != 4 or parts[:2] != ["backend", "endpoint"] or not serves_python_business(plan):
        return []
    from app.services.api_design import ApiDesignError, endpoint_api_fields, find_endpoint

    try:
        contract, endpoint = find_endpoint(plan, parts[2], parts[3])
    except ApiDesignError:
        return []
    return [{key: field[key] for key in ("location", "path", "type", "required")}
            for field in endpoint_api_fields(contract, endpoint) if field["side"] == "request"]
