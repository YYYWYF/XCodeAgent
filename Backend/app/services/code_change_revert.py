"""代码变更集的安全 Git 撤销服务。"""

from __future__ import annotations

import difflib
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.workspace.workspace import _is_sensitive_path


class CodeChangeRevertError(ValueError):
    """表示代码变更无法安全撤销。"""


class CodeChangeRevertFile(BaseModel):
    """描述反向补丁所需的单次文件变化。"""

    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(min_length=1)
    change_type: Literal["added", "modified", "deleted"] = Field(alias="changeType")
    diff: str
    truncated: bool = False
    binary: bool = False


class CodeChangeRevertSet(BaseModel):
    """描述需要整体撤销的历史变更集。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1)
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)
    files: list[CodeChangeRevertFile] = Field(min_length=1)


class CodeChangeRevertRequest(BaseModel):
    """校验前端发起的代码变更撤销请求。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["revert"]
    confirmed: bool
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)
    change_set: CodeChangeRevertSet = Field(alias="changeSet")


class CodeChangeRevertResult(BaseModel):
    """返回一次成功撤销的稳定业务结果。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["revert"] = "revert"
    change_set_id: str = Field(alias="changeSetId")
    workspace_root: str = Field(alias="workspaceRoot")
    reverted_paths: list[str] = Field(alias="revertedPaths")
    reverted_at: str = Field(alias="revertedAt")


def revert_code_change_set(request: CodeChangeRevertRequest) -> CodeChangeRevertResult:
    """校验并通过 Git 原子化反向应用指定历史变更集。"""

    if not request.confirmed:
        raise CodeChangeRevertError("撤销代码变更前需要用户确认。")

    workspace_root = _resolve_workspace_root(request.workspace_root)
    change_workspace_root = _resolve_workspace_root(request.change_set.workspace_root)
    if workspace_root != change_workspace_root:
        raise CodeChangeRevertError("历史变更所属工作目录与当前工作目录不一致。")

    git_root = _resolve_git_root(workspace_root)
    normalized_files = [
        (_normalize_change_path(item.path), item) for item in request.change_set.files
    ]
    repository_paths = [
        _repository_relative_path(workspace_root, git_root, relative_path)
        for relative_path, _item in normalized_files
    ]
    _assert_no_staged_changes(git_root, repository_paths)

    for relative_path, item in normalized_files:
        _assert_revertible_file(item, workspace_root, relative_path)

    patch = _build_atomic_reverse_patch(
        git_root,
        normalized_files,
        repository_paths,
    )
    _apply_reverse_patch(git_root, patch, check_only=True)
    _apply_reverse_patch(git_root, patch, check_only=False)

    return CodeChangeRevertResult(
        changeSetId=request.change_set.id,
        workspaceRoot=str(workspace_root),
        revertedPaths=list(dict.fromkeys(path for path, _item in normalized_files)),
        revertedAt=datetime.now(timezone.utc).isoformat(),
    )


def _resolve_workspace_root(value: str) -> Path:
    """解析并校验撤销请求中的工作目录。"""

    root = Path(value).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise CodeChangeRevertError("工作目录不存在或不是文件夹。")
    return root


def _resolve_git_root(workspace_root: Path) -> Path:
    """确认 Git 可用并返回工作目录所属仓库根目录。"""

    if not shutil.which("git"):
        raise CodeChangeRevertError("未检测到 Git，无法撤销本次修改。")
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(workspace_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        raise CodeChangeRevertError("当前工作目录不是 Git 工程，无法撤销本次修改。")
    return Path(completed.stdout.strip()).resolve()


def _normalize_change_path(value: str) -> str:
    """把历史文件路径规范为不可越界的 POSIX 相对路径。"""

    normalized = value.replace("\\", "/").strip()
    if any(character in normalized for character in ("\x00", "\r", "\n")):
        raise CodeChangeRevertError(f"变更文件路径无效：{value}")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise CodeChangeRevertError(f"变更文件路径无效：{value}")
    if ".xcodeagent" in path.parts:
        raise CodeChangeRevertError("Agent 内部状态文件不能通过历史变更撤销。")
    return path.as_posix()


def _repository_relative_path(
    workspace_root: Path,
    git_root: Path,
    relative_path: str,
) -> str:
    """将工作区相对路径转换为仓库根目录相对路径并阻止越界。"""

    target = (workspace_root / relative_path).resolve(strict=False)
    try:
        target.relative_to(workspace_root)
        repository_path = target.relative_to(git_root)
    except ValueError as exc:
        raise CodeChangeRevertError(f"变更文件路径超出工作目录：{relative_path}") from exc
    return repository_path.as_posix()


def _assert_revertible_file(
    item: CodeChangeRevertFile,
    workspace_root: Path,
    relative_path: str,
) -> None:
    """拒绝无法由完整文本补丁安全还原的文件。"""

    if item.binary:
        raise CodeChangeRevertError(f"二进制文件无法安全撤销：{relative_path}")
    if item.truncated:
        raise CodeChangeRevertError(f"Diff 已截断，无法安全撤销：{relative_path}")
    if not item.diff.strip():
        raise CodeChangeRevertError(f"缺少可反向应用的 Diff：{relative_path}")
    if _is_sensitive_path(workspace_root / relative_path):
        raise CodeChangeRevertError(f"敏感文件不能通过历史变更撤销：{relative_path}")


def _assert_no_staged_changes(git_root: Path, repository_paths: list[str]) -> None:
    """拒绝撤销包含暂存区改动的目标文件，避免工作区与索引不一致。"""

    completed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--", *repository_paths],
        cwd=str(git_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0:
        raise CodeChangeRevertError(completed.stderr.strip() or "无法检查 Git 暂存区状态。")
    staged_paths = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if staged_paths:
        raise CodeChangeRevertError(
            f"目标文件存在已暂存修改，无法安全撤销：{', '.join(staged_paths)}"
        )


def _normalize_patch(item: CodeChangeRevertFile, repository_path: str) -> str:
    """重写补丁文件头，使新增和删除文件可被 Git 精确反向应用。"""

    lines = item.diff.splitlines(keepends=True)
    old_header_index = next(
        (index for index, line in enumerate(lines) if line.startswith("--- ")),
        -1,
    )
    new_header_index = next(
        (index for index, line in enumerate(lines) if line.startswith("+++ ")),
        -1,
    )
    if old_header_index < 0 or new_header_index < 0 or new_header_index <= old_header_index:
        raise CodeChangeRevertError(f"Diff 文件头无效：{item.path}")

    old_path = "/dev/null" if item.change_type == "added" else f"a/{repository_path}"
    new_path = "/dev/null" if item.change_type == "deleted" else f"b/{repository_path}"
    lines[old_header_index] = f"--- {old_path}\n"
    lines[new_header_index] = f"+++ {new_path}\n"
    patch = "".join(lines)
    return patch if patch.endswith("\n") else f"{patch}\n"


def _build_atomic_reverse_patch(
    git_root: Path,
    normalized_files: list[tuple[str, CodeChangeRevertFile]],
    repository_paths: list[str],
) -> str:
    """在临时目录倒序演算多段变化，并生成可一次性应用的聚合补丁。"""

    unique_paths = list(dict.fromkeys(repository_paths))
    with tempfile.TemporaryDirectory(prefix="xcodeagent-revert-") as temporary_directory:
        sandbox_root = Path(temporary_directory)
        for repository_path in unique_paths:
            source = git_root / repository_path
            destination = sandbox_root / repository_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.exists():
                if source.is_symlink() or not source.is_file():
                    raise CodeChangeRevertError(f"目标路径不是普通文件：{repository_path}")
                shutil.copy2(source, destination)

        changes = list(zip(normalized_files, repository_paths, strict=True))
        for (_relative_path, item), repository_path in reversed(changes):
            patch = _normalize_patch(item, repository_path)
            _apply_reverse_patch(sandbox_root, patch, check_only=True)
            _apply_reverse_patch(sandbox_root, patch, check_only=False)

        aggregate_parts = [
            _diff_sandbox_file(
                before_path=sandbox_root / repository_path,
                after_path=git_root / repository_path,
                repository_path=repository_path,
            )
            for repository_path in unique_paths
        ]
        aggregate_patch = "".join(aggregate_parts)
        if not aggregate_patch.strip():
            raise CodeChangeRevertError("当前工作区中没有可撤销的代码变化。")
        return aggregate_patch


def _diff_sandbox_file(
    *,
    before_path: Path,
    after_path: Path,
    repository_path: str,
) -> str:
    """生成临时还原状态到当前工作区状态的标准文本补丁。"""

    before_exists = before_path.is_file()
    after_exists = after_path.is_file()
    before_lines = (
        before_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if before_exists
        else []
    )
    after_lines = (
        after_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if after_exists
        else []
    )
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{repository_path}" if before_exists else "/dev/null",
            tofile=f"b/{repository_path}" if after_exists else "/dev/null",
        )
    )


def _apply_reverse_patch(git_root: Path, patch: str, *, check_only: bool) -> None:
    """先检查或实际执行整组 Git 反向补丁。"""

    args = ["git", "apply", "--reverse", "--whitespace=nowarn"]
    if check_only:
        args.append("--check")
    completed = subprocess.run(
        args,
        cwd=str(git_root),
        input=patch,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode == 0:
        return
    detail = completed.stderr.strip() or completed.stdout.strip()
    if check_only:
        raise CodeChangeRevertError(
            f"文件内容已发生变化，无法安全撤销；未修改任何文件。{f' {detail}' if detail else ''}"
        )
    raise CodeChangeRevertError(f"Git 撤销失败：{detail or '未知错误'}")
