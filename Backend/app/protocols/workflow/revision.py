"""主 Workflow formal revision 动作的严格解析与生命周期绑定。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.domain.application_lifecycle import PendingInteractionType
from app.domain.application_revision import RevisionDraftInteraction
from app.services.application_lifecycle import load_application_lifecycle
from app.workspace.revision_draft_documents import load_revision_draft


def parse_revision_draft_interaction(value: Any) -> RevisionDraftInteraction | None:
    """严格解析 revisionInteraction，不接受旧 clarification 或任意恢复字段。"""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("revisionInteraction 必须是对象。")
    return RevisionDraftInteraction.model_validate(value)


def bind_revision_draft_interaction(
    workspace: str | Path,
    interaction: RevisionDraftInteraction,
) -> dict[str, Any]:
    """绑定当前 formal lease、Lifecycle interaction 和草稿正文哈希并返回恢复凭据。"""

    lifecycle = load_application_lifecycle(workspace)
    active = lifecycle.active_formal_revision if lifecycle is not None else None
    if lifecycle is None or active is None or active.change_id != interaction.change_id:
        raise ValueError("revisionInteraction 不属于当前 active formal revision。")
    if active.current_artifact != interaction.artifact_key:
        raise ValueError("revisionInteraction artifactKey 与当前草稿不匹配。")
    if lifecycle.revision != interaction.based_on_lifecycle_revision:
        raise ValueError("revisionInteraction 基于过期 lifecycle revision。")

    matched_run_id = ""
    for run_id, execution in lifecycle.active_executions.items():
        pending = execution.pending_interaction
        if (
            pending is not None
            and pending.id == interaction.interaction_id
            and pending.type == PendingInteractionType.REVISION_DRAFT_CONFIRMATION
            and pending.based_on_revision == interaction.based_on_lifecycle_revision
        ):
            matched_run_id = run_id
            break
    if not matched_run_id:
        raise ValueError("revisionInteraction 已过期或不是当前草稿确认交互。")

    _metadata, markdown, _artifact = load_revision_draft(
        workspace,
        change_id=interaction.change_id,
        artifact_key=interaction.artifact_key,
    )
    actual_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    if actual_sha256 != interaction.draft_sha256:
        raise ValueError("revisionInteraction draftSha256 已过期，请刷新草稿。")
    return {
        "interaction": interaction.model_dump(mode="json", by_alias=False, exclude_none=True),
        "request": active.request,
        "target": active.target.model_dump(mode="json", by_alias=True),
        "lifecycleSubmission": {
            "runId": matched_run_id,
            "id": interaction.interaction_id,
            "basedOnRevision": interaction.based_on_lifecycle_revision,
        },
    }
