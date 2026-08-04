"""进入 UI 确认节点时准备设计稿工程：clone GitHub 模板 + 安装依赖 + 启动 dev server。

设计稿工程不再写死在固定路径，而是按工作区隔离：从 GitHub 模板仓库 clone 到
当前工作区的 .xcodeagent/ui-design/ 目录，复用 frontend_project_launcher 启动
dev server，返回工程目录与 preview_origin 供设计稿代码写入与前端 iframe 嵌入。
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from app.services.frontend_project_launcher import launch_frontend_project


logger = logging.getLogger(__name__)

UI_DESIGN_REPO_URL = "https://github.com/ruyue1/xcode-agent-ui-design-template"
UI_DESIGN_RELATIVE_DIR = ".xcodeagent/ui-design"
# 设计稿工程用独立的 runtime 子目录与 PID 文件，避免与正式前端预览冲突。
UI_DESIGN_RUNTIME_SUBDIR = "launch-ui-design"
CLONE_TIMEOUT_SECONDS = 120
PULL_TIMEOUT_SECONDS = 60


def ui_design_project_dir(workspace: str | Path) -> Path:
    """返回工作区下的设计稿工程目录路径。"""

    return Path(workspace).expanduser().resolve() / UI_DESIGN_RELATIVE_DIR


def _clone_template(project_dir: Path) -> tuple[bool, str]:
    """clone GitHub 模板到 project_dir，返回 (是否成功, 信息)。"""

    project_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(
            ["git", "clone", "--depth", "1", UI_DESIGN_REPO_URL, str(project_dir)],
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"git clone 执行异常：{exc}"
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        return False, f"git clone 失败：{stderr[:300] or '未知错误'}"
    return True, "git clone 成功"


def _update_template(project_dir: Path) -> tuple[bool, str]:
    """已存在 clone 时拉取最新模板，返回 (是否成功, 信息)。

    拉取失败不阻断（保留现有版本继续用），仅记录警告。
    """

    try:
        completed = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=PULL_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"git pull 执行异常：{exc}"
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        return False, f"git pull 失败：{stderr[:300] or '未知错误'}"
    return True, "git pull 成功"


def setup_ui_design_project(workspace: str | Path) -> dict[str, Any]:
    """准备设计稿工程：clone/更新模板 + 启动 dev server。

    返回:
        {status, project_dir, preview_origin, message}
        - status: "ready" | "failed"
        - project_dir: 设计稿工程绝对路径（clone 成功即有值，便于代码写入）
        - preview_origin: dev server 地址（如 http://localhost:3003），启动失败为空串
    """

    root = Path(workspace).expanduser().resolve()
    project_dir = ui_design_project_dir(root)
    git_dir = project_dir / ".git"
    package_json = project_dir / "package.json"

    # 幂等：已有 clone 则更新；已有 package.json 但无 .git 视为已初始化，跳过 clone。
    if git_dir.is_dir():
        ok, msg = _update_template(project_dir)
        if not ok:
            logger.warning("ui_design_template_update_failed dir=%s msg=%s", project_dir, msg)
    elif package_json.is_file():
        logger.info("ui_design_project_exists_without_git dir=%s", project_dir)
    else:
        ok, msg = _clone_template(project_dir)
        if not ok:
            logger.error("ui_design_clone_failed msg=%s", msg)
            return {
                "status": "failed",
                "project_dir": str(project_dir),
                "preview_origin": "",
                "message": msg,
            }

    # 启动 dev server（复用 frontend_project_launcher 的 install + 启动 + 健康检查）。
    launch = launch_frontend_project(
        root,
        project_dir=project_dir,
        runtime_subdir=UI_DESIGN_RUNTIME_SUBDIR,
    )
    preview_url = str(launch.get("preview_url") or "").rstrip("/")
    if launch.get("status") != "running":
        # 启动失败仍返回 project_dir，让代码生成能继续（只是预览不可用）。
        logger.warning(
            "ui_design_dev_server_failed status=%s message=%s",
            launch.get("status"),
            launch.get("message"),
        )
        return {
            "status": "failed",
            "project_dir": str(project_dir),
            "preview_origin": "",
            "message": f"设计稿预览服务启动失败：{launch.get('message')}",
        }
    return {
        "status": "ready",
        "project_dir": str(project_dir),
        "preview_origin": preview_url,
        "message": "设计稿工程已就绪。",
    }
