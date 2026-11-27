"""应用代码在远端仓库中的分支：分支名校验、远端存在性检查与基线推送。

新建应用时用户填写分支名，应用代码按分支隔离在同一个远端仓库里。分支名随
`.devagentstudio/application.json` 持久化；本模块只做确定性的 Git 动作，不参与规划状态。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.git_branch import (
    GitBranchError,
    is_non_fast_forward_rejection,
    validate_branch_name,
)
from app.services.git_credentials import git_env, git_failure_detail
from app.services.preview_runtime_state import redact
from app.services.version_publish import build_authenticated_remote_url
from app.services.workspace_process_registry import workspace_process_registry

APPLICATION_CONFIG_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / "application.json"

# 分支推送发生在 Bootstrap 请求路径上，前端在等结果，超时要比版本发布（120s）短。
_BRANCH_PUSH_TIMEOUT_SECONDS = 30
_BRANCH_CHECK_TIMEOUT_SECONDS = 20
# 单次查询已等待完整超时时间；网络抖动时立即再做一次有界尝试，不放宽失败阻断。
_BRANCH_CHECK_TIMEOUT_RETRIES = 1
# 网络类失败的重试次数与间隔（见 push_baseline_to_branch 的说明）。
_PUSH_NETWORK_RETRIES = 2
_PUSH_RETRY_DELAY_SECONDS = 1.5


def read_workspace_repository_target(workspace_root: str | Path) -> tuple[str, str]:
    """读取工作区配置中的代码提交目标：仓库地址和分支名。

    **分支名为空表示该应用未配置分支**（例如由「添加本地文件夹」接入的工作区），
    调用方应跳过远端分支动作，而不是当成错误。
    """

    application_path = Path(workspace_root).expanduser() / APPLICATION_CONFIG_RELATIVE_PATH
    try:
        application = json.loads(application_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", ""
    if not isinstance(application, dict):
        return "", ""

    raw_repo_url = application.get("repoUrl")
    repo_url = raw_repo_url.strip() if isinstance(raw_repo_url, str) else ""
    raw_branch = application.get("branchName")
    branch_name = raw_branch.strip() if isinstance(raw_branch, str) else ""
    return repo_url, branch_name


def read_workspace_branch_name(workspace_root: str | Path) -> str:
    """只读工作区的当前分支名；缺失时返回空串。

    产物归属标签（`DevelopmentArtifactProgress.completed_branch_name`）用它记录
    "这个产物是在哪一轮完成的"。读不到时返回空串，调用方按"未知归属"处理，
    不要伪造一个值。
    """

    _, branch_name = read_workspace_repository_target(workspace_root)
    return branch_name


def list_remote_branches(repo_url: str) -> list[str]:
    """读取远端全部分支名并去重；读取失败必须阻止名称校验继续。"""

    try:
        remote_url = build_authenticated_remote_url(repo_url)
        # 查询无需当前目录是仓库，使用独立临时目录避免依赖用户工作区。
        with tempfile.TemporaryDirectory(prefix="devagentstudio-branch-check-") as scratch:
            for attempt in range(_BRANCH_CHECK_TIMEOUT_RETRIES + 1):
                try:
                    completed = _run_git(
                        Path(scratch),
                        ["ls-remote", "--heads", remote_url],
                        timeout=_BRANCH_CHECK_TIMEOUT_SECONDS,
                    )
                    break
                except subprocess.TimeoutExpired:
                    if attempt >= _BRANCH_CHECK_TIMEOUT_RETRIES:
                        raise
                    # 超时已经等待过完整周期，可直接重试；明确的认证/权限拒绝不进入此分支。
    except Exception as exc:  # noqa: BLE001 - 凭证、网络及超时统一映射为可展示的错误
        raise GitBranchError(f"无法读取远端版本列表：{redact(str(exc))}") from exc
    if completed.returncode != 0:
        raise GitBranchError(f"无法读取远端版本列表：{redact(_failure_detail(completed))}")
    prefix = "refs/heads/"
    return sorted({
        ref[len(prefix):]
        for line in completed.stdout.splitlines()
        if len(parts := line.split()) == 2
        and (ref := parts[1]).startswith(prefix)
    })


def check_remote_branch(repo_url: str, branch_name: str) -> bool:
    """按完整远端分支列表检查规范分支名是否已经占用。"""

    return validate_branch_name(branch_name) in list_remote_branches(repo_url)


def assert_remote_branch_available(repo_url: str, branch_name: str) -> list[str]:
    """创建新版本前确认远端没有同名分支，返回完整列表供冲突提示使用。"""

    branch = validate_branch_name(branch_name)
    branches = list_remote_branches(repo_url)
    if branch in branches:
        raise GitBranchError(f"分支 {branch} 已存在于码云仓库，请修改分支名。")
    return branches


def push_baseline_to_branch(
    workspace_root: str | Path,
    repo_url: str,
    branch_name: str,
) -> dict[str, Any]:
    """普通推送当前 HEAD，保留非快进拒绝且不覆盖或删除远端分支。

    这是 Bootstrap 链路里的非致命步骤：实际推送失败仅报告，不回滚本地模板或提交；
    已存在的本迭代分支允许正常快进，分叉历史交由 Git 拒绝。
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

    # 网络类失败自动重试。用户环境里 github.com 走 VPN/代理的 fake-IP，TLS 握手
    # （实测约 0.6s）偶发被重置，报 SSL_ERROR_SYSCALL —— 同一秒重试往往就成功。
    # 只重试网络类失败：真正的冲突（non-fast-forward）重试没有意义，直接返回结果。
    completed = _run_git(root, arguments, timeout=_BRANCH_PUSH_TIMEOUT_SECONDS)
    for _ in range(_PUSH_NETWORK_RETRIES):
        if completed.returncode == 0:
            break
        if not _is_transient_push_failure(_failure_detail(completed)):
            break
        time.sleep(_PUSH_RETRY_DELAY_SECONDS)
        completed = _run_git(root, arguments, timeout=_BRANCH_PUSH_TIMEOUT_SECONDS)

    if completed.returncode == 0:
        return _branch_result(branch, "pushed", commit_sha, "")

    detail = _failure_detail(completed)
    if is_non_fast_forward_rejection(detail):
        return _branch_result(
            branch,
            "failed",
            commit_sha,
            f"远端分支 {branch} 已有与本地分叉的提交，普通推送被拒绝；"
            f"本地提交已保留，请先处理远端变更后重试。{detail}",
        )
    return _branch_result(branch, "failed", commit_sha, detail)


