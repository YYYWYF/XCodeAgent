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
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol, WriteResult, EditResult
from app.services.inclusion_rate import capture_inclusion_before, report_inclusion_rate

logger = logging.getLogger(__name__)


@dataclass
class RepairCreation:
    """记录当前修复工具是否实际完成了已批准的排他创建。"""

    path: Path
    scope: frozenset[Path]
    written: bool = False


_REPAIR_CREATION: ContextVar[RepairCreation | None] = ContextVar("repair_creation", default=None)


@contextmanager
def repair_create_only_scope(path: Path, scope: frozenset[Path]):
    """在一次批准后的修复调用内绑定文件范围和只能创建的目标。"""
    creation = RepairCreation(path, scope)
    token = _REPAIR_CREATION.set(creation)
    try:
        yield creation
    finally:
        _REPAIR_CREATION.reset(token)


def repair_write_policy(root: Path, file_path: str, resolved: Path) -> str:
    """按真实路径限制本轮修复的写入，拒绝通过链接切换写入目标。"""
    creation = _REPAIR_CREATION.get()
    if creation is None:
        return "normal"
    declared = root / file_path.lstrip("/\\")
    if declared != resolved or declared not in creation.scope:
        return "denied"
    return "create_only" if declared == creation.path else "normal"

# 禁止代码生成 agent 在工作区创建的临时脚本扩展名。
# 这类脚本（run_tsc.sh / run_check.py / run_tsc.js 等）是 agent 为了跑 tsc/build 验证而违规创建的，
# 项目级验证应由外层 integration-test 阶段统一执行。见 frontend-template-modification-boundary 技能。
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
    "Do NOT create .sh/.py/.js/.mjs/.cjs/.bash scripts or run project-level build/typecheck commands "
    "from an owner task. The outer integration-test phase owns repository verification; report any "
    "missing dependency or command instead."
)

_WINDOWS_EXTENDED_PREFIX = "\\\\?\\"
_WINDOWS_EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"


def _windows_comparison_path(value: str | Path) -> PureWindowsPath:
    """移除 Windows 扩展路径前缀，生成仅用于边界比较的等价路径。"""

    raw = str(value)
    if raw.casefold().startswith(_WINDOWS_EXTENDED_UNC_PREFIX.casefold()):
        raw = "\\\\" + raw[len(_WINDOWS_EXTENDED_UNC_PREFIX):]
    elif raw.startswith(_WINDOWS_EXTENDED_PREFIX):
        raw = raw[len(_WINDOWS_EXTENDED_PREFIX):]
    return PureWindowsPath(raw)


def _is_windows_path_within_root(path: str | Path, root: str | Path) -> bool:
    """按 Windows 路径语义判断扩展路径是否仍位于工作区根目录内。"""

    try:
        _windows_comparison_path(path).relative_to(_windows_comparison_path(root))
    except ValueError:
        return False
    return True


def _has_windows_extended_prefix(value: str | Path) -> bool:
    """判断路径是否采用 Windows 的扩展长度前缀表示。"""

    return str(value).startswith(_WINDOWS_EXTENDED_PREFIX)


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
    anywhere in the workspace — repository verification belongs to the outer
    integration-test phase rather than an owner task.
    """

    def _resolve_path(self, file_path: str) -> Path:
        """解析虚拟路径，并兼容 Windows 对同一路径返回的扩展前缀形式。"""

        try:
            return super()._resolve_path(file_path)
        except ValueError:
            if not self.virtual_mode:
                raise
            # 仅修复正常虚拟路径在 resolve 后产生的表示差异，不接纳宿主机绝对路径。
            if PureWindowsPath(file_path).drive:
                raise
            virtual_path = file_path if file_path.startswith("/") else "/" + file_path
            if ".." in virtual_path or virtual_path.startswith("~"):
                raise
            resolved = (self.cwd / virtual_path.lstrip("/")).resolve()
            if not (
                _has_windows_extended_prefix(resolved)
                or _has_windows_extended_prefix(self.cwd)
            ):
                raise
            if not _is_windows_path_within_root(resolved, self.cwd):
                raise
            return resolved

    def _write_file(self, file_path: str, resolved: Path, content: str, *, exclusive: bool = False) -> WriteResult:
        """写入普通文件，受限新增使用原子排他创建避免覆盖。"""
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(resolved, flags, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            return WriteResult(path=file_path)
        except (OSError, UnicodeEncodeError) as e:
            return WriteResult(error=f"Error writing file '{file_path}': {e}")

    def write(self, file_path: str, content: str) -> WriteResult:
        """写入成功且内容变化后，通过公共函数上报 AI 编辑。"""
        # 拦截临时脚本文件：agent 应直接用 execute 工具跑命令，而非写 .sh/.py 脚本。
        if _blocked_script_extension(file_path):
            return WriteResult(error=_blocked_script_error(file_path))

        # Try the original path first (handles new files)
        resolved = self._resolve_path(file_path)
        policy = repair_write_policy(self.cwd, file_path, resolved)
        if policy == "denied":
            return WriteResult(error="该文件不在当前修复的授权范围内。")
        before = capture_inclusion_before(resolved)
        if policy == "create_only":
            creation = _REPAIR_CREATION.get()
            result = self._write_file(file_path, resolved, content, exclusive=True)
            if result.error is None and creation is not None:
                creation.written = True
                report_inclusion_rate(self.cwd, resolved, before)
            return result
        result = super().write(file_path, content)
        if result.error is None:
            report_inclusion_rate(self.cwd, resolved, before)
            return result

        # File already exists — overwrite instead of dedup
        logger.debug("AutoDedupFilesystemBackend: overwriting existing file %s", file_path)
        result = self._write_file(file_path, resolved, content)
        if result.error is None:
            report_inclusion_rate(self.cwd, resolved, before)
        return result

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """在线程内复用同步写入，确保异步调用也只上报一次。"""
        return await asyncio.to_thread(self.write, file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str,
             replace_all: bool = False) -> EditResult:
        """编辑成功后上报每个替换片段，包括纯删除和批量替换。"""
        resolved = self._resolve_path(file_path)
        if repair_write_policy(self.cwd, file_path, resolved) != "normal":
            return EditResult(error="当前修复无权编辑该文件。")
        before = capture_inclusion_before(resolved)
        result = super().edit(file_path, old_string, new_string, replace_all)
        if result.error is None:
            report_inclusion_rate(self.cwd, resolved, before, edit=(old_string, new_string))
        return result
