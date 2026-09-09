"""以 Engine 原始顺序应用三类文件操作，并提供受限 Git 回退。"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path, PurePosixPath

from app.services.template_reconcile.models import ChangeSetBody
from app.services.template_reconcile.runtime_state import ReconcileAttempt, save_reconcile_attempt
from app.services.workspace_bootstrap.git_manager import BootstrapGitError
from app.services.workspace_process_registry import workspace_process_registry


class TemplateApplyError(ValueError):
    """表示模板操作无法安全应用或回退。"""

    code = "TEMPLATE_APPLY_FAILED"


def git_head(workspace: str | Path) -> str:
    """读取 Reconcile 前可用于恢复的已验证 Git HEAD。"""

    return _git(workspace, ["rev-parse", "--verify", "HEAD"]).strip()


def assert_clean_worktree(workspace: str | Path) -> None:
    """要求用户先提交业务修改，避免 Git 回退覆盖未提交内容。"""

    if _git(workspace, ["status", "--porcelain"]).strip():
        raise TemplateApplyError("TEMPLATE_WORKTREE_DIRTY：请先提交或清理工作区修改后再补充模板能力。")


def apply_file_operations(
    workspace: str | Path,
    change_set: ChangeSetBody,
    attempt: ReconcileAttempt,
) -> ReconcileAttempt:
    """严格按 ChangeSet 顺序写入普通文件，并在每次 ADD 后持久化恢复信息。"""

    root = Path(workspace).expanduser().resolve()
    current = attempt
    for operation in change_set.operations:
        target = _target_path(root, operation.path)
        if operation.type == "DELETE_FILE":
            _require_regular_file(target, operation.path)
            target.unlink()
            continue
        if operation.type == "ADD_FILE":
            if target.exists() or target.is_symlink():
                raise TemplateApplyError(f"ADD_FILE 目标已存在：{operation.path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_new_file(target, operation.content)
            current = replace(current, added_paths=(*current.added_paths, operation.path))
            save_reconcile_attempt(root, current)
            continue
        _require_regular_file(target, operation.path)
        original_mode = target.stat().st_mode & 0o777
        _replace_text_file(target, operation.content, original_mode)
    return current


def rollback_workspace(workspace: str | Path, attempt: ReconcileAttempt) -> None:
    """回到 Attempt 记录的 HEAD，并仅删除本次明确记录的新增文件。"""

    root = Path(workspace).expanduser().resolve()
    _git(root, ["reset", "--hard", attempt.pre_reconcile_head])
    for raw_path in reversed(attempt.added_paths):
        target = _target_path(root, raw_path)
        if target.is_file() and not target.is_symlink():
            target.unlink()
    _remove_empty_parents(root, attempt.added_paths)


def _git(workspace: str | Path, arguments: list[str]) -> str:
    """经 Workspace 进程登记运行 Git，并收敛为模板事务错误。"""

    root = Path(workspace).expanduser().resolve()
    try:
        result = workspace_process_registry.run(
            ["git", *arguments], workspace=root, cwd=str(root), capture_output=True,
            text=True, timeout=30, check=False,
        )
    except Exception as exc:
        raise TemplateApplyError("执行 Template Reconcile Git 操作失败。") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise TemplateApplyError(f"Template Reconcile Git 操作失败：{detail[:512]}")
    return str(result.stdout or "")


def _target_path(root: Path, raw_path: str) -> Path:
    """把 Engine 相对 POSIX 路径解析到 Workspace，并拒绝越界和 .git。"""

    if not raw_path or "\\" in raw_path:
        raise TemplateApplyError("模板操作包含无效路径。")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.parts[0] in {".git", ".xcodeagent"}:
        raise TemplateApplyError("模板操作路径越出允许范围。")
    return root.joinpath(*path.parts)


def _require_regular_file(target: Path, raw_path: str) -> None:
    """确保 UPDATE/DELETE 不会跟随符号链接或作用于特殊文件。"""

    if not target.is_file() or target.is_symlink():
        raise TemplateApplyError(f"模板操作目标不是普通文件：{raw_path}")


def _write_new_file(path: Path, content: str) -> None:
    """创建 mode 0644 的 UTF-8 普通文件。"""

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _replace_text_file(path: Path, content: str, mode: int) -> None:
    """原子替换既有文本文件，并保留其原始权限位。"""

    temporary = path.with_name(f".{path.name}.reconcile.tmp")
    try:
        _write_new_file(temporary, content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_empty_parents(root: Path, paths: tuple[str, ...]) -> None:
    """删除 ADD 回退后留下的空父目录，但绝不删除 Workspace 根。"""

    for raw_path in paths:
        parent = _target_path(root, raw_path).parent
        while parent != root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
