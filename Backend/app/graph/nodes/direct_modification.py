from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from langgraph.config import get_stream_writer

from app.agents.direct_modification import (
    DirectModificationDecision,
    answer_casual_conversation,
    answer_workspace_question,
    classify_direct_modification_intent,
    invoke_data_source_direct_modification,
    invoke_frontend_direct_modification,
    invoke_workspace_direct_modification,
    parse_direct_modification_agent_result,
)
from app.agents.change_impact_analyzer import (
    _analysis_queries,
    analyze_change_impact,
    validate_change_impact_analysis,
)
from app.agents.tool_activity_stream import ToolActivityCallback
from app.graph.nodes.common import (
    capture_agent_file_changes,
    refresh_code_graph_after_changes,
    workspace_from_state,
)
from app.graph.state import ProjectState
from app.graph.subgraphs.testing import _check_progress_snapshot_writer
from app.services.direct_modification import (
    append_direct_conversation_summary,
    direct_path_matches_owner,
    direct_final_message,
    direct_state_message,
    direct_test_log_paths,
    validated_dynamic_workspace_paths,
    validated_direct_stage_result,
)
from app.services.integration_test_runner import run_integration_checks
from app.services.revision_routing import (
    build_small_task_revision_confirmation,
    route_from_change_impact,
)
from app.services.test_validation import evaluate_quality_gate
from app.domain.change_impact import (
    AnalysisStatus,
    ChangeImpactAnalysis,
    ContractStage,
)
from app.services.change_code_scan import sanitize_code_scan_evidence, scan_targeted_code
from app.services.change_contracts import load_confirmed_contract_corpus
from app.workspace.code_changes import CapturedWorkspaceChanges, merge_code_change_sets
from app.workspace.test_documents import write_test_report_json, write_test_report_markdown
from app.workspace.workspace_snapshot_documents import load_workspace_snapshot_json


