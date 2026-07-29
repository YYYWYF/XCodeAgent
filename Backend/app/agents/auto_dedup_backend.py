"""
Auto-dedup FilesystemBackend wrapper with shell execution support.

When `write()` encounters "already exists", instead of returning an error
it **overwrites** the existing file.  This is the correct behaviour for
code-generation agents that may re-write the same path multiple times
(e.g. first draft then refined version).  The previous dedup behaviour
(appending _2, _3, …) created confusing duplicate files like
index.tsx / index_2.tsx / index_3.tsx.

Also implements ``SandboxBackendProtocol`` so that the deepagents ``execute``
tool works for running shell commands (tsc, pnpm, etc.) directly on the host.
"""

from __future__ import annotations

import logging
import os
import subprocess

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol, ExecuteResponse, SandboxBackendProtocol, WriteResult

logger = logging.getLogger(__name__)


class AutoDedupFilesystemBackend(FilesystemBackend, SandboxBackendProtocol):
    """FilesystemBackend that overwrites on write conflict and executes shell commands.

    The base ``FilesystemBackend.write()`` refuses to overwrite an existing
    file, returning an error message.  In a code-generation workflow the agent
    frequently needs to re-write the same path (draft → refine, or task retry).
    Overwriting is the expected behaviour; the last write wins.

    Implements ``SandboxBackendProtocol.execute()`` to run shell commands
    (e.g. ``pnpm run build``, ``npx tsc --noEmit``) directly on the host in
    the workspace root directory.  This stops agents from polluting the
    workspace with temporary ``run_tsc*.js`` / ``*.sh`` scripts.
    """

    @property
    def id(self) -> str:
        """Unique identifier — uses workspace root path."""
        return f"host-{self.cwd}"

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Run a shell command on the host in the workspace root directory.

        Args:
            command: Shell command string to execute.
            timeout: Optional timeout in seconds (forwarded to subprocess.run).
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(self.cwd),
                timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                if output:
                    output += "\n"
                output += result.stderr
            return ExecuteResponse(
                output=output,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Command timed out after {timeout}s: {command}",
                exit_code=-1,
            )
        except OSError as exc:
            return ExecuteResponse(
                output=f"Failed to execute command: {exc}",
                exit_code=-1,
            )

    def _overwrite(self, file_path: str, content: str) -> WriteResult:
        """Overwrite *file_path* with *content*, creating it if necessary."""
        try:
            resolved = self._resolve_path(file_path)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(resolved, flags, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            return WriteResult(path=file_path)
        except (OSError, UnicodeEncodeError) as e:
            return WriteResult(error=f"Error writing file '{file_path}': {e}")

    def write(self, file_path: str, content: str) -> WriteResult:
        # Try the original path first (handles new files)
        result = super().write(file_path, content)
        if result.error is None:
            return result

        # File already exists — overwrite instead of dedup
        logger.debug("AutoDedupFilesystemBackend: overwriting existing file %s", file_path)
        return self._overwrite(file_path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        result = await super().awrite(file_path, content)
        if result.error is None:
            return result

        logger.debug("AutoDedupFilesystemBackend: overwriting existing file %s", file_path)
        return self._overwrite(file_path, content)
