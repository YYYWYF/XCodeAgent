"""把已校验模板 ZIP 以可回滚事务物化到 Workspace。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.services.template_state import TEMPLATE_STATE_RELATIVE_PATH
from app.services.workspace_bootstrap.git_manager import BootstrapGitManager
from app.services.workspace_bootstrap.models import TemplatePackageError, WorkspaceBootstrapError

BOOTSTRAP_STAGING_RELATIVE_PATH = Path(".xcodeagent/bootstrap-staging")
_ROOTS = ("frontend", "backend")


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
        template_state: dict[str, object],
        readiness: Callable[[Path], None] | None = None,
    ) -> str:
        """在 staging 解压后提交两个根、Git baseline 与唯一 TemplateState。"""

        root = Path(workspace).expanduser().resolve()
        self._preflight(root)
        journal = BootstrapJournal(workspace=root)
        try:
            journal.staging = self._extract_to_staging(root, Path(archive_path))
            for name in _ROOTS:
                source = journal.staging / name
                target = root / name
                os.replace(source, target)
                journal.moved_roots.append(target)
            journal.git_initialized = True
            self._git_manager.initialize_baseline(root)
            _write_template_state(root / TEMPLATE_STATE_RELATIVE_PATH, template_state)
            journal.template_state_written = True
            if readiness is not None:
                readiness(root)
            _remove_managed_path(journal.staging)
            _remove_empty_staging_parent(root)
            return str(root)
        except Exception:
            try:
                journal.rollback()
                _remove_empty_staging_parent(root)
            except WorkspaceBootstrapError:
                # 原始提交失败才是调用方的主要错误；残留由 Attach 兜底收尾。
                pass
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


def _write_template_state(path: Path, template_state: dict[str, object]) -> None:
    """以同目录原子替换落盘 Engine 原样输出的 TemplateState。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(template_state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _remove_managed_path(path: Path) -> None:
    """删除已解析到受管路径的单个文件、目录或符号链接。"""

    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _remove_empty_staging_parent(workspace: Path) -> None:
    """在最后一个事务 staging 清除后移除空的 staging 根目录。"""

    parent = workspace / BOOTSTRAP_STAGING_RELATIVE_PATH
    try:
        parent.rmdir()
    except FileNotFoundError:
        return
    except OSError:
        return
