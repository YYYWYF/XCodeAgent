"""为用户工程命令解析并锁定本轮 Node 和包管理器。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from _thread import RLock
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4
from weakref import WeakValueDictionary

from app.services.workspace_process_registry import workspace_process_registry
from app.utils.subprocess_output import subprocess_output_text

FRONTEND_NODE_FILE = "frontend-node.json"
FRONTEND_DEPENDENCIES_NODE_FILE = "frontend-dependencies-node.json"
_NODE_EXPRESSION = "JSON.stringify([process.execPath, process.version])"
_PROBE_TIMEOUT = 20
_operation_locks: WeakValueDictionary[str, RLock] = WeakValueDictionary()
_operation_locks_guard = threading.Lock()


class FrontendNodeError(RuntimeError):
    """表示用户工程的 Node 工具链缺失或不一致。"""


@dataclass(frozen=True)
class FrontendNodeRuntime:
    """保存本轮 Node 的真实路径、版本和独立子进程环境。"""

    executable: Path
    version: str
    source: str
    env: Mapping[str, str]

    def evidence(self) -> dict[str, str]:
        """返回可安全记录到启动或测试证据的运行时身份。"""
        return {"path": str(self.executable), "version": self.version, "source": self.source}

    def describe(self) -> str:
        """格式化当前 Node，供诊断和运行日志使用。"""
        return f"Node {self.version} ({self.source}) {self.executable}"

    def matches(self, evidence: Any) -> bool:
        """用真实路径与完整版本匹配既有运行时证据。"""
        return (
            isinstance(evidence, dict)
            and isinstance(evidence.get("path"), str)
            and os.path.normcase(evidence["path"]) == os.path.normcase(str(self.executable))
            and evidence.get("version") == self.version
        )

    def command(self, name: str) -> str:
        """在选定 Node 的 PATH 中查找包管理器，保留其启动脚本入口。

        Windows 上 Corepack 会把 pnpm.CMD / yarn.CMD 等 shim 装进 Node 安装目录，
        任何子命令都会先触发 Corepack 联网解析版本；企业内网无法访问
        registry.npmjs.org 时这一步必然失败。这里扫描 PATH 中所有候选，
        优先返回非 Corepack shim 的真实可执行文件；找不到时退而返回第一个候选。
        """
        candidates = _path_commands_all(name, self.env["PATH"])
        if not candidates:
            raise FrontendNodeError(f"未找到包管理器命令：{name}。当前选择 {self.describe()}。")
        for candidate in candidates:
            if not _is_corepack_shim(candidate):
                return candidate
        return candidates[0]

    def verify(
        self, command: str, *, workspace: Path, cwd: Path, npx: bool = False, before_install: bool = False,
    ) -> None:
        """从包管理器的脚本环境检查实际 Node，发现被项目或启动器替换时显式失败。"""
        args = ["--offline", "-c", f'node -p "{_NODE_EXPRESSION}"'] if npx else [
            "exec", "node", "-p", _NODE_EXPRESSION,
        ]
        try:
            if before_install and Path(command).stem.lower() == "yarn":
                actual_path, actual_version = _probe_yarn_launcher(command, self.env, workspace, cwd)
            else:
                actual_path, actual_version = _probe_node([command, *args], self.env, workspace, cwd)
        except (FrontendNodeError, OSError) as exc:
            raise FrontendNodeError(f"无法核对包管理器的 Node。当前选择 {self.describe()}。{exc}") from exc
        if not self.matches({"path": str(actual_path), "version": actual_version}):
            raise FrontendNodeError(
                f"包管理器使用 Node {actual_version} {actual_path}，与当前选择的 {self.describe()} 不一致。"
                "请检查包管理器路径及项目的 Node 版本配置。"
            )


def frontend_package_manager(cwd: Path, package_json: dict[str, Any]) -> str:
    """优先采用用户声明的受支持包管理器，否则沿用锁文件与默认值。"""
    declaration = package_json.get("packageManager")
    if isinstance(declaration, str):
        name, _, version = declaration.partition("@")
        if name in {"pnpm", "yarn"} and version:
            return name
    if (cwd / "pnpm-lock.yaml").is_file():
        return "pnpm"
    return "yarn" if (cwd / "yarn.lock").is_file() else "pnpm"


def resolve_frontend_node(workspace: Path) -> FrontendNodeRuntime:
    """按 Windows NVM、macOS NVM 或继承的 PATH 锁定一次操作的 Node。"""
    env = dict(os.environ)
    windows = sys.platform == "win32"
    nvm_key = "NVM_SYMLINK" if windows else "NVM_BIN"
    configured = env.get(nvm_key, "")
    node_name = "node.exe" if windows else "node"
    source = nvm_key if configured else "PATH"
    if configured:
        candidate = Path(configured) / node_name
        if not candidate.is_absolute() or not candidate.is_file():
            raise FrontendNodeError(f"{nvm_key} 指向的 Node 无效：{candidate}。请先执行 nvm use 选择已安装版本。")
    else:
        located = _path_command(node_name, env.get("PATH", os.defpath))
        if not located:
            raise FrontendNodeError("未在 PATH 中找到 Node。请安装 Node 或使用 nvm use 选择已安装版本。")
        candidate = Path(located)
    try:
        executable = candidate.resolve(strict=True)
        actual_path, version = _probe_node([str(executable), "-p", _NODE_EXPRESSION], env, workspace, workspace)
    except (OSError, RuntimeError) as exc:
        raise FrontendNodeError(f"无法使用 {source} 选择的 Node {candidate}。{exc}") from exc
    if configured and actual_path != executable:
        raise FrontendNodeError(f"{nvm_key} 选择的 Node {executable} 实际转发到 {actual_path}。请检查 nvm 配置。")

    # Windows 的 NVM 链接会随 nvm use 变化，子进程只保留选定版本的真实目录。
    excluded = {os.path.normcase(str(actual_path.parent))}
    if configured:
        excluded.add(os.path.normcase(os.path.normpath(configured)))
    entries = env.get("PATH", os.defpath).split(os.pathsep)
    env["PATH"] = os.pathsep.join([
        str(actual_path.parent),
        *(entry for entry in entries if entry and os.path.normcase(os.path.normpath(entry.strip('"'))) not in excluded),
    ])
    return FrontendNodeRuntime(actual_path, version, source, env)


def _path_command(name: str, path: str) -> str | None:
    """在 Windows 只按指定 PATH 查询，避免 shutil.which 额外搜索 Studio 工作目录。"""
    if sys.platform != "win32":
        return shutil.which(name, path=path)
    for entry in path.split(os.pathsep):
        if entry:
            command = shutil.which(str(Path(entry.strip('"')).absolute() / name))
            if command:
                return command
    return None


def _path_commands_all(name: str, path: str) -> list[str]:
    """按 PATH 顺序收集所有命中该命令的候选路径，保持原有 Windows 查询语义。"""
    if sys.platform != "win32":
        located = shutil.which(name, path=path)
        return [located] if located else []
    seen: set[str] = set()
    candidates: list[str] = []
    for entry in path.split(os.pathsep):
        if not entry:
            continue
        command = shutil.which(str(Path(entry.strip('"')).absolute() / name))
        if command:
            key = os.path.normcase(command)
            if key not in seen:
                seen.add(key)
                candidates.append(command)
    return candidates


def _is_corepack_shim(command: str) -> bool:
    """识别 Windows 上的 Corepack shim：脚本内容里显式引用 corepack。

    Corepack 在 Node 安装目录写入 pnpm.CMD / yarn.CMD 等批处理脚本，前几行
    就会出现 "corepack" 字样（调用 corepack.js 或 node corepack）。真实安装
    的 pnpm（如 npm i -g pnpm 落在 %APPDATA%\\npm）脚本不含此引用。
    仅 Windows 需要此判定；非 Windows 平台返回 False。
    """
    if sys.platform != "win32":
        return False
    try:
        head = Path(command).read_text(encoding="utf-8", errors="ignore")[:2048]
    except OSError:
        return False
    return "corepack" in head.lower()


def _probe_yarn_launcher(
    command: str, env: Mapping[str, str], workspace: Path, cwd: Path,
) -> tuple[Path, str]:
    """Yarn exec 需要可用的依赖状态，安装前先检查 Yarn 启动脚本实际调用的 Node。"""
    with TemporaryDirectory(prefix="studio-node-") as directory:
        probe = Path(directory) / "probe.cjs"
        probe.write_text(
            f"require('fs').writeSync(1, {_NODE_EXPRESSION} + '\\n'); process.exit(0);",
            encoding="utf-8",
        )
        quoted_probe = json.dumps(probe.as_posix(), ensure_ascii=False)
        node_options = f'{env.get("NODE_OPTIONS", "")} --require {quoted_probe}'.strip()
        return _probe_node([command, "--version"], {**env, "NODE_OPTIONS": node_options}, workspace, cwd)


def _probe_node(
    argv: list[str], env: Mapping[str, str], workspace: Path, cwd: Path,
) -> tuple[Path, str]:
    """读取短命令返回的实际 Node 路径与版本，并复用工作区进程取消边界。"""
    try:
        completed = workspace_process_registry.run(
            argv, workspace=workspace, cwd=str(cwd), env=env, capture_output=True,
            timeout=_PROBE_TIMEOUT, check=False,
        )
        if completed.returncode != 0:
            detail = (subprocess_output_text(completed.stderr) or subprocess_output_text(completed.stdout)).strip()
            raise FrontendNodeError(detail[-1200:] or f"Node 探测退出码 {completed.returncode}。")
        lines = subprocess_output_text(completed.stdout).strip().splitlines()
        path, version = json.loads(lines[-1]) if lines else (None, None)
        if not isinstance(path, str) or not isinstance(version, str) or not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-+][\w.-]+)?", version):
            raise ValueError("Node 探测结果无效")
        return Path(path).resolve(strict=True), version
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        raise FrontendNodeError(f"Node 探测失败。{exc}") from exc


def read_node_record(path: Path) -> dict[str, Any]:
    """读取本轮前端进程或依赖安装的运行时记录。"""
    try:
        if path.is_symlink():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_node_record(path: Path, value: dict[str, Any]) -> None:
    """原子写入运行时记录，避免并发读取看到半份证据。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".frontend-node-{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def has_matching_frontend_dependencies(runtime_root: Path, cwd: Path, node: FrontendNodeRuntime) -> bool:
    """验证已安装依赖属于相同工程和 Node。"""
    return node.matches(frontend_dependencies_node(runtime_root, cwd))


