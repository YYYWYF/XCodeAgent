"""技术规划的显式架构选择；Runtime 开放调试验证并明确说明未完成链路。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any
from app.services.runtime_business_contract import runtime_business_architecture


def topology_selection_payload() -> dict[str, Any]:
    """向原 AG-UI 审阅门提供可用选项和未完成能力的准确原因。"""

    return {
        "mode": "technical_plan_topology_selection", "status": "requires_user_input",
        "message": "请选择应用架构；选择后仍需确认最终技术规划。",
        "questions": [],
        "topologyOptions": [
            {"type": "backend_direct", "label": "Java 业务应用", "available": True,
             "description": "前端 + Java 后端，沿用当前内置数据库与开发流程。"},
            {"type": "agent_runtime_direct", "label": "Agent Runtime 应用", "available": True,
             "description": "前端 + Python Runtime，支持业务接口与七模块智能体。已接入数据库、模型注入和受控启动，需同步当前 Runtime 模板后验证。当前仅支持关闭登录和授权的应用。"},
        ],
    }


def select_technical_plan_topology(
    plan: dict[str, Any], topology_type: str, application_config: dict[str, Any],
) -> dict[str, Any]:
    """确定性写入用户选择，不修改实体、接口、页面或应用能力开关。"""

    options = topology_selection_payload()["topologyOptions"]
    option = next((item for item in options if item["type"] == topology_type), None)
    if option is None:
        raise ValueError("所选应用架构不受支持，请基于当前选项重新选择。")
    if not option["available"]:
        raise ValueError(option["reason"])
    if topology_type == "backend_direct" and plan.get("agent_contracts"):
        raise ValueError("当前技术规划包含可执行 Agent，不能使用未承载 Runtime 的 Java 业务架构。")
    if plan.get("artifact_type") != "technical-plan":
        raise ValueError("架构选择只能绑定当前 TechnicalPlan。")
    if plan.get("sourceConfigRevision") != application_config.get("configRevision"):
        raise ValueError("应用配置已变化，请重新生成技术规划后选择架构。")
    runtime = topology_type == "agent_runtime_direct"
    if runtime and ((application_config.get("auth") or {}).get("enable") is not False
                    or (application_config.get("authorization") or {}).get("enabled") is not False):
        raise ValueError("当前 Runtime 调试仅支持已关闭登录和授权的应用，请先在应用配置中明确关闭这两项。")
    candidate = deepcopy(plan)
    facts = {
        "type": topology_type, "configRevision": application_config.get("configRevision"),
        "authEnabled": (application_config.get("auth") or {}).get("enable"),
        "authorizationEnabled": (application_config.get("authorization") or {}).get("enabled"),
        "entities": plan.get("entities"), "api_contracts": plan.get("api_contracts"),
        "pages": plan.get("pages"),
        "agent_contracts": plan.get("agent_contracts"),
    }
    digest = hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    service_id = "agent-runtime" if runtime else "backend"
    candidate["topology"] = {
        "type": topology_type, "serviceIds": [service_id], "publicEdgeServiceId": service_id,
        "authenticationTermination": "anonymous-session" if runtime else "backend",
        "sourceFactsSha256": f"sha256:{digest}",
    }
    if runtime:
        candidate["architecture"] = runtime_business_architecture()
    candidate["confirmation_status"] = "pending_user_confirmation"
    return candidate


def validate_selected_topology(plan: dict[str, Any], application_config: dict[str, Any]) -> None:
    """正式确认前核对选择及摘要，防止文档同步或模型改写隐藏实现边界。"""

    topology = plan.get("topology")
    if not isinstance(topology, dict) or not isinstance(topology.get("type"), str):
        raise ValueError("请先明确选择应用架构，不能默认生成 Java 应用。")
    expected = select_technical_plan_topology(plan, topology["type"], application_config)["topology"]
    if topology != expected:
        raise ValueError("技术规划或应用配置已变化，请重新选择架构并确认新版技术规划。")
