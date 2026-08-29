"""应用二次修改统一路由与正式修订交互模型。"""

from __future__ import annotations

from enum import StrEnum
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RevisionModel(BaseModel):
    """为二次修改模型提供严格的当前合同与驼峰序列化。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class RevisionRoute(StrEnum):
    """定义工作台自然语言输入的五类稳定顶层路由。"""

    CASUAL_CHAT = "casual_chat"
    WORKSPACE_QUESTION = "workspace_question"
    CLARIFICATION = "clarification"
    IMPLEMENTATION_FIX = "implementation_fix"
    FORMAL_REVISION = "formal_revision"


class FormalRevisionBranch(StrEnum):
    """定义正式修改进入原设计流程或工作台草稿流程的分支。"""

    DESIGN_STAGE_REVISION = "design_stage_revision"
    WORKBENCH_PLAN_REVISION = "workbench_plan_revision"


class RevisionType(StrEnum):
    """定义确定性路由所识别的六类正式修改事实。"""

    REQUIREMENT_SCOPE_CHANGE = "requirement_scope_change"
    PRODUCT_BEHAVIOR_CHANGE = "product_behavior_change"
    UI_VISUAL_CHANGE = "ui_visual_change"
    TECHNICAL_CONTRACT_CHANGE = "technical_contract_change"
    ENDPOINT_IMPLEMENTATION_CHANGE = "endpoint_implementation_change"
    DATA_SOURCE_CHANGE = "data_source_change"


class EarliestRevisionArtifact(StrEnum):
    """定义一次正式修改允许选择的最早权威产物。"""

    REQUIREMENT_SPEC = "requirement-spec"
    PRODUCT_PLAN = "product-plan"
    UI_DESIGN = "ui-design"
    TECHNICAL_PLAN = "technical-plan"


class RevisionTarget(RevisionModel):
    """保存应用、页面或接口会话的稳定业务目标。"""

    type: Literal["application", "page", "endpoint"]
    page_id: str | None = Field(default=None, alias="pageId", max_length=512)
    api_contract_id: str | None = Field(
        default=None,
        alias="apiContractId",
        max_length=512,
    )
    endpoint_id: str | None = Field(default=None, alias="endpointId", max_length=512)

    @model_validator(mode="after")
    def validate_identifiers(self) -> "RevisionTarget":
        """要求页面和接口目标携带完整稳定标识，禁止模糊目标进入正式流程。"""

        if self.type == "page" and not str(self.page_id or "").strip():
            raise ValueError("页面修订目标必须提供 pageId。")
        if self.type == "endpoint" and (
            not str(self.api_contract_id or "").strip()
            or not str(self.endpoint_id or "").strip()
        ):
            raise ValueError("接口修订目标必须提供 apiContractId 和 endpointId。")
        return self


class RevisionRoutingCandidate(RevisionModel):
    """校验模型产出的只读分类候选，写权限仍由确定性服务控制。"""

    route: RevisionRoute
    formal_branch: FormalRevisionBranch | None = Field(
        default=None,
        alias="formalBranch",
    )
    revision_type: RevisionType | None = Field(default=None, alias="revisionType")
    earliest_artifact: EarliestRevisionArtifact | None = Field(
        default=None,
        alias="earliestArtifact",
    )
    owner: Literal["frontend", "backend", "fullstack", "workspace", "none", "unknown"]
    affected_artifact_keys: list[str] = Field(
        default_factory=list,
        alias="affectedArtifactKeys",
        max_length=100,
    )
    affected_resource_keys: list[str] = Field(
        default_factory=list,
        alias="affectedResourceKeys",
        max_length=100,
    )
    candidate_paths: list[str] = Field(
        default_factory=list,
        alias="candidatePaths",
        max_length=100,
    )
    questions: list[str] = Field(default_factory=list, max_length=10)
    reason: str = Field(min_length=1, max_length=2048)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_formal_fields(self) -> "RevisionRoutingCandidate":
        """要求 formal_revision 同时给出 branch、类型和最早产物。"""

        formal_values = (
            self.formal_branch,
            self.revision_type,
            self.earliest_artifact,
        )
        if self.route == RevisionRoute.FORMAL_REVISION and any(
            value is None for value in formal_values
        ):
            raise ValueError("formal_revision 必须提供 formalBranch、revisionType 和 earliestArtifact。")
        if self.route != RevisionRoute.FORMAL_REVISION and any(
            value is not None for value in formal_values
        ):
            raise ValueError("非正式修改不得携带 formal revision 字段。")
        return self


class RevisionImpact(RevisionModel):
    """投影执行任何正式分支前的只读影响范围确认内容。"""

    formal_branch: FormalRevisionBranch = Field(alias="formalBranch")
    revision_type: RevisionType = Field(alias="revisionType")
    earliest_artifact: EarliestRevisionArtifact = Field(alias="earliestArtifact")
    affected_artifacts: list[str] = Field(alias="affectedArtifacts", max_length=100)
    affected_resources: list[str] = Field(alias="affectedResources", max_length=100)
    reason: str = Field(min_length=1, max_length=2048)
    risks: list[str] = Field(default_factory=list, max_length=20)
    # 影响卡只投影当前 JSON 证据，不把 Markdown 或全局 Contract ID 写入生命周期。
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    analysis_status: Literal["completed", "insufficient_evidence"] = Field(
        default="completed",
        alias="analysisStatus",
    )


class ConfirmedImpact(RevisionModel):
    """引用一次由服务端签发并一次性消费的影响范围确认。"""

    interaction_id: str = Field(alias="interactionId", min_length=1, max_length=256)


class StartRevisionRequest(RevisionModel):
    """校验进入设计分支或工作台草稿分支的统一服务端请求。"""

    source: Literal["conversation_handoff"]
    formal_branch: FormalRevisionBranch = Field(alias="formalBranch")
    target: RevisionTarget
    request: str = Field(min_length=1, max_length=16_000)
    confirmed_impact: ConfirmedImpact = Field(alias="confirmedImpact")


class RevisionContinuationRequest(RevisionModel):
    """校验前端只能回传 changeId 与不透明一次性 continuation token。"""

    change_id: str = Field(alias="changeId", min_length=1, max_length=256)
    token: str = Field(min_length=32, max_length=1024)


class RevisionDraftInteraction(RevisionModel):
    """校验工作台草稿卡只能提交当前产物与当前正文版本的结构化动作。"""

    change_id: str = Field(alias="changeId", min_length=1, max_length=256)
    interaction_id: str = Field(alias="interactionId", min_length=1, max_length=256)
    based_on_lifecycle_revision: int = Field(alias="basedOnLifecycleRevision", ge=1)
    artifact_key: str = Field(alias="artifactKey", min_length=1, max_length=512)
    draft_sha256: str = Field(alias="draftSha256", pattern=r"^[0-9a-f]{64}$")
    action: Literal["confirm", "save", "revise", "discard"]
    feedback: str | None = Field(default=None, max_length=16_000)
    edited_markdown: str | None = Field(default=None, alias="editedMarkdown", max_length=2_000_000)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "RevisionDraftInteraction":
        """要求 revise 携带反馈、save 携带正文，避免空动作改变 lifecycle。"""

        if self.action == "revise" and not str(self.feedback or "").strip():
            raise ValueError("revision draft revise 必须提供 feedback。")
        if self.action == "save" and self.edited_markdown is None:
            raise ValueError("revision draft save 必须提供 editedMarkdown。")
        return self


class RevisionArtifactReference(RevisionModel):
    """记录工作台正式产物对一个直接上游 canonical 的哈希引用。"""

    artifact_key: str = Field(alias="artifactKey", min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RevisionDraftMetadata(RevisionModel):
    """描述一个 change 下唯一当前工作台正式产物草稿。"""

    schema_version: Literal["revision-draft.v1"] = Field(
        default="revision-draft.v1",
        alias="schemaVersion",
    )
    change_id: str = Field(alias="changeId", min_length=1, max_length=256)
    artifact_key: str = Field(alias="artifactKey", min_length=1, max_length=512)
    kind: Literal["technical_plan", "database_design"]
    target_id: str = Field(alias="targetId", min_length=1, max_length=512)
    status: Literal["pending_user_confirmation"] = "pending_user_confirmation"
    base_canonical_sha256: str = Field(
        alias="baseCanonicalSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    based_on_canonical: list[RevisionArtifactReference] = Field(
        default_factory=list,
        alias="basedOnCanonical",
        max_length=100,
    )
    generated_at: datetime = Field(alias="generatedAt")
    hidden: dict[str, Any] = Field(default_factory=dict)


class PendingRevisionImpact(RevisionModel):
    """保存尚未批准且未获取 formal revision lease 的影响范围确认。"""

    interaction_id: str = Field(alias="interactionId", min_length=1, max_length=256)
    source_thread_id: str = Field(alias="sourceThreadId", min_length=1, max_length=512)
    source_run_id: str = Field(alias="sourceRunId", min_length=1, max_length=512)
    request: str = Field(min_length=1, max_length=16_000)
    target: RevisionTarget
    impact: RevisionImpact
    based_on_lifecycle_revision: int = Field(alias="basedOnLifecycleRevision", ge=1)
    status: Literal["pending"] = "pending"


class ActiveFormalRevision(RevisionModel):
    """保存一个 application 唯一 active formal revision 及 continuation 绑定。"""

    change_id: str = Field(alias="changeId", min_length=1, max_length=256)
    formal_branch: FormalRevisionBranch = Field(alias="formalBranch")
    source_thread_id: str = Field(alias="sourceThreadId", min_length=1, max_length=512)
    source_run_id: str = Field(alias="sourceRunId", min_length=1, max_length=512)
    request: str = Field(min_length=1, max_length=16_000)
    target: RevisionTarget
    impact_interaction_id: str = Field(
        alias="impactInteractionId",
        min_length=1,
        max_length=256,
    )
    planning_thread_id: str = Field(alias="planningThreadId", min_length=1, max_length=512)
    status: Literal[
        "design_planning",
        "drafting",
        "awaiting_user",
        "continuation_ready",
        "building",
        "stopped",
        "failed",
    ]
    current_artifact: str | None = Field(default=None, alias="currentArtifact", max_length=512)
    remaining_artifacts: list[str] = Field(
        default_factory=list,
        alias="remainingArtifacts",
        max_length=100,
    )
    technical_plan_sha256: str | None = Field(
        default=None,
        alias="technicalPlanSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    continuation_token_sha256: str | None = Field(
        default=None,
        alias="continuationTokenSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    continuation_lifecycle_revision: int | None = Field(
        default=None,
        alias="continuationLifecycleRevision",
        ge=1,
    )
    continuation_source_run_id: str | None = Field(
        default=None,
        alias="continuationSourceRunId",
        max_length=512,
    )
    continuation_consumed_at: datetime | None = Field(
        default=None,
        alias="continuationConsumedAt",
    )
