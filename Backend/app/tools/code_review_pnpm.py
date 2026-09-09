"""代码审查修复专用的前端 pnpm 安装工具。"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.tools import ToolException, tool

from app.services.workspace_process_registry import workspace_process_registry


PNPM_INSTALL_TOOL_NAME = "pnpm_install_frontend"
PNPM_INSTALL_TIMEOUT_SECONDS = 180
PNPM_OUTPUT_TAIL_LIMIT = 4_000


def pnpm_install_evidence_path(workspace_root: str | Path) -> Path:
    """返回当前工作区最近一次审查安装证据文件路径。"""

    return (
        Path(workspace_root).expanduser().resolve()
        / ".xcodeagent/runtime/code-review/pnpm-install/latest.json"
    )


def read_pnpm_install_evidence(workspace_root: str | Path) -> dict[str, Any] | None:
    """读取专用工具写入的最近一次结构化安装证据。"""

    root = Path(workspace_root).expanduser().resolve()
    evidence_path = pnpm_install_evidence_path(root)
    try:
        evidence_path.resolve(strict=True).relative_to(root)
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def create_code_review_pnpm_install_tool(workspace_root: str | None):
    """创建无参数、固定工作目录和固定命令的 pnpm 安装工具。"""

    root = Path(workspace_root).expanduser().resolve() if workspace_root else None

    @tool(PNPM_INSTALL_TOOL_NAME)
    def pnpm_install_frontend() -> str:
        """在用户工作区的 frontend 目录执行固定的 pnpm install。"""

        if root is None or not root.is_dir():
            raise ToolException("代码审查 pnpm 安装缺少有效工作区。")
        frontend = root / "frontend"
        if frontend.is_symlink():
            raise ToolException("frontend 目录不能是符号链接。")
        try:
            resolved_frontend = frontend.resolve(strict=True)
            resolved_frontend.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ToolException("frontend 目录无效或超出工作区。") from exc
        package_json = resolved_frontend / "package.json"
        if package_json.is_symlink() or not package_json.is_file():
            raise ToolException("frontend/package.json 不存在或不是普通文件。")
        lockfile = resolved_frontend / "pnpm-lock.yaml"
        if lockfile.is_symlink() or (lockfile.exists() and not lockfile.is_file()):
            raise ToolException("frontend/pnpm-lock.yaml 不能是符号链接或非普通文件。")
        pnpm_command = shutil.which("pnpm")
        if not pnpm_command:
            raise ToolException("未找到 pnpm 命令。")

        resolved_runtime_root = _prepare_runtime_root(root)
        execution_id = uuid4().hex
        log_root = resolved_runtime_root / execution_id
        log_root.mkdir()
        try:
            completed = workspace_process_registry.run(
                [pnpm_command, "install"],
                workspace=root,
                cwd=resolved_frontend,
                capture_output=True,
                text=True,
                check=False,
                timeout=PNPM_INSTALL_TIMEOUT_SECONDS,
                shell=False,
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            exit_code = -1
            stdout = _subprocess_text(exc.stdout)
            stderr = _subprocess_text(exc.stderr) or "pnpm install 执行超时。"
            timed_out = True
        except OSError as exc:
            exit_code = -1
            stdout = ""
            stderr = str(exc)
            timed_out = False

        if exit_code == 0 and not _is_safe_generated_lockfile(lockfile, resolved_frontend):
            exit_code = -1
            stderr = f"{stderr}\npnpm install 未在 frontend 内生成安全的 pnpm-lock.yaml。".strip()

        stdout_path = log_root / "stdout.log"
        stderr_path = log_root / "stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        evidence = {
            "execution_id": execution_id,
            "status": "passed" if exit_code == 0 else "failed",
            "exit_code": exit_code,
            "timed_out": timed_out,
            "command": ["pnpm", "install"],
            "cwd": "frontend",
            "stdout_log": stdout_path.relative_to(root).as_posix(),
            "stderr_log": stderr_path.relative_to(root).as_posix(),
            "stdout_tail": stdout[-PNPM_OUTPUT_TAIL_LIMIT:],
            "stderr_tail": stderr[-PNPM_OUTPUT_TAIL_LIMIT:],
            "completed_at": datetime.now(UTC).isoformat(),
        }
        evidence_path = resolved_runtime_root / "latest.json"
        evidence_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if exit_code != 0:
            raise ToolException(
                "pnpm install 执行失败，详细输出已保存到代码审查运行日志。"
            )
        return json.dumps(evidence, ensure_ascii=False)

    return pnpm_install_frontend


def _subprocess_text(value: Any) -> str:
    """将超时异常中的 bytes 或文本输出统一转换为字符串。"""

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _prepare_runtime_root(root: Path) -> Path:
    """逐级创建并校验运行目录，拒绝任何可逃逸工作区的符号链接。"""

    current = root
    for part in (".xcodeagent", "runtime", "code-review", "pnpm-install"):
        candidate = current / part
        if candidate.is_symlink():
            raise ToolException("代码审查运行日志目录不能包含符号链接。")
        try:
            candidate.mkdir(exist_ok=True)
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ToolException("代码审查运行日志目录超出工作区。") from exc
        current = resolved
    return current


def _is_safe_generated_lockfile(lockfile: Path, frontend: Path) -> bool:
    """确认 pnpm 生成的是 frontend 内的普通非符号链接 lockfile。"""

    if lockfile.is_symlink() or not lockfile.is_file():
        return False
    try:
        lockfile.resolve(strict=True).relative_to(frontend)
    except (OSError, RuntimeError, ValueError):
        return False
    return True
