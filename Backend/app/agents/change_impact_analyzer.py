"""只读 ChangeImpactAnalyzer：先比较已确认 JSON 契约，再按需取得代码证据。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.domain.change_impact import (
    AnalysisStatus,
    AtomicChange,
    ChangeImpactAnalysis,
    ContractEvidence,
    ContractImpact,
    ContractStage,
    CodeScanEvidence,
    ConflictRelation,
    analysis_to_json,
)
from app.services.change_contracts import ContractCorpus, ContractFactRecord, load_confirmed_contract_corpus
from app.services.change_code_scan import sanitize_code_scan_evidence, scan_targeted_code
from app.utils.model_output import extract_json_object


class CodeScanner(Protocol):
    """描述 Analyzer 可选的只读目标代码扫描器。"""

    def __call__(self, **kwargs: Any) -> CodeScanEvidence | dict[str, Any]:
        """依据用户请求返回局部实现证据。"""


class ChangeImpactAnalyzerError(ValueError):
    """表示模型结果缺少可复核 JSON 证据或违反 Analyzer 合同。"""


CHANGE_IMPACT_ANALYZER_SYSTEM_PROMPT = """你是软件开发工作流中的 ChangeImpactAnalyzer（变更影响分析器）。
你是只读分析器，不修改代码、契约或工作流，也不决定要重新执行哪些节点。
你的唯一任务是判断用户最新请求是否使“当前已确认 JSON 契约事实”失效。

必须遵守：
1. 先把复合请求拆成 atomicChanges，再比较给出的 JSON 事实。
2. 只能引用输入中出现的 artifactKey、jsonPointer、selector、artifactSha256；不得编造 Contract ID。
3. invalidates 必须携带可定位的 contractEvidence；找不到足够事实时必须使用 unknown，不能猜测。
4. preserves 也必须有已确认 JSON 事实作为证据；没有证据不是 preserves。
5. 只有所有契约事实 preserves 时，才可以把 codeScan 标记为需要执行；契约失效时 codeScan.performed 必须为 false。
6. 不输出 route、owner、formalBranch、revisionType、earliestArtifact、needConfirmation 或任何 Workflow 决策字段。
7. 只返回一个 JSON 对象，不要 Markdown、解释文字或代码围栏。

