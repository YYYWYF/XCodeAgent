"""验证 Runtime 模板真实入口与策略，不启动进程、不安装依赖或执行业务 SQL。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.agent_runtime_template_policy import load_agent_runtime_template_policy
from app.services.workspace_bootstrap.models import WorkspaceBootstrapReadinessError


def validate_runtime_entrypoints(workspace: Path) -> None:
    """验证锁文件、受控启动入口、七模块路径和路由描述，拒绝空壳 Runtime。"""

    runtime = workspace / "agent-runtime"
    if runtime.is_symlink() or not runtime.is_dir():
        raise WorkspaceBootstrapReadinessError("Runtime 工程必须是工作区内真实目录，不能是符号链接。")
    project_path = _require_file(runtime, Path("pyproject.toml"))
    _require_file(runtime, Path("uv.lock"))
    try:
        project = tomllib.loads(project_path.read_text(encoding="utf-8"))
        entrypoint = project.get("project", {}).get("scripts", {}).get("agent-runtime")
    except (OSError, UnicodeError, ValueError, AttributeError) as exc:
        raise WorkspaceBootstrapReadinessError("Runtime pyproject.toml 无法读取或结构无效。") from exc
    if not isinstance(entrypoint, str) or re.fullmatch(
        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", entrypoint,
    ) is None:
        raise WorkspaceBootstrapReadinessError("Runtime 缺少合法 project.scripts.agent-runtime 启动入口。")
    module = entrypoint.split(":", 1)[0]
    source = Path("src", *module.split("."))
    relative = source.with_suffix(".py") if (runtime / source.with_suffix(".py")).exists() else source / "__init__.py"
    _require_file(runtime, relative)
    try:
        load_agent_runtime_template_policy(runtime)
    except ValueError as exc:
        raise WorkspaceBootstrapReadinessError(str(exc)) from exc
    descriptor = _require_file(workspace, WORKSPACE_ARTIFACT_DIR / "template-contracts/route-projector.json")
    try:
        route_contract = json.loads(descriptor.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise WorkspaceBootstrapReadinessError("Runtime 模板 Route Projector Descriptor 无法读取。") from exc
    if not isinstance(route_contract, dict) or not route_contract:
        raise WorkspaceBootstrapReadinessError("Runtime 模板 Route Projector Descriptor 必须是非空对象。")


def _require_file(root: Path, relative: Path) -> Path:
    """拒绝源文件及中间目录链接，避免通过模板入口跳出本次工作区。"""

    target = root
    for part in relative.parts:
        target = target / part
        if target.is_symlink():
            raise WorkspaceBootstrapReadinessError(f"Runtime 模板路径不能包含符号链接：{relative.as_posix()}。")
    if not target.is_file():
        raise WorkspaceBootstrapReadinessError(f"Runtime 模板缺少真实文件：{relative.as_posix()}。")
    return target
