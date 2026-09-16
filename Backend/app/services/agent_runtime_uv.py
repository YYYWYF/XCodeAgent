"""解析本机 uv，并在缺失时用官方脚本安装。"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.utils.subprocess_output import subprocess_output_text


UV_INSTALL_TIMEOUT_SECONDS = 180
_UV_INSTALL_SCRIPT_URL = "https://astral.sh/uv/install.sh"
_UV_INSTALL_PS1_URL = "https://astral.sh/uv/install.ps1"


def resolve_uv_command() -> str | None:
    """在 PATH 和官方默认安装位置查找 uv 可执行文件。"""

    found = shutil.which("uv") or shutil.which("uv.exe")
    if found:
        return found
    for candidate in _uv_well_known_paths():
        if not candidate.is_file():
            continue
        if os.name == "nt" or os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def install_uv_with_official_script(*, runtime_root: Path) -> dict[str, Any]:
    """用官方安装脚本安装 uv，并把输出写入既有 install 日志文件。"""

    runtime_root.mkdir(parents=True, exist_ok=True)
    argv = _uv_install_argv()
    started_at = datetime.now(UTC).isoformat()
    stdout = ""
    stderr = ""
    returncode: int | None = None
    timed_out = False
    error: str | None = None
    if argv is None:
        stderr = "未找到可用于安装 uv 的 Shell。"
        error = stderr
    else:
        try:
            completed = subprocess.run(
                argv,
                text=True,
                capture_output=True,
                timeout=UV_INSTALL_TIMEOUT_SECONDS,
                check=False,
            )
            stdout = subprocess_output_text(completed.stdout)
            stderr = subprocess_output_text(completed.stderr)
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = subprocess_output_text(exc.stdout)
            stderr = subprocess_output_text(exc.stderr)
            timed_out = True
        except OSError as exc:
            stderr = str(exc)
            error = str(exc)
    stdout_path = runtime_root / "agent-runtime-install.stdout.log"
    stderr_path = runtime_root / "agent-runtime-install.stderr.log"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    return {
        "argv": argv or [],
        "returncode": returncode,
        "timed_out": timed_out,
        "error": error,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "succeeded": returncode == 0 and not timed_out and error is None,
    }


def _uv_install_argv() -> list[str] | None:
    """构造当前操作系统上的官方 uv 安装命令。"""

    if os.name == "nt":
        powershell = (
            shutil.which("pwsh")
            or shutil.which("pwsh.exe")
            or shutil.which("powershell")
            or shutil.which("powershell.exe")
        )
        if not powershell:
            return None
        return [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"irm {_UV_INSTALL_PS1_URL} | iex",
        ]
    curl = shutil.which("curl") or "curl"
    return [
        "sh",
        "-c",
        f"{curl} -LsSf {_UV_INSTALL_SCRIPT_URL} | sh",
    ]


def _uv_well_known_paths() -> list[Path]:
    """返回官方安装脚本常用的 uv 落点。"""

    local_bin = Path.home() / ".local" / "bin"
    if os.name == "nt":
        return [local_bin / "uv.exe", local_bin / "uv"]
    return [local_bin / "uv"]
