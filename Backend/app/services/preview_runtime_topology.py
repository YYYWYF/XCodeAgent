"""从当前技术规划读取预览界面的服务拓扑。"""

import json
from pathlib import Path

from app.branding import WORKSPACE_ARTIFACT_DIR


def preview_topology_type(workspace: str | Path) -> str | None:
    """只投影当前明确选择的拓扑，不根据目录或运行状态猜测应用类型。"""

    path = Path(workspace).expanduser().resolve() / WORKSPACE_ARTIFACT_DIR / "plans" / "technical-plan.json"
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    topology = plan.get("topology") if isinstance(plan, dict) else None
    topology_type = topology.get("type") if isinstance(topology, dict) else None
    return topology_type if topology_type in {"backend_direct", "agent_runtime_direct"} else None
