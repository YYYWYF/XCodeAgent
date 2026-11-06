"""检查独立 Creator 工具环境；不安装包、读取密钥或启动 Creator 服务。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from app.services.workspace_process_registry import workspace_process_registry


_CREATOR = "@za38.03/agent-ui-creator"
_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", re.ASCII)
_PACKAGE_VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", re.ASCII)
_NODE_PROBE = (
    "process.stdout.write(JSON.stringify({nodeVersion:process.versions.node,"
    "executable:process.execPath,electronVersion:process.versions.electron??null}))"
)
ToolHostErrorCode: TypeAlias = Literal[
    "invalid_location", "invalid_node", "creator_unavailable", "creator_identity_mismatch",
    "invalid_metadata", "unsupported_engine", "node_incompatible",
    "probe_failed", "probe_timeout", "probe_cancelled",
    "start_failed", "start_timeout", "bridge_failed", "bridge_timeout", "stop_unconfirmed",
    "tool_busy", "host_closed", "binding_changed",
]
NodeRelease: TypeAlias = tuple[int, int, int]


class AgentUiToolHostError(ValueError):
    """仅返回稳定错误码和安全文案，不传播进程输出或配置内容。"""

    def __init__(self, code: ToolHostErrorCode, message: str) -> None:
        """保存供未来 AG-UI 调用方投射的基础设施错误。"""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AgentUiToolHostInspection:
    """保存一次环境预检证据，不代表 registry 验证或 Creator 启动成功。"""

    workspace: Path
    installation_root: Path
    node_executable: Path
    node_version: str
    creator_version: str
    node_engine: str
    package_json_sha256: str


def _existing_path(value: str | Path, code: ToolHostErrorCode) -> Path:
    """只接受实际存在的原生绝对路径，URL 须由调用方先正确转换。"""
    try:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("absolute path required")
        return path.resolve(strict=True)
    except (OSError, ValueError, RuntimeError, TypeError):
        raise AgentUiToolHostError(code, "工具环境路径无效或不存在，请提供原生绝对路径。") from None


def _independent_root(value: str | Path, workspace: Path) -> Path:
    """保护目标工程，工具安装不能与目标目录重叠。"""
    root = _existing_path(value, "invalid_location")
    if not root.is_dir() or root.is_relative_to(workspace) or workspace.is_relative_to(root):
        raise AgentUiToolHostError("invalid_location", "工具安装必须使用独立于目标工程的目录。")
    return root


def _release(value: str) -> NodeRelease:
    """解析稳定 Node 三段版本；不把预发布版隐式当作正式版。"""
    match = _VERSION.fullmatch(value)
    if match is None:
        raise AgentUiToolHostError("invalid_node", "无法核验 Node 稳定版本。")
    return int(match[1]), int(match[2]), int(match[3])


def _engine_bounds(engine: object) -> list[tuple[NodeRelease, NodeRelease]]:
    """仅支持当前 Creator 使用的正 major caret 并集，不猜测其他 semver 语法。"""
    if not isinstance(engine, str) or not engine.strip():
        raise AgentUiToolHostError("unsupported_engine", "Creator 缺少可核验的 Node engines。")
    bounds: list[tuple[NodeRelease, NodeRelease]] = []
    for clause in engine.split("||"):
        match = re.fullmatch(r"\^([1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", clause.strip(), re.ASCII)
        if match is None:
            raise AgentUiToolHostError("unsupported_engine", "当前工具预检不支持该 Node engines 语法，需审阅后适配。")
        lower = int(match[1]), int(match[2]), int(match[3])
        bounds.append((lower, (lower[0] + 1, 0, 0)))
    return bounds


def _creator_metadata(installation: Path, version: str) -> tuple[str, str]:
    """只读取公开 package.json，核对计划版本并拒绝本地源码依赖。"""
    if not isinstance(version, str) or _PACKAGE_VERSION.fullmatch(version) is None:
        raise AgentUiToolHostError("creator_identity_mismatch", "Creator 预检必须绑定明确的精确包版本。")
    package = _existing_path(installation / "node_modules/@za38.03/agent-ui-creator/package.json", "creator_unavailable")
    if not package.is_relative_to(installation):
        raise AgentUiToolHostError("invalid_location", "Creator 包不能链接到工具安装目录外的源码。")
    try:
        with package.open("rb") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("metadata limit")
        metadata = json.loads(raw)
        if not isinstance(metadata, dict):
            raise ValueError("object required")
    except (OSError, ValueError, UnicodeError):
        raise AgentUiToolHostError("invalid_metadata", "Creator 公开包清单不可读取或结构无效。") from None
    if metadata.get("name") != _CREATOR or metadata.get("version") != version:
        raise AgentUiToolHostError("creator_identity_mismatch", "Creator 实际安装身份与审阅版本不一致。")
    exports = metadata.get("exports")
    if not isinstance(exports, dict) or "." not in exports or exports.get("./package.json") != "./package.json":
        raise AgentUiToolHostError("invalid_metadata", "Creator 缺少当前要求的公开包入口。")
    for field in ("dependencies", "optionalDependencies"):
        dependencies = metadata.get(field, {})
        if not isinstance(dependencies, dict) or any(
            not isinstance(specifier, str)
            or specifier.startswith(("workspace:", "file:", "link:", "portal:", ".", "/", "\\"))
            or re.match(r"^[A-Za-z]:[\\/]", specifier) is not None
            for specifier in dependencies.values()
        ):
            raise AgentUiToolHostError("invalid_metadata", "Creator 必须使用成品包依赖，不能包含本地源码引用。")
    engines = metadata.get("engines")
    engine = engines.get("node") if isinstance(engines, dict) else None
    _engine_bounds(engine)
    return str(engine), hashlib.sha256(raw).hexdigest()


def _probe_node(node: Path, installation: Path, workspace: Path, run_id: str, timeout: float) -> str:
    """经既有工作区进程栅栏探测 Node，固定脚本不加载包或继承密钥。"""
    environment = {key: value for key, value in os.environ.items()
                   if key in {"PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}}
    try:
        completed = workspace_process_registry.run(
            [str(node), "--eval", _NODE_PROBE], workspace=workspace, run_id=run_id,
            cwd=installation, env=environment, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise AgentUiToolHostError("probe_timeout", "Node 探测超时，已交由工作区进程设施回收。") from None
    except RuntimeError:
        raise AgentUiToolHostError("probe_cancelled", "工作区或当前运行已停止，Node 探测已取消。") from None
    except OSError:
        raise AgentUiToolHostError("probe_failed", "无法执行所选 Node，请检查可执行文件和权限。") from None
    if completed.returncode != 0:
        raise AgentUiToolHostError("probe_failed", "所选 Node 探测失败，未取得环境身份。")
    try:
        if len(completed.stdout) > 4096:
            raise ValueError("probe failed")
        evidence = json.loads(completed.stdout)
        if not isinstance(evidence, dict) or set(evidence) != {"nodeVersion", "executable", "electronVersion"}:
            raise ValueError("invalid evidence")
        version = evidence["nodeVersion"]
        if not isinstance(version, str) or evidence["electronVersion"] is not None:
            raise ValueError("not standalone Node")
        executable = evidence["executable"]
        if not isinstance(executable, str) or _existing_path(executable, "invalid_node") != node:
            raise ValueError("executable mismatch")
        _release(version)
        return version
    except (ValueError, TypeError, OSError):
        raise AgentUiToolHostError("invalid_node", "所选进程未提供匹配的独立 Node 身份证据。") from None


def inspect_agent_ui_tool_host(
    *, workspace: str | Path, installation_root: str | Path,
    node_executable: str | Path, creator_version: str, run_id: str = "", timeout_seconds: float = 5.0,
) -> AgentUiToolHostInspection:
    """只读核验独立工具环境；每次调用重新读取身份，不执行安装或 Creator 模型请求。"""
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise AgentUiToolHostError("probe_failed", "Node 探测必须使用有限的正超时预算。")
    target = _existing_path(workspace, "invalid_location")
    if not target.is_dir():
        raise AgentUiToolHostError("invalid_location", "目标工程必须是现有目录。")
    installation = _independent_root(installation_root, target)
    node = _existing_path(node_executable, "invalid_node")
    if not node.is_file() or node.name.lower() not in {"node", "node.exe"}:
        raise AgentUiToolHostError("invalid_node", "必须显式指定独立 Node 可执行文件。")
    engine, digest = _creator_metadata(installation, creator_version)
    version = _probe_node(node, installation, target, run_id, timeout_seconds)
    release = _release(version)
    if not any(lower <= release < upper for lower, upper in _engine_bounds(engine)):
        raise AgentUiToolHostError("node_incompatible", f"当前 Node {version} 不满足 Creator engines {engine}。")
    return AgentUiToolHostInspection(target, installation, node, version, creator_version, engine, digest)
