"""为冻结后端保留原生语法加载的子进程隔离，避免把 EXE 当作 Python 解释器。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from app.utils.subprocess_platform import WINDOWS_CREATE_NO_WINDOW


PARSER_PROBE_ARGUMENT = "--devagentstudio-parser-probe"
logger = logging.getLogger(__name__)


def run_frozen_parser_probe(grammar: str, timeout_seconds: float) -> bool:
    """通过冻结入口的专用模式探测语法，保留 CRG 的超时和失败语义。"""

    try:
        completed = subprocess.run(
            [sys.executable, PARSER_PROBE_ARGUMENT, grammar],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
            creationflags=WINDOWS_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("tree-sitter parser probe failed for %s: %s", grammar, exc)
        return False
    return completed.returncode == 0


def dispatch_parser_probe(arguments: list[str]) -> None:
    """在加载配置和 HTTP 服务前分流探测子进程，完成后立即退出。"""

    if not arguments or arguments[0] != PARSER_PROBE_ARGUMENT:
        return
    if len(arguments) != 2 or not arguments[1].strip():
        raise SystemExit(2)

    from tree_sitter_language_pack import get_parser

    # 仅加载指定语法，不执行传入代码；原生崩溃仍被隔离在探测进程内。
    get_parser(arguments[1])
    raise SystemExit(0)
