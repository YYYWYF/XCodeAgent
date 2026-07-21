"""
Auto-dedup FilesystemBackend wrapper.

When `write()` encounters "already exists", instead of returning an error
it **overwrites** the existing file.  This is the correct behaviour for
code-generation agents that may re-write the same path multiple times
(e.g. first draft then refined version).  The previous dedup behaviour
(appending _2, _3, …) created confusing duplicate files like
index.tsx / index_2.tsx / index_3.tsx.

Also patches `awrite()` with the same logic.
"""

from __future__ import annotations

import logging
import os

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol, WriteResult

logger = logging.getLogger(__name__)


class AutoDedupFilesystemBackend(FilesystemBackend):
    """FilesystemBackend that overwrites on write conflict instead of erroring.

    The base ``FilesystemBackend.write()`` refuses to overwrite an existing
    file, returning an error message.  In a code-generation workflow the agent
    frequently needs to re-write the same path (draft → refine, or task retry).
    Overwriting is the expected behaviour; the last write wins.
    """

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