def classify_direct_modification(state: ProjectState) -> dict[str, Any]:
    """识别快速修改归属，并在不安全时转为澄清或正式规划提示。"""

    request = str(state.get("request") or "").strip()
    if state.get("direct_modification_handoff_decision") == "rejected":
        # 用户已经拒绝上一轮写入确认时，不再重新调用任何分析模型。
        decision = DirectModificationDecision(
            intent="clarification",
            owner="unknown",
            scope="clarification",
            confidence=1.0,
            reason="用户已取消本次修改。",
            clarification_question="本次修改已取消。",
        )
    else:
        decision = classify_direct_modification_intent(
            user_request=request,
            conversation_summary=str(state.get("direct_modification_summary") or ""),
            workspace_snapshot=_workspace_snapshot_for_classification(state),
            on_response_delta=_conversation_text_delta_writer(),
        )
    # 二次修改只消费分类模型返回的路由 JSON；不再额外调用模型生成 Contract Evidence。
    # 正式分支仍由后续一次性 interaction 和用户确认控制，不能因省略证据而绕过确认门。
    target_paths = list(decision.target_paths)
    element_path = _direct_element_workspace_path(state)
    if (
        decision.intent == "implementation_fix"
        and decision.owner in {"frontend", "fullstack"}
        and element_path
    ):
        target_paths = list(dict.fromkeys([element_path, *target_paths]))[:100]
    dynamic_workspace_paths = validated_dynamic_workspace_paths(
        workspace=workspace_from_state(state),
        request=request,
        owner=decision.owner,
        target_paths=target_paths,
    )
    approved_paths = list(
        dict.fromkeys(
            [
                *state.get("direct_modification_approved_paths", []),
                *dynamic_workspace_paths,
            ]
        )
    )[:100]
    base: dict[str, Any] = {
        "phase": "classify_intent",
        "conversation_intent": decision.intent,
        "direct_modification_owner": decision.owner,
        "direct_modification_scope": decision.scope,
        "direct_modification_confidence": decision.confidence,
        "direct_modification_reason": decision.reason,
        "conversation_response": decision.response,
        "direct_stage_results": {},
        "direct_code_change_sets": [],
        "direct_modification_result": {},
        "direct_modification_target_paths": target_paths,
        "direct_modification_approved_paths": approved_paths,
        "change_impact_analysis": {},
        "change_impact_code_scan_required": False,
        "change_impact_code_scan": {},
        "backend_handoff": {},
        "integration_repair_enabled": False,
        "unit_test_generation_enabled": False,
        "frontend_performance_test_enabled": False,
        "repair_iteration": max(0, int(state.get("repair_iteration", 0) or 0)),
        "max_repair_iterations": max(1, int(state.get("max_repair_iterations", 3) or 3)),
        "repair_task_plan": {},
        "repair_tasks": [],
        "small_task_tasks": [],
        "small_task_results": [],
        "small_task_code_change_sets": [],
        "small_task_handoff": {},
        "small_task_handoff_submission": {},
        "small_task_route": "",
        "test_results": [],
        "test_report": {},
        "test_report_path": "",
        "test_report_json_path": "",
        "quality_gate_passed": False,
        "launch_result": {},
        "preview_url": "",
        "code_changes": {},
        "acceptance_request": {},
        "timeline": ["classify_intent"],
    }
    if state.get("direct_modification_handoff_decision") == "rejected":
        return {
            **base,
            "status": "completed",
            "message": "用户已取消本次修改确认，本次工作区不会继续写入。",
            "clarification": {},
        }
    if decision.intent == "formal_revision":
        interaction_id = f"impact_{uuid4().hex}"
        formal_branch = str(decision.formal_branch or "")
        earliest_artifact = str(decision.earliest_artifact or "")
        revision_type = str(decision.revision_type or "")
        affected_artifacts = list(decision.affected_artifact_keys)
        if earliest_artifact and earliest_artifact not in affected_artifacts:
            affected_artifacts.insert(0, earliest_artifact)
        impact = {
            "interactionId": interaction_id,
            "formalBranch": formal_branch,
            "revisionType": revision_type,
            "earliestArtifact": earliest_artifact,
            "affectedArtifacts": affected_artifacts,
            "affectedResources": _revision_affected_resources(
                state.get("change_target"),
                list(decision.affected_resource_keys),
            ),
            "reason": decision.reason,
            "evidence": [],
            "analysisStatus": "completed",
            "risks": [
                "所有受影响正式产物必须重新确认后才能进入 Build。",
                (
                    "确认后恢复原 planning thread，并重新经过原设计确认门。"
                    if formal_branch == "design_stage_revision"
                    else "确认后才创建隔离草稿，当前 canonical 暂不改变。"
                ),
            ],
            "status": "pending",
        }
        confirmation_label = (
            "确认并返回设计阶段"
            if formal_branch == "design_stage_revision"
            else "确认并进入规划阶段"
        )
        message = "该请求会修改已确认的正式语义，请确认是否进入正式修改流程。"
        return {
            **base,
            "status": "requires_user_input",
            "message": message,
            "revision_impact": impact,
            "clarification": {
                "mode": "revision_impact_confirmation",
                "status": "requires_user_input",
                "message": message,
                "reason": decision.reason,
                "workflowIntent": "development_readiness_gate",
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
    if (
        decision.intent == "implementation_fix"
        and decision.owner in {"frontend", "backend", "fullstack"}
        and str(state.get("direct_modification_handoff_decision") or "").strip().lower()
        != "approved"
    ):
        target_resources = _revision_affected_resources(
            state.get("change_target"),
            list(decision.affected_resource_keys),
        )
        owner_label = {
            "frontend": "前端代码",
            "backend": "后端代码",
            "fullstack": "前后端代码",
        }[decision.owner]
        return {
            **base,
            "status": "requires_user_input",
            "message": f"该请求属于{owner_label}实现修复，请先确认修改范围。",
            "clarification": {
                "mode": "implementation_fix_confirmation",
                "status": "requires_user_input",
                "message": f"该请求属于不改变已确认产品语义的{owner_label}实现修复。",
                "reason": decision.reason,
                "requestedPaths": target_paths,
                "requestedResources": target_resources,
                "owner": decision.owner,
                "questions": [
                    {
                        "id": "implementation_fix_confirmation",
                        "header": "实现修改确认",
                        "question": f"将直接修改{owner_label}并执行后续检查，是否确认继续？",
                        "type": "yesno",
                        "allowOther": False,
                    }
                ],
            },
        }
    if decision.intent == "casual_chat" and decision.response:
        return {
            **base,
            "status": "completed",
            "message": decision.response,
            "conversation_response": decision.response,
            "clarification": {},
            "timeline": ["classify_intent"],
        }
    if decision.intent == "clarification" or decision.owner == "unknown":
        question = decision.clarification_question
        return {
            **base,
            "status": "requires_user_input",
            "message": question,
            "clarification": {
                "mode": "direct_modification_clarification",
                "status": "requires_user_input",
                "message": question,
                "questions": [
                    {
                        "id": "direct_modification_clarification",
                        "header": "补充修改信息",
                        "question": question,
                        "type": "text",
                        "placeholder": "请描述具体功能、位置和期望结果。",
                    }
                ],
            },
        }
    return {
        **base,
        "status": "in_progress",
        "message": _classification_message(decision.intent, decision.owner),
        "clarification": {},
    }


def _workspace_snapshot_for_classification(state: ProjectState) -> dict[str, Any]:
    """读取已有的可选工作区摘要，并附带当前页面或接口目标。"""

    snapshot: dict[str, Any] = {}
    snapshot_path = str(state.get("workspace_snapshot_path") or "").strip()
    if snapshot_path:
        try:
            loaded_snapshot = load_workspace_snapshot_json(snapshot_path)
            if isinstance(loaded_snapshot, dict):
                snapshot = loaded_snapshot
        except (OSError, ValueError, TypeError):
            pass
    if not snapshot:
        summary = state.get("workspace_snapshot_summary")
        snapshot = summary if isinstance(summary, dict) else {}
    context = dict(snapshot)
    target = state.get("change_target")
    if isinstance(target, dict) and target:
        context["currentTarget"] = target
    element_context = state.get("element_context")
    if isinstance(element_context, dict) and element_context:
        context["currentElement"] = element_context
    return context


def _direct_element_workspace_path(state: ProjectState) -> str:
    """读取协议边界已解析的 DOM 源码工作区相对路径。"""

    context = state.get("element_context")
    if not isinstance(context, dict):
        return ""
    return str(context.get("workspacePath") or "").strip().replace("\\", "/").lstrip("/")


def _should_run_change_impact_analysis(
    request: str,
    decision: DirectModificationDecision,
) -> bool:
    """判断自由对话是否需要契约先行分析，避免分类器误把语义修改当作普通问答。"""

    intent = str(decision.intent or "")
    if intent in {"implementation_fix", "formal_revision", "clarification"}:
        return True
    if intent not in {"casual_chat", "workspace_question"}:
        return True
    text = str(request or "").casefold()
    mutation_markers = (
        "删除", "移除", "去掉", "下线", "新增", "添加", "增加", "迁移", "搬到",
        "修改", "改成", "调整", "替换", "重构", "修复", "修一下", "fix", "remove",
        "delete", "add", "change", "modify", "refactor", "update",
    )
    if not any(marker in text for marker in mutation_markers):
        return False
    # 明确的普通工作区文件修改已有精确路径边界；它不需要用产品契约阻断。
    if decision.owner == "workspace" and decision.target_paths:
        return False
    return True


def _run_change_impact_analyzer(
    state: ProjectState,
    request: str,
) -> ChangeImpactAnalysis:
    """在任何代码写入前读取已确认 JSON 并执行窄职责影响分析。"""

    workspace = workspace_from_state(state)
    target = state.get("change_target")
    target = target if isinstance(target, dict) else None
    if not workspace:
        # 没有显式工作区时不能凭模型猜测契约，统一返回安全 unknown。
        from app.agents.change_impact_analyzer import _insufficient_analysis

        return _insufficient_analysis("没有显式 workspaceRoot，无法读取已确认 JSON 契约。")
    return analyze_change_impact(
        request,
        workspace,
        target=target,
        allow_code_scan=False,
    )


def _decision_from_impact_analysis(
    decision: Any,
    analysis: ChangeImpactAnalysis,
    *,
    request: str,
    workspace: str | None = None,
) -> Any:
    """把 Analyzer 的事实结果转换为本节点可用的局部执行候选。"""

    try:
        routing = route_from_change_impact(
            analysis,
            user_request=request,
            workspace=workspace,
            owner=str(getattr(decision, "owner", "unknown") or "unknown"),
            # 契约阶段只负责证明 preserves；先取得只读 code.scan 证据，
            # 不把“待扫描”误降级成澄清，也不因此获得代码写权限。
            allow_pending_code_scan=True,
        )
    except Exception:  # noqa: BLE001 - 证据失败不能阻断已明确的正式修订判定
        if _is_complete_formal_revision(decision):
            # 正式修订本身仍需用户确认；Analyzer 只负责补充 JSON 证据，不能把
            # “新增页面”等没有既有事实可引用的明确语义改写成普通澄清。
            return decision
        return replace(
            decision,
            intent="clarification",
            owner="unknown",
            scope="clarification",
            confidence=0.0,
            reason="契约影响证据无法在当前 JSON 中复核。",
            clarification_question="请重新描述具体业务对象、页面或接口及期望行为。",
        )
    candidate = routing.candidate
    if candidate.route.value == "clarification":
        if _is_complete_formal_revision(decision):
            # 新增页面/模块等请求可能没有对应的既有 JSON 条目。此时保留模型
            # 已明确的 formal branch，影响卡会标记证据不足，但仍要求用户确认。
            return decision
        return replace(
            decision,
            intent="clarification",
            owner="unknown",
            scope="clarification",
            confidence=min(float(getattr(decision, "confidence", 0.0) or 0.0), 0.69),
            reason="当前已确认 JSON 契约不足，不能安全判断这是契约修改还是实现问题。",
            clarification_question="请补充要修改的业务对象、页面或接口，以及期望保持不变的行为。",
        )
    if candidate.route.value == "formal_revision":
        evidence = list(analysis.invalidated_contracts)
        earliest = analysis.earliest_affected_contract_stage
        # Router 已经依据最早失效产物展开了确定性的下游闭包；这里不能再只
        # 取直接 evidence，否则影响卡会漏掉 UiDesign/TechnicalPlan 等必然需要
        # 重新确认的后继产物。资源同样优先使用 Router 的归一化结果，避免把
        # 影响卡重新退化为 Analyzer 的局部命中列表。
        artifact_keys = list(
            dict.fromkeys(
                candidate.affected_artifact_keys
                or (item.artifact_key for item in evidence)
            )
        )
        resource_keys = list(
            dict.fromkeys(
                candidate.affected_resource_keys
                or _resource_keys_from_evidence(evidence)
            )
        )
        return replace(
            decision,
            intent="formal_revision",
            owner="none",
            scope="formal_revision",
            reason=(analysis.request_summary or request)[:2_048],
            formal_branch=candidate.formal_branch.value if candidate.formal_branch else None,
            revision_type=candidate.revision_type.value if candidate.revision_type else None,
            earliest_artifact=candidate.earliest_artifact.value if candidate.earliest_artifact else (
                _artifact_for_stage(evidence, earliest) if earliest else None
            ),
            affected_artifact_keys=tuple(artifact_keys),
            affected_resource_keys=tuple(resource_keys),
        )
    if candidate.owner not in {"frontend", "backend", "fullstack", "workspace"} or (
        candidate.owner == "workspace" and not getattr(decision, "target_paths", ())
    ):
        # Analyzer 只证明契约保持，并不替分类器猜测实现归属；没有安全 owner
        # 时必须停在澄清，不得让 graph 以 implementation_fix 继续写入。
        return replace(
            decision,
            intent="clarification",
            owner="unknown",
            scope="clarification",
            confidence=min(float(getattr(decision, "confidence", 0.0) or 0.0), 0.69),
            reason="契约保持，但无法安全确定实现修改归属。",
            clarification_question="请补充具体的前端页面、后端接口或源码位置。",
        )
    # 没有失效事实时保留原 owner；后续节点才可以取得目标导向代码证据。
    return replace(
        decision,
        intent="implementation_fix",
        scope="direct",
        reason=(analysis.request_summary or getattr(decision, "reason", ""))[:2_048],
    )


def _is_complete_formal_revision(decision: Any) -> bool:
    """判断初始分类是否已提供足够正式修订字段，允许保留影响确认门。"""

    return bool(
        getattr(decision, "intent", "") == "formal_revision"
        and getattr(decision, "formal_branch", None)
        and getattr(decision, "revision_type", None)
        and getattr(decision, "earliest_artifact", None)
        and float(getattr(decision, "confidence", 0.0) or 0.0) >= 0.70
    )


def _artifact_for_stage(evidence: list[Any], stage: ContractStage) -> str:
    """从最早层证据映射现有正式产物名称。"""

    preferred = {
        "requirement-spec",
        "product-plan",
        "ui-design",
        "technical-plan",
    }
    for item in evidence:
        if getattr(item, "contract_stage", None) == stage and getattr(item, "artifact_key", "") in preferred:
            return str(item.artifact_key)
    return "requirement-spec" if stage == ContractStage.REQUIREMENT_DESIGN else "technical-plan"


def _resource_keys_from_evidence(evidence: list[Any]) -> list[str]:
    """从 JSON 事实选择器提取页面、操作和接口资源键。"""

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
            if value:
                candidate = f"{prefix}:{value}"
                if candidate not in result:
                    result.append(candidate)
    return result


def scan_change_impact_code(state: ProjectState) -> dict[str, Any]:
    """在契约 preserves 后执行一次限定代码扫描，并把原始发现写入状态。"""

    if state.get("change_impact_code_scan_required") is not True:
        return {
            "phase": "change_impact_code_scan",
            "status": "requires_user_input",
            "message": "当前运行尚未通过契约 preserves 闸门，禁止执行代码扫描。",
            "change_impact_code_scan_required": False,
            "clarification": {
                "mode": "change_impact_insufficient_evidence",
                "status": "requires_user_input",
                "message": "当前运行尚未通过契约 preserves 闸门，暂不扫描或修改代码。",
                "questions": [],
            },
            "timeline": ["change_impact_code_scan"],
        }
    raw_analysis = state.get("change_impact_analysis")
    if not isinstance(raw_analysis, dict):
        return {
            "phase": "change_impact_code_scan",
            "status": "failed",
            "message": "缺少契约影响分析，禁止执行代码扫描。",
            "change_impact_code_scan_required": False,
            "timeline": ["change_impact_code_scan"],
        }
    try:
        analysis = ChangeImpactAnalysis.model_validate(raw_analysis)
    except Exception:
        return {
            "phase": "change_impact_code_scan",
            "status": "requires_user_input",
            "message": "契约影响分析无法复核，暂不修改代码。",
            "clarification": {
                "mode": "change_impact_insufficient_evidence",
                "status": "requires_user_input",
                "message": "当前证据不足以证明这是实现问题，请补充具体页面、接口和预期行为。",
                "questions": [
                    {
                        "id": "change_impact_insufficient_evidence",
                        "header": "补充实现证据",
                        "question": "请补充具体页面、接口、操作和预期行为。",
                        "type": "text",
                    }
                ],
            },
            "change_impact_code_scan_required": False,
            "timeline": ["change_impact_code_scan"],
        }
    if analysis.earliest_affected_contract_stage is not None or any(
        change.contract_impact != "preserves" for change in analysis.atomic_changes
    ):
        return {
            "phase": "change_impact_code_scan",
            "status": "failed",
            "message": "已发现契约失效或未知事实，禁止继续扫描代码。",
            "change_impact_code_scan_required": False,
            "timeline": ["change_impact_code_scan"],
        }
    workspace = workspace_from_state(state)
    if not workspace:
        return {
            "phase": "change_impact_code_scan",
            "status": "requires_user_input",
            "message": "没有显式 workspaceRoot，无法取得实现证据。",
            "change_impact_code_scan_required": False,
            "clarification": {
                "mode": "change_impact_insufficient_evidence",
                "status": "requires_user_input",
                "message": "请提供当前工程工作区后再判断实现问题。",
                "questions": [],
            },
            "timeline": ["change_impact_code_scan"],
        }
    target = state.get("change_target")
    target = target if isinstance(target, dict) else None
    request = str(state.get("request") or "")
    try:
        # 代码扫描节点不能信任 checkpoint 里保存的旧分析：重新读取当前确认
        # JSON、重新建立 bounded candidates，并复核正文/阶段/selector/hash，
        # 防止伪造 state 或文件在两节点之间变化后绕过契约门。
        corpus = load_confirmed_contract_corpus(workspace)
        candidate_facts = corpus.search(
            _analysis_queries(request, target=target),
            top_k=80,
        )
        if not candidate_facts:
            raise ValueError("当前 JSON 中没有与请求相关的候选事实。")
        if (
            corpus.relevant_unavailable_artifacts(request, target=target)
            or corpus.relevant_skipped_artifacts(request, target=target)
        ):
            raise ValueError("相关已确认 JSON 产物缺失，覆盖不完整。")
        validate_change_impact_analysis(
            analysis,
            corpus=corpus,
            candidate_facts=candidate_facts,
        )
        if analysis.analysis_status != AnalysisStatus.COMPLETED:
            raise ValueError("契约分析尚未完成。")
        if any(
            change.code_scan.performed or change.code_scan.findings
            for change in analysis.atomic_changes
        ):
            # contract-only Analyzer 的 codeScan 必须为空；已有扫描结果来自
            # 不可信恢复态时丢弃并重新扫描，而不是直接交给写 Agent。
            raise ValueError("恢复态携带了未经本节点执行的 code.scan 结果。")
    except Exception:  # noqa: BLE001 - 任何复核失败都只能停在澄清
        return {
            "phase": "change_impact_code_scan",
            "status": "requires_user_input",
            "message": "当前契约证据无法在最新已确认 JSON 中复核，暂不写入代码。",
            "clarification": {
                "mode": "change_impact_insufficient_evidence",
                "status": "requires_user_input",
                "message": "当前证据不足以证明这是实现问题，请刷新已确认 JSON 或补充具体页面、接口和预期行为。",
                "questions": [
                    {
                        "id": "change_impact_insufficient_evidence",
                        "header": "补充实现证据",
                        "question": "请补充具体页面、接口、操作和预期行为。",
                        "type": "text",
                    }
                ],
            },
            "change_impact_code_scan_required": False,
            "timeline": ["change_impact_code_scan"],
        }
    scan = sanitize_code_scan_evidence(
        scan_targeted_code(
            workspace=workspace,
            request=request,
            candidate_paths=state.get("direct_modification_target_paths", []),
            target=target,
            max_results=20,
        ),
        workspace=workspace,
        max_results=20,
        require_exists=True,
    )
    scan_json = scan.model_dump(mode="json", by_alias=True)
    changes = [
        item.model_copy(update={"code_scan": scan}).model_dump(mode="json", by_alias=True)
        for item in analysis.atomic_changes
    ]
    # 直接更新 JSON，避免把模型对象重新构造成不同的 schema 形状。
    updated_analysis = {**raw_analysis, "atomicChanges": changes}
    if not scan.findings:
        return {
            "phase": "change_impact_code_scan",
            "status": "requires_user_input",
            "message": "契约保持，但没有取得足够的实现层代码证据，暂不写入代码。",
            "change_impact_analysis": updated_analysis,
            "change_impact_code_scan": scan_json,
            "change_impact_code_scan_required": False,
            "clarification": {
                "mode": "change_impact_insufficient_evidence",
                "status": "requires_user_input",
                "message": "没有找到能证明实现问题的源码位置，请补充具体文件、组件或接口。",
                "questions": [
                    {
                        "id": "change_impact_insufficient_evidence",
                        "header": "补充实现证据",
                        "question": "请补充具体文件、组件或接口位置，以及期望行为。",
                        "type": "text",
                    }
                ],
            },
            "timeline": ["change_impact_code_scan"],
        }
    return {
        "phase": "change_impact_code_scan",
        "status": "in_progress",
        "message": "契约事实保持，已取得目标代码实现证据。",
        "change_impact_analysis": updated_analysis,
        "change_impact_code_scan": scan_json,
        "change_impact_code_scan_required": False,
        "clarification": {},
        "timeline": ["change_impact_code_scan"],
    }


def _direct_source_candidates(
    state: ProjectState,
    *,
    owner: str,
) -> list[str]:
    """从代码证据和扫描快照提取源码候选，让执行 Agent 优先读取业务代码。"""

    element_path = _direct_element_workspace_path(state)
    candidates: list[str] = (
        [element_path]
        if element_path and owner == "frontend" and direct_path_matches_owner(element_path, owner)
        else []
    )

    # ChangeImpactAnalyzer 的 code.scan 已经给出局部定位时优先使用它；
    # 重新校验路径和 owner，避免把模型或扫描器返回的越界路径直接交给写 Agent。
    scan = state.get("change_impact_code_scan")
    findings = scan.get("findings") if isinstance(scan, dict) else []
    workspace = workspace_from_state(state)
    if isinstance(findings, list):
        root = Path(workspace).expanduser().resolve() if workspace else None
        for item in findings:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or "").strip().replace("\\", "/").lstrip("/")
            if (
                not raw_path
                or _is_generated_or_dependency_path(raw_path)
                or not direct_path_matches_owner(raw_path, owner)
                or root is None
            ):
                continue
            candidate = (root / raw_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_file() and raw_path not in candidates:
                candidates.append(raw_path)
            if len(candidates) >= 100:
                return candidates

    snapshot = _workspace_snapshot_for_classification(state)
    section = snapshot.get("frontend" if owner == "frontend" else "backend")
    section = section if isinstance(section, dict) else {}
    keys = (
        ("pages", "components", "api_clients")
        if owner == "frontend"
        else ("api_routes", "models")
    )
    for key in keys:
        values = section.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            path = str(item.get("path") or "").strip() if isinstance(item, dict) else ""
            normalized = path.replace("\\", "/").lstrip("/")
            if not normalized or _is_generated_or_dependency_path(normalized):
                continue
            if normalized not in candidates:
                candidates.append(normalized)
            if len(candidates) >= 100:
                return candidates
    return candidates


def _is_generated_or_dependency_path(path: str) -> bool:
    """拒绝把依赖、缓存和构建产物作为自由对话源码候选。"""

    ignored = {
        ".next",
        ".turbo",
        ".venv",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
    }
    return any(part.casefold() in ignored for part in path.split("/"))


def _classification_message(intent: str, owner: str) -> str:
    """为自由对话分类结果生成不暴露内部 Prompt 的简短状态。"""

    if intent == "casual_chat":
        return "已识别为常规对话。"
    if intent == "workspace_question":
        return "已识别为只读工作区问答。"
    return f"已识别为 {owner} 局部工作区修改。"


def _revision_affected_resources(target: Any, resources: list[str]) -> list[str]:
    """把当前会话目标确定性补入影响资源，避免模型遗漏 page 或 endpoint。"""

    result = list(dict.fromkeys(resources))
    if not isinstance(target, dict):
        return result
    target_type = str(target.get("type") or "")
    target_id = ""
    if target_type == "page":
        target_id = str(target.get("pageId") or "").strip()
    elif target_type == "endpoint":
        target_id = str(target.get("endpointId") or "").strip()
    key = f"{target_type}:{target_id}" if target_type and target_id else ""
    if key and key not in result:
        result.insert(0, key)
    return result


def respond_to_casual_conversation(state: ProjectState) -> dict[str, Any]:
    """使用无工具模型生成常规对话回复并直接形成可完成状态。"""

    response = answer_casual_conversation(
        user_request=str(state.get("request") or ""),
        conversation_summary=str(state.get("direct_modification_summary") or ""),
    )
    return {
        "phase": "respond_conversation",
        "status": "completed" if response else "failed",
        "message": response or "对话模型没有返回有效内容。",
        "conversation_response": response,
        "clarification": {},
        "timeline": ["respond_conversation"],
    }


def respond_to_workspace_question(state: ProjectState) -> dict[str, Any]:
    """调用只读工作区 Agent 回答需要工程证据的问题。"""

    response = answer_workspace_question(
        user_request=str(state.get("request") or ""),
        conversation_summary=str(state.get("direct_modification_summary") or ""),
        workspace=workspace_from_state(state),
        selected_skill_names=state.get("selected_skill_names"),
        on_tool_activity=_tool_activity_writer("answer_workspace"),
        on_text_delta=_conversation_text_delta_writer(),
    )
    return {
        "phase": "answer_workspace",
        "status": "completed" if response else "failed",
        "message": response or "工作区问答 Agent 没有返回有效内容。",
        "conversation_response": response,
        "clarification": {},
        "timeline": ["answer_workspace"],
    }


def execute_frontend_direct_modification(state: ProjectState) -> dict[str, Any]:
    """使用共用 Frontend Agent 和独立 Prompt 执行局部前端修改。"""

    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="direct_modification.frontend",
        action=lambda: invoke_frontend_direct_modification(
            user_request=str(state.get("request") or ""),
            conversation_summary=str(state.get("direct_modification_summary") or ""),
            backend_handoff=state.get("backend_handoff"),
            element_context=state.get("element_context"),
            candidate_files=_direct_source_candidates(state, owner="frontend"),
            approved_paths=state.get("direct_modification_approved_paths"),
            workspace=workspace,
            selected_skill_names=state.get("selected_skill_names"),
            on_tool_activity=_tool_activity_writer("execute_frontend"),
        ),
        capture_exceptions=True,
    )
    stage_result = validated_direct_stage_result(
        _direct_result_from_capture(captured),
        code_change_set=captured.code_change_set,
        owner="frontend",
    )
    code_graph_index = refresh_code_graph_after_changes(
        workspace,
        [captured.code_change_set] if captured.code_change_set else [],
        on_progress=_code_graph_progress_writer("execute_frontend"),
    )
    return _stage_update(
        state,
        stage="frontend",
        phase="execute_frontend",
        stage_result=stage_result,
        code_change_set=captured.code_change_set,
        code_graph_index=code_graph_index,
    )


def execute_backend_direct_modification(state: ProjectState) -> dict[str, Any]:
    """使用共用 Data Source Agent 和独立 Prompt 执行局部后端修改。"""

    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="direct_modification.data_source",
        action=lambda: invoke_data_source_direct_modification(
            user_request=str(state.get("request") or ""),
            conversation_summary=str(state.get("direct_modification_summary") or ""),
            candidate_files=_direct_source_candidates(state, owner="backend"),
            approved_paths=state.get("direct_modification_approved_paths"),
            workspace=workspace,
            selected_skill_names=state.get("selected_skill_names"),
            on_tool_activity=_tool_activity_writer("execute_backend"),
        ),
        capture_exceptions=True,
    )
    stage_result = validated_direct_stage_result(
        _direct_result_from_capture(captured),
        code_change_set=captured.code_change_set,
        owner="backend",
    )
    code_graph_index = refresh_code_graph_after_changes(
        workspace,
        [captured.code_change_set] if captured.code_change_set else [],
        on_progress=_code_graph_progress_writer("execute_backend"),
    )
    handoff = dict(stage_result.get("backendHandoff") or {})
    handoff["changedFiles"] = list(stage_result.get("changedFiles") or [])
    return {
        **_stage_update(
            state,
            stage="backend",
            phase="execute_backend",
            stage_result=stage_result,
            code_change_set=captured.code_change_set,
            code_graph_index=code_graph_index,
        ),
        "backend_handoff": handoff,
    }