def create_local_branch(
    workspace_root: str | Path,
    branch_name: str,
    *,
    start_point: str | None = None,
) -> dict[str, Any]:
    """在工作区建出分支并切过去，随即把该分支推到远端。

    发起新迭代选择「新建分支」时调用。**必须在清空规划产物之前执行** —— 新分支要指向
    当前这次已提交的代码，晚于清空就会把新分支建在残缺的树上。

    `start_point` 用于「基于历史版本继续开发」：传入历史分支名时，新分支从那条历史
    分支的 HEAD 建出（`git checkout -b <branch> <start_point>`），而不是从当前 HEAD。
    调用方必须先保证工作区干净（无未提交改动），否则 checkout 会失败或被拒绝。

    分支创建失败返回 failed，调用方必须中止迭代；已经成功创建本地分支但实际推送失败时，
    返回 created_local_only 并保留本地代码，供后续普通推送重试。
    """

    root = Path(workspace_root).expanduser().resolve()
    try:
        branch = validate_branch_name(branch_name)
    except GitBranchError as exc:
        return _branch_result(branch_name, "failed", "", str(exc))

    # start_point 校验：必须在本地能解析成一个 revision，否则 checkout -b 会失败。
    # 在历史分支场景下这是历史分支名（如 "v1_0"）；调用方传 None 时落到当前 HEAD。
    resolved_start_point = ""
    if start_point:
        candidate = start_point.strip()
        if not candidate:
            return _branch_result(branch, "failed", "", "startPoint 不能为空字符串。")
        resolved = _run_git(
            root, ["rev-parse", "--verify", f"{candidate}^{{commit}}"], timeout=15
        )
        if resolved.returncode != 0 or not resolved.stdout.strip():
            return _branch_result(
                branch,
                "failed",
                "",
                f"起点 {candidate} 在本地仓库中不存在，无法基于此版本创建。",
            )
        resolved_start_point = candidate

    commit_sha = _read_head(root)
    if not commit_sha:
        return _branch_result(
            branch, "failed", "", "当前工作区还没有基线提交，不能创建版本。"
        )
    # 历史分支起点时，结果里的 commitSha 应该是新分支指向的 sha，而不是切分支前的 HEAD。
    if resolved_start_point:
        start_sha = _run_git(
            root,
            ["rev-parse", f"{resolved_start_point}^{{commit}}"],
            timeout=15,
        )
        if start_sha.returncode == 0 and start_sha.stdout.strip():
            commit_sha = start_sha.stdout.strip()

    # 新建分支语义是"创建一条全新版本"：本地已存在同名分支时必须拒绝，而不是切过去。
    # 切过去会把后续开发直接叠加到那条旧分支上、覆盖其历史 —— 用户本想新建一个版本
    # 便于回退，结果却在旧版本上改动，恰好违背了新迭代"可回退"的初衷。前端弹窗虽有
    # 重名校验，但那是 UI 层提示，不能作为唯一防线；这里在后端再拦一道，任何绕过
    # 前端的调用都会被拒绝。
    existing = _run_git(
        root, ["branch", "--list", "--format=%(refname:short)", branch], timeout=15
    )
    if existing.returncode == 0 and branch in existing.stdout.split():
        return _branch_result(
            branch,
            "failed",
            commit_sha,
            f"版本 {branch} 已存在，不能重复创建。请换一个新版本号；如需在该版本上继续，请直接切换到该版本。",
        )
    # 在切换本地分支前复查远端；读取失败不能被当成没有冲突。
    repo_url, _ = read_workspace_repository_target(root)
    if repo_url:
        try:
            assert_remote_branch_available(repo_url, branch)
        except GitBranchError as exc:
            return _branch_result(branch, "failed", commit_sha, str(exc))
    create_args = ["checkout", "-b", branch]
    if resolved_start_point:
        create_args.append(resolved_start_point)
    switch = _run_git(root, create_args, timeout=60)
    if switch.returncode != 0:
        return _branch_result(branch, "failed", commit_sha, _failure_detail(switch))

    if not repo_url:
        return _branch_result(
            branch, "created", commit_sha, "版本已在本地创建，但当前应用未配置仓库地址。"
        )

    pushed = push_baseline_to_branch(root, repo_url, branch)
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
        return f"无法在本地创建版本 {branch}：{_failure_detail(created)}"
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


