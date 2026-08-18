"""二次修改完成后的安全版本检查与 Git 提交服务。"""

from __future__ import annotations

import hashlib
import hmac
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.workspace.workspace import _is_sensitive_path


class VersionControlError(ValueError):
    """表示当前工作区不能安全完成版本控制动作。"""


class VersionControlFile(BaseModel):
    """描述 Git 工作区中的一个实际变更文件。"""

    model_config = ConfigDict(populate_by_name=True)

    path: str
    status: str
    index_status: str = Field(alias="indexStatus")
    worktree_status: str = Field(alias="worktreeStatus")
    staged: bool
    untracked: bool


class VersionControlSnapshot(BaseModel):
    """返回提交前重新读取的仓库事实和变更指纹。"""

    model_config = ConfigDict(populate_by_name=True)

    workspace_root: str = Field(alias="workspaceRoot")
    repository_root: str = Field(alias="repositoryRoot")
    branch: str
    head: str
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    dirty: bool
    has_staged_changes: bool = Field(alias="hasStagedChanges")
    files: list[VersionControlFile]
    requested_paths: list[str] = Field(alias="requestedPaths")
    eligible_paths: list[str] = Field(alias="eligiblePaths")
    unavailable_paths: list[str] = Field(alias="unavailablePaths")


class InspectVersionControlRequest(BaseModel):
    """校验一次只读 Git 状态检查请求。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["inspect"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)
    requested_paths: list[str] = Field(alias="requestedPaths", min_length=1, max_length=200)


class CommitVersionControlRequest(BaseModel):
    """校验一次需要用户明确确认的精确文件提交请求。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["commit"]
    confirmed: bool
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)
    requested_paths: list[str] = Field(alias="requestedPaths", min_length=1, max_length=200)
    selected_paths: list[str] = Field(alias="selectedPaths", min_length=1, max_length=200)
    expected_fingerprint: str = Field(
        alias="expectedFingerprint", pattern=r"^[a-f0-9]{64}$"
    )
    message: str = Field(min_length=1, max_length=200)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        """把提交信息限制为单行可审阅文本。"""

        normalized = value.strip()
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("提交信息必须是单行文本。")
        return normalized


