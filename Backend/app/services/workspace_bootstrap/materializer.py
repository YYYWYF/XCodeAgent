"""把已校验模板 ZIP 以可回滚事务物化到 Workspace。"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.services.template_reconcile.protocol_v2 import TemplateStateV2
from app.services.template_state import TEMPLATE_STATE_RELATIVE_PATH
from app.utils.atomic_json import atomic_write_json
from app.services.workspace_bootstrap.fs import remove_managed_path
from app.services.workspace_bootstrap.git_manager import BootstrapGitManager
from app.services.workspace_bootstrap.models import TemplatePackageError, WorkspaceBootstrapError

BOOTSTRAP_STAGING_RELATIVE_PATH = Path(".xcodeagent/bootstrap-staging")
_BASE_ROOTS = ("frontend", "backend")
_PLATFORM_RESERVED_PATHS = frozenset({Path(".git"), BOOTSTRAP_STAGING_RELATIVE_PATH})

# Bootstrap 受管产物的唯一清单：受管根、基线仓库、模板状态与 staging 要么齐全、要么全无。
# `_preflight` 按它拒绝覆盖，发起新迭代与中断收尾按它退回未 Bootstrap 状态；
# 三处共用同一份清单，避免出现"删了状态却留着根目录"的半状态导致后续 Bootstrap 必然冲突。
BOOTSTRAP_MANAGED_RELATIVE_PATHS: tuple[Path, ...] = (
    *(Path(name) for name in _BASE_ROOTS),
    Path("agent-runtime"),
    Path(".git"),
    TEMPLATE_STATE_RELATIVE_PATH,
    BOOTSTRAP_STAGING_RELATIVE_PATH,
)


def clear_bootstrap_managed_artifacts(workspace: str | Path) -> None:
    """把 Workspace 退回未 Bootstrap 状态，使下一次 Bootstrap 可以重新物化模板。

    发起新迭代要以全新模板重建应用代码，必须先走这一步：只清 `.xcodeagent` 规划产物
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
        template_state: TemplateStateV2,
        managed_roots: tuple[str, ...] = _BASE_ROOTS,
        readiness: Callable[[Path], None] | None = None,
    ) -> str:
        """完整提交 ZIP 的安全文件、动态受管 roots、Git baseline 与唯一 TemplateState。"""

        root = Path(workspace).expanduser().resolve()
        self._preflight(root, managed_roots)
        journal = BootstrapJournal(workspace=root)
        try:
            journal.staging = self._extract_to_staging(
                root, Path(archive_path), managed_roots
            )
            manifest = _build_file_manifest(journal.staging)
            targets = _materialization_targets(journal.staging)
            _validate_materialization_targets_available(root, targets)
            for relative_path in targets:
                source = journal.staging / relative_path
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, target)
                journal.moved_roots.append(target)
            _verify_materialized_files(root, manifest)
            journal.template_state_written = True
            _write_template_state(root / TEMPLATE_STATE_RELATIVE_PATH, template_state)
            journal.git_initialized = True
            self._git_manager.initialize_baseline(root, managed_roots=managed_roots)
            _remove_managed_path(journal.staging)
            _remove_empty_staging_parent(root)
            # Readiness 必须在 staging 已清除但仍可回滚的事务边界内执行。
            if readiness is not None:
                readiness(root)
            return str(root)
        except Exception:
            try:
                journal.rollback()
                _remove_empty_staging_parent(root)
            except WorkspaceBootstrapError:
                # 原始提交失败才是调用方的主要错误；残留由 Attach 兜底收尾。
                pass
            raise

    def _preflight(self, workspace: Path, managed_roots: tuple[str, ...]) -> None:
        """拒绝覆盖已有模板 roots、仓库或唯一模板状态。"""

        if not workspace.is_dir() or workspace.is_symlink():
            raise WorkspaceBootstrapError("Workspace 必须是已存在且非符号链接的目录。")
        collisions = [
            name
            for name in (*managed_roots, ".git", str(TEMPLATE_STATE_RELATIVE_PATH))
            if (workspace / name).exists() or (workspace / name).is_symlink()
        ]
        if collisions:
            raise WorkspaceBootstrapError("Workspace 已存在 Bootstrap 受管产物：" + "、".join(collisions))

    def _extract_to_staging(
        self,
        workspace: Path,
        archive_path: Path,
        managed_roots: tuple[str, ...],
    ) -> Path:
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
            if not all((staging / name).is_dir() for name in managed_roots):
                raise TemplatePackageError("模板 ZIP 解压后缺少受管根目录。")
            return staging
        except Exception:
            _remove_managed_path(staging)
            raise


def _write_template_state(path: Path, template_state: TemplateStateV2) -> None:
    """以同目录原子替换落盘 Engine 原样输出的 TemplateState。"""

    atomic_write_json(path, template_state.model_dump(mode="json"))


def _materialization_targets(staging: Path) -> list[Path]:
    """返回 ZIP 全部可提交顶层单元，且不覆盖平台专属路径。"""

    targets: list[Path] = []
    for child in sorted(staging.iterdir(), key=lambda item: item.name):
        if child.name == ".git":
            raise TemplatePackageError("模板 ZIP 不允许写入平台 Git 元数据。")
        if child.name != ".xcodeagent":
            targets.append(Path(child.name))
            continue

        # `.xcodeagent` 同时承载平台数据和模板契约，只逐项提交其模板子项。
        for nested_child in sorted(child.iterdir(), key=lambda item: item.name):
            relative_path = Path(".xcodeagent") / nested_child.name
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

    remove_managed_path(path)


def _remove_empty_staging_parent(workspace: Path) -> None:
    """在最后一个事务 staging 清除后移除空的 staging 根目录。"""

    parent = workspace / BOOTSTRAP_STAGING_RELATIVE_PATH
    try:
        parent.rmdir()
    except FileNotFoundError:
        return
    except OSError:
        return