def push_workspace_branch(workspace_root: str | Path) -> dict[str, Any]:
    """把工作区当前 HEAD 推到应用配置里那条分支；**永不抛异常**。

    分支名与仓库地址都从工作区自己的 `application.json` 读（`read_workspace_repository_target`），
    所以调用方不需要重复解析配置。未配置分支名的工作区（如「添加本地文件夹」接入的目录）
    直接返回 skipped。

    已存在的远端分支仅允许快进推送，不接受覆盖授权或自动删除。

    两处调用方共用它：
    - Bootstrap 收尾（`workspace_bootstrap/service.py`）—— 基线提交后建出远端分支；
    - 工作台的「重试推送」动作（`protocols/repository_branch.py`）—— 上次失败后重试。
    两边都要求非致命，所以异常在这里收敛成 `status: "failed"`。
    """

    root = Path(workspace_root).expanduser().resolve()
    try:
        repo_url, branch_name = read_workspace_repository_target(root)
        if not branch_name:
            return _branch_result("", "skipped", "", "当前应用未配置版本号，跳过远端分支推送。")
        if not repo_url:
            return _branch_result(branch_name, "skipped", "", "当前应用未配置仓库地址。")
        return push_baseline_to_branch(root, repo_url, branch_name)
    except Exception as exc:  # noqa: BLE001 - 调用方都要求非致命
        return _branch_result("", "failed", "", f"远端版本推送失败：{exc}")


def _branch_result(
    branch_name: str, status: str, commit_sha: str, message: str
) -> dict[str, Any]:
    """构造统一的远端分支动作结果。

    message 会持久化到 application.json 并随提交进入 git 历史，所以必须脱敏：
    env 模式下 push 命令的 URL 包含 user:token，整段塞进 message 就会让 token
    跟着进入下一次 push（GitHub Push Protection 会扫到并拒绝，更糟的是 token 永久
    进入远端仓库历史）。这里调 preview_runtime_state.redact 把 `://user:pass@` 形式
    的凭证统一改成 `[REDACTED]`，保留主机与路径便于排查。
    """

    return {
        "branchName": branch_name,
        "status": status,
        "commitSha": commit_sha,
        "message": redact(message),
    }


def _is_transient_push_failure(detail: str) -> bool:
    """判断推送失败是否属于"重试就可能成功"的网络类故障。

    只认明确的网络/传输特征；**不**把 non-fast-forward 这类真实冲突算进来 ——
    那种重试多少次都一样，应该直接把结果告诉用户。
    """

    lowered = detail.lower()
    return any(
        marker in lowered
        for marker in (
            "ssl_error",
            "ssl_connect",
            "unable to access",
            "could not resolve",
            "connection reset",
            "connection timed out",
            "operation timed out",
            "rpc failed",
            "early eof",
            "the remote end hung up",
        )
    )


def _read_head(repository_root: Path) -> str:
    """读取工作区 HEAD 的提交哈希；仓库或基线缺失时返回空串。"""

    completed = _run_git(repository_root, ["rev-parse", "HEAD"], timeout=15)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _failure_detail(completed: subprocess.CompletedProcess[str]) -> str:
    """从失败命令中提取可展示的原因（认证类失败附带可操作提示），并限制长度。"""

    return git_failure_detail(completed)


def _run_git(
    cwd: Path,
    arguments: list[str],
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """以固定参数和超时执行无 shell 的 Git 子命令。"""

    if shutil.which("git") is None:
        raise GitBranchError("未检测到 Git，无法执行远端版本操作。")
    return workspace_process_registry.run(
        ["git", *arguments],
        workspace=cwd,
        cwd=str(cwd),
        # 禁用交互式提问：无 TTY 时等输入会把推送流程挂住（见 services/git_credentials）。
        env=git_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
