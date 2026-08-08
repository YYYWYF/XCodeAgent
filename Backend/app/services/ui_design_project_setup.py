"""进入 UI 确认节点时准备设计稿落盘目录。

方案 B：不再 clone GitHub 模板工程、不再 pnpm install、不再启动 dev server。
设计稿 .tsx 直接落到工作区 .xcodeagent/ui-design/pages/<PageKey>/index.tsx，
前端用 DesignRenderer（同源 iframe + 预打包 antd5 runtime + sucrase 编译）
渲染。本模块只负责确保目录存在，返回工程目录供代码写入。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

UI_DESIGN_RELATIVE_DIR = ".xcodeagent/ui-design"


def ui_design_project_dir(workspace: str | Path) -> Path:
    """返回工作区下的设计稿目录路径。"""

    return Path(workspace).expanduser().resolve() / UI_DESIGN_RELATIVE_DIR


def setup_ui_design_project(workspace: str | Path) -> dict[str, Any]:
    """准备设计稿落盘目录。

    方案 B 下仅确保 .xcodeagent/ui-design 目录存在，供 persist_page_code
    写入 .tsx。不再 clone 模板、不再安装依赖、不再启动 dev server——
    渲染由前端 DesignRenderer 负责。

    返回:
        {status, project_dir, message}
        - status: "ready" | "failed"
        - project_dir: 设计稿目录绝对路径
        - message: 状态说明
    """

    root = Path(workspace).expanduser().resolve()
    project_dir = ui_design_project_dir(root)
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.exception("ui_design_dir_setup_failed dir=%s", project_dir)
        return {
            "status": "failed",
            "project_dir": str(project_dir),
            "message": f"设计稿目录创建失败：{exc}",
        }
    return {
        "status": "ready",
        "project_dir": str(project_dir),
        "message": "设计稿目录已就绪。",
    }
