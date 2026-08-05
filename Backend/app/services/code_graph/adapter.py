from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from uuid import uuid4

from app.services.code_graph.models import (
    CodeGraphIndexResult,
    CodeGraphProgress,
    CodeGraphQuery,
    CodeGraphQueryResult,
)


ProgressCallback = Callable[[CodeGraphProgress], None]


class CodeReviewGraphAdapter:
    """隔离 code-review-graph 核心 parser/store API 的适配器。"""

    def __init__(self) -> None:
        """初始化延迟导入的第三方适配器。"""

        self._imports: tuple[Any, Any, Any] | None = None

    def available(self) -> bool:
        """返回 CRG 核心包是否可以在当前 Python 环境中导入。"""

        try:
            self._load_imports()
        except Exception:
            return False
        return True

    def version(self) -> str:
        """读取已安装 CRG 版本，失败时返回空字符串。"""

        try:
            return importlib.metadata.version("code-review-graph")
        except importlib.metadata.PackageNotFoundError:
            return ""

    def build_full(
        self,
        workspace_root: Path,
        source_files: list[str],
        db_path: Path,
        *,
        callback: ProgressCallback | None = None,
    ) -> CodeGraphIndexResult:
        """按照 XCodeAgent 提供的文件清单执行一次全量解析。"""

        started = time.perf_counter()
        self._emit(
            callback,
            CodeGraphProgress(
                stage="parsing",
                status="running",
                message="正在扫描用户工作区代码，解析文件、符号和依赖关系…",
                files_discovered=len(source_files),
            ),
        )
        GraphStore, CodeParser, _ = self._load_imports()
        root = workspace_root.resolve()
        temporary_db = db_path.with_name(f".{db_path.name}.{uuid4().hex}.tmp")
        try:
            with GraphStore(temporary_db) as store:
                parser = CodeParser(root)
                files_indexed = 0
                symbols_indexed = 0
                relations_indexed = 0
                warnings: list[str] = []
                for relative in source_files:
                    if not self._safe_relative(relative):
                        warnings.append("ignored unsafe path")
                        continue
                    absolute = (root / relative).resolve()
                    if not self._is_inside(absolute, root) or not absolute.is_file():
                        continue
                    try:
                        raw = absolute.read_bytes()
                        nodes, edges = parser.parse_bytes(Path(relative), raw)
                        store.store_file_nodes_edges(
                            relative,
                            nodes,
                            edges,
                            hashlib.sha256(raw).hexdigest(),
                        )
                        files_indexed += 1
                        symbols_indexed += len(nodes)
                        relations_indexed += len(edges)
                    except (OSError, PermissionError) as exc:
                        warnings.append(self._warning(relative, type(exc).__name__))
                    except Exception as exc:  # pragma: no cover - parser-specific failures
                        warnings.append(
                            self._warning(relative, f"parser error ({type(exc).__name__})")
                        )
                    if files_indexed % 50 == 0 or files_indexed == len(source_files):
                        self._emit(
                            callback,
                            CodeGraphProgress(
                                stage="parsing",
                                status="running",
                                message=(
                                    f"正在解析代码文件（{files_indexed}/"
                                    f"{len(source_files)}）…"
                                ),
                                files_discovered=len(source_files),
                                files_indexed=files_indexed,
                                symbols_indexed=symbols_indexed,
                                relations_indexed=relations_indexed,
                            ),
                        )
                store.set_metadata("last_build_type", "full")
                store.set_metadata("last_updated", str(int(time.time())))
                summary = self._graph_summary(store, root)
            # 只有完整 DB 成功关闭后才替换正式文件，避免中断留下半张图。
            temporary_db.replace(db_path)
        finally:
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{temporary_db}{suffix}")
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass
        duration_ms = int((time.perf_counter() - started) * 1_000)
        self._emit(
            callback,
            CodeGraphProgress(
                stage="linking",
                status="running",
                message="正在建立调用关系并定位相关测试…",
                files_discovered=len(source_files),
                files_indexed=files_indexed,
                symbols_indexed=summary["nodes"],
                relations_indexed=summary["edges"],
            ),
        )
        return CodeGraphIndexResult(
            status="ready",
            provider_version=self.version(),
            build_type="full",
            files_indexed=summary["files"],
            symbols_indexed=summary["nodes"],
            relations_indexed=summary["edges"],
            languages=tuple(summary["languages"]),
            nodes_by_kind=tuple(summary["nodes_by_kind"]),
            relations_by_kind=tuple(summary["relations_by_kind"]),
            sample_symbols=tuple(summary["sample_symbols"]),
            warnings=tuple(warnings[:5]),
            warning_count=len(warnings),
            duration_ms=duration_ms,
            message="代码扫描完成，已建立工作区代码索引。",
            files=tuple(source_files),
        )

    def update_incremental(
        self,
        workspace_root: Path,
        source_files: list[str],
        changed_files: list[str],
        db_path: Path,
        *,
        callback: ProgressCallback | None = None,
    ) -> CodeGraphIndexResult:
        """按照显式变更路径执行增量解析并更新同一个 SQLite 图。"""

        started = time.perf_counter()
        GraphStore, CodeParser, find_dependents = self._load_imports()
        root = workspace_root.resolve()
        allowed = {
            relative
            for relative in source_files
            if self._safe_relative(relative)
        }
        changed = {
            relative
            for relative in changed_files
            if self._safe_relative(relative)
        }
        current = set(allowed)
        parse_files = set(changed & current)
        deleted = changed - current
        self._emit(
            callback,
            CodeGraphProgress(
                stage="parsing",
                status="running",
                message="正在增量更新变更文件的代码索引…",
                files_discovered=len(source_files),
            ),
        )
        with GraphStore(db_path) as store:
            dependent_files: set[str] = set()
            for relative in sorted(changed):
                try:
                    dependent_files.update(
                        self._relative_path(path, root)
                        for path in find_dependents(store, relative, max_hops=2)
                    )
                except Exception:
                    continue
            parse_files.update(dependent_files & current)
            if deleted:
                for relative in sorted(deleted):
                    store.remove_file_data(relative)
            parser = CodeParser(root)
            warnings: list[str] = []
            files_updated = 0
            for relative in sorted(parse_files):
                absolute = (root / relative).resolve()
                if not self._is_inside(absolute, root) or not absolute.is_file():
                    continue
                try:
                    raw = absolute.read_bytes()
                    nodes, edges = parser.parse_bytes(Path(relative), raw)
                    store.store_file_nodes_edges(
                        relative,
                        nodes,
                        edges,
                        hashlib.sha256(raw).hexdigest(),
                    )
                    files_updated += 1
                except (OSError, PermissionError) as exc:
                    warnings.append(self._warning(relative, type(exc).__name__))
                except Exception as exc:  # pragma: no cover - parser-specific failures
                    warnings.append(
                        self._warning(relative, f"parser error ({type(exc).__name__})")
                    )
            store.set_metadata("last_build_type", "incremental")
            store.set_metadata("last_updated", str(int(time.time())))
            summary = self._graph_summary(store, root)
        duration_ms = int((time.perf_counter() - started) * 1_000)
        return CodeGraphIndexResult(
            status="ready",
            provider_version=self.version(),
            build_type="incremental",
            files_indexed=summary["files"],
            symbols_indexed=summary["nodes"],
            relations_indexed=summary["edges"],
            languages=tuple(summary["languages"]),
            nodes_by_kind=tuple(summary["nodes_by_kind"]),
            relations_by_kind=tuple(summary["relations_by_kind"]),
            sample_symbols=tuple(summary["sample_symbols"]),
            warnings=tuple(warnings[:5]),
            warning_count=len(warnings),
            duration_ms=duration_ms,
            message=(
                f"代码索引已增量更新，重新解析 {files_updated} 个文件。"
            ),
            files=tuple(source_files),
        )

    def query(
        self,
        workspace_root: Path,
        db_path: Path,
        request: CodeGraphQuery,
        *,
        workspace_revision: str,
    ) -> CodeGraphQueryResult:
        """从指定 workspaceRoot 的 SQLite 图执行受限查询。"""

        GraphStore, _, _ = self._load_imports()
        root = workspace_root.resolve()
        limit = max(1, min(int(request.max_results), 40))
        depth = max(0, min(int(request.max_depth), 2))
        with GraphStore(db_path) as store:
            if request.operation in {"search_symbols", "entrypoints"}:
                nodes = store.search_nodes(request.query.strip(), limit=limit)
                return CodeGraphQueryResult(
                    status="ready",
                    operation=request.operation,
                    workspace_revision=workspace_revision,
                    matches=[self._node_dict(node, root) for node in nodes],
                    truncated=len(nodes) >= limit,
                    message=f"找到 {len(nodes)} 个相关代码节点。",
                )
            if request.operation == "file_summary":
                relative = self._normalize_relative(
                    request.query or (request.paths or ("",))[0], root
                )
                absolute = (root / relative).resolve()
                if not relative or not self._is_inside(absolute, root):
                    return CodeGraphQueryResult(
                        status="ready",
                        operation=request.operation,
                        workspace_revision=workspace_revision,
                        message="文件路径不在当前 workspaceRoot 内。",
                    )
                nodes = store.get_nodes_by_file(relative)
                return CodeGraphQueryResult(
                    status="ready",
                    operation=request.operation,
                    workspace_revision=workspace_revision,
                    matches=[self._node_dict(node, root) for node in nodes[:limit]],
                    truncated=len(nodes) > limit,
                    message=f"文件 {relative} 包含 {len(nodes)} 个图节点。",
                )
            node = self._resolve_node(store, root, request.query, request.paths)
            if node is None:
                return CodeGraphQueryResult(
                    status="ready",
                    operation=request.operation,
                    workspace_revision=workspace_revision,
                    message="未找到匹配的代码节点。",
                )
            if request.operation in {"references", "impact"}:
                relations: list[dict[str, Any]] = []
                matches: list[dict[str, Any]] = []
                if request.operation == "references":
                    if request.direction in {"both", "incoming", "callers"}:
                        relations.extend(
                            self._edge_dict(edge, root)
                            for edge in store.get_edges_by_target(node.qualified_name)
                            if edge.kind in {"CALLS", "REFERENCES", "IMPORTS_FROM"}
                        )
                    if request.direction in {"both", "outgoing", "callees"}:
                        relations.extend(
                            self._edge_dict(edge, root)
                            for edge in store.get_edges_by_source(node.qualified_name)
                            if edge.kind in {"CALLS", "REFERENCES", "IMPORTS_FROM"}
                        )
                    relations = relations[: limit * 4]
                    return CodeGraphQueryResult(
                        status="ready",
                        operation=request.operation,
                        workspace_revision=workspace_revision,
                        matches=matches,
                        relations=relations,
                        truncated=len(relations) >= limit * 4,
                        message=f"找到 {len(relations)} 条相关关系。",
                    )
                impact = store.get_impact_radius(
                    [self._normalize_relative(node.file_path, root)],
                    max_depth=depth,
                    max_nodes=limit,
                )
                matches = [
                    self._node_dict(item, root)
                    for item in impact.get("impacted_nodes", [])
                ]
                return CodeGraphQueryResult(
                    status="ready",
                    operation=request.operation,
                    workspace_revision=workspace_revision,
                    matches=matches[:limit],
                    relations=[
                        self._edge_dict(item, root)
                        for item in impact.get("edges", [])[: limit * 4]
                    ],
                    impacted_files=[
                        self._relative_path(path, root)
                        for path in impact.get("impacted_files", [])[:limit]
                    ],
                    truncated=bool(impact.get("truncated")),
                    message="已计算当前节点的影响范围。",
                )
            if request.operation == "related_tests":
                tests = store.get_transitive_tests(node.qualified_name, max_depth=depth)
                return CodeGraphQueryResult(
                    status="ready",
                    operation=request.operation,
                    workspace_revision=workspace_revision,
                    related_tests=[
                        self._node_dict(item, root)
                        for item in tests[:limit]
                        if isinstance(item, dict)
                    ],
                    truncated=len(tests) > limit,
                    message=f"找到 {min(len(tests), limit)} 个相关测试。",
                )
        return CodeGraphQueryResult(
            status="ready",
            operation=request.operation,
            workspace_revision=workspace_revision,
            message="暂不支持该代码图查询。",
        )

    def stats(self, db_path: Path) -> dict[str, Any]:
        """读取 SQLite 图的统计信息。"""

        GraphStore, _, _ = self._load_imports()
        with GraphStore(db_path) as store:
            # 固定 cache 布局为 <workspace>/.xcodeagent/cache/code-graph/v1；
            # 这里只用于把样例节点转换为 workspace-relative path。
            workspace_root = db_path.parents[4] if len(db_path.parents) > 4 else db_path.parent
            return self._graph_summary(store, workspace_root)

    def _graph_summary(self, store: Any, workspace_root: Path) -> dict[str, Any]:
        """从 CRG 公共 API 提取有界统计和代表性符号。"""

        stats = store.get_stats()
        samples: list[dict[str, Any]] = []
        samples_per_file: dict[str, int] = {}
        try:
            candidates = store.get_nodes_by_size(min_lines=0, limit=64)
        except Exception:
            candidates = []
        for node in candidates:
            if str(self._value(node, "kind") or "").casefold() == "file":
                continue
            preview = self._node_dict(node, workspace_root)
            path = str(preview.get("path") or "")
            if not path or samples_per_file.get(path, 0) >= 2:
                continue
            samples.append(
                {
                    "name": preview.get("name", ""),
                    "kind": preview.get("kind", ""),
                    "language": preview.get("language", ""),
                    "path": path,
                    "lineStart": preview.get("lineStart", 0),
                    "lineEnd": preview.get("lineEnd", 0),
                }
            )
            samples_per_file[path] = samples_per_file.get(path, 0) + 1
            if len(samples) >= 8:
                break
        return {
            "files": max(0, int(stats.files_count)),
            "nodes": max(0, int(stats.total_nodes)),
            "edges": max(0, int(stats.total_edges)),
            "languages": sorted(str(item)[:40] for item in stats.languages),
            "nodes_by_kind": self._distributions(stats.nodes_by_kind),
            "relations_by_kind": self._distributions(stats.edges_by_kind),
            "sample_symbols": samples,
        }

    @staticmethod
    def _distributions(value: Any) -> list[dict[str, Any]]:
        """把 CRG 类型计数排序并裁剪为前端可展示的最多十二类。"""

        if not isinstance(value, dict):
            return []
        items = [
            {"kind": str(kind)[:80], "count": max(0, int(count))}
            for kind, count in value.items()
            if str(kind).strip()
        ]
        return sorted(items, key=lambda item: (-item["count"], item["kind"]))[:12]

    @staticmethod
    def _warning(path: str, detail: str) -> str:
        """生成不包含宿主机路径和异常正文的脱敏扫描 warning。"""

        safe_path = path.replace("\\", "/")[:200]
        return f"{safe_path}: {detail[:100]}"

    def _load_imports(self) -> tuple[Any, Any, Any]:
        """只导入 CRG 的 parser、GraphStore 和增量依赖查找函数。"""

        if self._imports is None:
            graph = importlib.import_module("code_review_graph.graph")
            parser = importlib.import_module("code_review_graph.parser")
            incremental = importlib.import_module("code_review_graph.incremental")
            self._imports = (
                graph.GraphStore,
                parser.CodeParser,
                incremental.find_dependents,
            )
        return self._imports

    @staticmethod
    def _emit(callback: ProgressCallback | None, progress: CodeGraphProgress) -> None:
        """安全发送一次进度，避免 UI 回调失败影响索引。"""

        if callback is None:
            return
        try:
            callback(progress)
        except Exception:
            return

    @staticmethod
    def _safe_relative(value: str) -> bool:
        """判断输入路径是否为不越界的 workspace-relative path。"""

        path = PurePosixPath(str(value).replace("\\", "/"))
        normalized = str(value).replace("\\", "/")
        return bool(value) and not path.is_absolute() and not (
            len(normalized) >= 2 and normalized[1] == ":"
        ) and ".." not in path.parts

    @classmethod
    def _normalize_relative(cls, value: str, root: Path | None = None) -> str:
        """把图中绝对路径或用户相对路径统一为 POSIX 相对路径。"""

        normalized = str(value or "").replace("\\", "/")
        if root is not None:
            try:
                normalized = str(Path(normalized).resolve().relative_to(root.resolve()))
            except (ValueError, OSError):
                pass
        normalized = normalized.lstrip("/")
        if not cls._safe_relative(normalized):
            return ""
        return PurePosixPath(normalized).as_posix()

    @classmethod
    def _relative_path(cls, value: str | Path, root: Path) -> str:
        """将内部绝对文件路径安全转换为相对路径。"""

        return cls._normalize_relative(str(value), root)

    @staticmethod
    def _is_inside(path: Path, root: Path) -> bool:
        """判断解析后的路径是否仍位于用户 workspaceRoot 内。"""

        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    def _resolve_node(
        self,
        store: Any,
        root: Path,
        query: str,
        paths: tuple[str, ...],
    ) -> Any | None:
        """按限定的 qualified name、文件路径或符号名称解析一个图节点。"""

        target = str(query or "").strip()
        node = store.get_node(target) if target else None
        if node is not None:
            return node
        safe_target = self._qualified_to_absolute(target, root)
        node = store.get_node(safe_target) if safe_target else None
        if node is not None:
            return node
        legacy_target = self._qualified_to_absolute(target, root, absolute=True)
        node = store.get_node(legacy_target) if legacy_target else None
        if node is not None:
            return node
        for path in paths or ((target,) if target else ()):
            relative = self._normalize_relative(path, root)
            if relative:
                nodes = store.get_nodes_by_file(relative)
                if not nodes:
                    # 兼容首版曾写入绝对路径的本地缓存，schema 升级后会逐步淘汰。
                    nodes = store.get_nodes_by_file(str((root / relative).resolve()))
                if nodes:
                    return nodes[0]
        candidates = store.search_nodes(target, limit=5) if target else []
        return candidates[0] if len(candidates) == 1 else None

    def _node_dict(self, node: Any, root: Path) -> dict[str, Any]:
        """把 CRG GraphNode 转换为脱敏的 Agent 结果。"""

        file_path = self._relative_path(self._value(node, "file_path"), root)
        kind = str(self._value(node, "kind") or "").strip().lower()
        return {
            "name": str(self._value(node, "name"))[:200],
            "qualifiedName": self._safe_qualified_name(
                self._value(node, "qualified_name"), root
            )[:400],
            "path": file_path,
            "kind": kind[:80],
            "language": str(self._value(node, "language") or "")[:40],
            "lineStart": max(0, int(self._value(node, "line_start") or 0)),
            "lineEnd": max(0, int(self._value(node, "line_end") or 0)),
            "isTest": bool(self._value(node, "is_test")),
        }

    def _edge_dict(self, edge: Any, root: Path) -> dict[str, Any]:
        """把 CRG GraphEdge 转换为脱敏的 Agent 结果。"""

        edge_kind = str(self._value(edge, "kind") or "").strip().lower()
        return {
            "type": edge_kind[:80],
            "from": self._safe_qualified_name(
                self._value(edge, "source_qualified") or self._value(edge, "source"),
                root,
            )[:400],
            "to": self._safe_qualified_name(
                self._value(edge, "target_qualified") or self._value(edge, "target"),
                root,
            )[:400],
            "path": self._relative_path(self._value(edge, "file_path"), root),
            "line": max(0, int(self._value(edge, "line") or 0)),
        }

    @staticmethod
    def _value(value: Any, key: str) -> Any:
        """同时读取 CRG dataclass 和 dict 结果，兼容不同查询 API。"""

        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    @classmethod
    def _safe_qualified_name(cls, value: Any, root: Path) -> str:
        """将 CRG 内部绝对 qualified name 脱敏为 workspace-relative 标识。"""

        raw = str(value or "")
        file_part, separator, symbol = raw.partition("::")
        relative = cls._relative_path(file_part, root)
        if not relative:
            return ""
        return f"{relative}{separator}{symbol}" if separator else relative

    @classmethod
    def _qualified_to_absolute(
        cls,
        value: str,
        root: Path,
        *,
        absolute: bool = False,
    ) -> str:
        """把 Agent 返回的相对 qualified name 转成 CRG 内部查询标识。"""

        file_part, separator, symbol = str(value or "").partition("::")
        relative = cls._normalize_relative(file_part, root)
        if not relative:
            return ""
        file_value = str((root / relative).resolve()) if absolute else relative
        return f"{file_value}{separator}{symbol}" if separator else file_value
