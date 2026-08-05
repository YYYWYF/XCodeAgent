from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal


CodeGraphStatus = Literal[
    "ready",
    "cache_hit",
    "indexing",
    "stale",
    "failed",
    "unavailable",
    "skipped",
]


@dataclass(frozen=True)
class CodeGraphProgress:
    """描述一次代码图索引的安全进度快照。"""

    stage: str
    status: str
    message: str
    files_discovered: int = 0
    files_indexed: int = 0
    symbols_indexed: int = 0
    relations_indexed: int = 0
    cache_hit: bool = False

    def as_dict(self) -> dict[str, Any]:
        """把进度快照转换为 AG-UI 可序列化的结构。"""

        return {
            "stage": self.stage,
            "status": self.status,
            "message": self.message,
            "filesDiscovered": self.files_discovered,
            "filesIndexed": self.files_indexed,
            "symbolsIndexed": self.symbols_indexed,
            "relationsIndexed": self.relations_indexed,
            "cacheHit": self.cache_hit,
        }


@dataclass(frozen=True)
class CodeGraphIndexResult:
    """描述一次代码图校验、构建或降级结果。"""

    status: CodeGraphStatus
    provider: str = "code-review-graph"
    provider_version: str = ""
    build_type: str = ""
    workspace_revision: str = ""
    files_indexed: int = 0
    symbols_indexed: int = 0
    relations_indexed: int = 0
    languages: tuple[str, ...] = ()
    nodes_by_kind: tuple[dict[str, Any], ...] = ()
    relations_by_kind: tuple[dict[str, Any], ...] = ()
    sample_symbols: tuple[dict[str, Any], ...] = ()
    message: str = ""
    warnings: tuple[str, ...] = ()
    warning_count: int = 0
    duration_ms: int = 0
    cache_hit: bool = False
    manifest_fingerprint: str = ""
    files: tuple[str, ...] = ()
    progress: CodeGraphProgress | None = None

    @property
    def available(self) -> bool:
        """返回当前结果是否可以为 Agent 提供图查询。"""

        return self.status in {"ready", "cache_hit"}

    def as_dict(self) -> dict[str, Any]:
        """把索引结果转换为前端和工作流状态共用的安全结构。"""

        payload: dict[str, Any] = {
            "provider": self.provider,
            "providerVersion": self.provider_version,
            "status": self.status,
            "available": self.available,
            "buildType": self.build_type,
            "workspaceRevision": self.workspace_revision,
            "message": self.message,
            "warningCount": max(self.warning_count, len(self.warnings)),
            "warnings": list(self.warnings[:5]),
            "cacheHit": self.cache_hit,
            "progress": self.progress.as_dict() if self.progress else None,
        }
        # 未完成或降级结果不携带零值图统计，避免 UI 把文件搜索 fallback
        # 误显示成一张已经建立但恰好为空的代码图。
        if self.available:
            payload.update(
                {
                    "filesIndexed": self.files_indexed,
                    "symbolsIndexed": self.symbols_indexed,
                    "relationsIndexed": self.relations_indexed,
                    "languages": list(self.languages),
                    "nodesByKind": [dict(item) for item in self.nodes_by_kind[:12]],
                    "relationsByKind": [dict(item) for item in self.relations_by_kind[:12]],
                    "sampleSymbols": [dict(item) for item in self.sample_symbols[:8]],
                    "durationMs": self.duration_ms,
                }
            )
        return payload


@dataclass(frozen=True)
class CodeGraphQuery:
    """限制 Agent 可执行的代码图查询参数。"""

    operation: str
    query: str = ""
    paths: tuple[str, ...] = ()
    direction: str = "both"
    max_results: int = 20
    max_depth: int = 2


@dataclass
class CodeGraphQueryResult:
    """保存经过裁剪的图查询结果和稳定状态信息。"""

    status: str
    workspace_revision: str = ""
    operation: str = ""
    matches: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    related_tests: list[dict[str, Any]] = field(default_factory=list)
    impacted_files: list[str] = field(default_factory=list)
    truncated: bool = False
    message: str = ""
    fallback: str = ""

    def as_dict(self) -> dict[str, Any]:
        """把查询结果转换为模型可消费的 bounded JSON。"""

        payload: dict[str, Any] = {
            "schemaVersion": "xcodeagent.code_graph_context.v1",
            "status": self.status,
            "workspaceRevision": self.workspace_revision,
            "operation": self.operation,
            "matches": self.matches[:40],
            "relations": self.relations[:80],
            "relatedTests": self.related_tests[:20],
            "impactedFiles": self.impacted_files[:40],
            "truncated": self.truncated,
            "message": self.message[:1_000],
            "fallback": self.fallback,
        }
        # 代码图结果只用于导航，硬性限制传给 Agent 的 JSON 大小，避免图查询
        # 意外挤占后续源码读取和模型推理的上下文预算。
        while len(json.dumps(payload, ensure_ascii=False)) > 16_384:
            if payload["relations"]:
                payload["relations"] = payload["relations"][:-10]
            elif payload["matches"]:
                payload["matches"] = payload["matches"][:-5]
            elif payload["relatedTests"]:
                payload["relatedTests"] = payload["relatedTests"][:-5]
            elif payload["impactedFiles"]:
                payload["impactedFiles"] = payload["impactedFiles"][:-5]
            else:
                payload["message"] = str(payload["message"])[:200]
                payload["truncated"] = True
                break
            payload["truncated"] = True
        return payload
