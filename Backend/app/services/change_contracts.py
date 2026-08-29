"""从当前已确认 JSON 产物建立只读契约事实索引。

这里有意不读取 Markdown，也不创建全局 Contract ID。Analyzer 需要的定位信息
始终绑定在本次读取到的 artifact key、JSON Pointer、选择器和文件哈希上。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.domain.change_impact import ContractStage


_ARTIFACTS: tuple[tuple[str, str, ContractStage], ...] = (
    ("requirement-spec", ".xcodeagent/specs/requirement-spec.json", ContractStage.REQUIREMENT_DESIGN),
    ("product-plan", ".xcodeagent/plans/product-plan.json", ContractStage.REQUIREMENT_DESIGN),
    ("ui-design", ".xcodeagent/specs/ui-designs.json", ContractStage.REQUIREMENT_DESIGN),
    ("technical-plan", ".xcodeagent/plans/technical-plan.json", ContractStage.PLANNING_DESIGN),
)
_ID_KEYS = (
    "pageId", "page_id", "actionId", "action_id", "endpointId", "endpoint_id",
    "entityId", "entity_id", "moduleId", "module_id", "flowId", "flow_id", "id",
)
_LABEL_KEYS = (
    "name", "title", "label", "description", "goal", "summary", "behavior",
    "expectedResult", "expected_result", "path", "method", "usage", "responsibility",
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{1,80}")
_KEY_LABELS = {
    "pages": "页面",
    "actions": "操作",
    "feature_modules": "功能模块",
    "business_flows": "业务流程",
    "entities": "实体",
    "api_contracts": "接口契约",
    "endpoints": "接口端点",
    "schemas": "数据结构",
    "roles": "角色",
    "user_roles": "用户角色",
    "acceptance_criteria": "验收标准",
    "architecture": "架构",
    "data_sources": "数据源",
}

# 用于判断“缺失的产物是否可能承载本次请求涉及的语义”。这不是路由规则，
# 只决定是否需要把覆盖不足降级为 unknown；真正的分支仍由证据驱动。
_ARTIFACT_RELEVANCE_HINTS: dict[str, tuple[str, ...]] = {
    "requirement-spec": (
        "需求", "功能", "能力", "页面", "流程", "角色", "权限", "业务",
        "行为", "操作", "验收", "删除", "移除", "下线", "新增", "添加", "迁移",
        "requirement", "feature", "page", "flow", "role", "permission",
    ),
    "product-plan": (
        "产品", "功能", "能力", "页面", "流程", "角色", "权限", "业务",
        "行为", "操作", "验收", "删除", "移除", "新增", "添加", "迁移",
        "product", "feature", "page", "flow", "behavior",
    ),
    "ui-design": (
        "页面", "界面", "视觉", "样式", "布局", "颜色", "尺寸", "交互",
        "按钮", "组件", "ui", "ux", "css", "style", "layout", "visual",
        "button", "component", "screen",
    ),
    "technical-plan": (
        "接口", "api", "endpoint", "响应", "请求", "字段", "数据", "数据库",
        "数据源", "架构", "模块", "服务", "组件", "实体", "模型", "实现",
        "代码", "依赖", "技术", "后端", "前端", "source", "database", "schema",
        "architecture", "module", "service", "entity", "implementation", "code",
    ),
}


@dataclass(frozen=True)
class ContractFactRecord:
    """索引中的一条 JSON 事实及其可复核定位。"""

    artifact_key: str
    relative_path: str
    json_pointer: str
    selector: dict[str, str]
    artifact_sha256: str
    contract_stage: ContractStage
    title: str
    existing_fact: str
    value: Any

    def reference(self) -> dict[str, Any]:
        """返回不含全局 ID 的当前请求范围事实引用。"""

        return {
            "artifactKey": self.artifact_key,
            "jsonPointer": self.json_pointer,
            "selector": dict(self.selector),
            "artifactSha256": self.artifact_sha256,
            "contractStage": self.contract_stage.value,
            "existingFact": self.existing_fact,
        }


@dataclass(frozen=True)
class ContractArtifactRecord:
    """保存一个已确认 JSON 产物的原始内容和哈希。"""

    artifact_key: str
    relative_path: str
    path: Path
    contract_stage: ContractStage
    artifact_sha256: str
    document: dict[str, Any]


@dataclass(frozen=True)
class ContractCorpus:
    """当前工作区的已确认 JSON 事实集合。"""

    workspace: Path
    artifacts: dict[str, ContractArtifactRecord]
    facts: tuple[ContractFactRecord, ...]
    unavailable_artifacts: tuple[str, ...]
    # UI 设计可以由用户明确跳过。跳过不是一份可供 Analyzer 引用的契约，
    # 但也不等同于文件缺失；单纯实现修复不应因此被 coverage guard 阻断。
    skipped_artifacts: tuple[str, ...] = ()

    @property
    def has_confirmed_artifacts(self) -> bool:
        """判断当前工作区是否至少有一份可作为证据的已确认 JSON。"""

        return bool(self.artifacts)

    @property
    def coverage_complete(self) -> bool:
        """判断四份当前权威产物是否都存在且已确认。"""

        return not self.unavailable_artifacts and not self.skipped_artifacts

    def relevant_skipped_artifacts(
        self,
        request: str,
        *,
        target: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        """只在请求确实要求 UI 契约时报告被明确跳过的 UI 产物。"""

        if "ui-design" not in self.skipped_artifacts:
            return ()
        text = str(request or "").casefold()
        # 页面目标本身不能证明用户要求改变视觉契约；按钮无响应等实现
        # 修复应继续使用 Requirement/Product/Technical JSON 作为证据。只有
        # 明确的视觉/布局/主题语义需要 UI 契约时，跳过状态才构成覆盖缺口。
        visual_hints = (
            "视觉", "样式", "布局", "颜色", "尺寸", "间距", "主题", "响应式",
            "外观", "ui", "ux", "css", "style", "layout", "visual", "theme",
            "responsive", "spacing", "appearance",
        )
        if any(hint in text for hint in visual_hints):
            return ("ui-design",)
        return ()

    def search(
        self,
        queries: Sequence[str] | str,
        *,
        stages: Sequence[ContractStage | str] | None = None,
        top_k: int = 20,
    ) -> list[ContractFactRecord]:
        """按关键词检索事实，返回带完整定位的候选，不读取任何 Markdown。"""

        raw_queries = [queries] if isinstance(queries, str) else list(queries)
        terms = _search_terms(raw_queries)
        if not terms:
            return []
        allowed = {
            stage.value if isinstance(stage, ContractStage) else str(stage)
            for stage in (stages or [])
        }
        ranked: list[tuple[float, ContractFactRecord]] = []
        for fact in self.facts:
            if allowed and fact.contract_stage.value not in allowed:
                continue
            haystack = f"{fact.artifact_key} {fact.title} {fact.existing_fact}".casefold()
            score = 0.0
            for term in terms:
                folded = term.casefold()
                if folded in haystack:
                    score += 2.0 if len(folded) > 1 else 0.5
                if folded and folded == fact.artifact_key.casefold():
                    score += 1.0
            if score:
                ranked.append((score, fact))
        ranked.sort(key=lambda item: (-item[0], item[1].artifact_key, item[1].json_pointer))
        return [fact for _, fact in ranked[: max(1, min(int(top_k), 100))]]

    def read(self, references: Iterable[dict[str, Any] | ContractFactRecord]) -> list[ContractFactRecord]:
        """按 artifact key、JSON Pointer 和哈希精确读取已索引事实。"""

        by_key = {(fact.artifact_key, fact.json_pointer): fact for fact in self.facts}
        result: list[ContractFactRecord] = []
        for reference in references:
            if isinstance(reference, ContractFactRecord):
                candidate = reference
            elif isinstance(reference, dict):
                key = str(reference.get("artifactKey") or reference.get("artifact_key") or "")
                pointer = str(reference.get("jsonPointer") or reference.get("json_pointer") or "")
                candidate = by_key.get((key, pointer))
                if candidate is None:
                    continue
                expected_hash = str(reference.get("artifactSha256") or reference.get("artifact_sha256") or "")
                if expected_hash and expected_hash != candidate.artifact_sha256:
                    continue
            else:
                continue
            if candidate not in result:
                result.append(candidate)
        return result

    def prompt_context(self, facts: Sequence[ContractFactRecord] | None = None, *, limit: int = 80) -> list[dict[str, Any]]:
        """生成只包含 JSON 事实定位的有界模型上下文。"""

        selected = list(facts if facts is not None else self.facts)[: max(1, min(limit, 120))]
        return [
            {
                **fact.reference(),
                "title": fact.title,
                "relativePath": fact.relative_path,
            }
            for fact in selected
        ]

    def relevant_unavailable_artifacts(
        self,
        request: str,
        *,
        target: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        """返回可能承载本次语义、但当前没有确认 JSON 的产物。"""

        if not self.unavailable_artifacts:
            return ()
        text = str(request or "").casefold()
        target_type = str((target or {}).get("type") or "").casefold()
        relevant: set[str] = set()
        # 目标类型是服务端校验过的上下文，比自然语言关键词更可靠。
        if target_type == "page":
            relevant.update({"requirement-spec", "product-plan", "ui-design"})
        elif target_type == "endpoint":
            relevant.update({"requirement-spec", "product-plan", "technical-plan"})
        elif target_type == "application":
            relevant.update(_ARTIFACT_RELEVANCE_HINTS)
        for artifact_key, hints in _ARTIFACT_RELEVANCE_HINTS.items():
            if any(hint.casefold() in text for hint in hints):
                relevant.add(artifact_key)
        return tuple(
            key for key, _path, _stage in _ARTIFACTS
            if key in relevant and key in self.unavailable_artifacts
        )


def load_confirmed_contract_corpus(workspace: str | Path) -> ContractCorpus:
    """只加载四个权威 JSON 文件中 confirmation_status=confirmed 的内容。"""

    root = Path(workspace).expanduser().resolve()
    artifacts: dict[str, ContractArtifactRecord] = {}
    facts: list[ContractFactRecord] = []
    unavailable: list[str] = []
    skipped: list[str] = []
    for artifact_key, relative_path, stage in _ARTIFACTS:
        path = root / relative_path
        try:
            resolved_path = path.resolve()
            resolved_path.relative_to(root)
        except (OSError, ValueError):
            unavailable.append(artifact_key)
            continue
        if path.is_symlink() or not resolved_path.is_file():
            unavailable.append(artifact_key)
            continue
        try:
            raw = resolved_path.read_bytes()
            document = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            unavailable.append(artifact_key)
            continue
        if not isinstance(document, dict):
            unavailable.append(artifact_key)
            continue
        confirmation_status = _confirmation_status(document)
        if confirmation_status == "skipped":
            # 明确跳过的 UI 不提供 ContractFactRecord；保留单独状态，供
            # 覆盖守卫区分“没有 UI 契约”和“JSON 文件损坏/缺失”。
            skipped.append(artifact_key)
            continue
        if confirmation_status != "confirmed":
            unavailable.append(artifact_key)
            continue
        digest = hashlib.sha256(raw).hexdigest()
        artifact = ContractArtifactRecord(
            artifact_key=artifact_key,
            relative_path=relative_path,
            path=resolved_path,
            contract_stage=stage,
            artifact_sha256=digest,
            document=document,
        )
        artifacts[artifact_key] = artifact
        facts.extend(_flatten_facts(artifact))
    return ContractCorpus(
        root,
        artifacts,
        tuple(facts),
        tuple(unavailable),
        tuple(skipped),
    )


def load_confirmed_json_contracts(workspace: str | Path) -> ContractCorpus:
    """使用更直观的名称读取当前已确认 JSON 契约（不读取 Markdown）。"""

    return load_confirmed_contract_corpus(workspace)


def contract_search(
    workspace: str | Path,
    queries: Sequence[str] | str,
    *,
    stages: Sequence[ContractStage | str] | None = None,
    top_k: int = 20,
) -> list[dict[str, Any]]:
    """提供给 Analyzer/测试使用的批量 contract.search 只读适配器。"""

    corpus = load_confirmed_contract_corpus(workspace)
    return [fact.reference() | {"title": fact.title} for fact in corpus.search(queries, stages=stages, top_k=top_k)]


def contract_read(workspace: str | Path, references: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """提供给 Analyzer/测试使用的批量 contract.read 只读适配器。"""

    corpus = load_confirmed_contract_corpus(workspace)
    return [fact.reference() | {"title": fact.title} for fact in corpus.read(references)]


def _is_confirmed(document: dict[str, Any]) -> bool:
    """严格判断 JSON 产物是否为当前已确认版本。"""

    return _confirmation_status(document) == "confirmed"


def _confirmation_status(document: dict[str, Any]) -> str:
    """读取当前正式 JSON 的确认状态，不把 skipped 当成已确认契约。"""

    return str(document.get("confirmation_status") or "").strip().casefold()


def _flatten_facts(artifact: ContractArtifactRecord) -> list[ContractFactRecord]:
    """把 JSON 中有业务语义的对象和标量列表展开为可检索事实。"""

    result: list[ContractFactRecord] = []

    def visit(value: Any, pointer: str, parent_key: str = "") -> None:
        """递归遍历 JSON，并为对象节点生成一条紧凑事实。"""

        if isinstance(value, dict):
            selector = _selector(value)
            text = _fact_text(value, parent_key=parent_key)
            if text and (selector or _has_semantic_key(value)):
                title = _title(value, parent_key)
                result.append(
                    ContractFactRecord(
                        artifact.artifact_key,
                        artifact.relative_path,
                        pointer or "/",
                        selector,
                        artifact.artifact_sha256,
                        artifact.contract_stage,
                        title,
                        text,
                        value,
                    )
                )
            for key, child in value.items():
                child_pointer = f"{pointer}/{_escape_pointer(str(key))}" if pointer else f"/{_escape_pointer(str(key))}"
                visit(child, child_pointer, str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_pointer = f"{pointer}/{index}" if pointer else f"/{index}"
                visit(child, child_pointer, parent_key)
        elif isinstance(value, (str, int, float, bool)) and str(value).strip():
            if parent_key in {"confirmation_status", "generated_at", "version", "status"}:
                return
            label = _KEY_LABELS.get(parent_key, parent_key or "事实")
            result.append(
                ContractFactRecord(
                    artifact.artifact_key,
                    artifact.relative_path,
                    pointer or "/",
                    {},
                    artifact.artifact_sha256,
                    artifact.contract_stage,
                    label,
                    f"{label}：{value}",
                    value,
                )
            )

    visit(artifact.document, "")
    # 同一 pointer 的对象/叶子重复度很高，保留对象摘要优先，避免上下文膨胀。
    deduped: list[ContractFactRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for fact in result:
        identity = (fact.artifact_key, fact.json_pointer, fact.existing_fact)
        if identity not in seen:
            seen.add(identity)
            deduped.append(fact)
    return deduped


def _selector(value: dict[str, Any]) -> dict[str, str]:
    """提取页面、操作、接口和实体等局部选择器。"""

    return {
        key: str(value[key]).strip()
        for key in _ID_KEYS
        if key in value and str(value[key]).strip()
    }


def _has_semantic_key(value: dict[str, Any]) -> bool:
    """判断对象是否包含可供 Analyzer 比较的语义字段。"""

    return any(key in value for key in (*_LABEL_KEYS, *_ID_KEYS))


def _title(value: dict[str, Any], parent_key: str) -> str:
    """为事实生成短标题，不引入额外稳定身份。"""

    for key in ("name", "title", "label", "pageId", "actionId", "endpointId", "id"):
        if str(value.get(key) or "").strip():
            return str(value[key]).strip()[:240]
    return _KEY_LABELS.get(parent_key, parent_key or "JSON 事实")[:240]


def _fact_text(value: dict[str, Any], *, parent_key: str) -> str:
    """把对象中的业务语义压缩成可检索中文文本。"""

    parts: list[str] = []
    prefix = _KEY_LABELS.get(parent_key, parent_key)
    if prefix:
        parts.append(prefix)
    for key, item in value.items():
        if key in {"confirmation_status", "generated_at", "version", "status"}:
            continue
        if isinstance(item, (str, int, float, bool)) and str(item).strip():
            label = _KEY_LABELS.get(key, key)
            parts.append(f"{label} {item}")
        elif isinstance(item, list) and item and all(isinstance(entry, (str, int, float, bool)) for entry in item[:20]):
            parts.append(f"{_KEY_LABELS.get(key, key)} {'、'.join(str(entry) for entry in item[:20])}")
        elif isinstance(item, dict) and key in {"behavior", "authentication", "architecture"}:
            nested = _fact_text(item, parent_key=key)
            if nested:
                parts.append(nested)
    return "；".join(parts)[:4_000]


def _escape_pointer(value: str) -> str:
    """按 RFC 6901 转义 JSON Pointer 的键。"""

    return value.replace("~", "~0").replace("/", "~1")


def _search_terms(queries: Iterable[str]) -> list[str]:
    """从多条查询提取中英文关键词，并补充常见业务同义词。"""

    terms: list[str] = []
    aliases = {
        "详情": ("detail", "详情页"),
        "首页": ("home", "主页"),
        "登录": ("login", "认证"),
        "按钮": ("button", "onClick"),
        "接口": ("api", "endpoint"),
        "数据源": ("source", "database"),
    }
    for query in queries:
        text = str(query or "").strip()
        if not text:
            continue
        terms.append(text)
        for run in _CJK_RE.findall(text):
            terms.append(run)
            # 两字以上的中文连续词拆成双字片段，覆盖“详情页/登录按钮”等组合表达。
            if len(run) > 2:
                terms.extend(run[index : index + 2] for index in range(len(run) - 1))
            for key, values in aliases.items():
                if key in run:
                    terms.extend(values)
        terms.extend(_WORD_RE.findall(text))
    return list(dict.fromkeys(term for term in terms if term))[:120]
