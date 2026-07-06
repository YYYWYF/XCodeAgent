from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


SubagentMode = Literal["plan-only", "direct-write"]
SubagentStatus = Literal["done", "failed", "blocked"]


class ContextBudget(BaseModel):
    max_input_tokens: int = Field(default=24000, alias="maxInputTokens")
    max_output_tokens: int = Field(default=6000, alias="maxOutputTokens")


class SubagentInput(BaseModel):
    run_id: str = Field(alias="runId")
    task: Dict[str, Any]
    contract: Dict[str, Any]
    workspace: Dict[str, Any] = Field(default_factory=dict)
    allowed_files: List[str] = Field(default_factory=list, alias="allowedFiles")
    forbidden_files: List[str] = Field(default_factory=list, alias="forbiddenFiles")
    mode: SubagentMode = "plan-only"
    context_budget: ContextBudget = Field(default_factory=ContextBudget, alias="contextBudget")


class SubagentResult(BaseModel):
    run_id: str = Field(alias="runId")
    task_id: str = Field(alias="taskId")
    agent_id: str = Field(alias="agentId")
    status: SubagentStatus
    mode: SubagentMode
    summary: str
    changed_files: List[str] = Field(default_factory=list, alias="changedFiles")
    proposed_files: List[str] = Field(default_factory=list, alias="proposedFiles")
    proposed_patch: str = Field(default="", alias="proposedPatch")
    acceptance_evidence: List[str] = Field(default_factory=list, alias="acceptanceEvidence")
    verification_notes: List[str] = Field(default_factory=list, alias="verificationNotes")
    risks: List[str] = Field(default_factory=list)
    next_actions: List[str] = Field(default_factory=list, alias="nextActions")


def build_subagent_input(
    *,
    run_id: str,
    task: Dict[str, Any],
    contract: Dict[str, Any],
    workspace: Dict[str, Any],
) -> Dict[str, Any]:
    mode = "direct-write" if task.get("executionMode") == "subagent-direct-write" else "plan-only"
    target_files = _string_list(task.get("targetFiles"))
    payload = SubagentInput(
        runId=run_id,
        task=task,
        contract=contract,
        workspace=workspace,
        allowedFiles=target_files if mode == "direct-write" else [],
        forbiddenFiles=[] if mode == "direct-write" else target_files,
        mode=mode,
    )
    return _dump(payload)


def placeholder_result(*, run_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(task.get("id") or "task")
    agent_id = str(task.get("assignedAgent") or "main-agent")
    mode = "direct-write" if task.get("executionMode") == "subagent-direct-write" else "plan-only"
    result = SubagentResult(
        runId=run_id,
        taskId=task_id,
        agentId=agent_id,
        status="blocked",
        mode=mode,
        summary="Subagent runtime protocol is ready, but autonomous task execution is not enabled in this slice.",
        changedFiles=[],
        proposedFiles=_string_list(task.get("targetFiles")),
        acceptanceEvidence=[],
        verificationNotes=[],
        risks=["需要后续接入真实 subagent runner 后执行。"],
        nextActions=["由主 Agent 确认执行计划后调度该任务。"],
    )
    return _dump(result)


def assert_direct_write_allowed(*, task: Dict[str, Any], path: str) -> None:
    allowed = set(_string_list(task.get("targetFiles")))
    if task.get("executionMode") != "subagent-direct-write" or path not in allowed:
        raise ValueError(f"Subagent is not allowed to write {path}.")


def subagent_capabilities() -> Dict[str, Any]:
    return {
        "agents": ["scout", "frontend-builder", "backend-builder", "fullstack-builder", "verifier"],
        "modes": ["plan-only", "direct-write"],
        "writePolicy": "direct-write subagents may only write explicit allowedFiles from their assigned task.",
        "status": "protocol-ready",
    }


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item).strip()]


def _dump(value: BaseModel) -> Dict[str, Any]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(by_alias=True)
    return value.dict(by_alias=True)
