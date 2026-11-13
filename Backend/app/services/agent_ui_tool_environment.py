"""按本机工具配置选择 Node 和项目精确 pnpm，不读取设计模型凭据。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from app.services.workspace_process_registry import workspace_process_registry


def _corepack_home() -> Path:
    """沿 Corepack 当前公开目录约定定位缓存，支持用户现有的标准缓存设置。"""
    configured = os.environ.get("COREPACK_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    base = os.environ.get("XDG_CACHE_HOME") or os.environ.get("LOCALAPPDATA")
    directory = Path(base) if base else Path.home() / ("AppData/Local" if os.name == "nt" else ".cache")
    return directory / "node/corepack"


def _javascript_entry(command: str | None, package: str, entry: str) -> Path | None:
    """解析 npm/Homebrew/Corepack 的原生 JavaScript 入口，不执行 shell 或 cmd 包装。"""
    if not command:
        return None
    path = Path(command).resolve()
    if path.is_file() and path.suffix in {".js", ".cjs", ".mjs"}:
        return path
    for parent in (Path(command).parent, path.parent):
        candidate = parent / "node_modules" / package / entry
        if candidate.is_file():
            return candidate.resolve()
    return None


def _run_tool(argv: list[str], workspace: Path, cwd: Path, run_id: str, *, installing: bool = False) -> str:
    """通过进程 owner 执行固定工具命令，取消、超时或失败均不回显原始输出。"""
    from app.services.agent_ui_design_project import command_environment

    environment = command_environment()
    for key in ("COREPACK_HOME", "XDG_CACHE_HOME", "LOCALAPPDATA"):
        if os.environ.get(key):
            environment[key] = os.environ[key]
    environment.update({"COREPACK_ENABLE_DOWNLOAD_PROMPT": "0", "COREPACK_DEFAULT_TO_LATEST": "0"})
    try:
        result = workspace_process_registry.run(argv, workspace=workspace, run_id=run_id, cwd=cwd,
            env=environment, capture_output=True, text=True, encoding="utf-8", check=False,
            # pnpm 可能按 packageManager 分派到精确缓存版本；冷启动仍须保留有界预算。
            timeout=120 if installing else 30)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        raise ValueError("项目 pnpm 工具执行失败或已取消，请检查默认 Node、Corepack 和网络。") from None
    if result.returncode != 0:
        raise ValueError("项目 pnpm 工具执行失败，请检查 Corepack 缓存和网络。")
    return result.stdout.strip() if len(result.stdout) <= 4096 else ""


def resolve_node_executable() -> Path:
    """为全部 Agent UI 工具统一选择 Node；显式配置无效时不回退到 PATH。"""
    configured_node = os.environ.get("DEVAGENTSTUDIO_AGENT_UI_NODE_EXECUTABLE", "").strip()
    if configured_node:
        configured_path = Path(configured_node).expanduser()
        if not configured_path.is_absolute() or not configured_path.is_file():
            raise ValueError("Agent UI 配置的 Node 必须是实际存在的原生绝对文件路径。")
        executable = str(configured_path)
    else:
        executable = shutil.which("node")
    if not executable:
        raise ValueError("未找到默认 Node，请安装符合 Agent UI 工具 engines 的 Node 并重新启动应用。")
    return Path(executable).resolve()


def resolve_design_tools(workspace: Path, package_json: Path, run_id: str = "") -> tuple[Path, Path]:
    """使用统一 Node 及项目声明的精确 pnpm，不切换系统全局版本。"""
    try:
        manager = json.loads(package_json.read_text(encoding="utf-8")).get("packageManager")
    except (OSError, ValueError, AttributeError):
        raise ValueError("项目 packageManager 无法读取，请检查 package.json。") from None
    match = re.fullmatch(r"pnpm@((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))",
                        manager if isinstance(manager, str) else "")
    if match is None:
        raise ValueError("项目 packageManager 必须声明精确的 pnpm 版本。")
    node = resolve_node_executable()
    version = match[1]
    cached = _corepack_home() / "v1/pnpm" / version / "bin/pnpm.cjs"
    candidates = [cached, _javascript_entry(shutil.which("pnpm"), "pnpm", "bin/pnpm.cjs")]
    for candidate in candidates:
        if candidate and candidate.is_file() and _run_tool([str(node), str(candidate), "--version"],
                workspace, package_json.parent, run_id) == version:
            return node, candidate.resolve()
    corepack = _javascript_entry(shutil.which("corepack"), "corepack", "dist/corepack.js")
    if corepack is None:
        raise ValueError(f"未找到项目要求的 pnpm {version}，请安装 Corepack 或该版本的 pnpm。")
    _run_tool([str(node), str(corepack), "install", "--global", "--cache-only", f"pnpm@{version}"],
              workspace, package_json.parent, run_id, installing=True)
    if not cached.is_file() or _run_tool([str(node), str(cached), "--version"],
            workspace, package_json.parent, run_id) != version:
        raise ValueError(f"Corepack 未提供项目要求的 pnpm {version}。")
    return node, cached.resolve()
