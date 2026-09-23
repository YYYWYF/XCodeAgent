"""应用代码在远端仓库中的分支：分支名校验、远端存在性检查与基线推送。

新建应用时用户填写分支名，应用代码按分支隔离在同一个远端仓库里。分支名随
`.devagentstudio/application.json` 持久化；本模块只做确定性的 Git 动作，不参与规划状态。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.git_branch import GitBranchError, validate_branch_name
from app.services.version_publish import build_authenticated_remote_url
from app.services.workspace_process_registry import workspace_process_registry

APPLICATION_CONFIG_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / "application.json"

# 分支推送发生在 Bootstrap 请求路径上，前端在等结果，超时要比版本发布（120s）短。
_BRANCH_PUSH_TIMEOUT_SECONDS = 30
_BRANCH_CHECK_TIMEOUT_SECONDS = 20


def read_workspace_repository_target(workspace_root: str | Path) -> tuple[str, str, bool]:
    """读取工作区配置中的代码提交目标：仓库地址、分支名、用户是否已确认覆盖。

    **分支名为空表示该应用未配置分支**（例如由「添加本地文件夹」接入的工作区），
    调用方应跳过远端分支动作，而不是当成错误。
    """

    application_path = Path(workspace_root).expanduser() / APPLICATION_CONFIG_RELATIVE_PATH
    try:
        application = json.loads(application_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", "", False
    if not isinstance(application, dict):
        return "", "", False

    raw_repo_url = application.get("repoUrl")
    repo_url = raw_repo_url.strip() if isinstance(raw_repo_url, str) else ""
    raw_branch = application.get("branchName")
    branch_name = raw_branch.strip() if isinstance(raw_branch, str) else ""
    confirmed = application.get("branchOverwriteConfirmed") is True
    return repo_url, branch_name, confirmed


def check_remote_branch(repo_url: str, branch_name: str) -> bool:
    """检查远端仓库是否已存在该分支。"""

    branch = validate_branch_name(branch_name)
    try:
        remote_url = build_authenticated_remote_url(repo_url)
    except Exception as exc:  # noqa: BLE001 - 凭证缺失等统一收敛为本模块错误
        raise GitBranchError(str(exc)) from exc
    # `git ls-remote` 不要求当前目录是仓库，但需要一个真实存在的 cwd。
    with tempfile.TemporaryDirectory(prefix="devagentstudio-branch-check-") as scratch:
        completed = _run_git(
            Path(scratch),
            ["ls-remote", "--heads", remote_url, f"refs/heads/{branch}"],
            timeout=_BRANCH_CHECK_TIMEOUT_SECONDS,
        )
    if completed.returncode != 0:
        detail = _failure_detail(completed)
        raise GitBranchError(f"无法读取远端分支列表：{detail}")
    return bool(completed.stdout.strip())


def push_baseline_to_branch(
    workspace_root: str | Path,
    repo_url: str,
    branch_name: str,
    *,
    allow_overwrite: bool,
) -> dict[str, Any]:
    """把工作区当前的 HEAD 推成远端分支，并把任何失败收敛为结果而不抛异常。

    这是 Bootstrap 链路里的**非致命**步骤：远端不可达、令牌失效或分支冲突都只报告，
    不影响应用创建与规划流程继续。

    `allow_overwrite` 为真（用户在新建应用时确认过覆盖）时才加 `--force`；否则用普通
    推送 —— 分支不存在时同样能建出来，分支已存在时会被 Git 拒绝，从而避免悄悄覆盖
    别处刚建出来的分支。
    """

    root = Path(workspace_root).expanduser().resolve()
    try:
        branch = validate_branch_name(branch_name)
    except GitBranchError as exc:
        return _branch_result(branch_name, "failed", "", str(exc))

    # 先把本地分支对上，再推送。
    # `git push HEAD:refs/heads/<branch>` 只在**远端**建分支，本地仍是 `git init` 留下的
    # main。而历史预览（rev-parse）与只读源码（git show）都按分支名在**本地**解析，
    # 于是初始分支永远查不到 —— 界面报"分支 dev 在仓库中不存在，无法预览"。
    local_error = _ensure_local_branch(root, branch)
    if local_error:
        return _branch_result(branch, "failed", "", local_error)

    try:
        remote_url = build_authenticated_remote_url(repo_url)
    except Exception as exc:  # noqa: BLE001 - 凭证缺失等都要收敛成结果
        return _branch_result(branch, "failed", "", str(exc))

    commit_sha = _read_head(root)
    arguments = ["push", remote_url, f"HEAD:refs/heads/{branch}"]
    if allow_overwrite:
        arguments.append("--force")
    completed = _run_git(root, arguments, timeout=_BRANCH_PUSH_TIMEOUT_SECONDS)
    if completed.returncode == 0:
        return _branch_result(branch, "pushed", commit_sha, "")

    detail = _failure_detail(completed)
    if not allow_overwrite and _is_rejected_push(detail):
        return _branch_result(
            branch,
            "skipped",
            commit_sha,
            f"远端分支 {branch} 已存在，未覆盖其中的代码。",
        )
    return _branch_result(branch, "failed", commit_sha, detail)


def create_local_branch(
    workspace_root: str | Path,
    branch_name: str,
    *,
    allow_overwrite: bool,
) -> dict[str, Any]:
    """在工作区建出分支并切过去，随即把该分支推到远端。

    发起新迭代选择「新建分支」时调用。**必须在清空规划产物之前执行** —— 新分支要指向
    当前这次已提交的代码，晚于清空就会把新分支建在残缺的树上。

    与 `push_baseline_to_branch` 一样**永不抛异常**：分支建不出来时只报告结果，由调用方
    决定是否继续（当前调用方会继续，并提示用户）。
    """

    root = Path(workspace_root).expanduser().resolve()
    try:
        branch = validate_branch_name(branch_name)
    except GitBranchError as exc:
        return _branch_result(branch_name, "failed", "", str(exc))

    commit_sha = _read_head(root)
    if not commit_sha:
        return _branch_result(
            branch, "failed", "", "当前工作区还没有基线提交，不能创建分支。"
        )

    # 已存在同名本地分支时切过去（不重建），否则从当前 HEAD 建出并切过去。
    existing = _run_git(
        root, ["branch", "--list", "--format=%(refname:short)", branch], timeout=15
    )
    if existing.returncode == 0 and branch in existing.stdout.split():
        switch = _run_git(root, ["checkout", branch], timeout=60)
    else:
        switch = _run_git(root, ["checkout", "-b", branch], timeout=60)
    if switch.returncode != 0:
        return _branch_result(branch, "failed", commit_sha, _failure_detail(switch))

    repo_url, _, _ = read_workspace_repository_target(root)
    if not repo_url:
        return _branch_result(
            branch, "created", commit_sha, "分支已在本地创建，但当前应用未配置仓库地址。"
        )

    pushed = push_baseline_to_branch(
        root, repo_url, branch, allow_overwrite=allow_overwrite
    )
    if pushed["status"] == "pushed":
        return _branch_result(branch, "created", commit_sha, "")
    # 本地分支已经切过去了，远端没推上去：把远端的原因如实带出来。
    return _branch_result(branch, "created_local_only", commit_sha, pushed["message"])


def _ensure_local_branch(root: Path, branch: str) -> str:
    """确保工作区本地存在该分支并切过去；已存在则原样保留。

    本地分支必须与「应用分支」同名：历史分支预览（`git rev-parse <branch>`）与只读源码
    （`git show <branch>:<path>`）都按分支名在本地解析，本地没有这个分支就整个用不了。
    已存在时不动它 —— 那可能是用户正在开发的位置，切换会打乱工作区。

    返回错误详情，成功时为空串。
    """

    listing = _run_git(
        root, ["branch", "--list", "--format=%(refname:short)", branch], timeout=15
    )
    if listing.returncode == 0 and branch in listing.stdout.split():
        return ""
    created = _run_git(root, ["checkout", "-b", branch], timeout=60)
    if created.returncode != 0:
        return f"无法在本地创建分支 {branch}：{_failure_detail(created)}"
    return ""


def delete_remote_branch(repo_url: str, branch_name: str) -> dict[str, Any]:
    """删除远端仓库上的该分支；失败收敛为结果而不抛异常。

    删除的是**远端**分支，不碰本地分支，也不重写其他分支的历史 —— 这正是分支模型
    比「删版本」安全的地方（对比 `git tag -d` + force push 重写历史）。
    """

    try:
        branch = validate_branch_name(branch_name)
    except GitBranchError as exc:
        return _branch_result(branch_name, "failed", "", str(exc))

    try:
        remote_url = build_authenticated_remote_url(repo_url)
    except Exception as exc:  # noqa: BLE001 - 凭证缺失等都要收敛成结果
        return _branch_result(branch, "failed", "", str(exc))

    # `git push --delete` 需要处在仓库内（`ls-remote` 不需要）。用一个临时空仓库执行，
    # 这样删远端分支不依赖工作区是否还在，也不受工作区当前分支影响。
    with tempfile.TemporaryDirectory(prefix="devagentstudio-branch-delete-") as scratch:
        scratch_root = Path(scratch)
        init = _run_git(scratch_root, ["init", "--quiet"], timeout=15)
        if init.returncode != 0:
            return _branch_result(branch, "failed", "", _failure_detail(init))
        completed = _run_git(
            scratch_root,
            ["push", remote_url, "--delete", f"refs/heads/{branch}"],
            timeout=_BRANCH_PUSH_TIMEOUT_SECONDS,
        )
    if completed.returncode == 0:
        return _branch_result(branch, "deleted", "", "")
    return _branch_result(branch, "failed", "", _failure_detail(completed))


def _branch_result(
    branch_name: str, status: str, commit_sha: str, message: str
) -> dict[str, Any]:
    """构造统一的远端分支动作结果。"""

    return {
        "branchName": branch_name,
        "status": status,
        "commitSha": commit_sha,
        "message": message,
    }


def _is_rejected_push(detail: str) -> bool:
    """判断推送失败是否只是"远端分支已存在且历史分叉"这类拒绝。"""

    lowered = detail.lower()
    return "rejected" in lowered or "non-fast-forward" in lowered or "fetch first" in lowered


def _read_head(repository_root: Path) -> str:
    """读取工作区 HEAD 的提交哈希；仓库或基线缺失时返回空串。"""

    completed = _run_git(repository_root, ["rev-parse", "HEAD"], timeout=15)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _failure_detail(completed: subprocess.CompletedProcess[str]) -> str:
    """从失败命令中提取可展示的原因，并限制长度。"""

    detail = (completed.stderr or completed.stdout or "").strip() or "未知错误"
    return detail[:512]


def _run_git(
    cwd: Path,
    arguments: list[str],
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """以固定参数和超时执行无 shell 的 Git 子命令。"""

    if shutil.which("git") is None:
        raise GitBranchError("未检测到 Git，无法执行远端分支操作。")
    return workspace_process_registry.run(
        ["git", *arguments],
        workspace=cwd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
