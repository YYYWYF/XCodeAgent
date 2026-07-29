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

# 禁止代码生成 agent 在工作区创建的临时脚本扩展名。
# 这类脚本（run_tsc.sh / run_check.py / run_tsc.js 等）是 agent 为了跑 tsc/build 验证而违规创建的，
# 正确做法是直接用 `execute` 工具跑命令。见 frontend-template-modification-boundary 技能。
# 注意：agent 会换扩展名绕过（.sh 被拦就改 .js），所以这里把常见脚本扩展名一并拦掉。
BLOCKED_SCRIPT_EXTENSIONS = (".sh", ".bash", ".ps1", ".bat", ".py", ".js", ".mjs", ".cjs")

# 这些是模板工程自带的合法配置文件（已存在），即使扩展名命中也不应被当作临时脚本拦截。
# 它们本就存在，write 会走"已存在"分支；这里列出仅作双保险，避免边界误伤。
ALLOWED_CONFIG_FILENAMES = frozenset({
    "postcss.config.js",
    "tailwind.config.js",
    "postcss.config.mjs",
    "tailwind.config.mjs",
    "postcss.config.cjs",
    "tailwind.config.cjs",
})

_BLOCKED_SCRIPT_MESSAGE = (
    "Creating script files ({ext}) is forbidden in the workspace. "
    "Do NOT create .sh/.py/.js/.mjs/.cjs/.bash scripts to run build or typecheck commands. "
    "Use the `execute` tool directly instead, e.g. execute(command=\"cd frontend && npx tsc --noEmit\") "
    "or execute(command=\"cd frontend && pnpm run build\"). The execute tool returns "
    "{{exit_code, stdout, stderr}} — read them directly, no wrapper script needed."
)


def _blocked_script_extension(file_path: str) -> str | None:
    """Return the matched blocked extension if *file_path* is a temp script, else None."""
    filename = file_path.rsplit("/", 1)[-1]
    if filename in ALLOWED_CONFIG_FILENAMES:
        return None
    lower = filename.lower()
    for ext in BLOCKED_SCRIPT_EXTENSIONS:
        if lower.endswith(ext):
            return ext
    return None


def _blocked_script_error(file_path: str) -> str:
    """Build the error message returned when a blocked script write is attempted."""
    ext = _blocked_script_extension(file_path) or ""
    return _BLOCKED_SCRIPT_MESSAGE.format(ext=ext)


class AutoDedupFilesystemBackend(FilesystemBackend):
    """FilesystemBackend that overwrites on write conflict instead of erroring.

    The base ``FilesystemBackend.write()`` refuses to overwrite an existing
    file, returning an error message.  In a code-generation workflow the agent
    frequently needs to re-write the same path (draft → refine, or task retry).
    Overwriting is the expected behaviour; the last write wins.

    Also blocks creation of temporary script files (.sh/.py/.bash/.ps1/.bat)
    anywhere in the workspace — agents must use the `execute` tool to run
    build/typecheck commands instead of writing wrapper scripts.
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
        # 拦截临时脚本文件：agent 应直接用 execute 工具跑命令，而非写 .sh/.py 脚本。
        if _blocked_script_extension(file_path):
            return WriteResult(error=_blocked_script_error(file_path))

        # Try the original path first (handles new files)
        result = super().write(file_path, content)
        if result.error is None:
            return result

        # File already exists — overwrite instead of dedup
        logger.debug("AutoDedupFilesystemBackend: overwriting existing file %s", file_path)
        return self._overwrite(file_path, content)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        if _blocked_script_extension(file_path):
            return WriteResult(error=_blocked_script_error(file_path))

        result = await super().awrite(file_path, content)
        if result.error is None:
            return result

        logger.debug("AutoDedupFilesystemBackend: overwriting existing file %s", file_path)
        return self._overwrite(file_path, content)
