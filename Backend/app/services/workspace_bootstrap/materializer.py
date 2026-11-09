"""把已校验模板 ZIP 以可回滚事务物化到 Workspace。"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.template_reconcile.protocol_v3 import TemplateStateV3
from app.services.template_state import TEMPLATE_STATE_RELATIVE_PATH
from app.utils.atomic_json import atomic_write_json
from app.services.workspace_bootstrap.git_manager import BootstrapGitManager
from app.services.workspace_bootstrap.models import TemplatePackageError, WorkspaceBootstrapError
from app.services.workspace_bootstrap.run_log import TemplateRunLog
from app.services.authorization_projection_sources import SOURCE_RECEIPT

BOOTSTRAP_STAGING_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / "bootstrap-staging"
_ROOTS = ("frontend", "backend")
_PLATFORM_RESERVED_PATHS = frozenset({Path(".git"), BOOTSTRAP_STAGING_RELATIVE_PATH})

# Bootstrap 受管产物的唯一清单：受管根、基线仓库、模板状态与 staging 要么齐全、要么全无。
# `_preflight` 按它拒绝覆盖，发起新迭代与中断收尾按它退回未 Bootstrap 状态；
# 三处共用同一份清单，避免出现"删了状态却留着根目录"的半状态导致后续 Bootstrap 必然冲突。
BOOTSTRAP_MANAGED_RELATIVE_PATHS: tuple[Path, ...] = (
    Path(_ROOTS[0]),
    Path(_ROOTS[1]),
    Path(".git"),
    TEMPLATE_STATE_RELATIVE_PATH,
    SOURCE_RECEIPT,
    BOOTSTRAP_STAGING_RELATIVE_PATH,
)


def clear_bootstrap_managed_artifacts(workspace: str | Path) -> None:
    """把 Workspace 退回未 Bootstrap 状态，使下一次 Bootstrap 可以重新物化模板。

    发起新迭代要以全新模板重建应用代码，必须先走这一步：只清 `.devagentstudio` 规划产物
    会留下受管根目录，`_preflight` 随即以"已存在受管产物"拒绝 Bootstrap。
    """

    root = Path(workspace).expanduser().resolve(strict=False)
    for relative in BOOTSTRAP_MANAGED_RELATIVE_PATHS:
        _remove_managed_path(root / relative)



@dataclass
class BootstrapJournal:
    """仅在当前进程存活的 Bootstrap 操作中记录可逆写入。"""

    workspace: Path
    staging: Path | None = None
    moved_roots: list[Path] = field(default_factory=list)
    git_initialized: bool = False
    template_state_written: bool = False

    def rollback(self) -> None:
        """按受管范围逆序删除本次事务产物，保留生命周期和正式规划产物。"""

        errors: list[Exception] = []
        for root in reversed(self.moved_roots):
            try:
                _remove_managed_path(root)
            except Exception as exc:  # pragma: no cover - 由调用者的故障注入覆盖
                errors.append(exc)
        if self.template_state_written:
            try:
                _remove_managed_path(self.workspace / TEMPLATE_STATE_RELATIVE_PATH)
            except Exception as exc:  # pragma: no cover - 同上
                errors.append(exc)
        if self.git_initialized:
            try:
                _remove_managed_path(self.workspace / ".git")
            except Exception as exc:  # pragma: no cover - 同上
                errors.append(exc)
        if self.staging is not None:
            try:
                _remove_managed_path(self.staging)
            except Exception as exc:  # pragma: no cover - 同上
                errors.append(exc)
        if errors:
            raise WorkspaceBootstrapError("Bootstrap rollback 未能清理全部受管产物。") from errors[0]


class WorkspaceMaterializer:
    """执行 Preparation 与不可中断 Commit Section 的确定性文件事务。"""

    def __init__(self, *, git_manager: BootstrapGitManager | None = None) -> None:
        """允许测试替换 Git 管理器，同时保持生产默认实现。"""

        self._git_manager = git_manager or BootstrapGitManager()

    def materialize(
        self,
        *,
        workspace: str | Path,
        archive_path: str | Path,
        template_state: TemplateStateV3,
        before_baseline: Callable[[Path], None] | None = None,
        readiness: Callable[[Path], None] | None = None,
        run_log: TemplateRunLog | None = None,
    ) -> str:
        """完整提交 ZIP 的安全文件、Git baseline 与唯一 TemplateState。"""

        root = Path(workspace).expanduser().resolve()
        with _logged_stage(run_log, "materialization_preflight", "检查物化目标路径", workspacePath=str(root)):
            self._preflight(root)
        journal = BootstrapJournal(workspace=root)
        try:
            with _logged_stage(run_log, "zip_extraction", "解压模板 ZIP 到隔离 staging", archivePath=str(archive_path)):
                journal.staging = self._extract_to_staging(root, Path(archive_path))
                if run_log is not None:
                    # 计数只为日志服务，目录遍历故障不能触发物化回滚。
                    extracted_count = None
                    try:
                        extracted_count = sum(1 for path in journal.staging.rglob("*") if path.is_file())
                    except OSError as exc:
                        run_log.event(stage="zip_extraction", event="file_count_failed",
                                      message="无法统计解压文件数量", level="WARNING", exception=exc)
                    run_log.event(
                        stage="zip_extraction",
                        event="extraction_summary",
                        message="模板 ZIP 解压完成",
                        stagingPath=str(journal.staging),
                        extractedFileCount=extracted_count,
                    )
            with _logged_stage(run_log, "materialization_manifest", "生成模板文件校验清单"):
                # 来源摘要在隔离 ZIP 内容上生成，不能在首次投影时信任可编辑工程当前值。
                from app.services.authorization_projection_sources import record_materialized_authorization_sources
                record_materialized_authorization_sources(journal.staging, template_state)
                manifest = _build_file_manifest(journal.staging)
                targets = _materialization_targets(journal.staging)
                _validate_materialization_targets_available(root, targets)
                if run_log is not None:
                    run_log.event(
                        stage="materialization_manifest",
                        event="manifest_created",
                        message="模板物化目标已校验",
                        fileCount=len(manifest),
                        targetRoots=[path.as_posix() for path in targets],
                    )
            with _logged_stage(
                run_log,
                "template_file_materialization",
                "移动模板文件到目标目录",
                targetRoots=[path.as_posix() for path in targets],
            ):
                for relative_path in targets:
                    source = journal.staging / relative_path
                    target = root / relative_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, target)
                    journal.moved_roots.append(target)
            with _logged_stage(run_log, "materialized_file_verification", "核对物化文件摘要", fileCount=len(manifest)):
                _verify_materialized_files(root, manifest)
            if before_baseline is not None:
                with _logged_stage(run_log, "configuration_revision_check", "核对物化期间的应用配置版本"):
                    before_baseline(root)
            with _logged_stage(run_log, "template_state_commit", "提交模板状态文件", statePath=str(root / TEMPLATE_STATE_RELATIVE_PATH)):
                journal.template_state_written = True
                _write_template_state(root / TEMPLATE_STATE_RELATIVE_PATH, template_state)
            with _logged_stage(run_log, "git_baseline", "初始化 Git 并提交模板基线"):
                journal.git_initialized = True
                baseline_commit = self._git_manager.initialize_baseline(root)
                if run_log is not None:
                    run_log.event(
                        stage="git_baseline",
                        event="baseline_committed",
                        message="Git 模板基线已提交",
                        commitSha=baseline_commit,
                    )
            with _logged_stage(run_log, "staging_cleanup", "清理隔离 staging 目录"):
                _remove_managed_path(journal.staging)
                _remove_empty_staging_parent(root)
            # Readiness 必须在 staging 已清除但仍可回滚的事务边界内执行。
            if readiness is not None:
                readiness(root)
            return str(root)
        except Exception as exc:
            try:
                if run_log is not None:
                    run_log.event(
                        stage=run_log.last_failed_stage,
                        event="rollback_started",
                        message="物化失败，开始回滚受管产物",
                        level="WARNING",
                        exception=exc,
                    )
                journal.rollback()
                _remove_empty_staging_parent(root)
                if run_log is not None:
                    run_log.event(
                        stage="materialization_rollback",
                        event="rollback_completed",
                        message="物化回滚完成",
                    )
            except Exception as rollback_exc:
                if run_log is not None:
                    run_log.event(
                        stage="materialization_rollback",
                        event="rollback_failed",
                        message="物化回滚未能清理全部受管产物",
                        level="ERROR",
                        exception=rollback_exc,
                    )
                if not isinstance(rollback_exc, WorkspaceBootstrapError):
                    raise
                # 原始提交失败才是调用方的主要错误；受管残留由 Attach 兜底收尾。
            raise

    def _preflight(self, workspace: Path) -> None:
        """拒绝覆盖已有模板 roots、仓库或唯一模板状态。"""

        if not workspace.is_dir() or workspace.is_symlink():
            raise WorkspaceBootstrapError("Workspace 必须是已存在且非符号链接的目录。")
        collisions = [
            name
            for name in ("frontend", "backend", ".git", str(TEMPLATE_STATE_RELATIVE_PATH))
            if (workspace / name).exists() or (workspace / name).is_symlink()
        ]
        if collisions:
            raise WorkspaceBootstrapError("Workspace 已存在 Bootstrap 受管产物：" + "、".join(collisions))

    def _extract_to_staging(self, workspace: Path, archive_path: Path) -> Path:
        """逐条写入已验证 ZIP，绝不使用 `extractall()`。"""

        staging = workspace / BOOTSTRAP_STAGING_RELATIVE_PATH / uuid.uuid4().hex
        staging.mkdir(parents=True, exist_ok=False)
        try:
            with zipfile.ZipFile(archive_path) as package:
                for entry in package.infolist():
                    if entry.is_dir() or entry.filename == str(TEMPLATE_STATE_RELATIVE_PATH):
                        continue
                    target = staging / entry.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(entry, "r") as source, target.open("xb") as destination:
                        shutil.copyfileobj(source, destination)
            if not all((staging / name).is_dir() for name in _ROOTS):
                raise TemplatePackageError("模板 ZIP 解压后缺少受管根目录。")
            return staging
        except Exception:
            _remove_managed_path(staging)
            raise


def _write_template_state(path: Path, template_state: TemplateStateV3) -> None:
    """以同目录原子替换落盘 Engine 原样输出的 TemplateState。"""

    atomic_write_json(path, template_state.model_dump(mode="json", exclude_none=True))


def _materialization_targets(staging: Path) -> list[Path]:
    """返回 ZIP 全部可提交顶层单元，且不覆盖平台专属路径。"""

    targets: list[Path] = []
    for child in sorted(staging.iterdir(), key=lambda item: item.name):
        if child.name == ".git":
            raise TemplatePackageError("模板 ZIP 不允许写入平台 Git 元数据。")
        if child.name != WORKSPACE_ARTIFACT_DIR.name:
            targets.append(Path(child.name))
            continue

        # `.devagentstudio` 同时承载平台数据和模板契约，只逐项提交其模板子项。
        for nested_child in sorted(child.iterdir(), key=lambda item: item.name):
            relative_path = WORKSPACE_ARTIFACT_DIR / nested_child.name
            if relative_path in _PLATFORM_RESERVED_PATHS:
                raise TemplatePackageError("模板 ZIP 不允许写入 Bootstrap staging。")
            targets.append(relative_path)
    return targets


def _build_file_manifest(staging: Path) -> dict[Path, str]:
    """为 staging 中每个 ZIP 文件记录 SHA-256，作为完整物化校验依据。"""

    manifest: dict[Path, str] = {}
    for path in sorted(staging.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative_path = path.relative_to(staging)
        manifest[relative_path] = _sha256_file(path)
    return manifest


def _validate_materialization_targets_available(workspace: Path, targets: list[Path]) -> None:
    """在任何移动前确认全部目标空闲，避免可预见的半提交。"""

    collisions = [
        relative_path.as_posix()
        for relative_path in targets
        if (workspace / relative_path).exists() or (workspace / relative_path).is_symlink()
    ]
    if collisions:
        raise WorkspaceBootstrapError("Workspace 已存在 ZIP 物化目标：" + "、".join(collisions))


def _verify_materialized_files(workspace: Path, manifest: dict[Path, str]) -> None:
    """确认 ZIP 的每个文件均按原相对路径、原字节内容提交到 Workspace。"""

    for relative_path, expected_sha256 in manifest.items():
        target = workspace / relative_path
        if not target.is_file() or target.is_symlink() or _sha256_file(target) != expected_sha256:
            raise WorkspaceBootstrapError(
                f"模板 ZIP 完整性校验失败：{relative_path.as_posix()} 未被完整物化。"
            )


def _sha256_file(path: Path) -> str:
    """计算单个普通文件的 SHA-256，避免把整个文件读入内存。"""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_managed_path(path: Path) -> None:
    """删除已解析到受管路径的单个文件、目录或符号链接。"""

    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, onexc=_clear_readonly_and_retry)


def _clear_readonly_and_retry(function: Callable[[str], None], path: str, error: BaseException) -> None:
    """Windows 删除 Git 只读对象失败时清除只读属性并重试原操作。"""

    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _remove_empty_staging_parent(workspace: Path) -> None:
    """在最后一个事务 staging 清除后移除空的 staging 根目录。"""

    parent = workspace / BOOTSTRAP_STAGING_RELATIVE_PATH
    try:
        parent.rmdir()
    except FileNotFoundError:
        return
    except OSError:
        return


def _logged_stage(run_log: TemplateRunLog | None, stage: str, message: str, **fields: object):
    """提供可选的阶段记录上下文，独立调用 Materializer 时不改变原行为。"""

    if run_log is None:
        return nullcontext()
    return run_log.stage(stage, message, **fields)
