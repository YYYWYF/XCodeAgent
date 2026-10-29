"""将当前 Endpoint 业务职责绑定到显式 Python Runtime，而非旧 Direct SQL 合同。"""

from collections.abc import Mapping
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
