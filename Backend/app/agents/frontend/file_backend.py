"""前端步骤内的精确读写与文件修改观测。"""

import os
import re
from contextvars import ContextVar
from hashlib import sha256
from pathlib import Path

from deepagents.backends.protocol import EditResult, WriteResult
from deepagents.backends.utils import perform_string_replacement

from app.agents.auto_dedup_backend import (
    AutoDedupFilesystemBackend,
    _blocked_script_error,
    _blocked_script_extension,
    repair_write_policy,
)
from app.services.inclusion_rate import capture_inclusion_before, report_inclusion_rate

FILE_ACCESS: ContextVar[tuple[object, str] | None] = ContextVar("frontend_build_file", default=None)
_NEWLINE = re.compile(r"\r\n|\r|\n")


def file_revision(stat_result) -> tuple:
    """读取能关联文件身份与外部修改的版本信息。"""
    info = stat_result
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _replacement(source: str, old: str, new: str, replace_all: bool):
    """复用原匹配错误语义，并在原文中替换以保留范围外的换行和编码。"""
    normalized_old = _NEWLINE.sub("\n", old)
    normalized_new = _NEWLINE.sub("\n", new)
    result = perform_string_replacement(
        _NEWLINE.sub("\n", source), normalized_old, normalized_new, replace_all,
    )
    if isinstance(result, str):
        return result
    if old == new:
        return source, result[1]
    # 按原有内容匹配 CRLF/LF/CR；模型按 LF 提交的编辑沿用被换段的换行风格。
    pattern = r"(?:\r\n|\r(?!\n)|(?<!\r)\n)".join(
        re.escape(part) for part in normalized_old.split("\n")
    ) if normalized_old else r"(?!(?<=\r)\n)"

    def replace(match: re.Match) -> str:
        """保留替换段原有的逐行换行，允许调用方显式改变换行。"""
        if "\r" in new or normalized_old == normalized_new:
            return new
        endings = iter(_NEWLINE.findall(match[0]))
        default = _NEWLINE.search(source)
        fallback = default[0] if default else "\n"
        return _NEWLINE.sub(lambda _: next(endings, fallback), new)

    return re.sub(pattern, replace, source), result[1]


class FrontendScopedFilesystemBackend(AutoDedupFilesystemBackend):
    """仅在原工具权限和步骤锁内比较并提交候选内容。"""

    def __init__(self, scope):
        """绑定此次 Agent 的工作区和动态授权。"""
        super().__init__(root_dir=scope.root, virtual_mode=True)
        self.scope = scope

    def _resolve_path(self, file_path: str) -> Path:
        """在物理访问前再次核对实际工具持有的路径授权。"""
        if FILE_ACCESS.get() != (self.scope, file_path):
            raise PermissionError("前端 Build 文件访问未获授权")
        return self.scope.physical_path(file_path)

    def write(self, file_path: str, content: str) -> WriteResult:
        """沿用受限创建权限；已有文件进入同描述符的比较和写入。"""
        if _blocked_script_extension(file_path):
            return WriteResult(error=_blocked_script_error(file_path))
        path = self._resolve_path(file_path)
        policy = repair_write_policy(self.cwd, file_path, path)
        if policy == "denied":
            return WriteResult(error="该文件不在当前修复的授权范围内。")
        # API 创建始终排他执行，即使授权后目标被并发创建也不覆盖。
        if policy == "create_only" or self.scope.task_level or not path.exists():
            result = super().write(file_path, content) if policy == "create_only" else self._create(
                file_path, path, content,
            )
            if not result.error:
                self.scope.note_mutation(file_path, None, sha256(content.encode("utf-8")).hexdigest())
            return result
        result = self._update(file_path, path, content=content)
        return WriteResult(path=result.path, error=result.error)

    def _create(self, file_path: str, path: Path, content: str) -> WriteResult:
        """复用排他创建与原编辑上报，避免覆盖同时出现的文件。"""
        before = capture_inclusion_before(path)
        result = self._write_file(file_path, path, content, exclusive=True)
        if not result.error:
            report_inclusion_rate(self.cwd, path, before)
        return result

    def edit(self, file_path: str, old_string: str, new_string: str,
             replace_all: bool = False) -> EditResult:
        """仅对通过原匹配校验的编辑比较最终字节。"""
        path = self._resolve_path(file_path)
        if repair_write_policy(self.cwd, file_path, path) != "normal":
            return EditResult(error="当前修复无权编辑该文件。")
        return self._update(file_path, path, edit=(old_string, new_string, replace_all))

    def _update(self, file_path, path, *, content=None, edit=None) -> EditResult:
        """在同一个文件描述符上读、校验版本、比较和写回，避免先截断再比较。"""
        try:
            fd = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r+b") as stream:
                version = file_revision(os.fstat(stream.fileno()))
                original = stream.read()
                occurrences = 0
                if edit is not None:
                    replacement = _replacement(original.decode("utf-8"), *edit)
                    if isinstance(replacement, str):
                        return EditResult(error=replacement)
                    content, occurrences = replacement
                candidate = content.encode("utf-8")
                if (file_revision(os.fstat(stream.fileno())) != version
                    or file_revision(self._resolve_path(file_path).stat()) != version):
                    return EditResult(error="文件已变化，请读取当前版本后重试。")
                if candidate != original:
                    before = capture_inclusion_before(path)
                    if file_revision(path.stat()) != version:
                        return EditResult(error="文件已变化，请读取当前版本后重试。")
                    stream.seek(0)
                    stream.write(candidate)
                    stream.truncate()
                    stream.flush()
                    report_inclusion_rate(self.cwd, path, before, edit=edit[:2] if edit else None)
                self.scope.note_mutation(
                    file_path, sha256(original).hexdigest(), sha256(candidate).hexdigest(),
                )
                return EditResult(path=file_path, occurrences=occurrences)
        except (OSError, UnicodeError) as exc:
            return EditResult(error=f"Error {'editing' if edit else 'writing'} file '{file_path}': {exc}")