def frontend_dependencies_node(runtime_root: Path, cwd: Path) -> dict[str, Any] | None:
    """读取当前工程最近一次成功安装依赖所使用的 Node。"""
    record = read_node_record(runtime_root / FRONTEND_DEPENDENCIES_NODE_FILE)
    node = record.get("nodeRuntime")
    return node if record.get("cwd") == str(cwd) and isinstance(node, dict) else None


def record_frontend_dependencies(runtime_root: Path, cwd: Path, node: FrontendNodeRuntime | None) -> None:
    """安装前清除旧证据，安装和 Node 校验通过后才发布新证据。"""
    path = runtime_root / FRONTEND_DEPENDENCIES_NODE_FILE
    if node is None:
        path.unlink(missing_ok=True)
    else:
        write_node_record(path, {"cwd": str(cwd), "nodeRuntime": node.evidence()})


def frontend_build_check(state: dict[str, Any]) -> dict[str, Any]:
    """读取本轮前端构建事实，供独立性能检查核对 Node。"""
    return next(
        (item for item in state.get("test_results", [])
         if isinstance(item, dict) and item.get("id") == "frontend_build"),
        {},
    )


def frontend_operation_lock(workspace: Path) -> RLock:
    """串行化同一工作区的安装与使用，防止另一轮覆盖依赖证据。"""
    key = os.path.normcase(str(workspace.resolve()))
    with _operation_locks_guard:
        lock = _operation_locks.get(key)
        if lock is None:
            lock = RLock()
            _operation_locks[key] = lock
        return lock
