"""按 Git 版本（tag）物化代码并启动历史版本预览。

历史版本回看时，源码面板按 tag 只读读取（见 `revision_workspace.py`），但**预览需要一个
真正跑起来的应用** —— 只读读取给不出 dev server。这里把该版本的树物化到一个独立目录，
再用既有的前端启动器把它跑起来。

两个关键设计：

- **用 `git worktree add` 而不是 checkout**：worktree 在独立目录建树，不碰工作区，
  与 `revision_workspace.py`「绝不 checkout」的原则不冲突。
- **独立 runtime_subdir 与独立端口**：复用 `launch_frontend_project` 的 `runtime_subdir`
  隔离进程登记（设计稿预览的 `launch-ui-design` 是同一先例）。端口不显式指定 ——
  vite 在 3000 被当前预览占用时会自动递增，启动器已从日志读回真实地址
  （见 `_resolve_actual_preview_url`）。所以当前预览与历史预览可以并存。
"""

from __future__ import annotations

import hashlib
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from app.services.frontend_project_launcher import (
    SERVER_READY_INTERVAL_SECONDS,
    _preview_is_ready,
    _resolve_actual_preview_url,
    _running_pids,
    launch_frontend_project,
    stop_frontend_project,
)
from app.services.version_control import _run_git

ProgressCallback = Callable[[str, str, int], None]

# 物化目录放在工作区运行时目录下：`.xcodeagent/runtime/` 已被 .gitignore 排除，
# 不会污染 `git status`，且随工作区一起被清理。
REVISION_PREVIEW_ROOT = ".xcodeagent/runtime/revision-preview"
REVISION_PREVIEW_RUNTIME_SUBDIR_PREFIX = "launch-revision-"

# 等该版本的 dev server 真正就绪的上限。比常规预览宽松：历史版本的首次启动要现编译
# 该版本的树，比"服务已经在跑"的常规场景慢。
REVISION_PREVIEW_READY_TIMEOUT_SECONDS = 120

# 目录名只保留安全字符；tag 可以含 `/`（如 release/v1.0），不能直接当目录名。
_UNSAFE_DIR_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class RevisionPreviewError(ValueError):
    """表示历史版本预览无法安全启动。"""


def revision_preview_runtime_subdir(revision: str) -> str:
    """该版本的独立运行时目录名，用于进程登记与 PID 文件隔离。"""

    return f"{REVISION_PREVIEW_RUNTIME_SUBDIR_PREFIX}{_revision_slug(revision)}"


def _revision_slug(revision: str) -> str:
    """把 revision 压成安全且稳定的目录名片段。"""

    slug = _UNSAFE_DIR_CHARS.sub("-", str(revision or "").strip()).strip("-")
    # 极端情况下（全是非法字符）slug 可能为空，用内容哈希兜底，保证目录名非空且唯一。
    digest = hashlib.sha256(str(revision or "").encode("utf-8")).hexdigest()[:8]
    return f"{slug[:60] or 'rev'}-{digest}"


def revision_preview_dir(workspace_root: str | Path, revision: str) -> Path:
    """该版本的物化目录。"""

    root = Path(workspace_root).expanduser().resolve()
    return root / REVISION_PREVIEW_ROOT / _revision_slug(revision)


