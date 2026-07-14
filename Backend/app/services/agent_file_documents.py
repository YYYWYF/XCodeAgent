from __future__ import annotations

import hashlib
import hmac
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.services.user_skills import ApiModel, user_skills_working_dir


AGENTS_FILE_NAME = "AGENTS.md"
# Keep environment-level instructions small enough to fit safely in the
# DeepAgents system context alongside workflow prompts, skills, and task input.
MAX_AGENTS_CONTENT_BYTES = 32 * 1024
DEFAULT_AGENTS_CONTENT = (
    "# XCodeAgent 工作区指令\n\n"
    "## 工作方式\n"
    "- 先理解任务，再制定清晰的实施计划。\n"
    "- 优先复用现有代码与项目规范；仅修改完成需求所必需的内容。\n"
    "- 完成修改后执行相关检查，并如实报告结果。\n\n"
    "## 代码质量\n"
    "- 保持代码清晰、可维护，并处理边界情况。\n"
    "- 不引入未确认的新依赖，不提交敏感信息。\n\n"
    "## 协作\n"
    "- 不确定的要求先向用户澄清。\n"
)


class AgentFileDocument(ApiModel):
    name: str = Field(default=AGENTS_FILE_NAME)
    relative_path: str = Field(default=AGENTS_FILE_NAME)
    content: str
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    updated_at: str = Field(min_length=1)


class GetAgentFileRequest(ApiModel):
    action: Literal["get"]


class SaveAgentFileRequest(ApiModel):
    action: Literal["save"]
    content: str
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class AgentFilePathError(ValueError):
    pass


class AgentFileContentError(ValueError):
    pass


class AgentFileRevisionConflictError(RuntimeError):
    pass


def resolve_agent_files_root() -> Path:
    """Return the current environment's XCodeAgent data directory."""

    return Path.home() / user_skills_working_dir()


def agent_files_root_label() -> str:
    return f"~/{user_skills_working_dir()}"


def ensure_agents_document(*, root: Path | None = None) -> AgentFileDocument:
    """Create the environment AGENTS.md once, without changing existing content."""

    environment_root = _ensure_environment_root(root)
    agents_file = environment_root / AGENTS_FILE_NAME
    _create_default_document_if_missing(agents_file)
    return _read_document(agents_file)


def read_agents_document(*, root: Path | None = None) -> AgentFileDocument:
    return ensure_agents_document(root=root)


def save_agents_document(
    content: str,
    expected_revision: str,
    *,
    root: Path | None = None,
) -> AgentFileDocument:
    environment_root = _ensure_environment_root(root)
    agents_file = environment_root / AGENTS_FILE_NAME
    _create_default_document_if_missing(agents_file)
    encoded_content = _encode_content(content)

    current_content = _read_content_bytes(agents_file)
    current_revision = _content_revision(current_content)
    if not hmac.compare_digest(current_revision, expected_revision):
        raise AgentFileRevisionConflictError(
            "AGENTS.md 已被其他操作修改，请重新打开后再保存。"
        )

    file_mode = stat.S_IMODE(_regular_file_stat(agents_file).st_mode)
    _atomic_replace(agents_file, encoded_content, file_mode)
    return _document_from_content(agents_file, content, encoded_content)


def _ensure_environment_root(root: Path | None) -> Path:
    environment_root = root or resolve_agent_files_root()
    if environment_root.is_symlink():
        raise AgentFilePathError("XCodeAgent 环境目录不允许使用符号链接。")
    try:
        environment_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise AgentFilePathError("无法创建 XCodeAgent 环境目录。") from exc
    if environment_root.is_symlink() or not environment_root.is_dir():
        raise AgentFilePathError(f"{agent_files_root_label()} 不可用。")
    return environment_root


def _create_default_document_if_missing(agents_file: Path) -> None:
    if agents_file.is_symlink():
        raise AgentFilePathError("AGENTS.md 不允许使用符号链接。")
    if agents_file.exists():
        _regular_file_stat(agents_file)
        return

    try:
        _create_file(agents_file, DEFAULT_AGENTS_CONTENT.encode("utf-8"))
    except FileExistsError:
        # Another backend process created the file first; it remains authoritative.
        _regular_file_stat(agents_file)


def _create_file(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            descriptor = None
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_document(agents_file: Path) -> AgentFileDocument:
    content_bytes = _read_content_bytes(agents_file)
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentFileContentError("AGENTS.md 必须使用 UTF-8 编码。") from exc
    return _document_from_content(agents_file, content, content_bytes)


def _document_from_content(
    agents_file: Path,
    content: str,
    content_bytes: bytes,
) -> AgentFileDocument:
    file_stat = _regular_file_stat(agents_file)
    return AgentFileDocument(
        content=content,
        revision=_content_revision(content_bytes),
        size_bytes=len(content_bytes),
        updated_at=datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc).isoformat(),
    )


def _read_content_bytes(agents_file: Path) -> bytes:
    file_stat = _regular_file_stat(agents_file)
    if file_stat.st_size > MAX_AGENTS_CONTENT_BYTES:
        raise AgentFileContentError("AGENTS.md 不能超过 32 KiB。")
    try:
        with agents_file.open("rb") as file:
            content = file.read(MAX_AGENTS_CONTENT_BYTES + 1)
    except OSError as exc:
        raise AgentFilePathError("无法读取 AGENTS.md。") from exc
    if len(content) > MAX_AGENTS_CONTENT_BYTES:
        raise AgentFileContentError("AGENTS.md 不能超过 32 KiB。")
    return content


def _encode_content(content: str) -> bytes:
    try:
        encoded_content = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AgentFileContentError("AGENTS.md 必须是有效的 UTF-8 文本。") from exc
    if len(encoded_content) > MAX_AGENTS_CONTENT_BYTES:
        raise AgentFileContentError("AGENTS.md 不能超过 32 KiB。")
    return encoded_content


def _regular_file_stat(path: Path) -> os.stat_result:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise AgentFilePathError("AGENTS.md 不存在或无法访问。") from exc
    if stat.S_ISLNK(file_stat.st_mode):
        raise AgentFilePathError("AGENTS.md 不允许使用符号链接。")
    if not stat.S_ISREG(file_stat.st_mode):
        raise AgentFilePathError("AGENTS.md 必须是常规文件。")
    return file_stat


def _content_revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_replace(path: Path, content: bytes, file_mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".AGENTS.md.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary_path, file_mode)
        _regular_file_stat(path)
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
