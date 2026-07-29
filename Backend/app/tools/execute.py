"""Standalone shell execution tool for code-generation agents.

The deepagents ``FilesystemMiddleware`` does not support ``SandboxBackendProtocol``
backends when ``FilesystemPermission`` rules are in use — it raises
``NotImplementedError``.  Rather than pushing execute into the backend layer,
this tool uses ``subprocess.run`` directly in the workspace root, bypassing the
middleware permission system entirely.

This is intentionally NOT a ``SandboxBackendProtocol.execute()`` implementation —
it's a standalone tool passed via ``create_deep_agent(tools=[...])``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

WORKSPACE_ROOT: str | None = None
"""Injected workspace root — set by :func:`create_execute_tool` once per agent."""


class ExecuteInput(BaseModel):
    command: str = Field(
        description=(
            "Shell command to execute in the workspace root directory. "
            "Use absolute paths or prefix frontend commands with 'cd frontend &&'. "
            "Prefer the repository's declared package manager, local dependencies, and package scripts. "
            "Run checks directly without output-truncating or success-forcing constructs such as "
            "'| head', '| tail', '|| true', or '; true', because they can hide the real exit code. "
            "Examples: 'cd frontend && pnpm typecheck', "
            "'cd frontend && pnpm build', 'pnpm test'."
        )
    )
    timeout: int | None = Field(
        default=None,
        description="Optional timeout in seconds. Defaults to no timeout.",
    )


def create_execute_tool(workspace_root: str | None):
    """Create an `execute` shell tool bound to *workspace_root*."""

    cwd = str(Path(workspace_root).resolve()) if workspace_root else str(Path.cwd())

    @tool("execute", args_schema=ExecuteInput)
    def execute_command(command: str, timeout: int | None = None) -> str:
        """Execute a shell command in the workspace root directory.

        Use this tool to run build, typecheck, lint, test, and dev-server
        commands.  Do NOT create temporary .sh/.js/.py scripts — call this
        tool directly instead. Use the returned exit_code, stdout, and stderr
        to diagnose failures, fix relevant code, and rerun the failed check.

        Commands execute in the workspace's frontend/ directory by default.
        Long-running commands like dev servers may time out; use timeout=0
        for those if needed.
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=timeout,
            )
            return json.dumps(
                {
                    "tool": "execute",
                    "exit_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                ensure_ascii=False,
            )
        except subprocess.TimeoutExpired:
            return json.dumps(
                {
                    "tool": "execute",
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"Command timed out after {timeout}s: {command}",
                },
                ensure_ascii=False,
            )
        except OSError as exc:
            return json.dumps(
                {
                    "tool": "execute",
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": str(exc),
                },
                ensure_ascii=False,
            )

    return execute_command