def materialize_revision(workspace_root: str | Path, revision: str) -> Path:
    """把指定版本物化到独立目录，返回该目录。

    已物化且指向同一 commit 时直接复用，避免每次点预览都重建树。
    """

    root = Path(workspace_root).expanduser().resolve()
    revision = str(revision or "").strip()
    if not revision:
        raise RevisionPreviewError("缺少要预览的版本标识。")

    target = revision_preview_dir(root, revision)
    expected_commit = _resolve_revision_commit(root, revision)

    if target.is_dir() and _worktree_commit(target) == expected_commit:
        return target

    # 目录存在但指向别的 commit（或不是合法 worktree）：先摘掉再重建。
    if target.exists():
        _remove_worktree(root, target)

    target.parent.mkdir(parents=True, exist_ok=True)
    completed = _run_git(
        root, ["worktree", "add", "--detach", str(target), expected_commit], timeout=120
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise RevisionPreviewError(f"无法物化版本 {revision} 的代码：{detail}")
    return target


def _resolve_revision_commit(root: Path, revision: str) -> str:
    """把 tag/分支/commit 解析成确定的 commit 哈希；不存在时报明确错误。"""

    completed = _run_git(root, ["rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"])
    commit = completed.stdout.strip() if completed.returncode == 0 else ""
    if not commit:
        raise RevisionPreviewError(f"版本 {revision} 在仓库中不存在，无法预览。")
    return commit


def _worktree_commit(directory: Path) -> str:
    """读取已物化目录当前指向的 commit；不是合法 worktree 时返回空串。"""

    completed = _run_git(directory, ["rev-parse", "--verify", "--quiet", "HEAD"])
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _remove_worktree(root: Path, target: Path) -> None:
    """摘除物化目录；失败时退回直接删除并 prune，避免残留注册项。"""

    _unlink_reused_dependencies(target)
    completed = _run_git(root, ["worktree", "remove", "--force", str(target)], timeout=60)
    if completed.returncode != 0 and target.exists():
        shutil.rmtree(target, ignore_errors=True)
    # 无论上面成败都 prune 一次：崩溃或强删都可能留下注册项。
    _run_git(root, ["worktree", "prune"])


def _unlink_reused_dependencies(target: Path) -> None:
    """先摘掉依赖软链再删目录。

    `node_modules` 是指向工作区的软链，删除目录本身不会跟着删目标，但先摘掉更明确 ——
    也避免 `git worktree remove` 把软链当作待清理内容处理时出现意外。
    """

    link = target / "frontend" / "node_modules"
    if link.is_symlink():
        link.unlink(missing_ok=True)


def _can_reuse_dependencies(workspace_root: Path, revision_dir: Path) -> bool:
    """工作区已装依赖且与目标版本的依赖声明一致时，可以复用。

    按 `package.json` 判定而不是锁文件：锁文件可能因为安全 override 修复而不同
    （线上 v1.0 就是这样），但声明的依赖一致就足以支撑渲染。历史预览的目的是看当时
    的界面，不是校验锁文件。
    """

    workspace_package = workspace_root / "frontend" / "package.json"
    revision_package = revision_dir / "frontend" / "package.json"
    if not workspace_package.is_file() or not revision_package.is_file():
        return False
    if not (workspace_root / "frontend" / "node_modules").is_dir():
        return False
    try:
        return workspace_package.read_bytes() == revision_package.read_bytes()
    except OSError:
        return False


def _link_reused_dependencies(workspace_root: Path, revision_dir: Path) -> bool:
    """把工作区的 node_modules 软链进物化目录，返回是否成功复用。"""

    link = revision_dir / "frontend" / "node_modules"
    if link.exists() or link.is_symlink():
        return True
    source = workspace_root / "frontend" / "node_modules"
    try:
        link.symlink_to(source, target_is_directory=True)
    except OSError:
        return False
    return True


def start_revision_preview(
    workspace_root: str | Path,
    revision: str,
    *,
    report_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """物化该版本并启动独立的前端预览，返回预览地址。"""

    def report(stage: str, message: str, percent: int) -> None:
        if report_progress is not None:
            report_progress(stage, message, percent)

    root = Path(workspace_root).expanduser().resolve()
    revision = str(revision or "").strip()

    report("materialize", f"正在准备版本 {revision} 的代码…", 20)
    revision_dir = materialize_revision(root, revision)

    reused = _can_reuse_dependencies(root, revision_dir) and _link_reused_dependencies(
        root, revision_dir
    )
    if reused:
        report("install", "依赖与当前工作区一致，复用已安装的依赖。", 45)
    else:
        report("install", "正在安装该版本的依赖…", 45)

    report("launch", "正在启动该版本的前端服务…", 70)
    runtime_root = root / ".xcodeagent" / "runtime" / revision_preview_runtime_subdir(revision)
    # 清掉上一次启动的 stdout 日志再启动：下面的地址解析是从日志里找 `Local:` 行，
    # 留着旧内容就可能解析出上一次那个已经失效的端口。
    (runtime_root / "frontend.stdout.log").unlink(missing_ok=True)
    launch = launch_frontend_project(
        root,
        project_dir=revision_dir / "frontend",
        runtime_subdir=revision_preview_runtime_subdir(revision),
        # 复用依赖时跳过安装；否则让启动器正常安装该版本的依赖。
        skip_install=reused,
        # 复用的方式是把工作区的 node_modules 软链进来，要一并告诉包管理器别去"纠正"它。
        linked_dependencies=reused,
        # 必须绕开"服务已在运行就直接复用"这条捷径。它按运行时目录里的 PID 文件判断，
        # 再探测脚本里写死的端口（本模板是 3000）—— 而那个端口是工作区预览在占着，于是
        # 只要上次留下过一个 PID 文件，这里就会把**工作区的应用**当成该版本的预览返回，
        # 界面上就又看到了最新版本。历史版本预览要的是自己那个 dev server，宁可重启。
        force_restart=True,
    )
    if str(launch.get("status") or "failed") != "running":
        raise RevisionPreviewError(
            str(launch.get("message") or "历史版本前端服务启动失败。")
        )

    # 不信启动器返回的地址。它按脚本里写死的端口（3000）做健康检查，而那个端口正是
    # 工作区预览在监听，于是检查会立刻通过、返回的还是工作区那个地址 —— 预览就又会显示
    # 最新版本。这里只认该版本自己日志里的实际监听地址（vite 在端口被占用时会递增）。
    preview_url = _await_revision_preview_url(runtime_root)
    if not preview_url:
        detail = _launch_failure_detail(runtime_root)
        raise RevisionPreviewError(f"版本 {revision} 的前端服务未就绪。{detail}")

    report("ready", "该版本的预览已就绪。", 100)
    return {
        "status": "running",
        "revision": revision,
        "preview_url": preview_url,
        "reused_dependencies": reused,
        "runtime_subdir": revision_preview_runtime_subdir(revision),
        "materialized_path": str(revision_dir),
    }


def _await_revision_preview_url(runtime_root: Path) -> str:
    """轮询该版本自己的启动日志，返回实际监听且可访问的地址；超时返回空串。"""

    stdout_log = runtime_root / "frontend.stdout.log"
    deadline = time.monotonic() + REVISION_PREVIEW_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        preview_url = _resolve_actual_preview_url(
            stdout_log=stdout_log, stdout_offset=0, fallback_url=""
        )
        if preview_url and _preview_is_ready(preview_url):
            return preview_url
        # 进程已退出就不必再等：日志不会再长出新的监听地址。
        if not _running_pids(_runtime_pid(runtime_root)):
            return ""
        time.sleep(SERVER_READY_INTERVAL_SECONDS)
    return ""


def _runtime_pid(runtime_root: Path) -> list[int]:
    """读取该版本运行时目录里登记的 PID；缺失或非法时返回空列表。"""

    try:
        return [int((runtime_root / "frontend.pid").read_text(encoding="utf-8").strip())]
    except (OSError, ValueError):
        return []


def _launch_failure_detail(runtime_root: Path) -> str:
    """摘出启动日志的尾部，让失败原因能在界面上直接看到。"""

    for name in ("frontend.stderr.log", "frontend.stdout.log"):
        try:
            tail = (runtime_root / name).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if tail:
            return f"（{name} 末尾：{tail[-400:]}）"
    return ""


def stop_revision_preview(workspace_root: str | Path, revision: str) -> dict[str, Any]:
    """停止该版本的前端服务并摘除物化目录。"""

    root = Path(workspace_root).expanduser().resolve()
    revision = str(revision or "").strip()
    if not revision:
        return {"status": "stopped", "revision": revision}

    stop_frontend_project(root, runtime_subdir=revision_preview_runtime_subdir(revision))
    target = revision_preview_dir(root, revision)
    if target.exists():
        _remove_worktree(root, target)
    return {"status": "stopped", "revision": revision, "removed_path": str(target)}


def revision_preview_state(workspace_root: str | Path, revision: str) -> dict[str, Any]:
    """查询该版本预览的当前状态，供前端决定是否需要重新启动。

    地址来源与启动器一致：从本次启动的 stdout 日志里解析 dev server 实际监听地址
    （vite 在端口被占用时会自动递增，推算值不可靠）。不用 `preview-runtime.json` ——
    那个文件由预览协议写、字段是 camelCase，且启动路径不保证写它。
    """

    root = Path(workspace_root).expanduser().resolve()
    revision = str(revision or "").strip()
    if not revision:
        return {"status": "idle", "revision": revision}

    runtime_root = root / ".xcodeagent" / "runtime" / revision_preview_runtime_subdir(revision)
    target = revision_preview_dir(root, revision)

    pid = 0
    try:
        pid = int((runtime_root / "frontend.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = 0

    preview_url = _resolve_actual_preview_url(
        stdout_log=runtime_root / "frontend.stdout.log",
        stdout_offset=0,
        fallback_url="",
    )
    # 必须确认进程还活着：日志里的地址会一直留在文件里，只看地址会把已经退出的服务
    # 报成 running，前端便不会重新拉起，用户对着一个死地址看空白。
    running = pid > 0 and bool(preview_url) and bool(_running_pids([pid]))
    return {
        "status": "running" if running else "idle",
        "revision": revision,
        "pid": pid,
        "preview_url": preview_url if running else "",
        "materialized": target.is_dir(),
    }
