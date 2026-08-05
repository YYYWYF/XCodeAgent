from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from pathlib import Path, PurePosixPath
from typing import Any

from app.services.code_graph.adapter import CodeReviewGraphAdapter, ProgressCallback
from app.services.code_graph.models import (
    CodeGraphIndexResult,
    CodeGraphProgress,
    CodeGraphQuery,
    CodeGraphQueryResult,
)
from app.workspace.spec_documents import REPOSITORY_ROOT


INDEX_SCHEMA_VERSION = "xcodeagent.code-graph.v1.1"
INDEX_CONFIG_VERSION = "source-inventory-v1"
DEFAULT_INDEX_TIMEOUT_SECONDS = 30.0
_INDEX_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="code-graph")


class CodeGraphManager:
    """按用户 workspaceRoot 管理 CRG 索引、缓存、并发和查询。"""

    def __init__(self, adapter: CodeReviewGraphAdapter | None = None) -> None:
        """创建一个可被正式流和快速流共享的代码图管理器。"""

        self.adapter = adapter or CodeReviewGraphAdapter()
        self._states: dict[str, tuple[threading.RLock, Future[CodeGraphIndexResult] | None]] = {}
        self._states_lock = threading.Lock()

    def available(self) -> bool:
        """返回 CRG 核心模块是否可用。"""

        try:
            return self.adapter.available()
        except Exception:
            return False

    def inspect(
        self,
        workspace_root: Path,
        files: list[str],
        *,
        revision: str,
        callback: ProgressCallback | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """实现 WorkspaceInspector 的 provider 接口并返回安全摘要。"""

        result = self.ensure_index(
            workspace_root,
            files,
            revision=revision,
            callback=callback,
            timeout_seconds=timeout_seconds,
        )
        return result.as_dict()

    def ensure_index(
        self,
        workspace_root: Path,
        source_files: list[str],
        *,
        revision: str,
        changed_files: list[str] | None = None,
        callback: ProgressCallback | None = None,
        timeout_seconds: float | None = None,
    ) -> CodeGraphIndexResult:
        """校验索引并按需要执行 cache hit、增量或全量构建。"""

        root = workspace_root.expanduser().resolve()
        if not root.is_dir():
            result = CodeGraphIndexResult(
                status="skipped",
                message="用户 workspaceRoot 不存在或不是目录。",
            )
            self._emit(callback, result)
            return result
        if not self._is_allowed_workspace_root(root):
            result = CodeGraphIndexResult(
                status="skipped",
                message="代码图只允许扫描显式用户 workspaceRoot，不扫描 XCodeAgent 工程目录。",
            )
            self._emit(callback, result)
            return result
        if not self.available():
            result = CodeGraphIndexResult(
                status="unavailable",
                provider_version=self.adapter.version(),
                message="code-review-graph 未安装，已回退到确定性文件扫描。",
            )
            self._emit(callback, result)
            return result
        files = self._safe_source_files(root, source_files)
        fingerprint = self._manifest_fingerprint(root, files)
        provider_version = self.adapter.version()
        index_dir = root / ".xcodeagent" / "cache" / "code-graph" / "v1"
        db_path = index_dir / "graph.sqlite3"
        metadata_path = index_dir / "index.json"
        metadata = self._read_metadata(metadata_path)
        if (
            db_path.is_file()
            and metadata.get("schemaVersion") == INDEX_SCHEMA_VERSION
            and metadata.get("configVersion") == INDEX_CONFIG_VERSION
            and metadata.get("providerVersion") == provider_version
            and metadata.get("workspaceRevision") == revision
            and metadata.get("manifestFingerprint") == fingerprint
            and metadata.get("status") in {"ready", "cache_hit"}
        ):
            try:
                stats = self.adapter.stats(db_path)
                result = CodeGraphIndexResult(
                    status="cache_hit",
                    provider_version=provider_version,
                    build_type="cache_hit",
                    workspace_revision=revision,
                    files_indexed=int(stats.get("files", 0)),
                    symbols_indexed=int(stats.get("nodes", 0)),
                    relations_indexed=int(stats.get("edges", 0)),
                    languages=tuple(str(item) for item in stats.get("languages", [])),
                    message="代码索引缓存已就绪，本次无需重新扫描。",
                    cache_hit=True,
                    manifest_fingerprint=fingerprint,
                    files=tuple(files),
                )
                self._emit(callback, result)
                return result
            except Exception:
                metadata = {}
        key = str(root)
        lock, future = self._state_for(key)
        with lock:
            if future is None or future.done():
                build_type = (
                    "full"
                    if (
                        not db_path.is_file()
                        or metadata.get("schemaVersion") != INDEX_SCHEMA_VERSION
                        or metadata.get("configVersion") != INDEX_CONFIG_VERSION
                        or metadata.get("providerVersion") != provider_version
                        or not metadata
                    )
                    else "incremental"
                )
                old_files = [str(item) for item in metadata.get("files", [])]
                future = _INDEX_EXECUTOR.submit(
                    self._build,
                    root,
                    files,
                    old_files,
                    revision,
                    fingerprint,
                    db_path,
                    metadata_path,
                    build_type,
                    changed_files,
                    callback,
                )
                self._set_future(key, future)
        wait_seconds = (
            _configured_timeout_seconds()
            if timeout_seconds is None
            else max(0.0, float(timeout_seconds))
        )
        try:
            result = future.result(timeout=wait_seconds)
            # 同一 workspace 的旧后台任务只能串行执行；如果调用方在旧任务
            # 运行期间看到了更新的清单，旧结果完成后立即排队一次新构建，避免
            # 把旧 revision 错误地作为当前任务的索引结果返回。
            if (
                result.workspace_revision != revision
                or result.manifest_fingerprint != fingerprint
            ):
                return self.ensure_index(
                    root,
                    files,
                    revision=revision,
                    changed_files=changed_files,
                    callback=callback,
                    timeout_seconds=0,
                )
            return result
        except TimeoutError:
            progress = CodeGraphProgress(
                stage="parsing",
                status="running",
                message="代码扫描耗时较长，索引将在后台继续构建；当前任务将使用文件搜索。",
                files_discovered=len(files),
            )
            self._emit(callback, progress)
            return CodeGraphIndexResult(
                status="indexing",
                provider_version=self.adapter.version(),
                build_type="background",
                workspace_revision=revision,
                message=progress.message,
                manifest_fingerprint=fingerprint,
                files=tuple(files),
                progress=progress,
            )
        except Exception as exc:
            self._mark_failed(metadata_path, str(exc))
            result = CodeGraphIndexResult(
                status="failed",
                provider_version=self.adapter.version(),
                workspace_revision=revision,
                message=f"代码图构建失败，已切换为文件搜索：{str(exc)[:300]}",
                manifest_fingerprint=fingerprint,
                files=tuple(files),
            )
            self._emit(callback, result)
            return result

    def query(
        self,
        workspace_root: Path,
        request: CodeGraphQuery,
    ) -> CodeGraphQueryResult:
        """从已就绪的用户工作区索引执行一次 bounded 查询。"""

        root = workspace_root.expanduser().resolve()
        if not root.is_dir():
            return CodeGraphQueryResult(
                status="skipped",
                operation=request.operation,
                message="用户 workspaceRoot 不存在或不是目录。",
                fallback="workspace_search",
            )
        if not self._is_allowed_workspace_root(root):
            return CodeGraphQueryResult(
                status="skipped",
                operation=request.operation,
                message="代码图不扫描 XCodeAgent 工程目录。",
                fallback="workspace_search",
            )
        metadata_path = root / ".xcodeagent" / "cache" / "code-graph" / "v1" / "index.json"
        db_path = metadata_path.parent / "graph.sqlite3"
        metadata = self._read_metadata(metadata_path)
        if not db_path.is_file() or metadata.get("status") != "ready":
            return CodeGraphQueryResult(
                status="unavailable",
                operation=request.operation,
                message="代码图当前不可用。",
                fallback="workspace_search",
            )
        try:
            return self.adapter.query(
                root,
                db_path,
                request,
                workspace_revision=str(metadata.get("workspaceRevision") or ""),
            )
        except Exception as exc:
            return CodeGraphQueryResult(
                status="failed",
                operation=request.operation,
                message=f"代码图查询失败，已回退到文件搜索：{type(exc).__name__}。",
                fallback="workspace_search",
            )

    def update_paths(
        self,
        workspace_root: Path,
        changed_files: list[str],
        *,
        callback: ProgressCallback | None = None,
    ) -> CodeGraphIndexResult:
        """根据 Agent 写入后的变更路径刷新用户 workspaceRoot 的索引。"""

        from app.services.workspace_inspector import (
            source_files_for_code_graph,
            workspace_inventory,
        )

        root = workspace_root.expanduser().resolve()
        if not root.is_dir() or not self._is_allowed_workspace_root(root):
            result = CodeGraphIndexResult(
                status="skipped",
                message="代码图只允许扫描显式用户 workspaceRoot，不扫描 XCodeAgent 工程目录。",
            )
            self._emit(callback, result)
            return result
        inventory, revision = workspace_inventory(root)
        files = source_files_for_code_graph(root, inventory)
        return self.ensure_index(
            root,
            files,
            revision=revision,
            changed_files=changed_files,
            callback=callback,
        )

    def _build(
        self,
        root: Path,
        files: list[str],
        old_files: list[str],
        revision: str,
        fingerprint: str,
        db_path: Path,
        metadata_path: Path,
        build_type: str,
        explicit_changed_files: list[str] | None,
        callback: ProgressCallback | None,
    ) -> CodeGraphIndexResult:
        """在线程池中构建索引并原子写入 XCodeAgent 元数据。"""

        index_dir = metadata_path.parent
        index_dir.mkdir(parents=True, exist_ok=True)
        self._emit(
            callback,
            CodeGraphProgress(
                stage="inventory",
                status="running",
                message="正在读取用户工作区文件清单…",
                files_discovered=len(files),
            ),
        )
        if build_type == "full":
            result = self.adapter.build_full(root, files, db_path, callback=callback)
        else:
            old_set = set(old_files)
            current_set = set(files)
            changed = (
                list(explicit_changed_files)
                if explicit_changed_files is not None
                else sorted(old_set.symmetric_difference(current_set))
            )
            changed.extend(sorted(old_set.symmetric_difference(current_set)))
            old_manifest = self._read_metadata(metadata_path).get("fileStats", {})
            for relative in sorted(current_set & old_set):
                path = root / relative
                try:
                    stat = path.stat()
                    current_stat = f"{stat.st_size}:{stat.st_mtime_ns}"
                    if old_manifest.get(relative) != current_stat:
                        changed.append(relative)
                except OSError:
                    changed.append(relative)
            result = self.adapter.update_incremental(
                root,
                files,
                sorted(set(changed)),
                db_path,
                callback=callback,
            )
        result = CodeGraphIndexResult(
            **{
                **result.__dict__,
                "workspace_revision": revision,
                "manifest_fingerprint": fingerprint,
                "files": tuple(files),
            }
        )
        self._write_metadata(
            metadata_path,
            {
                "schemaVersion": INDEX_SCHEMA_VERSION,
                "configVersion": INDEX_CONFIG_VERSION,
                "provider": result.provider,
                "providerVersion": result.provider_version,
                "workspaceRevision": revision,
                "manifestFingerprint": fingerprint,
                "status": "ready",
                "buildType": result.build_type,
                "files": files,
                "fileStats": self._file_stats(root, files),
                "filesIndexed": result.files_indexed,
                "symbolsIndexed": result.symbols_indexed,
                "relationsIndexed": result.relations_indexed,
                "warnings": list(result.warnings),
                "updatedAt": int(time.time()),
            },
        )
        self._emit(callback, result)
        return result

    def _state_for(self, key: str) -> tuple[threading.RLock, Future[CodeGraphIndexResult] | None]:
        """获取或创建一个 workspace 专属锁和后台任务槽位。"""

        with self._states_lock:
            state = self._states.get(key)
            if state is None:
                state = (threading.RLock(), None)
                self._states[key] = state
            return state

    def _set_future(self, key: str, future: Future[CodeGraphIndexResult]) -> None:
        """记录 workspace 当前唯一的索引后台任务。"""

        with self._states_lock:
            lock, _ = self._states[key]
            self._states[key] = (lock, future)

    @staticmethod
    def _read_metadata(path: Path) -> dict[str, Any]:
        """安全读取索引元数据，损坏时返回空对象。"""

        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _is_allowed_workspace_root(root: Path) -> bool:
        """拒绝把 Agent 自己的仓库或其子目录当作用户代码图工作区。"""

        try:
            root.relative_to(REPOSITORY_ROOT.resolve())
        except ValueError:
            return True
        return False

    @staticmethod
    def _safe_source_files(root: Path, source_files: list[str]) -> list[str]:
        """只保留当前工作区内存在的源码相对路径，防止清单越界。"""

        safe_files: set[str] = set()
        resolved_root = root.resolve()
        for item in source_files:
            normalized = str(item or "").replace("\\", "/")
            candidate = PurePosixPath(normalized)
            if (
                not normalized
                or candidate.is_absolute()
                or (len(normalized) >= 2 and normalized[1] == ":")
                or ".." in candidate.parts
            ):
                continue
            absolute = (root / Path(normalized)).resolve()
            try:
                absolute.relative_to(resolved_root)
            except ValueError:
                continue
            if absolute.is_file():
                safe_files.add(candidate.as_posix())
        return sorted(safe_files)

    @staticmethod
    def _write_metadata(path: Path, value: dict[str, Any]) -> None:
        """原子写入索引元数据，避免中途异常留下半份 JSON。"""

        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def _mark_failed(cls, path: Path, message: str) -> None:
        """把失败状态写入索引元数据，阻止后续查询误用旧图。"""

        previous = cls._read_metadata(path)
        try:
            cls._write_metadata(
                path,
                {
                    **previous,
                    "schemaVersion": INDEX_SCHEMA_VERSION,
                    "status": "failed",
                    "message": message[:500],
                    "updatedAt": int(time.time()),
                },
            )
        except OSError:
            # 元数据目录不可写时仍然保持工作流降级语义，不能让索引故障
            # 反向阻断代码生成或测试节点。
            return

    @staticmethod
    def _file_stats(root: Path, files: list[str]) -> dict[str, str]:
        """生成用于增量判断的文件大小和修改时间指纹。"""

        result: dict[str, str] = {}
        for relative in files:
            try:
                stat = (root / relative).stat()
                result[relative] = f"{stat.st_size}:{stat.st_mtime_ns}"
            except OSError:
                continue
        return result

    @classmethod
    def _manifest_fingerprint(cls, root: Path, files: list[str]) -> str:
        """为用户源码清单生成稳定且不暴露路径的短指纹。"""

        payload = json.dumps(cls._file_stats(root, files), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _emit(callback: ProgressCallback | None, value: CodeGraphProgress | CodeGraphIndexResult) -> None:
        """把索引进度安全转发给调用方。"""

        if callback is None:
            return
        try:
            if isinstance(value, CodeGraphIndexResult):
                callback(
                    CodeGraphProgress(
                        stage="ready" if value.available else value.status,
                        status="completed" if value.available else value.status,
                        message=value.message,
                        files_discovered=value.files_indexed,
                        files_indexed=value.files_indexed,
                        symbols_indexed=value.symbols_indexed,
                        relations_indexed=value.relations_indexed,
                        cache_hit=value.cache_hit,
                    )
                )
            else:
                callback(value)
        except Exception:
            return


_DEFAULT_MANAGER = CodeGraphManager()


def get_code_graph_manager() -> CodeGraphManager:
    """返回进程内共享的代码图管理器。"""

    return _DEFAULT_MANAGER


def _configured_timeout_seconds() -> float:
    """读取代码图前台等待上限，非法环境变量时回退到 30 秒。"""

    try:
        return max(0.0, float(os.getenv("XCODEAGENT_CODE_GRAPH_TIMEOUT_SECONDS", "30")))
    except (TypeError, ValueError):
        return DEFAULT_INDEX_TIMEOUT_SECONDS