class VersionControlCommitResult(BaseModel):
    """返回成功提交及提交后的剩余工作区状态。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["commit"] = "commit"
    workspace_root: str = Field(alias="workspaceRoot")
    repository_root: str = Field(alias="repositoryRoot")
    commit_sha: str = Field(alias="commitSha", min_length=7)
    message: str
    committed_paths: list[str] = Field(alias="committedPaths")
    remaining_dirty: bool = Field(alias="remainingDirty")
    snapshot: VersionControlSnapshot


def inspect_version_control(request: InspectVersionControlRequest) -> VersionControlSnapshot:
    """重新读取独立工作区仓库状态，并只返回本轮请求关联的变更文件。"""

    workspace_root = _resolve_workspace_root(request.workspace_root)
    repository_root = _resolve_independent_repository_root(workspace_root)
    requested_paths = _normalize_requested_paths(workspace_root, request.requested_paths)
    status_bytes, status_files = _read_status(repository_root)
    status_by_path = {item.path: item for item in status_files}
    eligible_paths = [path for path in requested_paths if path in status_by_path]
    unavailable_paths = [path for path in requested_paths if path not in status_by_path]
    head = _read_head(repository_root)
    branch = _read_branch(repository_root)
    fingerprint = _build_fingerprint(
        repository_root,
        workspace_root,
        head,
        status_bytes,
        status_files,
    )
    return VersionControlSnapshot(
        workspaceRoot=str(workspace_root),
        repositoryRoot=str(repository_root),
        branch=branch,
        head=head,
        fingerprint=fingerprint,
        dirty=bool(status_files),
        hasStagedChanges=any(item.staged for item in status_files),
        files=[status_by_path[path] for path in eligible_paths],
        requestedPaths=requested_paths,
        eligiblePaths=eligible_paths,
        unavailablePaths=unavailable_paths,
    )


def commit_version_control(
    request: CommitVersionControlRequest,
) -> VersionControlCommitResult:
    """在指纹和暂存区复验通过后，仅提交用户明确选择的文件。"""

    if not request.confirmed:
        raise VersionControlError("提交代码前需要用户明确确认。")

    inspect_request = InspectVersionControlRequest(
        action="inspect",
        workspaceRoot=request.workspace_root,
        requestedPaths=request.requested_paths,
    )
    snapshot = inspect_version_control(inspect_request)
    if not hmac.compare_digest(snapshot.fingerprint, request.expected_fingerprint):
        raise VersionControlError("工作区已发生变化，请重新审阅后再提交。")
    if snapshot.head == "UNBORN":
        raise VersionControlError("当前仓库还没有基线提交，不能作为二次修改直接提交。")
    if snapshot.has_staged_changes:
        raise VersionControlError("检测到已有暂存修改，请先处理暂存区后再提交。")

    workspace_root = Path(snapshot.workspace_root)
    repository_root = Path(snapshot.repository_root)
    selected_paths = _normalize_requested_paths(workspace_root, request.selected_paths)
    eligible_paths = set(snapshot.eligible_paths)
    if any(path not in eligible_paths for path in selected_paths):
        raise VersionControlError("所选文件已不属于当前可提交变更，请重新审阅。")

    _run_git_checked(
        repository_root,
        ["diff", "--check", "--", *selected_paths],
        "所选文件未通过空白错误检查",
    )
    _run_git_checked(
        repository_root,
        ["add", "--", *selected_paths],
        "无法暂存所选文件",
    )
    try:
        _run_git_checked(
            repository_root,
            ["diff", "--cached", "--check"],
            "暂存内容未通过提交前检查",
        )
        staged_paths = _read_staged_paths(repository_root)
        if set(staged_paths) != set(selected_paths):
            raise VersionControlError("暂存内容与所选文件不一致，已停止提交。")
        _run_git_checked(
            repository_root,
            ["commit", "-m", request.message],
            "Git 提交失败",
            timeout=60,
        )
    except Exception:
        _unstage_selected_paths(repository_root, selected_paths)
        raise

    commit_sha = _run_git_checked(
        repository_root,
        ["rev-parse", "HEAD"],
        "无法读取提交结果",
    ).strip()
    after_snapshot = inspect_version_control(inspect_request)
    return VersionControlCommitResult(
        workspaceRoot=str(workspace_root),
        repositoryRoot=str(repository_root),
        commitSha=commit_sha,
        message=request.message,
        committedPaths=selected_paths,
        remainingDirty=after_snapshot.dirty,
        snapshot=after_snapshot,
    )


def _resolve_workspace_root(value: str) -> Path:
    """解析并校验版本控制动作指定的工作区根目录。"""

    root = Path(value).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise VersionControlError("工作目录不存在或不是文件夹。")
    return root


def _resolve_independent_repository_root(workspace_root: Path) -> Path:
    """要求工作区本身就是 Git 根目录，避免误提交父目录仓库。"""

    if not shutil.which("git"):
        raise VersionControlError("未检测到 Git，无法检查版本状态。")
    completed = _run_git(
        workspace_root,
        ["rev-parse", "--show-toplevel"],
    )
    if completed.returncode != 0:
        raise VersionControlError("当前工作目录还不是 Git 仓库。")
    repository_root = Path(completed.stdout.strip()).resolve()
    if repository_root != workspace_root:
        raise VersionControlError("工作区必须使用自己的 Git 仓库，不能提交到父目录仓库。")
    return repository_root


def _normalize_requested_paths(workspace_root: Path, values: list[str]) -> list[str]:
    """规范化、去重并拒绝越界或敏感的提交候选路径。"""

    normalized_paths: list[str] = []
    for value in values:
        raw_value = value.replace("\\", "/").strip()
        raw_path = Path(raw_value).expanduser()
        if raw_path.is_absolute():
            try:
                raw_value = raw_path.resolve(strict=False).relative_to(workspace_root).as_posix()
            except ValueError as exc:
                raise VersionControlError(f"变更文件超出工作目录：{value}") from exc
        path = PurePosixPath(raw_value)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(character in raw_value for character in ("\x00", "\r", "\n"))
        ):
            raise VersionControlError(f"变更文件路径无效：{value}")
        normalized = path.as_posix()
        target = (workspace_root / normalized).resolve(strict=False)
        try:
            target.relative_to(workspace_root)
        except ValueError as exc:
            raise VersionControlError(f"变更文件超出工作目录：{value}") from exc
        if ".git" in path.parts or ".xcodeagent" in path.parts or _is_sensitive_path(target):
            raise VersionControlError(f"敏感或内部文件不能提交：{normalized}")
        if normalized not in normalized_paths:
            normalized_paths.append(normalized)
    if not normalized_paths:
        raise VersionControlError("至少需要一个变更文件。")
    return normalized_paths


def _read_status(repository_root: Path) -> tuple[bytes, list[VersionControlFile]]:
    """使用 NUL 分隔的 porcelain 输出读取全部暂存、未暂存和未跟踪文件。"""

    completed = subprocess.run(
        [
            "git",
            "-c",
            "core.quotepath=false",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        cwd=str(repository_root),
        capture_output=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise VersionControlError(f"无法读取 Git 状态：{detail or '未知错误'}")

    records = completed.stdout.split(b"\0")
    files: list[VersionControlFile] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        status_code = record[:2].decode("ascii", errors="replace")
        path = record[3:].decode("utf-8", errors="replace")
        index_status, worktree_status = status_code[0], status_code[1]
        files.append(
            VersionControlFile(
                path=path,
                status=status_code,
                indexStatus=index_status,
                worktreeStatus=worktree_status,
                staged=index_status not in {" ", "?"},
                untracked=status_code == "??",
            )
        )
        if index_status in {"R", "C"} and index < len(records):
            index += 1
    return completed.stdout, files


def _read_head(repository_root: Path) -> str:
    """读取当前 HEAD；未建立基线时返回稳定占位值。"""

    completed = _run_git(repository_root, ["rev-parse", "HEAD"])
    return completed.stdout.strip() if completed.returncode == 0 else "UNBORN"


def _read_branch(repository_root: Path) -> str:
    """读取当前分支名称，并为 detached HEAD 提供明确文案。"""

    completed = _run_git(repository_root, ["branch", "--show-current"])
    branch = completed.stdout.strip() if completed.returncode == 0 else ""
    return branch or "detached HEAD"


def _build_fingerprint(
    repository_root: Path,
    workspace_root: Path,
    head: str,
    status_bytes: bytes,
    status_files: list[VersionControlFile],
) -> str:
    """按工作区、HEAD、差异内容和未跟踪文件元数据生成并发复验指纹。"""

    digest = hashlib.sha256()
    digest.update(str(workspace_root).encode("utf-8"))
    digest.update(b"\0")
    digest.update(head.encode("ascii", errors="replace"))
    digest.update(b"\0")
    digest.update(status_bytes)
    for arguments in (["diff", "--binary"], ["diff", "--cached", "--binary"]):
        completed = subprocess.run(
            ["git", *arguments],
            cwd=str(repository_root),
            capture_output=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            raise VersionControlError("无法生成当前 Git 变更指纹。")
        digest.update(b"\0")
        digest.update(hashlib.sha256(completed.stdout).digest())
    for item in status_files:
        if not item.untracked:
            continue
        target = repository_root / item.path
        try:
            file_stat = target.stat()
        except OSError as exc:
            raise VersionControlError("未跟踪文件在状态检查期间发生变化，请重试。") from exc
        digest.update(b"\0")
        digest.update(item.path.encode("utf-8"))
        digest.update(f":{file_stat.st_size}:{file_stat.st_mtime_ns}".encode("ascii"))
    return digest.hexdigest()


def _read_staged_paths(repository_root: Path) -> list[str]:
    """读取提交前暂存区的精确文件集合。"""

    completed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        cwd=str(repository_root),
        capture_output=True,
        check=False,
        timeout=15,
    )
    if completed.returncode != 0:
        raise VersionControlError("无法复核暂存文件。")
    return [
        item.decode("utf-8", errors="replace")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def _unstage_selected_paths(repository_root: Path, selected_paths: list[str]) -> None:
    """提交失败时只还原本轮创建的暂存状态，不触碰工作区内容。"""

    subprocess.run(
        ["git", "restore", "--staged", "--", *selected_paths],
        cwd=str(repository_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )


def _run_git(
    repository_root: Path,
    arguments: list[str],
    *,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    """以固定参数和超时执行无 shell 的 Git 子命令。"""

    return subprocess.run(
        ["git", *arguments],
        cwd=str(repository_root),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _run_git_checked(
    repository_root: Path,
    arguments: list[str],
    error_prefix: str,
    *,
    timeout: int = 15,
) -> str:
    """执行必须成功的 Git 子命令，并把错误转换为稳定业务异常。"""

    completed = _run_git(repository_root, arguments, timeout=timeout)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise VersionControlError(f"{error_prefix}：{detail}")
    return completed.stdout
