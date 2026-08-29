"""应用二次修改的路由结果合同、安全校验、分支和影响范围规则。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.domain.application_revision import (
    EarliestRevisionArtifact,
    FormalRevisionBranch,
    RevisionImpact,
    RevisionRoute,
    RevisionRoutingCandidate,
    RevisionType,
)
from app.domain.change_impact import (
    AnalysisStatus,
    ChangeImpactAnalysis,
    ContractImpact,
    ContractStage,
)
from app.agents.change_impact_analyzer import (
    _analysis_queries,
    validate_change_impact_analysis,
)
from app.services.change_code_scan import sanitize_code_scan_evidence
from app.services.change_contracts import load_confirmed_contract_corpus


_ARTIFACT_PRIORITY = {
    "requirement-spec": 0,
    "product-plan": 1,
    "ui-design": 2,
    "technical-plan": 3,
}
_FORMAL_ARTIFACT_INFO = {
    "requirement-spec": (
        RevisionType.REQUIREMENT_SCOPE_CHANGE,
        FormalRevisionBranch.DESIGN_STAGE_REVISION,
        EarliestRevisionArtifact.REQUIREMENT_SPEC,
    ),
    "product-plan": (
        RevisionType.PRODUCT_BEHAVIOR_CHANGE,
        FormalRevisionBranch.DESIGN_STAGE_REVISION,
        EarliestRevisionArtifact.PRODUCT_PLAN,
    ),
    "ui-design": (
        RevisionType.UI_VISUAL_CHANGE,
        FormalRevisionBranch.DESIGN_STAGE_REVISION,
        EarliestRevisionArtifact.UI_DESIGN,
    ),
    "technical-plan": (
        RevisionType.TECHNICAL_CONTRACT_CHANGE,
        FormalRevisionBranch.WORKBENCH_PLAN_REVISION,
        EarliestRevisionArtifact.TECHNICAL_PLAN,
    ),
}
_FORMAL_ARTIFACT_DOWNSTREAM = {
    "requirement-spec": ("product-plan", "ui-design", "technical-plan"),
    "product-plan": ("ui-design", "technical-plan"),
    "ui-design": ("technical-plan",),
    "technical-plan": (),
}
_FORMAL_ARTIFACT_FILE_PATTERNS = {
    key: re.compile(
        rf"(?:^|/){re.escape('ui-designs' if key == 'ui-design' else key)}\.(?:json|md)$",
        re.IGNORECASE,
    )
    for key in _FORMAL_ARTIFACT_INFO
}


@dataclass(frozen=True)
class RevisionRoutingResult:
    """保存确定性规则处理后的最终路由以及可选影响范围。"""

    candidate: RevisionRoutingCandidate
    impact: RevisionImpact | None = None


def route_from_change_impact(
    analysis: ChangeImpactAnalysis | dict[str, Any],
    *,
    user_request: str,
    workspace: str | None = None,
    target: dict[str, Any] | None = None,
    owner: str = "unknown",
    allow_pending_code_scan: bool = False,
) -> RevisionRoutingResult:
    """只依据已校验 Analyzer 事实生成执行候选，模型不能直接选择 Workflow 分支。

    ``allow_pending_code_scan`` 只表示“契约已 preserves、准备进入只读 code.scan”，
    不表示已经获得写权限；真正的 implementation_fix 仍必须在扫描节点取得代码发现后
    才能继续。默认值保持严格模式，供外部调用者直接得到最终路由。
    """

    normalized = (
        analysis
        if isinstance(analysis, ChangeImpactAnalysis)
        else ChangeImpactAnalysis.model_validate(analysis)
    )
    if workspace:
        corpus = load_confirmed_contract_corpus(workspace)
        # 路由前重新执行同一组有界候选检索，确保 Analyzer 不能引用本轮上下文之外
        # 的任意 JSON 事实；证据仍由 corpus.read 以 pointer/hash 精确复核。
        candidate_facts = corpus.search(
            _analysis_queries(user_request, target=target),
            top_k=80,
        )
        validate_change_impact_analysis(
            normalized,
            corpus=corpus,
            candidate_facts=candidate_facts,
        )
        # code.scan 只能引用当前工作区真实源码；路由层再次清洗，避免调用方
        # 直接构造带虚假路径的 ChangeImpactAnalysis 绕过扫描节点。
        for change in normalized.atomic_changes:
            if not change.code_scan.findings:
                continue
            sanitized = sanitize_code_scan_evidence(
                change.code_scan,
                workspace=workspace,
                max_results=100,
                require_exists=True,
            )
            if sanitized.model_dump(mode="json", by_alias=True) != change.code_scan.model_dump(
                mode="json", by_alias=True
            ):
                raise ValueError("code.scan 包含无法在当前工作区复核的 finding。")
    if normalized.analysis_status != AnalysisStatus.COMPLETED:
        candidate = RevisionRoutingCandidate(
            route=RevisionRoute.CLARIFICATION,
            owner="unknown",
            questions=["请补充具体业务对象、页面或接口及期望行为。"],
            reason="Analyzer 没有足够的已确认 JSON 证据。",
            confidence=0.0,
        )
        return RevisionRoutingResult(candidate=candidate)
    invalidated = list(normalized.invalidated_contracts)
    if invalidated:
        earliest_stage = min(
            (item.contract_stage for item in invalidated),
            key=lambda stage: 0 if stage == ContractStage.REQUIREMENT_DESIGN else 1,
        )
        artifact_keys = _artifact_closure(
            item.artifact_key for item in invalidated
        )
        resources = _resource_keys_from_evidence(invalidated)
        earliest_artifact = _earliest_artifact_from_evidence(invalidated, earliest_stage)
        branch = (
            FormalRevisionBranch.DESIGN_STAGE_REVISION
            if earliest_stage == ContractStage.REQUIREMENT_DESIGN
            else FormalRevisionBranch.WORKBENCH_PLAN_REVISION
        )
        revision_type = _revision_type_from_evidence(invalidated, user_request=user_request)
        candidate = RevisionRoutingCandidate(
            route=RevisionRoute.FORMAL_REVISION,
            formalBranch=branch,
            revisionType=revision_type,
            earliestArtifact=earliest_artifact,
            owner="none",
            affectedArtifactKeys=artifact_keys,
            affectedResourceKeys=resources,
            candidatePaths=[],
            questions=[],
            reason=normalized.request_summary,
            confidence=1.0,
        )
        return RevisionRoutingResult(
            candidate=candidate,
            impact=_impact_from_candidate(candidate, target=target, evidence=invalidated, analysis_status=normalized.analysis_status.value),
        )
    if any(change.contract_impact != ContractImpact.PRESERVES for change in normalized.atomic_changes):
        candidate = RevisionRoutingCandidate(
            route=RevisionRoute.CLARIFICATION,
            owner="unknown",
            questions=["请补充能够定位当前实现的页面、接口或文件。"],
            reason="契约影响结果包含未知事实。",
            confidence=0.0,
        )
        return RevisionRoutingResult(candidate=candidate)
    has_code_evidence = any(
        bool(change.code_scan.performed and change.code_scan.findings)
        for change in normalized.atomic_changes
    )
    if not has_code_evidence and not allow_pending_code_scan:
        candidate = RevisionRoutingCandidate(
            route=RevisionRoute.CLARIFICATION,
            owner="unknown",
            questions=["请补充具体源码位置或可复现步骤，以取得实现问题证据。"],
            reason="契约保持，但 code.scan 没有返回可复核实现证据。",
            confidence=0.0,
        )
        return RevisionRoutingResult(candidate=candidate)
    safe_owner = owner if owner in {"frontend", "backend", "fullstack", "workspace"} else "unknown"
    candidate = RevisionRoutingCandidate(
        route=RevisionRoute.IMPLEMENTATION_FIX,
        owner=safe_owner,
        candidatePaths=[finding.path for change in normalized.atomic_changes for finding in change.code_scan.findings][:100],
        affectedArtifactKeys=[],
        affectedResourceKeys=_resource_keys_from_evidence(
            [evidence for change in normalized.atomic_changes for evidence in change.contract_evidence]
        ),
        questions=[],
        reason=normalized.request_summary,
        confidence=1.0,
    )
    return RevisionRoutingResult(candidate=candidate)


def build_small_task_revision_confirmation(
    *,
    state: dict[str, Any],
    escalation: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """把 SmallTask 的正式范围升级转换为统一只读影响确认合同。"""

    requested_resources = escalation.get("requestedResources")
    resource_keys = [
        (
            str(item)
            if not isinstance(item, dict)
            else f"{item.get('type') or 'resource'}:"
            f"{item.get('targetId') or item.get('target_id') or 'unknown'}"
        )
        for item in requested_resources
    ] if isinstance(requested_resources, list) else []
    target = state.get("change_target")
    target = target if isinstance(target, dict) else None
    # SmallTask 发现范围扩展时重新使用同一个 Analyzer；只有没有显式工作区的
    # 旧式单元调用才保留最小 TechnicalPlan 兜底，真实协议不会凭空制造 formal impact。
    request = str(state.get("request") or reason)
    routing: RevisionRoutingResult | None = None
    if state.get("change_impact_enabled") is True and state.get("workspace"):
        from app.agents.change_impact_analyzer import analyze_change_impact

        raw_analysis = escalation.get("changeImpactAnalysis") or escalation.get("change_impact_analysis")
        if isinstance(raw_analysis, dict):
            routing = route_from_change_impact(
                raw_analysis,
                user_request=request,
                workspace=str(state.get("workspace")),
                target=target,
            )
        else:
            analysis = analyze_change_impact(
                request,
                str(state.get("workspace")),
                target=target,
                allow_code_scan=False,
            )
            routing = route_from_change_impact(
                analysis,
                user_request=request,
                workspace=str(state.get("workspace")),
                target=target,
            )
    if routing is None:
        routing = enforce_revision_routing(
            {
                "route": "formal_revision",
                "formalBranch": "workbench_plan_revision",
                "revisionType": "technical_contract_change",
                "earliestArtifact": "technical-plan",
                "owner": "none",
                "affectedArtifactKeys": ["technical-plan"],
                "affectedResourceKeys": resource_keys,
                "candidatePaths": escalation.get("requestedPaths", []),
                "questions": [],
                "reason": reason,
                "confidence": 0.99,
            },
            user_request=request,
            target=target,
        )
    if routing.impact is None:
        return {
            "conversation_intent": "clarification",
            "clarification": {
                "mode": "change_impact_insufficient_evidence",
                "status": "requires_user_input",
                "message": "无法从已确认 JSON 证明 SmallTask 升级涉及正式契约，请补充业务对象或接口。",
                "reason": routing.candidate.reason,
                "questions": [
                    {
                        "id": "change_impact_insufficient_evidence",
                        "header": "补充契约证据",
                        "question": "请补充具体业务对象、页面或接口及预期行为。",
                        "type": "text",
                    }
                ],
            },
        }
    interaction_id = f"impact_{uuid4().hex}"
    impact = {
        "interactionId": interaction_id,
        **routing.impact.model_dump(mode="json", by_alias=True),
        "status": "pending",
    }
    confirmation_label = (
        "确认并返回设计阶段"
        if routing.impact.formal_branch == FormalRevisionBranch.DESIGN_STAGE_REVISION
        else "确认并进入规划阶段"
    )
    return {
        "conversation_intent": "formal_revision",
        "revision_impact": impact,
        "clarification": {
            "mode": "revision_impact_confirmation",
            "status": "requires_user_input",
            "message": "SmallTask 发现正式语义变化，请确认是否进入正式修改流程。",
            "reason": reason,
            "revisionImpact": impact,
            "questions": [
                {
                    "id": "revision_impact_confirmation",
                    "header": "正式修改确认",
                    "question": f"是否{confirmation_label}？",
                    "type": "yesno",
                    "allowOther": False,
                }
            ],
        },
    }


def enforce_revision_routing(
    payload: dict[str, Any],
    *,
    user_request: str,
    target: dict[str, Any] | None = None,
) -> RevisionRoutingResult:
    """校验模型候选并执行正式产物安全、字段合同和低置信度规则。"""

    normalized = _normalize_candidate_payload(payload)
    target_paths = [str(path).strip() for path in normalized.get("candidatePaths", [])]
    forced = _forced_formal_artifact_classification(target_paths)
    if forced is not None:
        revision_type, branch, earliest = forced
        normalized.update(
            {
                "route": RevisionRoute.FORMAL_REVISION,
                "formalBranch": branch,
                "revisionType": revision_type,
                "earliestArtifact": earliest,
                "owner": "none",
                "candidatePaths": [],
            }
        )
    # 既有页面的纯 UI 视觉、布局和交互微调属于当前工作台的小范围前端修改。
    # Prompt 已禁止这类请求进入 formal revision；这里再对模型偶发返回的
    # ui-design/ui_visual_change 做确定性兜底，避免重新回退到 UI 设计阶段。
    if forced is None and str(normalized.get("route") or "") == RevisionRoute.FORMAL_REVISION:
        if (
            str(normalized.get("revisionType") or "") == RevisionType.UI_VISUAL_CHANGE
            or str(normalized.get("earliestArtifact") or "") == EarliestRevisionArtifact.UI_DESIGN
        ):
            normalized.update(
                {
                    "route": RevisionRoute.IMPLEMENTATION_FIX,
                    "formalBranch": None,
                    "revisionType": None,
                    "earliestArtifact": None,
                    "owner": "frontend",
                    "affectedArtifactKeys": [],
                }
            )
    confidence = _safe_confidence(normalized.get("confidence"))
    if confidence < 0.70 or str(normalized.get("owner") or "") == "unknown":
        normalized = {
            "route": RevisionRoute.CLARIFICATION,
            "owner": "unknown",
            "questions": _questions(normalized),
            "reason": str(normalized.get("reason") or "目标或影响范围不够明确。"),
            "confidence": confidence,
        }
    candidate = RevisionRoutingCandidate.model_validate(normalized)
    if candidate.route != RevisionRoute.FORMAL_REVISION:
        return RevisionRoutingResult(candidate=candidate)
    candidate = _align_formal_branch(candidate)
    return RevisionRoutingResult(
        candidate=candidate,
        impact=_impact_from_candidate(candidate, target=target),
    )


def _normalize_candidate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """把模型字段归一到当前合同，并让旧 route 名称被严格 schema 拒绝。"""

    route = str(payload.get("route") or "clarification")
    return {
        "route": route,
        "formalBranch": payload.get("formalBranch") or payload.get("formal_branch"),
        "revisionType": payload.get("revisionType") or payload.get("revision_type"),
        "earliestArtifact": payload.get("earliestArtifact") or payload.get("earliest_artifact"),
        "owner": payload.get("owner") or "unknown",
        "affectedArtifactKeys": payload.get("affectedArtifactKeys")
        or payload.get("affected_artifact_keys")
        or [],
        "affectedResourceKeys": payload.get("affectedResourceKeys")
        or payload.get("affected_resource_keys")
        or [],
        "candidatePaths": payload.get("candidatePaths") or payload.get("targetPaths") or [],
        "questions": _questions(payload),
        "reason": str(payload.get("reason") or "模型没有提供有效的分类依据。"),
        "confidence": _safe_confidence(payload.get("confidence")),
    }


def _forced_formal_artifact_classification(
    target_paths: list[str],
) -> tuple[RevisionType, FormalRevisionBranch, EarliestRevisionArtifact] | None:
    """按明确正式产物路径选择最早分支，不解析自然语言或授予写权限。"""

    matches = [
        _FORMAL_ARTIFACT_INFO[key]
        for path in target_paths
        if (key := _formal_artifact_key_from_path(path)) is not None
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: _ARTIFACT_PRIORITY[item[2].value])


def _formal_artifact_key_from_path(path: str) -> str | None:
    """从候选路径识别当前四类正式 JSON/Markdown 产物名称。"""

    normalized = str(path or "").replace("\\", "/").strip()
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        return None
    for key, pattern in _FORMAL_ARTIFACT_FILE_PATTERNS.items():
        if pattern.search(normalized):
            return key
    # UiDesign 的 canonical 文件名是 ui-designs，允许没有扩展名的目录工具结果。
    if re.search(r"(?:^|/)ui-designs?(?:\.[^/]*)?$", normalized, re.IGNORECASE):
        return "ui-design"
    return None


def _revision_type_for_artifact(
    artifact: EarliestRevisionArtifact,
    requested: RevisionType | None,
) -> RevisionType:
    """保证最早正式产物与修订类型属于同一语义层。"""

    if artifact == EarliestRevisionArtifact.REQUIREMENT_SPEC:
        return RevisionType.REQUIREMENT_SCOPE_CHANGE
    if artifact == EarliestRevisionArtifact.PRODUCT_PLAN:
        return RevisionType.PRODUCT_BEHAVIOR_CHANGE
    if artifact == EarliestRevisionArtifact.UI_DESIGN:
        return RevisionType.UI_VISUAL_CHANGE
    if requested in {
        RevisionType.TECHNICAL_CONTRACT_CHANGE,
        RevisionType.ENDPOINT_IMPLEMENTATION_CHANGE,
        RevisionType.DATA_SOURCE_CHANGE,
    }:
        return requested
    return RevisionType.TECHNICAL_CONTRACT_CHANGE


def _artifact_closure(keys: Any) -> list[str]:
    """从最早失效产物确定性展开所有当前下游正式产物。"""

    requested = {
        str(key).strip()
        for key in keys
        if str(key).strip() in _FORMAL_ARTIFACT_INFO
    }
    expanded = set(requested)
    for key in tuple(requested):
        expanded.update(_FORMAL_ARTIFACT_DOWNSTREAM.get(key, ()))
    return sorted(expanded, key=lambda key: (_ARTIFACT_PRIORITY[key], key))


def _align_formal_branch(candidate: RevisionRoutingCandidate) -> RevisionRoutingCandidate:
    """用最早正式产物纠正 branch、owner 和类型，禁止模型越过安全边界。"""

    design_artifacts = {
        EarliestRevisionArtifact.REQUIREMENT_SPEC,
        EarliestRevisionArtifact.PRODUCT_PLAN,
        EarliestRevisionArtifact.UI_DESIGN,
    }
    expected_branch = (
        FormalRevisionBranch.DESIGN_STAGE_REVISION
        if candidate.earliest_artifact in design_artifacts
        else FormalRevisionBranch.WORKBENCH_PLAN_REVISION
    )
    expected_type = _revision_type_for_artifact(
        candidate.earliest_artifact,
        candidate.revision_type,
    )
    updates: dict[str, Any] = {
        "formal_branch": expected_branch,
        "owner": "none",
        "candidate_paths": [],
        "revision_type": expected_type,
        "affected_artifact_keys": _artifact_closure(
            [candidate.earliest_artifact.value, *candidate.affected_artifact_keys]
        ),
    }
    if all(getattr(candidate, key) == value for key, value in updates.items()):
        return candidate
    return candidate.model_copy(update=updates)


def _impact_from_candidate(
    candidate: RevisionRoutingCandidate,
    *,
    target: dict[str, Any] | None,
    evidence: list[Any] | None = None,
    analysis_status: str = "completed",
) -> RevisionImpact:
    """把最终 formal 路由转换为执行前只读影响范围卡。"""

    assert candidate.formal_branch is not None
    assert candidate.revision_type is not None
    assert candidate.earliest_artifact is not None
    affected_artifacts = _artifact_closure(
        [candidate.earliest_artifact.value, *candidate.affected_artifact_keys]
    )
    affected_resources = list(dict.fromkeys(candidate.affected_resource_keys))
    target_key = _target_resource_key(target)
    if target_key and target_key not in affected_resources:
        affected_resources.insert(0, target_key)
    risks = ["所有受影响正式产物必须重新确认后才能进入 Build。"]
    if candidate.formal_branch == FormalRevisionBranch.DESIGN_STAGE_REVISION:
        risks.append("确认后将恢复原设计规划 thread，并重新经过原确认门。")
    else:
        risks.append("确认后才创建隔离草稿，当前 canonical 在草稿确认前保持不变。")
    return RevisionImpact(
        formalBranch=candidate.formal_branch,
        revisionType=candidate.revision_type,
        earliestArtifact=candidate.earliest_artifact,
        affectedArtifacts=affected_artifacts,
        affectedResources=affected_resources,
        reason=candidate.reason,
        risks=risks,
        evidence=[
            item.model_dump(mode="json", by_alias=True)
            if hasattr(item, "model_dump")
            else dict(item)
            for item in (evidence or [])
        ],
        analysisStatus=analysis_status,
    )


def _earliest_artifact_from_evidence(
    evidence: list[Any],
    stage: ContractStage,
) -> EarliestRevisionArtifact:
    """把最早层 JSON 证据映射为现有正式产物枚举。"""

    mapping = {
        "requirement-spec": EarliestRevisionArtifact.REQUIREMENT_SPEC,
        "product-plan": EarliestRevisionArtifact.PRODUCT_PLAN,
        "ui-design": EarliestRevisionArtifact.UI_DESIGN,
        "technical-plan": EarliestRevisionArtifact.TECHNICAL_PLAN,
    }
    candidates = [
        mapping.get(str(getattr(item, "artifact_key", "")))
        for item in evidence
        if getattr(item, "contract_stage", None) == stage
        and mapping.get(str(getattr(item, "artifact_key", ""))) is not None
    ]
    if candidates:
        return min(candidates, key=lambda value: _ARTIFACT_PRIORITY.get(value.value, 99))
    return (
        EarliestRevisionArtifact.REQUIREMENT_SPEC
        if stage == ContractStage.REQUIREMENT_DESIGN
        else EarliestRevisionArtifact.TECHNICAL_PLAN
    )


def _resource_keys_from_evidence(evidence: list[Any]) -> list[str]:
    """从证据选择器提取受影响业务资源 key。"""

    result: list[str] = []
    for item in evidence:
        selector = getattr(item, "selector", {})
        if not isinstance(selector, dict):
            continue
        for key, prefix in (
            ("pageId", "page"),
            ("page_id", "page"),
            ("actionId", "action"),
            ("action_id", "action"),
            ("endpointId", "endpoint"),
            ("endpoint_id", "endpoint"),
            ("entityId", "entity"),
            ("entity_id", "entity"),
        ):
            value = str(selector.get(key) or "").strip()
            if value and f"{prefix}:{value}" not in result:
                result.append(f"{prefix}:{value}")
    return result


def _revision_type_from_evidence(
    evidence: list[Any],
    *,
    user_request: str,
) -> RevisionType:
    """根据权威产物和请求语义确定修订类型。"""

    artifacts = {str(getattr(item, "artifact_key", "")) for item in evidence}
    text = user_request.casefold()
    if "requirement-spec" in artifacts:
        return RevisionType.REQUIREMENT_SCOPE_CHANGE
    if "product-plan" in artifacts:
        return RevisionType.PRODUCT_BEHAVIOR_CHANGE
    if "ui-design" in artifacts:
        return RevisionType.UI_VISUAL_CHANGE
    if any(token in text for token in ("数据源", "数据库", "mysql", "mock", "表")):
        return RevisionType.DATA_SOURCE_CHANGE
    if any(token in text for token in ("实现", "内部", "软删除", "审计", "service", "模块")):
        return RevisionType.ENDPOINT_IMPLEMENTATION_CHANGE
    return RevisionType.TECHNICAL_CONTRACT_CHANGE


def _target_resource_key(target: dict[str, Any] | None) -> str:
    """把可选会话目标转换为稳定的影响资源 key。"""

    if not isinstance(target, dict):
        return ""
    target_type = str(target.get("type") or "")
    if target_type == "page":
        value = str(target.get("pageId") or target.get("page_id") or "").strip()
        return f"page:{value}" if value else ""
    if target_type == "endpoint":
        value = str(target.get("endpointId") or target.get("endpoint_id") or "").strip()
        return f"endpoint:{value}" if value else ""
    return "application" if target_type == "application" else ""

def _safe_confidence(value: Any) -> float:
    """把不可信模型置信度收敛到零到一。"""

    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _questions(payload: dict[str, Any]) -> list[str]:
    """提取有界澄清问题，并为旧 clarificationQuestion 提供同轮归一化。"""

    raw = payload.get("questions")
    if isinstance(raw, list):
        result = [str(item).strip() for item in raw if str(item).strip()]
        if result:
            return result[:10]
    value = str(payload.get("clarificationQuestion") or "").strip()
    return [value] if value else ["请补充具体修改目标、业务对象和期望结果。"]
