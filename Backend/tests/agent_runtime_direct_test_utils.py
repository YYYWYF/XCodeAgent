"""为 agent_runtime_direct 拓扑测试提供共享的正式事实夹具。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


# direct 拓扑的稳定设计事实；测试只在这些事实上做最小覆盖。
DIRECT_TOPOLOGY: dict[str, Any] = {
    "type": "agent_runtime_direct",
    "publicEdgeServiceId": "agent-runtime",
    "serviceIds": ["agent-runtime"],
    "authenticationTermination": "agent-runtime",
    "sourceFactsSha256": "sha256:" + "0" * 64,
}

# Runtime 模板必须为已请求的 Runtime 能力提供入口和 contract test 证据。
RUNTIME_CAPABILITY_ID = "agent_runtime_public_edge"

# 平台策略依赖的 Runtime 模板入口文件，与 agent_runtime_template_policy 保持一致。
RUNTIME_POLICY_FILES: tuple[str, ...] = (
    "src/app/agent/factory.py",
    "src/app/agent/context.py",
    "src/app/models/factory.py",
    "src/app/settings.py",
    "src/app/persistence/checkpointer.py",
    "src/app/tools/__init__.py",
    "src/app/interaction/schemas.py",
    "src/app/interaction/service.py",
)

_AGENT_CONTRACT: dict[str, Any] = {
    "agentId": "assistant",
    "invocation": {
        "serviceId": "agent-runtime",
        "exposure": "public",
        "path": "/ag-ui/agents/assistant",
    },
    "agentSettings": {"tools": {"bindings": []}},
}


def direct_topology_projection(**overrides: Any) -> dict[str, Any]:
    """返回 direct 拓扑的 TechnicalPlan 投影，可按需覆盖单个设计事实。"""

    return {**deepcopy(DIRECT_TOPOLOGY), **overrides}


def direct_project_plan(**overrides: Any) -> dict[str, Any]:
    """构造 direct 拓扑的最小 ProjectPlan（页面写在 frontend_pages 树）。"""

    plan: dict[str, Any] = {
        "version": "plan-v1",
        "topology": direct_topology_projection(),
        "frontend_pages": [{"pageId": "assistant", "references": {}}],
        "entities": [],
        "api_contracts": [],
        "agent_contracts": [deepcopy(_AGENT_CONTRACT)],
    }
    plan.update(overrides)
    return plan


def direct_technical_plan(**overrides: Any) -> dict[str, Any]:
    """构造 direct 拓扑的最小已确认 TechnicalPlan（页面写在扁平 pages）。"""

    plan: dict[str, Any] = {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "sourceConfigRevision": 1,
        "topology": direct_topology_projection(),
        "pages": [{"pageId": "assistant", "references": {}}],
        "entities": [],
        "api_contracts": [],
        "agent_contracts": [deepcopy(_AGENT_CONTRACT)],
    }
    plan.update(overrides)
    return plan


def direct_product_plan() -> dict[str, Any]:
    """构造包含一个浮窗 Agent Surface 的最小已确认 ProductPlan。"""

    return {
        "pages": [
            {
                "pageId": "assistant",
                "name": "助手页",
                "information_items": [{"itemId": "selected_ids", "label": "已选记录"}],
                "actions": [{"actionId": "open_assistant"}],
            }
        ],
        "agents": [
            {
                "agentId": "assistant",
                "name": "助手",
                "purpose": "分析记录并协助跟进。",
                "entryPageIds": ["assistant"],
                "capabilities": [{"capabilityId": "analyze", "name": "分析记录"}],
                "pageActionBindings": [
                    {
                        "pageId": "assistant",
                        "actionIds": ["open_assistant"],
                        "surface": {
                            "type": "floating_panel",
                            "enabled": True,
                            "contextItemIds": ["selected_ids"],
                        },
                    }
                ],
            }
        ],
    }


def write_application_config(root: Path, *, auth_enabled: bool = True) -> None:
    """写入满足当前 Schema 的最小 application.json。"""

    xcodeagent = root / ".xcodeagent"
    xcodeagent.mkdir(parents=True, exist_ok=True)
    (xcodeagent / "application.json").write_text(
        json.dumps(
            {
                "schemaVersion": 6,
                "configRevision": 1,
                "auth": {"enable": auth_enabled},
                "authorization": {
                    "enabled": False,
                    "initialAdministratorSubjects": [],
                },
            }
        ),
        encoding="utf-8",
    )


def write_technical_plan(root: Path, plan: dict[str, Any]) -> None:
    """把已确认 TechnicalPlan 写入工作区正式路径。"""

    plans = root / ".xcodeagent" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "technical-plan.json").write_text(json.dumps(plan), encoding="utf-8")


def write_runtime_template(
    root: Path,
    *,
    manifest_topology: str | None = None,
    capabilities: dict[str, Any] | None = None,
    auth_enabled: bool = True,
) -> None:
    """写入前端入口、Runtime 模板入口与 capability manifest。"""

    frontend = root / "frontend"
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / "package.json").write_text("{}\n", encoding="utf-8")

    runtime = root / "agent-runtime"
    for relative_path in RUNTIME_POLICY_FILES:
        target = runtime / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# test\n", encoding="utf-8")
    (runtime / "tests").mkdir(exist_ok=True)
    (runtime / "template-capabilities.json").write_text(
        json.dumps(
            {
                "topology": manifest_topology
                if manifest_topology is not None
                else str(DIRECT_TOPOLOGY["type"]),
                "capabilities": capabilities
                if capabilities is not None
                else {
                    RUNTIME_CAPABILITY_ID: {
                        "entrypoint": "src/app/main.py",
                        "contractTests": ["tests/test_public_edge.py"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    write_application_config(root, auth_enabled=auth_enabled)
