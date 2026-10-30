"""只向显式 Runtime 页面补正式 Agent 会话合同，不影响 Java 前端生成提示。"""

import json

from app.services.runtime_agent_contracts import agent_contract_index
from app.services.runtime_business_contract import serves_python_business


def runtime_frontend_instruction(plan: dict, tasks: list[dict]) -> str:
    """从当前已确认页面绑定生成同源 AG-UI 说明，不传播 Debug 或模型凭据。"""

    if not serves_python_business(plan):
        return ""
    contracts = agent_contract_index(plan)
    pages = {str(task.get("unit_id") or "").removeprefix("page:") for task in tasks
             if str(task.get("unit_id") or "").startswith("page:")}
    bindings = [{"agentId": agent_id, "name": contract["name"], "purpose": contract["purpose"],
                 "pageIds": sorted(set(contract["pageIds"]) & pages), "path": f"/agents/{agent_id}/run"}
                for agent_id, contract in contracts.items() if set(contract["pageIds"]) & pages]
    if not bindings:
        return ""
    return (
        "## Confirmed Runtime Agent surfaces\n"
        "Compose the platform's fixed AgentConversation components for these page-bound Agents. "
        "Their default Runtime adapter already uses @ag-ui/client HttpAgent and @ag-ui/core types, "
        "through the exact same-origin paths below. The template already supplies these dependencies and proxy. "
        "A conversational Agent run is not a business REST endpoint and must not be wrapped in useRequest/axios, "
        "handwritten fetch, SSE transport or manual event parser. Keep one stable thread per visible conversation, "
        "display official text events and consume agent_runtime_result.v1 / agent_runtime_error.v1 or agentRuntime "
        "snapshot. pendingInteraction requires explicit UI: show message and payload, submit interactionResponse "
        "with its exact interactionId and user value in forwardedProps. Confirmation/approval uses strict true/false; "
        "clarification supplies the actual answer, never auto-confirm. A RUN_FINISHED alone does not prove a "
        "successful business result. Show pending/error states and abort on unmount; never automatically replay a "
        "write on connection failure. Identity comes from server anonymous Cookie; never send a user/Principal, "
        "namespace, debug credential, model key, database key or fixed Runtime port. Agent Tools already call their "
        "ApplicationService locally; the page must not invoke those Tool endpoints to simulate Agent behavior. "
        "Preserve separately declared ordinary page business actions.\n"
        + json.dumps(bindings, ensure_ascii=False, indent=2) + "\n\n"
    )
