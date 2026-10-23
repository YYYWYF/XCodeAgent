"""应用版本发布服务：在工作区执行真实 Git 提交、打 Tag 并推送到远程仓库。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field

from app.services.git_branch import validate_branch_name
from app.services.git_commit_message import ensure_board_number
from app.services.git_credentials import (
    git_env,
    git_failure_detail,
    uses_local_credentials,
)
from app.services.workspace_process_registry import workspace_process_registry


class VersionPublishError(ValueError):
    """表示当前工作区不能完成版本发布动作。"""


class VersionPublishRequest(BaseModel):
    """校验一次「提交并推送」请求。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["publish"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)
    repo_url: str = Field(alias="repoUrl", min_length=1)
    # 应用在远端仓库中的分支名；推送目标就是它，不再打 Tag。
    branch_name: str = Field(alias="branchName", min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)


class VersionPublishResult(BaseModel):
    """返回成功提交后的仓库事实与 Git 引用。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["publish"] = "publish"
    workspace_root: str = Field(alias="workspaceRoot")
    repository_root: str = Field(alias="repositoryRoot")
    branch: str
    commit_sha: str = Field(alias="commitSha", min_length=7)


ProgressCallback = Callable[[str, str, int], None]


def publish_version(
    request: VersionPublishRequest,
    *,
    report_progress: ProgressCallback | None = None,
) -> VersionPublishResult:
    """在工作区执行 git add -A + commit + push，并报告三步进度。

    推送到 `request.branch_name` 指定的应用分支；不再打 Tag。
    report_progress 为可选的同步回调，接收 (stage, message, percent)。
    """

    def report(stage: str, message: str, percent: int) -> None:
        if report_progress is not None:
            report_progress(stage, message, percent)

    workspace_root = _resolve_workspace_root(request.workspace_root)
    repository_root = _resolve_repository_root(workspace_root)
    branch = validate_branch_name(request.branch_name)

    # 1. 打包：校验工作区是 Git 仓库、读取基线、解除 .devagentstudio 排除。
    report("package", "正在打包工作区变更…", 10)
    head = _read_head(repository_root)
    if head == "UNBORN":
        raise VersionPublishError("当前仓库还没有基线提交，不能直接发布版本。")
    # 移除 .git/info/exclude 中对 .devagentstudio 的排除，确保规划产物随版本提交。
    _ensure_devagentstudio_tracked(repository_root)

    # 2. 提交：git add -A + git commit。
    report("commit", "正在提交变更到本地仓库…", 30)
    _run_git_checked(
        repository_root,
        ["add", "-A"],
        "无法暂存工作区变更",
        # 首次发布/大量变更时 15s 默认超时不够；与下方 commit 一致放宽到 60s。
        # 超时被强杀后 git 可能来不及清理 .git/index.lock，导致下一次 add 报
        # "Unable to create index.lock: File exists"——见 _run_git 的清理逻辑。
        timeout=60,
    )
    # 运行时产物（日志、缓存、checkpoint）不得进入版本提交；
    # .gitignore 通常已排除，这里兜底从暂存区移除，避免大文件阻塞推送。
    _unstage_runtime_artifacts(repository_root)
    # 工作区干净时跳过提交（基于当前 HEAD 打 Tag）。
    has_changes = _has_staged_changes(repository_root)
    if has_changes:
        # 说明为空时回退到分支名。这里原先是 request.version_label —— 版本号模型
        # 去掉后该字段已不存在，说明为空时会抛 AttributeError（前端禁用了空说明提交，
        # 所以没暴露出来，但直接调接口就会中招）。
        # 统一补看板编号：提交入口不止一处，且看板要求每次提交都带编号。
        commit_message = ensure_board_number(request.description.strip() or branch)
        _run_git_checked(
            repository_root,
            ["commit", "-m", commit_message],
            "Git 提交失败",
            timeout=60,
        )
    commit_sha = _run_git_checked(
        repository_root,
        ["rev-parse", "HEAD"],
        "无法读取提交结果",
    ).strip()

    # 3. 推送到应用自己的分支。
    #    推送目标是**请求里带来的应用分支**（已在上面校验过），不是本地当前分支 ——
    #    过去所有应用都推本地 main，导致共用一个远端仓库时互相覆盖。
    report("push", "正在推送到应用分支…", 60)

    remote_url = build_authenticated_remote_url(request.repo_url)
    remote_name = PUBLISH_REMOTE_NAME
    _ensure_remote(repository_root, remote_name, remote_url)

    pushed = False
    try:
        _run_git_checked(
            repository_root,
            # 必须用全限定 refspec：仓库里可能同时存在同名的分支与**标签**
            # （旧版本号体系给每个版本打过 tag，如 v1.0），`HEAD:v1.0` 会被 git
            # 判成"匹配到多个"直接拒绝（dst refspec v1.0 matches more than one）。
            ["push", remote_name, f"HEAD:refs/heads/{branch}", "--force"],
            "推送提交到远程仓库失败",
            timeout=120,
        )
        pushed = True
    finally:
        if not pushed:
            # 推送失败时仅清理临时 remote，本地提交保留便于用户手动重推。
            _run_git(repository_root, ["remote", "remove", remote_name])

    # 4. 生成迭代上下文总结（AGENTS.md），供下一轮迭代的大模型作为起点。
    try:
        from app.services.iteration_service import generate_agents_context

        generate_agents_context(
            workspace_root,
            branch_name=branch,
            description=request.description,
        )
    except Exception:
        # AGENTS.md 生成失败不影响发布结果。
        pass

    report("done", "提交并推送完成。", 100)

    return VersionPublishResult(
        workspaceRoot=str(workspace_root),
        repositoryRoot=str(repository_root),
        branch=branch,
        commitSha=commit_sha,
    )


# 发布用的远端名。version_control 读取"远端已到哪"来界定本次推送范围，必须与此一致。
PUBLISH_REMOTE_NAME = "devagentstudio-publish"

_RUNTIME_ARTIFACT_PATHS = (
    ".devagentstudio/runtime",
    ".devagentstudio/cache",
    ".devagentstudio/checkpoints",
)


def _unstage_runtime_artifacts(repository_root: Path) -> None:
    """从暂存区移除运行时产物，防止大文件（如 checkpoint sqlite）阻塞推送。

    .gitignore 通常已排除这些路径，这里兜底处理漏配或旧工作区；
    移除暂存区条目不影响工作区文件本身。
    """

    for path in _RUNTIME_ARTIFACT_PATHS:
        _run_git(
            repository_root,
            ["rm", "-r", "--cached", "--ignore-unmatch", path],
        )


def _ensure_devagentstudio_tracked(repository_root: Path) -> None:
    """移除 .git/info/exclude 中对 .devagentstudio 的排除，确保规划产物随版本提交。

    旧工作区在 baseline 初始化时写过 `.devagentstudio/` 到 info/exclude；
    新工作区不再写。这里统一清理，保证发布时 git add -A 能包含 .devagentstudio。
    """

    exclude = repository_root / ".git" / "info" / "exclude"
    if not exclude.exists():
        return
    try:
        lines = exclude.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    filtered = [line for line in lines if line.strip() not in {".devagentstudio/", ".devagentstudio"}]
    if len(filtered) == len(lines):
        return
    try:
        exclude.write_text("\n".join(filtered) + ("\n" if filtered else ""), encoding="utf-8")
    except OSError as exc:
        raise VersionPublishError(f"无法更新 Git exclude 配置：{exc}") from exc


def _resolve_workspace_root(value: str) -> Path:
    """解析并校验版本发布指定的工作区根目录。"""

    root = Path(value).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise VersionPublishError("工作目录不存在或不是文件夹。")
    return root


def _resolve_repository_root(workspace_root: Path) -> Path:
    """要求工作区本身就是 Git 根目录。"""

    if not shutil.which("git"):
        raise VersionPublishError("未检测到 Git，无法发布版本。")
    completed = _run_git(workspace_root, ["rev-parse", "--show-toplevel"])
    if completed.returncode != 0:
        raise VersionPublishError("当前工作目录还不是 Git 仓库。")
    repository_root = Path(completed.stdout.strip()).resolve()
    if repository_root != workspace_root:
        raise VersionPublishError("工作区必须使用自己的 Git 仓库，不能提交到父目录仓库。")
    return repository_root


def _read_head(repository_root: Path) -> str:
    """读取当前 HEAD；未建立基线时返回占位值。"""

    completed = _run_git(repository_root, ["rev-parse", "HEAD"])
    return completed.stdout.strip() if completed.returncode == 0 else "UNBORN"


def _read_branch(repository_root: Path) -> str:
    """读取当前分支名称，detached HEAD 时回退 main。"""

    completed = _run_git(repository_root, ["branch", "--show-current"])
    branch = completed.stdout.strip() if completed.returncode == 0 else ""
    return branch or "main"


def _has_staged_changes(repository_root: Path) -> bool:
    """检查暂存区是否有待提交内容。"""

    completed = _run_git(repository_root, ["diff", "--cached", "--quiet"])
    # git diff --cached --quiet 退出码 1 表示有差异，0 表示无差异。
    return completed.returncode == 1


def _ensure_remote(repository_root: Path, name: str, url: str) -> None:
    """配置或更新临时 remote 指向带认证的远程地址。"""

    existing = _run_git(repository_root, ["remote"])
    if name in existing.stdout.split():
        _run_git_checked(
            repository_root, ["remote", "set-url", name, url], "无法更新远程仓库地址"
        )
    else:
        _run_git_checked(
            repository_root, ["remote", "add", name, url], "无法配置远程仓库"
        )


def build_authenticated_remote_url(repo_url: str) -> str:
    """把 repo_url 变成可推送的远端地址。

    远端分支动作（services/repository_branch.py）与版本发布共用这一处地址拼装，
    避免两份实现各自处理凭证。凭证来源由 services/git_credentials 决定：
    helper 模式下原样返回，认证交给本机 git 凭证助手；env 模式下注入 .env 里的
    用户名与 token。
    """

    if uses_local_credentials():
        # URL 里一旦内嵌用户名，git 就不再询问本机凭证助手 —— 这正是 helper 模式要避免的。
        # 同时原始地址也不会把 token 写进工作区的 .git/config。
        return repo_url

    username = os.getenv("DEVAGENTSTUDIO_GIT_USERNAME", "").strip()
    token = os.getenv("DEVAGENTSTUDIO_GIT_TOKEN", "").strip()
    if not username or not token:
        raise VersionPublishError(
            "未配置 Git 凭证，请在 .env 设置 DEVAGENTSTUDIO_GIT_USERNAME 与 DEVAGENTSTUDIO_GIT_TOKEN。"
        )

    parsed = urlparse(repo_url)
    if not parsed.scheme or not parsed.hostname:
        raise VersionPublishError(f"仓库地址格式无效：{repo_url}")

    # 重组为 https://username:token@host/path
    netloc = f"{username}:{token}@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def _run_git(
    repository_root: Path,
    arguments: list[str],
    *,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    """以固定参数和超时执行无 shell 的 Git 子命令。

    超时强杀后清理 `.git/index.lock`：`workspace_process_registry` 在超时时会
    force-kill 子进程，git 来不及走 atexit 清理，残留 lock 会让下一次 add/commit
    报 "Unable to create ... index.lock: File exists"。lock 只在进程活着时有意义，
    进程已死则锁是死的，删掉安全。
    """

    try:
        return workspace_process_registry.run(
            ["git", *arguments],
            workspace=repository_root,
            cwd=str(repository_root),
            # 禁用交互式提问：无 TTY 时等输入会把发布流程挂住（见 services/git_credentials）。
            env=git_env(),
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _cleanup_stale_index_lock(repository_root)
        raise


def _cleanup_stale_index_lock(repository_root: Path) -> None:
    """删除超时被杀的 git 进程留下的 `.git/index.lock`，让下一次 add/commit 不被阻塞。"""

    lock_path = repository_root / ".git" / "index.lock"
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        # 删不掉也不掩盖原始 TimeoutExpired——下一次 git 自己会报"File exists"，
        # 日志能看到原始超时，运维按 git 提示手动清理即可。
        pass


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
        # 认证类失败（缺凭证 / 无权限）在原文前附上可操作提示，用户才知道该去做什么。
        raise VersionPublishError(f"{error_prefix}：{git_failure_detail(completed)}")
    return completed.stdout
