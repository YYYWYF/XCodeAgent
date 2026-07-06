from __future__ import annotations

from typing import Any, AsyncIterator, Iterable, Optional
from uuid import uuid4

from ag_ui.core import (
    CustomEvent,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from ag_ui.encoder import EventEncoder

from app.agent import AgentRuntime
from app.orchestrator import (
    DevelopmentOrchestratorRuntime,
    attach_orchestration_data,
    summarize_orchestration_payload,
)
from app.requirement_intake import analyze_requirement_intake, summarize_intake
from app.tools.requirement_planner import (
    RequirementPlannerRuntime,
    attach_planning_data,
    summarize_planning_payload,
)


def build_ag_ui_stream(
    run_input: RunAgentInput,
    agent: AgentRuntime,
    planner: Optional[RequirementPlannerRuntime] = None,
    orchestrator: Optional[DevelopmentOrchestratorRuntime] = None,
    *,
    accept: Optional[str] = None,
) -> AsyncIterator[str]:
    encoder = EventEncoder(accept or "text/event-stream")
    message_id = str(uuid4())

    async def stream() -> AsyncIterator[str]:
        yield encoder.encode(
            RunStartedEvent(
                threadId=run_input.thread_id,
                runId=run_input.run_id,
                parentRunId=run_input.parent_run_id,
            )
        )

        try:
            user_message = _last_user_message(run_input)
            if not user_message:
                raise ValueError("AG-UI input does not contain a user message.")

            forwarded_props = _forwarded_props(run_input)
            if forwarded_props.get("agentMode") == "requirement-planner":
                if planner is None:
                    raise ValueError("Requirement planner is not configured.")

                planning_payload = await planner.run(
                    user_message,
                    planner_state=_optional_dict(forwarded_props.get("plannerState")),
                    application=_optional_dict(forwarded_props.get("application")),
                    action=_optional_str(forwarded_props.get("plannerAction")) or "answer",
                )
                answer = attach_planning_data(
                    summarize_planning_payload(planning_payload),
                    planning_payload,
                )

                yield encoder.encode(
                    CustomEvent(name="requirement-planner", value=planning_payload)
                )
                yield encoder.encode(StateSnapshotEvent(snapshot={"planning": planning_payload}))
                yield encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant"))
                for chunk in _chunk_text(answer):
                    yield encoder.encode(TextMessageContentEvent(messageId=message_id, delta=chunk))
                yield encoder.encode(TextMessageEndEvent(messageId=message_id))
                yield encoder.encode(
                    RunFinishedEvent(
                        threadId=run_input.thread_id,
                        runId=run_input.run_id,
                        result={
                            "messageId": message_id,
                            "agentMode": "requirement-planner",
                            "planning": planning_payload,
                        },
                    )
                )
                return

            if forwarded_props.get("agentMode") == "development-orchestrator":
                if orchestrator is None:
                    raise ValueError("Development orchestrator is not configured.")

                orchestration_payload = await orchestrator.run(
                    user_message,
                    orchestrator_state=_optional_dict(forwarded_props.get("orchestratorState")),
                    planner_state=_optional_dict(forwarded_props.get("plannerState")),
                    application=_optional_dict(forwarded_props.get("application")),
                    workspace_root=_optional_str(forwarded_props.get("workspaceRoot")),
                    action=_optional_str(forwarded_props.get("orchestratorAction")) or "answer",
                )
                answer = attach_orchestration_data(
                    summarize_orchestration_payload(orchestration_payload),
                    orchestration_payload,
                )

                yield encoder.encode(
                    CustomEvent(name="development-orchestrator", value=orchestration_payload)
                )
                yield encoder.encode(StateSnapshotEvent(snapshot={"orchestration": orchestration_payload}))
                yield encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant"))
                for chunk in _chunk_text(answer):
                    yield encoder.encode(TextMessageContentEvent(messageId=message_id, delta=chunk))
                yield encoder.encode(TextMessageEndEvent(messageId=message_id))
                yield encoder.encode(
                    RunFinishedEvent(
                        threadId=run_input.thread_id,
                        runId=run_input.run_id,
                        result={
                            "messageId": message_id,
                            "agentMode": "development-orchestrator",
                            "orchestration": orchestration_payload,
                        },
                    )
                )
                return

            intake_decision = analyze_requirement_intake(user_message)
            yield encoder.encode(CustomEvent(name="requirement-intake", value=intake_decision))

            if intake_decision["route"] == "development-orchestrator" and orchestrator is not None:
                orchestration_payload = await orchestrator.run(
                    user_message,
                    orchestrator_state={"requirement": user_message, "intake": intake_decision},
                    planner_state=_optional_dict(forwarded_props.get("plannerState")),
                    application=_optional_dict(forwarded_props.get("application")),
                    workspace_root=_optional_str(forwarded_props.get("workspaceRoot")),
                    action="start",
                )
                orchestration_payload["intake"] = intake_decision
                answer = "\n\n".join(
                    [
                        summarize_intake(intake_decision),
                        summarize_orchestration_payload(orchestration_payload),
                    ]
                )

                yield encoder.encode(
                    CustomEvent(name="development-orchestrator", value=orchestration_payload)
                )
                yield encoder.encode(
                    StateSnapshotEvent(
                        snapshot={"intake": intake_decision, "orchestration": orchestration_payload}
                    )
                )
                yield encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant"))
                for chunk in _chunk_text(answer):
                    yield encoder.encode(TextMessageContentEvent(messageId=message_id, delta=chunk))
                yield encoder.encode(TextMessageEndEvent(messageId=message_id))
                yield encoder.encode(
                    RunFinishedEvent(
                        threadId=run_input.thread_id,
                        runId=run_input.run_id,
                        result={
                            "messageId": message_id,
                            "agentMode": "development-orchestrator",
                            "intake": intake_decision,
                            "orchestration": orchestration_payload,
                        },
                    )
                )
                return

            result = await agent.chat(
                user_message,
                session_id=run_input.thread_id,
                system_prompt=_optional_str(forwarded_props.get("systemPrompt")),
                workspace_root=_optional_str(forwarded_props.get("workspaceRoot")),
                temperature=_optional_float(forwarded_props.get("temperature")),
                max_tokens=_optional_int(forwarded_props.get("maxTokens")),
                approval_decision=_optional_dict(forwarded_props.get("approvalDecision")),
            )
            answer = str(result.get("answer", ""))
            approval_payload = _optional_dict(result.get("approval"))
            approvals_payload = _optional_list(result.get("approvals"))
            code_changes_payload = _optional_dict(result.get("codeChanges"))
            if approval_payload:
                yield encoder.encode(
                    CustomEvent(name="tool-approval-required", value=approval_payload)
                )
            if code_changes_payload:
                yield encoder.encode(
                    CustomEvent(name="workspace-code-changes", value=code_changes_payload)
                )
            snapshot: dict[str, Any] = {}
            if approval_payload:
                snapshot["approval"] = approval_payload
            if approvals_payload:
                snapshot["approvals"] = approvals_payload
            if code_changes_payload:
                snapshot["codeChanges"] = code_changes_payload
            if snapshot:
                yield encoder.encode(StateSnapshotEvent(snapshot=snapshot))

            yield encoder.encode(TextMessageStartEvent(messageId=message_id, role="assistant"))
            for chunk in _chunk_text(answer):
                yield encoder.encode(TextMessageContentEvent(messageId=message_id, delta=chunk))
            yield encoder.encode(TextMessageEndEvent(messageId=message_id))
            yield encoder.encode(
                RunFinishedEvent(
                    threadId=run_input.thread_id,
                    runId=run_input.run_id,
                    result={
                        "messageId": message_id,
                        "agentMode": "main-agent",
                        "intake": intake_decision,
                        "model": result.get("model"),
                        "sessionId": result.get("session_id"),
                        "approval": approval_payload,
                        "approvals": approvals_payload,
                        "codeChanges": code_changes_payload,
                    },
                )
            )
        except Exception as exc:
            yield encoder.encode(RunErrorEvent(message=str(exc), code="AG_UI_RUN_FAILED"))

    return stream()


def _last_user_message(run_input: RunAgentInput) -> str:
    for message in reversed(run_input.messages):
        if getattr(message, "role", None) == "user":
            return _message_content_to_text(getattr(message, "content", ""))
    return ""


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part).strip()

    return str(content).strip()


def _forwarded_props(run_input: RunAgentInput) -> dict[str, Any]:
    value = run_input.forwarded_props
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip() else None


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_dict(value: Any) -> Optional[dict[str, Any]]:
    return value if isinstance(value, dict) else None


def _optional_list(value: Any) -> Optional[list[Any]]:
    return value if isinstance(value, list) else None


def _chunk_text(text: str, *, size: int = 80) -> Iterable[str]:
    if not text:
        yield ""
        return

    for index in range(0, len(text), size):
        yield text[index : index + size]
