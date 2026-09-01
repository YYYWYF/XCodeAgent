"""执行二次修改只读调查并复用现有路由合同。"""

from __future__ import annotations

from typing import Any

from app.agents.direct_modification import (
    DirectModificationDecision,
    _normalize_direct_modification_decision,
)
from app.agents.revision_investigator.agent import revision_investigator_prompt
from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.utils.model_output import extract_json_object


def investigate_direct_modification_intent(
    *,
    user_request: str,
    conversation_summary: str,
    workspace: str,
    workspace_snapshot: dict[str, Any] | None,
    fast_decision: DirectModificationDecision,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> DirectModificationDecision:
    """在快速分类证据不足时调用只读 Agent，并复用现有确定性路由校验。"""

    if not workspace.strip() or not user_request.strip():
        return fast_decision
    from app.agents import create_agent_bundle

    try:
        agent_text = invoke_agent_with_tool_activity(
            create_agent_bundle(workspace, selected_skill_names).revision_investigator,
            {
                "messages": [
                    {
                        "role": "user",
                        "content": revision_investigator_prompt(
                            request=user_request,
                            conversation_summary=conversation_summary,
                            fast_decision=_decision_payload(fast_decision),
                            current_target=(workspace_snapshot or {}).get("currentTarget"),
                        ),
                    }
                ]
            },
            workspace=workspace,
            on_tool_activity=on_tool_activity,
        )
        payload = extract_json_object(agent_text)
        if not isinstance(payload, dict):
            return fast_decision
        return _normalize_direct_modification_decision(
            payload,
            user_request=user_request,
            target=(workspace_snapshot or {}).get("currentTarget"),
        )
    except Exception:  # noqa: BLE001 - 调查失败必须保留原安全澄清结果
        return fast_decision


def _decision_payload(decision: DirectModificationDecision) -> dict[str, Any]:
    """把快速分类结果投影为只包含路由事实的有界调查输入。"""

    return {
        "route": decision.intent,
        "owner": decision.owner,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "clarificationQuestion": decision.clarification_question,
        "candidatePaths": list(decision.target_paths),
        "formalBranch": (
            decision.formal_branch.value if decision.formal_branch is not None else None
        ),
        "revisionType": (
            decision.revision_type.value if decision.revision_type is not None else None
        ),
        "earliestArtifact": (
            decision.earliest_artifact.value
            if decision.earliest_artifact is not None
            else None
        ),
        "affectedArtifactKeys": list(decision.affected_artifact_keys),
        "affectedResourceKeys": list(decision.affected_resource_keys),
    }