def execute_workspace_direct_modification(state: ProjectState) -> dict[str, Any]:
    """使用共享 SmallTask Agent 修改分类器明确给出的普通工作区路径。"""

    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="conversation.workspace_change",
        action=lambda: invoke_workspace_direct_modification(
            user_request=str(state.get("request") or ""),
            conversation_summary=str(state.get("direct_modification_summary") or ""),
            target_paths=list(state.get("direct_modification_target_paths", [])),
            approved_paths=state.get("direct_modification_approved_paths"),
            workspace=workspace,
            selected_skill_names=state.get("selected_skill_names"),
            on_tool_activity=_tool_activity_writer("execute_workspace"),
        ),
        capture_exceptions=True,
    )
    stage_result = validated_direct_stage_result(
        _direct_result_from_capture(captured),
        code_change_set=captured.code_change_set,
        owner="workspace",
    )
    update = _stage_update(
        state,
        stage="workspace",
        phase="execute_workspace",
        stage_result=stage_result,
        code_change_set=captured.code_change_set,
    )
    if update.get("status") == "in_progress":
        update["status"] = "completed"
    return update


def validate_direct_fix(state: ProjectState) -> dict[str, Any]:
    """只构建检查本轮真实改动所属工程层，并把可归因失败交给局部修复节点。"""

    repair_iteration = max(0, int(state.get("repair_iteration", 0) or 0))
    max_repair_iterations = max(
        1,
        int(state.get("max_repair_iterations", 3) or 3),
    )
    changed_paths = _direct_fix_changed_paths(state)
    affected_layers = set(changed_paths)
    validation_state = {
        **state,
        # 快速修改只做受影响层的构建验证，既不生成也不执行单元测试。
        "unit_test_generation_enabled": False,
        "repair_iteration": repair_iteration,
        "max_repair_iterations": max_repair_iterations,
    }
    result = run_integration_checks(
        validation_state,
        on_progress=_check_progress_snapshot_writer(),
        phase="build",
        artifact_namespace="direct-fix",
        affected_layers=affected_layers,
        install_frontend_dependencies=False,
    )
    test_results = _scope_direct_validation_results(
        [item for item in result.get("test_results", []) if isinstance(item, dict)],
        changed_paths=changed_paths,
    )
    scope_valid = bool(affected_layers) and bool(test_results)
    if not scope_valid:
        test_results = [
            {
                "id": "direct_fix_scope",
                "name": "快速修改范围检查",
                "passed": False,
                "required": True,
                "blocking": True,
                "evidence": "本轮真实差异无法映射到 Frontend 或 Backend 代码层。",
            }
        ]
    report = evaluate_quality_gate(
        test_results=test_results,
        source="direct_fix_validation",
    )
    passed = scope_valid and report["passed"] is True
    revision_requests = (
        [item for item in report.get("revision_requests", []) if isinstance(item, dict)]
        if scope_valid
        else []
    )
    can_repair = (
        not passed
        and bool(revision_requests)
        and repair_iteration < max_repair_iterations
    )
    report_json_path = write_test_report_json(validation_state, report)
    report_path = write_test_report_markdown(validation_state, report)
    return {
        "phase": "validate_direct_fix",
        "status": "completed" if passed else "failed",
        "message": (
            "本次修改范围验证通过。"
            if passed
            else (
                f"本次修改范围验证失败，准备执行第 {repair_iteration + 1}/{max_repair_iterations} 轮自动修复。"
                if can_repair
                else (
                    f"本次修改范围验证失败，自动修复已达到 {max_repair_iterations} 轮上限。"
                    if revision_requests and repair_iteration >= max_repair_iterations
                    else "本次修改缺少可验证的真实代码范围。"
                    if not scope_valid
                    else "本次修改范围验证失败，请查看验证日志。"
                )
            )
        ),
        "test_results": test_results,
        "test_events": result.get("test_events", []),
        "test_report": report,
        "test_report_path": report_path,
        "test_report_json_path": report_json_path,
        "quality_gate_passed": passed,
        "needs_revision": bool(revision_requests),
        "revision_requests": revision_requests,
        "repair_iteration": repair_iteration,
        "max_repair_iterations": max_repair_iterations,
        "integration_next_action": (
            "finalize_direct_modification"
            if passed
            else "direct_modification_repair"
            if can_repair
            else "handle_failure"
        ),
        "repair_task_plan": state.get("repair_task_plan", {}),
        "repair_tasks": state.get("repair_tasks", []),
        "small_task_tasks": state.get("small_task_tasks", []),
        "small_task_results": state.get("small_task_results", []),
        "small_task_code_change_sets": state.get("small_task_code_change_sets", []),
        "direct_code_change_sets": state.get("direct_code_change_sets", []),
        "clarification": {},
        "timeline": ["validate_direct_fix"],
    }