contractStage 只能是 requirement_design 或 planning_design；contractImpact 只能是 invalidates、preserves、unknown；
conflictRelation 只能是 contradicts、removes、reassigns、modifies、preserves。
"""


@dataclass(frozen=True)
class AnalyzerContext:
    """保存一次分析所用的 JSON 证据上下文，便于日志和测试复核。"""

    corpus: ContractCorpus
    candidate_facts: tuple[ContractFactRecord, ...]


class ChangeImpactAnalyzer:
    """窄职责契约失效检测器，不拥有任何写入工具。"""

    def __init__(
        self,
        *,
        model: Any | None = None,
        code_scanner: CodeScanner | None = None,
    ) -> None:
        """注入模型和只读代码扫描器，生产环境默认使用配置模型。"""

        self._model = model
        self._code_scanner = code_scanner or scan_targeted_code

    def analyze(
        self,
        request: str,
        workspace: str | Path,
        *,
        target: dict[str, Any] | None = None,
        candidate_paths: Iterable[str] | None = None,
        allow_code_scan: bool = False,
    ) -> ChangeImpactAnalysis:
        """执行 JSON 契约分析，并按显式开关决定是否取得代码证据。"""

        text = str(request or "").strip()
        if not text:
            return _insufficient_analysis("用户请求为空。")
        corpus = load_confirmed_contract_corpus(workspace)
        if not corpus.has_confirmed_artifacts:
            return _insufficient_analysis("当前工作区没有可用的已确认 JSON 契约。")
        candidate_facts = tuple(
            corpus.search(_analysis_queries(text, target=target), top_k=80)
        )
        if not candidate_facts:
            # 搜索不到事实时不能让模型把“没有证据”解释成 preserves。
            return _insufficient_analysis("当前已确认 JSON 中没有检索到与请求相关的契约事实。")
        context = AnalyzerContext(corpus=corpus, candidate_facts=candidate_facts)
        try:
            payload = self._invoke_model(text, context=context, target=target)
            analysis = normalize_change_impact_payload(
                payload,
                corpus=corpus,
                request=text,
                candidate_facts=candidate_facts,
            )
        except Exception as exc:  # noqa: BLE001 - 分析失败必须安全降级为 unknown
            analysis = _insufficient_analysis(f"Analyzer 输出无法复核：{type(exc).__name__}。")
        analysis = _apply_coverage_guard(
            analysis,
            corpus=corpus,
            request=text,
            target=target,
        )
        if allow_code_scan and _can_scan_code(analysis):
            analysis = self._attach_code_scan(
                analysis,
                workspace=workspace,
                request=text,
                target=target,
                candidate_paths=candidate_paths,
            )
        return analysis

    def analyze_contracts(
        self,
        request: str,
        workspace: str | Path,
        *,
        target: dict[str, Any] | None = None,
    ) -> ChangeImpactAnalysis:
        """只执行 Contract Search/Read 和影响判断，绝不触发 code.scan。"""

        return self.analyze(request, workspace, target=target, allow_code_scan=False)

    def _invoke_model(
        self,
        request: str,
        *,
        context: AnalyzerContext,
        target: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """把有界 JSON 事实传给模型，并只接受一个 JSON 对象。"""

        model = self._model or create_chat_model(Settings.from_env())
        prompt = _build_analyzer_prompt(
            request,
            context=context,
            target=target,
        )
        response = model.invoke(
            [
                SystemMessage(content=CHANGE_IMPACT_ANALYZER_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        text = _coerce_content_text(getattr(response, "content", response)) or ""
        payload = extract_json_object(text)
        if not isinstance(payload, dict):
            raise ChangeImpactAnalyzerError("模型没有返回 JSON 对象。")
        return payload

    def _attach_code_scan(
        self,
        analysis: ChangeImpactAnalysis,
        *,
        workspace: str | Path,
        request: str,
        target: dict[str, Any] | None,
        candidate_paths: Iterable[str] | None,
    ) -> ChangeImpactAnalysis:
        """在契约全部保持时调用一次目标导向扫描，并把证据附着到原子变更。"""

        try:
            raw = self._code_scanner(
                workspace=workspace,
                request=request,
                target=target,
                candidate_paths=list(candidate_paths or []),
                max_results=20,
            )
            scan = sanitize_code_scan_evidence(
                _camelize_code_scan(raw) if not isinstance(raw, CodeScanEvidence) else raw,
                workspace=workspace,
                max_results=20,
            )
        except Exception as exc:  # noqa: BLE001 - 代码证据失败不能升级为写权限
            scan = CodeScanEvidence(
                performed=True,
                reason=f"代码扫描失败：{type(exc).__name__}。",
                findings=[],
            )
        changes = [change.model_copy(update={"code_scan": scan}) for change in analysis.atomic_changes]
        warnings = list(analysis.warnings)
        if not scan.findings:
            warnings.append("没有取得足够的实现层代码证据。")
        return analysis.model_copy(update={"atomic_changes": changes, "warnings": warnings[:20]})


def analyze_change_impact(
    request: str,
    workspace: str | Path,
    *,
    target: dict[str, Any] | None = None,
    candidate_paths: Iterable[str] | None = None,
    allow_code_scan: bool = False,
    model: Any | None = None,
    code_scanner: CodeScanner | None = None,
) -> ChangeImpactAnalysis:
    """提供函数式入口，便于 Graph 节点和单元测试复用同一 Analyzer。"""

    return ChangeImpactAnalyzer(model=model, code_scanner=code_scanner).analyze(
        request,
        workspace,
        target=target,
        candidate_paths=candidate_paths,
        allow_code_scan=allow_code_scan,
    )


def normalize_change_impact_payload(
    payload: dict[str, Any],
    *,
    corpus: ContractCorpus,
    request: str,
    candidate_facts: Iterable[ContractFactRecord] | None = None,
) -> ChangeImpactAnalysis:
    """严格归一化模型 JSON，并验证每条 invalidates 证据确实来自当前文件。"""

    allowed_root = {
        "analysisStatus", "analysis_status", "requestSummary", "request_summary",
        "atomicChanges", "atomic_changes", "earliestAffectedContractStage",
        "earliest_affected_contract_stage", "invalidatedContracts", "invalidated_contracts",
        "warnings",
    }
    unknown = set(payload) - allowed_root
    if unknown:
        raise ChangeImpactAnalyzerError(f"Analyzer 返回了 Workflow 或未知字段：{sorted(unknown)}")
    raw_changes = payload.get("atomicChanges", payload.get("atomic_changes"))
    if not isinstance(raw_changes, list) or not raw_changes:
        raise ChangeImpactAnalyzerError("atomicChanges 不能为空。")
    changes: list[AtomicChange] = []
    candidate_facts_tuple = tuple(candidate_facts) if candidate_facts is not None else None
    for index, raw in enumerate(raw_changes[:100]):
        if not isinstance(raw, dict):
            raise ChangeImpactAnalyzerError(f"atomicChanges[{index}] 不是对象。")
        changes.append(
            _normalize_atomic_change(
                raw,
                corpus=corpus,
                request=request,
                index=index,
                candidate_facts=candidate_facts_tuple,
            )
        )
    invalidated = [
        evidence
        for change in changes
        if change.contract_impact == ContractImpact.INVALIDATES
        for evidence in change.contract_evidence
    ]
    earliest = _earliest_stage(invalidated)
    requested_earliest = payload.get(
        "earliestAffectedContractStage",
        payload.get("earliest_affected_contract_stage"),
    )
    if requested_earliest is not None and str(requested_earliest) != (earliest.value if earliest else ""):
        raise ChangeImpactAnalyzerError("earliestAffectedContractStage 与证据不一致。")
    status_key_present = "analysisStatus" in payload or "analysis_status" in payload
    raw_status = str(payload.get("analysisStatus", payload.get("analysis_status", "")) or "")
    if raw_status not in {item.value for item in AnalysisStatus}:
        # 缺省状态可以按原子事实推导（方便严格 JSON 输出的最小模型），
        # 但显式写入未知状态必须保守降级，不能把模型拼写错误当成 completed。
        raw_status = (
            AnalysisStatus.INSUFFICIENT_EVIDENCE.value
            if status_key_present
            or any(change.contract_impact == ContractImpact.UNKNOWN for change in changes)
            else AnalysisStatus.COMPLETED.value
        )
    if any(change.contract_impact == ContractImpact.UNKNOWN for change in changes):
        raw_status = AnalysisStatus.INSUFFICIENT_EVIDENCE.value
    if candidate_facts is not None and not list(candidate_facts) and not invalidated:
        # 明确没有相关候选时，不能把“未搜到”解释为 preserves。
        raw_status = AnalysisStatus.INSUFFICIENT_EVIDENCE.value
    refs = _dedupe_evidence(invalidated)
    raw_invalidated = payload.get(
        "invalidatedContracts",
        payload.get("invalidated_contracts"),
    )
    if raw_invalidated not in (None, []):
        if not isinstance(raw_invalidated, list):
            raise ChangeImpactAnalyzerError("invalidatedContracts 必须是数组。")
        declared = _dedupe_evidence(
            [
                _normalize_evidence(
                    item,
                    corpus=corpus,
                    candidate_facts=candidate_facts_tuple,
                )
                for item in raw_invalidated[:200]
            ]
        )
        declared_keys = {
            (
                item.artifact_key,
                item.json_pointer,
                item.artifact_sha256,
                item.conflict_relation.value,
            )
            for item in declared
        }
        actual_keys = {
            (
                item.artifact_key,
                item.json_pointer,
                item.artifact_sha256,
                item.conflict_relation.value,
            )
            for item in refs
        }
        if declared_keys != actual_keys:
            raise ChangeImpactAnalyzerError("invalidatedContracts 与 atomicChanges 证据不一致。")
    return ChangeImpactAnalysis(
        analysisStatus=raw_status,
        requestSummary=str(
            payload.get("requestSummary") or payload.get("request_summary") or request
        ).strip()[:4_000],
        atomicChanges=changes,
        earliestAffectedContractStage=earliest.value if earliest else None,
        invalidatedContracts=refs,
        warnings=_string_list(payload.get("warnings"), limit=20),
    )


def validate_change_impact_analysis(
    analysis: ChangeImpactAnalysis,
    *,
    corpus: ContractCorpus,
    candidate_facts: Iterable[ContractFactRecord] | None = None,
) -> ChangeImpactAnalysis:
    """再次校验领域对象，供服务端在进入 Router 前阻止伪造证据。"""

    # preserves 证据同样决定是否允许 code.scan，不能只验证 invalidates。
    refs = [
        evidence
        for change in analysis.atomic_changes
        for evidence in change.contract_evidence
    ]
    candidate_index = (
        {
            (fact.artifact_key, fact.json_pointer, fact.artifact_sha256): fact
            for fact in candidate_facts
        }
        if candidate_facts is not None
        else None
    )
    if refs:
        resolved = corpus.read([evidence.model_dump(by_alias=True) for evidence in refs])
        resolved_by_identity = {
            (item.artifact_key, item.json_pointer, item.artifact_sha256): item
            for item in resolved
        }
        for evidence in refs:
            identity = (
                evidence.artifact_key,
                evidence.json_pointer,
                evidence.artifact_sha256,
            )
            fact = resolved_by_identity.get(identity)
            if fact is None:
                raise ChangeImpactAnalyzerError("契约证据无法在当前 JSON 文件中复核。")
            if candidate_index is not None and identity not in candidate_index:
                raise ChangeImpactAnalyzerError(
                    "契约证据不在本次 bounded contract search/read 候选中。"
                )
            # 路由前不仅复核 hash，还复核服务端索引出的事实正文、阶段和 selector；
            # 这样手工构造一个“真文件 hash + 假 existingFact”的对象也不能取得权限。
            if (
                evidence.contract_stage != fact.contract_stage
                or evidence.existing_fact != fact.existing_fact
                or evidence.selector != fact.selector
            ):
                raise ChangeImpactAnalyzerError("契约证据内容与当前 JSON 事实不一致。")
    invalidated = [
        evidence
        for change in analysis.atomic_changes
        if change.contract_impact == ContractImpact.INVALIDATES
        for evidence in change.contract_evidence
    ]
    expected_invalidated = {
        (
            item.artifact_key,
            item.json_pointer,
            item.artifact_sha256,
            item.conflict_relation.value,
        )
        for item in invalidated
    }
    actual_invalidated = {
        (
            item.artifact_key,
            item.json_pointer,
            item.artifact_sha256,
            item.conflict_relation.value,
        )
        for item in analysis.invalidated_contracts
    }
    if expected_invalidated != actual_invalidated:
        raise ChangeImpactAnalyzerError("invalidatedContracts 与 atomicChanges 证据不一致。")
    return analysis


def _normalize_atomic_change(
    raw: dict[str, Any],
    *,
    corpus: ContractCorpus,
    request: str,
    index: int,
    candidate_facts: Iterable[ContractFactRecord] | None = None,
) -> AtomicChange:
    """归一化一条原子变更，并严格检查其证据引用。"""

    allowed = {
        "changeId", "change_id", "requestedChange", "requested_change", "contractImpact",
        "contract_impact", "contractEvidence", "contract_evidence", "codeScan", "code_scan",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ChangeImpactAnalyzerError(f"atomicChanges[{index}] 含未知字段：{sorted(unknown)}")
    impact = str(raw.get("contractImpact", raw.get("contract_impact", "unknown")) or "unknown")
    if impact not in {item.value for item in ContractImpact}:
        impact = ContractImpact.UNKNOWN.value
    raw_evidence = raw.get("contractEvidence", raw.get("contract_evidence", []))
    if not isinstance(raw_evidence, list):
        raise ChangeImpactAnalyzerError(f"atomicChanges[{index}].contractEvidence 必须是数组。")
    evidence = [
        _normalize_evidence(
            item,
            corpus=corpus,
            candidate_facts=candidate_facts,
        )
        for item in raw_evidence[:100]
    ]
    if impact == ContractImpact.INVALIDATES and not evidence:
        raise ChangeImpactAnalyzerError("invalidates 不能没有 JSON 定位。")
    if impact == ContractImpact.INVALIDATES and any(
        item.conflict_relation == ConflictRelation.PRESERVES for item in evidence
    ):
        raise ChangeImpactAnalyzerError("invalidates 的 contractEvidence 不能使用 preserves 关系。")
    if impact == ContractImpact.PRESERVES:
        if not evidence or any(item.conflict_relation != ConflictRelation.PRESERVES for item in evidence):
            raise ChangeImpactAnalyzerError("preserves 必须引用关系为 preserves 的 JSON 事实。")
    # 模型在 contract-only 阶段不能凭空声明自己执行过 code.scan；真正的扫描
    # 只能由服务端在 preserves 之后调用只读扫描器并回填证据。
    code_scan = CodeScanEvidence(
        performed=False,
        reason="契约阶段尚未执行 code.scan。",
        findings=[],
    )
    return AtomicChange(
        changeId=str(raw.get("changeId", raw.get("change_id", f"C{index + 1}")) or f"C{index + 1}")[:128],
        requestedChange=str(raw.get("requestedChange", raw.get("requested_change", request)) or request)[:4_000],
        contractImpact=impact,
        contractEvidence=evidence,
        codeScan=code_scan,
    )


def _normalize_evidence(
    raw: Any,
    *,
    corpus: ContractCorpus,
    candidate_facts: Iterable[ContractFactRecord] | None = None,
) -> ContractEvidence:
    """把模型证据映射到当前 JSON 事实，缺少指针或哈希时直接拒绝。"""

    if not isinstance(raw, dict):
        raise ChangeImpactAnalyzerError("contractEvidence 项必须是对象。")
    allowed = {
        "artifactKey", "artifact_key", "jsonPointer", "json_pointer", "selector",
        "artifactSha256", "artifact_sha256", "contractStage", "contract_stage",
        "existingFact", "existing_fact", "requestedChange", "requested_change",
        "conflictRelation", "conflict_relation", "reason",
    }
    if set(raw) - allowed:
        raise ChangeImpactAnalyzerError("contractEvidence 含未知字段。")
    key = str(raw.get("artifactKey", raw.get("artifact_key", "")) or "")
    pointer = str(raw.get("jsonPointer", raw.get("json_pointer", "")) or "")
    digest = str(raw.get("artifactSha256", raw.get("artifact_sha256", "")) or "")
    # 证据身份必须由模型明确携带；服务端只负责复核，不能替模型补齐
    # 缺失的 hash/pointer 后把“猜测”升级成正式影响。
    if not key or not pointer or not digest:
        raise ChangeImpactAnalyzerError(
            "contractEvidence 必须明确提供 artifactKey、jsonPointer 和 artifactSha256。"
        )
    matches = corpus.read([{"artifactKey": key, "jsonPointer": pointer, "artifactSha256": digest}])
    if not matches:
        raise ChangeImpactAnalyzerError("contractEvidence 未命中当前已确认 JSON 的 artifactKey/jsonPointer/hash。")
    fact = matches[0]
    if candidate_facts is not None:
        candidate_index = {
            (item.artifact_key, item.json_pointer, item.artifact_sha256)
            for item in candidate_facts
        }
        if (fact.artifact_key, fact.json_pointer, fact.artifact_sha256) not in candidate_index:
            raise ChangeImpactAnalyzerError(
                "contractEvidence 不在本次 bounded contract search/read 候选中。"
            )
    raw_selector = raw.get("selector")
    if raw_selector is None:
        selector = fact.selector
    elif isinstance(raw_selector, dict):
        selector = {str(k): str(v) for k, v in raw_selector.items()}
        # selector 是事实定位的一部分；允许模型省略整个字段，但一旦提供就
        # 必须与索引的完整 selector 相等，不能用空对象或子集伪造资源归属。
        if selector != fact.selector:
            raise ChangeImpactAnalyzerError("contractEvidence selector 与 JSON 事实不一致。")
    else:
        raise ChangeImpactAnalyzerError("contractEvidence selector 必须是对象。")
    stage = str(raw.get("contractStage", raw.get("contract_stage", fact.contract_stage.value)) or "")
    if stage != fact.contract_stage.value:
        raise ChangeImpactAnalyzerError("contractEvidence contractStage 与 JSON 事实不一致。")
    relation = str(raw.get("conflictRelation", raw.get("conflict_relation", "preserves")) or "preserves")
    if relation not in {item.value for item in ConflictRelation}:
        raise ChangeImpactAnalyzerError("contractEvidence conflictRelation 无效。")
    return ContractEvidence(
        artifactKey=fact.artifact_key,
        jsonPointer=fact.json_pointer,
        selector={str(k): str(v) for k, v in selector.items()},
        artifactSha256=fact.artifact_sha256,
        contractStage=fact.contract_stage.value,
        # 事实正文来自当前 JSON 索引，不能接受模型改写后再展示为证据。
        existingFact=fact.existing_fact,
        requestedChange=str(raw.get("requestedChange", raw.get("requested_change", "用户请求")) or "用户请求")[:4_000],
        conflictRelation=relation,
        reason=str(raw.get("reason") or "已依据当前 JSON 事实完成比较。")[:4_000],
    )


def _normalize_code_scan(raw: Any) -> CodeScanEvidence:
    """为每条原子变更提供安全的默认 codeScan 结构。"""

    if raw is None:
        return CodeScanEvidence(performed=False, reason="契约层分析尚未请求 code.scan。", findings=[])
    if not isinstance(raw, dict):
        raise ChangeImpactAnalyzerError("codeScan 必须是对象。")
    findings = raw.get("findings") if isinstance(raw.get("findings"), list) else []
    return CodeScanEvidence(
        performed=bool(raw.get("performed")),
        reason=str(raw.get("reason") or "未提供 code.scan 原因")[:2_000],
        findings=findings[:100],
    )


def _build_analyzer_prompt(
    request: str,
    *,
    context: AnalyzerContext,
    target: dict[str, Any] | None,
) -> str:
    """构造只含 JSON 事实的模型输入，明确禁止参考 Markdown。"""

    facts = context.corpus.prompt_context(context.candidate_facts, limit=80)
    return (
        "只比较下面提供的当前已确认 JSON 事实。不要读取或假设任何 Markdown 文档，也不要把代码路径当作契约证据。\n"
        f"用户请求：{request}\n"
        f"当前目标：{json.dumps(target or {}, ensure_ascii=False, separators=(',', ':'))}\n"
        f"JSON 契约候选：{json.dumps(facts, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "返回 JSON 形状：{\n"
        '  "analysisStatus": "completed|insufficient_evidence",\n'
        '  "requestSummary": "...",\n'
        '  "atomicChanges": [{"changeId":"C1","requestedChange":"...",'
        '"contractImpact":"invalidates|preserves|unknown",'
        '"contractEvidence":[{"artifactKey":"...","jsonPointer":"/...",'
        '"selector":{},"artifactSha256":"...","contractStage":"requirement_design|planning_design",'
        '"existingFact":"...","requestedChange":"...",'
        '"conflictRelation":"contradicts|removes|reassigns|modifies|preserves","reason":"..."}],'
        '"codeScan":{"performed":false,"reason":"...","findings":[]}}],\n'
        '  "earliestAffectedContractStage": "requirement_design|planning_design|null",\n'
        '  "invalidatedContracts": [],\n'
        '  "warnings": []\n'
        "}\n"
    )


def _analysis_queries(
    request: str,
    *,
    target: dict[str, Any] | None = None,
) -> list[str]:
    """生成多组检索词，避免只用用户原句导致契约漏检。"""

    queries = [request]
    aliases = {
        "详情": ["详情页", "页面职责", "detail"],
        "首页": ["主页", "home", "页面"],
        "登录": ["登录按钮", "认证", "login"],
        "按钮": ["操作", "onClick", "button"],
        "接口": ["API", "endpoint", "请求", "响应"],
        "数据源": ["数据库", "source", "实体"],
    }
    for source, values in aliases.items():
        if source in request:
            queries.extend(values)
    if isinstance(target, dict):
        # 当前页面/接口目标是服务端已校验的上下文，不是模型自报的 Contract ID；
        # 只把它作为检索提示，最终证据仍必须命中 JSON Pointer 与文件哈希。
        for key in ("pageId", "page_id", "apiContractId", "api_contract_id", "endpointId", "endpoint_id"):
            value = str(target.get(key) or "").strip()
            if value:
                queries.append(value)
    return queries[:20]


def _normalize_code_scan_payload(value: Any) -> dict[str, Any]:
    """兼容 code scan 字段的驼峰输入。"""

    return value if isinstance(value, dict) else {}


def _camelize_code_scan(value: Any) -> dict[str, Any]:
    """将代码扫描器的 snake_case 字段转换为领域模型别名。"""

    if not isinstance(value, dict):
        return {}
    findings: list[dict[str, Any]] = []
    for item in value.get("findings", []):
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                "path": item.get("path"),
                "summary": item.get("summary"),
                "symbol": item.get("symbol"),
                "relevantCode": item.get("relevantCode", item.get("relevant_code")),
                "lineStart": item.get("lineStart", item.get("line_start")),
                "lineEnd": item.get("lineEnd", item.get("line_end")),
            }
        )
    return {"performed": value.get("performed", False), "reason": value.get("reason") or "", "findings": findings}


def _can_scan_code(analysis: ChangeImpactAnalysis) -> bool:
    """仅当所有原子变更均明确 preserves 且分析完成时允许代码扫描。"""

    return (
        analysis.analysis_status == AnalysisStatus.COMPLETED
        and bool(analysis.atomic_changes)
        and all(change.contract_impact == ContractImpact.PRESERVES for change in analysis.atomic_changes)
    )


def _apply_coverage_guard(
    analysis: ChangeImpactAnalysis,
    *,
    corpus: ContractCorpus,
    request: str,
    target: dict[str, Any] | None,
) -> ChangeImpactAnalysis:
    """当可能相关的确认 JSON 缺失时阻止 preserves 被当成实现问题。"""

    missing = tuple(
        dict.fromkeys(
            [
                *corpus.relevant_unavailable_artifacts(request, target=target),
                *corpus.relevant_skipped_artifacts(request, target=target),
            ]
        )
    )
    if not missing:
        return analysis
    warning = (
        "相关已确认 JSON 产物缺失，无法把当前结论视为完整覆盖："
        + "、".join(missing)
        + "。"
    )
    warnings = [*analysis.warnings, warning][:20]
    # 已有真实 invalidates 证据足以证明正式契约至少发生变化；保留 formal
    # 路由，但把覆盖缺口作为告警，后续上游闭包会重新生成缺失产物。
    if analysis.invalidated_contracts:
        return analysis.model_copy(update={"warnings": warnings})
    if analysis.analysis_status == AnalysisStatus.INSUFFICIENT_EVIDENCE:
        return analysis.model_copy(update={"warnings": warnings})
    # preserves 结论不能在缺失相关产物时继续获得 code.scan 权限。附加一条
    # 明确的 unknown 原子变更，比仅改顶层 status 更容易在审计和 UI 中解释。
    unknown_change = AtomicChange(
        changeId="coverage-gap",
        requestedChange=str(request or "当前用户请求")[:4_000],
        contractImpact=ContractImpact.UNKNOWN.value,
        contractEvidence=[],
        codeScan=CodeScanEvidence(
            performed=False,
            reason="契约覆盖不完整，禁止 code.scan。",
            findings=[],
        ),
    )
    return analysis.model_copy(
        update={
            "analysis_status": AnalysisStatus.INSUFFICIENT_EVIDENCE,
            "atomic_changes": [*analysis.atomic_changes, unknown_change][:100],
            "warnings": warnings,
        }
    )


def _earliest_stage(evidence: Iterable[ContractEvidence]) -> ContractStage | None:
    """从失效证据中确定最早契约层级。"""

    stages = [item.contract_stage for item in evidence]
    if not stages:
        return None
    return min(stages, key=lambda stage: 0 if stage == ContractStage.REQUIREMENT_DESIGN else 1)


def _dedupe_evidence(items: Iterable[ContractEvidence]) -> list[ContractEvidence]:
    """按当前请求范围定位去重失效证据。"""

    result: list[ContractEvidence] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
        identity = (item.artifact_key, item.json_pointer, item.artifact_sha256, item.conflict_relation.value)
        if identity not in seen:
            seen.add(identity)
            result.append(item)
    return result


def _string_list(value: Any, *, limit: int) -> list[str]:
    """把告警数组裁剪为有限字符串。"""

    return [str(item).strip()[:2_000] for item in value if str(item).strip()][:limit] if isinstance(value, list) else []


def _insufficient_analysis(reason: str) -> ChangeImpactAnalysis:
    """构造不会获得写权限的 unknown 结果。"""

    return ChangeImpactAnalysis(
        analysisStatus=AnalysisStatus.INSUFFICIENT_EVIDENCE.value,
        requestSummary="无法基于当前已确认 JSON 完成契约影响判断。",
        atomicChanges=[
            AtomicChange(
                changeId="C1",
                requestedChange="当前用户请求",
                contractImpact=ContractImpact.UNKNOWN.value,
                contractEvidence=[],
                codeScan=CodeScanEvidence(performed=False, reason="契约证据不足，禁止 code.scan。", findings=[]),
            )
        ],
        earliestAffectedContractStage=None,
        invalidatedContracts=[],
        warnings=[reason[:2_000]],
    )


__all__ = [
    "CHANGE_IMPACT_ANALYZER_SYSTEM_PROMPT",
    "ChangeImpactAnalyzer",
    "ChangeImpactAnalyzerError",
    "analyze_change_impact",
    "analysis_to_json",
    "normalize_change_impact_payload",
    "validate_change_impact_analysis",
]