def _direct_fix_changed_paths(state: ProjectState) -> dict[str, list[str]]:
    """按工程层汇总本轮真实差异路径，作为快速验证和失败归因的唯一边界。"""

    paths: dict[str, list[str]] = {"frontend": [], "backend": []}
    for change_set in state.get("direct_code_change_sets", []):
        if not isinstance(change_set, dict):
            continue
        for item in change_set.get("files", []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip().replace("\\", "/").lstrip("/")
            root = path.split("/", 1)[0].casefold() if path else ""
            if root in paths and path.casefold() not in {
                existing.casefold() for existing in paths[root]
            }:
                paths[root].append(path)
    return {layer: values for layer, values in paths.items() if values}


def _scope_direct_validation_results(
    results: list[dict[str, Any]],
    *,
    changed_paths: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """把无法归因到本轮真实差异的同层历史失败降为非阻断告警。"""

    scoped: list[dict[str, Any]] = []
    for result in results:
        layer = str(result.get("layer") or "").strip().lower()
        paths = changed_paths.get(layer, [])
        if result.get("passed") is True or _direct_failure_matches_changes(result, paths):
            scoped.append(result)
            continue
        scoped.append(
            {
                **result,
                "blocking": False,
                "advisory": True,
                "evidence": (
                    f"{str(result.get('evidence') or '').strip()}；"
                    "失败证据未指向本次真实改动文件，按既有或无关失败记录。"
                ).strip("；"),
            }
        )
    return scoped


def _direct_failure_matches_changes(result: dict[str, Any], paths: list[str]) -> bool:
    """判断失败证据是否指向变更文件；工程级配置变更默认影响所属层全部检查。"""

    if not paths:
        return False
    global_markers = (
        "package.json",
        "pnpm-lock",
        "package-lock",
        "yarn.lock",
        "tsconfig",
        "vite.config",
        "pom.xml",
        "pyproject.toml",
        "requirements.txt",
    )
    normalized_paths = [path.replace("\\", "/").casefold() for path in paths]
    if any(marker in path for path in normalized_paths for marker in global_markers):
        return True
    evidence = "\n".join(
        str(result.get(key) or "")
        for key in ("evidence", "command")
    ).replace("\\", "/").casefold()
    return any(
        path in evidence or Path(path).name.casefold() in evidence
        for path in normalized_paths
        if path
    )


def finalize_direct_modification(state: ProjectState) -> dict[str, Any]:
    """把各阶段结果合并为快速修改公开终态，并更新有界会话摘要。"""

    current_status = str(state.get("status") or "failed")
    launch_result = state.get("launch_result") if isinstance(state.get("launch_result"), dict) else {}
    conversation_intent = str(state.get("conversation_intent") or "implementation_fix")
    is_answer = conversation_intent in {"casual_chat", "workspace_question"}
    is_cancelled = (
        str(state.get("direct_modification_handoff_decision") or "").strip().lower()
        == "rejected"
    )
    if is_cancelled:
        status = "completed"
    elif is_answer and current_status == "completed":
        status = "completed"
    elif launch_result and launch_result.get("status") != "failed":
        status = "completed"
    elif current_status == "requires_user_input":
        status = "requires_user_input"
    elif current_status == "failed" or launch_result.get("status") == "failed":
        status = "failed"
    else:
        status = current_status if current_status in {"completed", "failed"} else "failed"

    stage_results = _finalize_stage_results(
        state.get("direct_stage_results", {}),
        final_status=status,
    )
    stage_summaries = [
        str(item.get("summary") or "")
        for item in stage_results.values()
        if isinstance(item, dict) and str(item.get("summary") or "").strip()
    ]
    message = (
        str(state.get("conversation_response") or direct_state_message(state)).strip()
        if is_answer or is_cancelled
        else direct_final_message(
            status=status,
            current_message=direct_state_message(state),
            stage_summaries=stage_summaries,
        )
    )
    code_changes = merge_code_change_sets(state.get("direct_code_change_sets", []))
    normalized_test_report_path = str(state.get("test_report_path") or "").replace(
        "\\", "/"
    )
    public_test_report_path = (
        ".xcodeagent/reports/test-report.md"
        if normalized_test_report_path.endswith(".xcodeagent/reports/test-report.md")
        else None
    )
    direct_result = {
        "status": status,
        "intent": conversation_intent,
        "owner": state.get("direct_modification_owner", "unknown"),
        "scope": state.get("direct_modification_scope", "clarification"),
        "summary": message,
        "stageResults": stage_results,
        "codeChanges": code_changes or {},
        "tests": {
            "passed": state.get("quality_gate_passed") is True,
            "checks": state.get("test_results", []),
            "reportPath": public_test_report_path,
        },
        "logPaths": direct_test_log_paths(state.get("test_results", [])),
        "launchResult": launch_result,
        "previewUrl": state.get("preview_url"),
        "repairIteration": state.get("repair_iteration", 0),
        "maxRepairIterations": state.get("max_repair_iterations", 3),
        "repairTaskPlan": state.get("repair_task_plan", {}),
        "repairTasks": state.get("repair_tasks", []),
        "smallTaskResults": state.get("small_task_results", []),
        "changeImpactAnalysis": state.get("change_impact_analysis", {}),
        "changeImpactCodeScan": state.get("change_impact_code_scan", {}),
    }
    return {
        "phase": "conversation",
        "status": status,
        "message": message,
        "direct_modification_result": direct_result,
        "direct_stage_results": stage_results,
        "direct_modification_summary": append_direct_conversation_summary(
            str(state.get("direct_modification_summary") or ""),
            request=str(state.get("request") or ""),
            outcome=message,
        ),
        "code_changes": code_changes or {},
        "acceptance_request": {},
        "clarification": state.get("clarification", {}) if status == "requires_user_input" else {},
        "timeline": ["finalize_direct_modification"],
    }


def _stage_update(
    state: ProjectState,
    *,
    stage: str,
    phase: str,
    stage_result: dict[str, Any],
    code_change_set: dict[str, Any] | None,
    code_graph_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """合并单个 Agent 阶段结果和本轮权威代码差异。"""

    succeeded = stage_result.get("status") in {"completed", "already_satisfied"}
    has_partial_changes = (
        stage_result.get("partialChanges") is True
        and _code_change_set_has_files(code_change_set)
    )
    # 有真实落盘差异时，单次工具/模型异常只作为告警，继续交给独立验收节点判断。
    recoverable_failure = stage_result.get("status") == "failed" and has_partial_changes
    escalated = stage_result.get("status") in {
        "requires_user_confirmation",
        "requires_workflow",
    }
    change_sets = list(state.get("direct_code_change_sets", []))
    if code_change_set:
        change_sets.append(code_change_set)
    stage_status = (
        "requires_user_input"
        if escalated
        else "in_progress"
        if succeeded or recoverable_failure
        else "failed"
    )
    return {
        "phase": phase,
        "status": stage_status,
        "message": (
            "已保留已写入的修改，正在继续独立验收。"
            if recoverable_failure
            else stage_result.get("summary")
        ),
        "direct_stage_results": {
            **state.get("direct_stage_results", {}),
            stage: stage_result,
        },
        "direct_code_change_sets": change_sets,
        "code_changes": code_change_set or state.get("code_changes", {}),
        **({"code_graph_index": code_graph_index} if code_graph_index else {}),
        **(
            _direct_small_task_handoff(state, stage_result)
            if escalated
            else {"clarification": {}}
        ),
        "timeline": [phase],
    }


def _direct_small_task_handoff(
    state: ProjectState,
    stage_result: dict[str, Any],
) -> dict[str, Any]:
    """范围扩展保留确认卡；正式升级重新路由并生成统一只读影响确认。"""

    escalation = stage_result.get("escalation")
    escalation = escalation if isinstance(escalation, dict) else {}
    target = str(escalation.get("workflowIntent") or "development_readiness_gate")
    reason = str(
        escalation.get("reason")
        or stage_result.get("summary")
        or "该修改需要正式工作流。"
    )[:2_000]
    if stage_result.get("status") == "requires_workflow":
        return build_small_task_revision_confirmation(
            state=state,
            escalation=escalation,
            reason=reason,
        )
    clarification = {
        "mode": "small_task_scope_confirmation",
        "status": "requires_user_input",
        "message": "自由对话修改需要确认后才能继续。",
        "reason": reason,
        "requestedPaths": escalation.get("requestedPaths", []),
        "requestedResources": escalation.get("requestedResources", []),
        "questions": [
            {
                "id": "small_task_handoff",
                "header": "修改升级确认",
                "question": f"该修改需要扩大代码范围。原因：{reason} 是否确认？",
                "type": "yesno",
                "allowOther": False,
            }
        ],
    }
    return {"clarification": clarification}


def _direct_result_from_capture(captured: CapturedWorkspaceChanges) -> dict[str, Any]:
    """把 Agent 异常转换为带告警的阶段结果，同时保留已经产生的文件差异。"""

    if captured.error is None:
        return parse_direct_modification_agent_result(str(captured.value or ""))
    failure_reason = (
        f"{type(captured.error).__name__}: {captured.error}"
    )[:2_000]
    has_partial_changes = _code_change_set_has_files(captured.code_change_set)
    return {
        "status": "failed",
        "summary": (
            "快速修改 Agent 的某个工具调用中断，但已保留已写入的代码差异，正在继续验收。"
            if has_partial_changes
            else "快速修改 Agent 执行中断，未检测到已写入的代码差异。"
        ),
        "changedFiles": [],
        "verification": [],
        "alreadySatisfied": False,
        "failureReason": failure_reason,
        "partialChanges": has_partial_changes,
        "backendHandoff": {},
    }


def _code_change_set_has_files(code_change_set: dict[str, Any] | None) -> bool:
    """判断工作区快照是否捕获到至少一个真实文件差异。"""

    return bool(
        isinstance(code_change_set, dict)
        and any(
            isinstance(item, dict) and str(item.get("path") or "").strip()
            for item in code_change_set.get("files", [])
        )
    )


def _finalize_stage_results(
    raw_stage_results: Any,
    *,
    final_status: str,
) -> dict[str, dict[str, Any]]:
    """将已通过最终验收的部分失败阶段公开为成功，并保留原始工具告警。"""

    if not isinstance(raw_stage_results, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for stage, raw_item in raw_stage_results.items():
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        if final_status == "completed" and item.get("partialChanges") is True:
            original_summary = str(item.get("summary") or "").strip()
            item.update(
                {
                    "status": "completed",
                    "summary": "修改已落盘，并通过最终验收。",
                    "recoveredFromToolFailure": True,
                }
            )
            if original_summary:
                item["agentSummary"] = original_summary
        result[str(stage)] = item
    return result


def _tool_activity_writer(node_name: str) -> ToolActivityCallback:
    """把 Deep Agent 的安全化工具活动转发为 Graph custom stream。"""

    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = lambda _event: None

    def report(activity: dict[str, Any]) -> None:
        """发送一次带节点归属的工具活动。"""

        writer(
            {
                "type": "conversation.tool_activity",
                "node_name": node_name,
                "activity": activity,
            }
        )

    return report


def _code_graph_progress_writer(node_name: str) -> ToolActivityCallback:
    """把写入后的代码图刷新进度送入快速修改 AG-UI 流。"""

    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = lambda _event: None

    def report(progress: Any) -> None:
        """发送一条代码图刷新 custom 事件。"""

        detail = progress.as_dict() if hasattr(progress, "as_dict") else {}
        writer(
            {
                "type": "workspace_inspection.progress",
                "node_name": node_name,
                "message": str(detail.get("message") or "正在更新代码索引…"),
                "detail": detail,
            }
        )

    return report


def _conversation_text_delta_writer() -> Callable[[str], None] | None:
    """把模型文本增量写入 Graph custom stream，供 AG-UI 实时转发。"""

    try:
        writer = get_stream_writer()
    except RuntimeError:
        return None

    def report(delta: str) -> None:
        """发送一段不包含路由元数据的助手正文。"""

        if delta:
            writer({"type": "conversation.text_delta", "delta": delta})

    return report
