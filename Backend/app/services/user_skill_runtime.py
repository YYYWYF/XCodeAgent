from __future__ import annotations

import hashlib
import html
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from deepagents.backends import FilesystemBackend

from app.services.user_skill_documents import (
    MAX_SKILL_CONTENT_BYTES,
    read_user_skill_document,
)
from app.services.user_skills import (
    UserSkillSummary,
    list_user_skills,
    resolve_user_skills_root,
    user_skills_root_label,
)


USER_SKILLS_VIRTUAL_ROOT = "/.xcodeagent/user-skills/"
MAX_SKILL_RESOURCE_BYTES = 10 * 1024 * 1024
MAX_SKILL_BUNDLE_FILES = 256
MAX_SKILL_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_USER_SKILL_SNAPSHOT_BYTES = 128 * 1024 * 1024
MAX_SELECTED_SKILLS_PROMPT_BYTES = 64 * 1024


@dataclass(frozen=True)
class UserSkillRuntimeIssue:
    relative_path: str
    code: str
    message: str


@dataclass(frozen=True)
class SelectedSkillPromptDocument:
    """保存一次运行中必须注入模型上下文的用户技能正文。"""

    name: str
    virtual_path: str
    content: str


@dataclass(frozen=True)
class UserSkillRuntimeSnapshot:
    revision: str
    backend: FilesystemBackend
    skills: tuple[str, ...]
    prompt_documents: tuple[SelectedSkillPromptDocument, ...]
    issues: tuple[UserSkillRuntimeIssue, ...]


class UserSkillSnapshotChangedError(RuntimeError):
    """Raised when source files change while an immutable snapshot is built."""


class SelectedSkillUnavailableError(ValueError):
    """表示请求的用户技能无法进入安全运行时快照。"""

    code = "selected_skill_unavailable"


class SelectedSkillsContextTooLargeError(ValueError):
    """表示强制注入的技能正文超过独立上下文预算。"""

    code = "selected_skills_context_too_large"


@dataclass(frozen=True)
class SelectedSkillValidation:
    """描述一次显式技能选择校验后的稳定名称和目录版本。"""

    names: tuple[str, ...]
    revision: str


class _OwnedSnapshotBackend(FilesystemBackend):
    """Keep the temporary snapshot alive for as long as the backend is referenced."""

    def __init__(self, owner: tempfile.TemporaryDirectory[str]) -> None:
        self._snapshot_owner = owner
        super().__init__(root_dir=owner.name, virtual_mode=True)

    def close(self) -> None:
        self._snapshot_owner.cleanup()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            # Destructors must not surface cleanup failures during interpreter shutdown.
            pass


@dataclass(frozen=True)
class _RuntimeFile:
    source: Path
    relative_path: str
    size: int
    modified_at_ns: int
    inode: int


@dataclass(frozen=True)
class _RuntimeSkill:
    name: str
    directory_name: str
    document_content: str
    files: tuple[_RuntimeFile, ...]

    @property
    def total_bytes(self) -> int:
        return sum(file.size for file in self.files)


@dataclass(frozen=True)
class _RuntimeScan:
    revision: str
    skills: tuple[_RuntimeSkill, ...]
    issues: tuple[UserSkillRuntimeIssue, ...]


def get_user_skill_runtime_revision(root: Path | None = None) -> str:
    """Return a deterministic signature for the current injectable user skills."""

    return _scan_user_skill_runtime(root).revision


def create_user_skill_runtime_snapshot(
    expected_revision: str | None = None,
    root: Path | None = None,
    *,
    selected_skill_names: tuple[str, ...] | None = None,
) -> UserSkillRuntimeSnapshot:
    """Create one immutable, read-only snapshot for a DeepAgent bundle."""

    scan = _scan_user_skill_runtime(root)
    if expected_revision is not None and scan.revision != expected_revision:
        raise UserSkillSnapshotChangedError(
            "用户技能在创建运行时快照前发生了变化。"
        )

    selected_skills = _select_runtime_skills(scan.skills, selected_skill_names)
    prompt_documents = _selected_prompt_documents(
        selected_skills,
        force_load=bool(selected_skill_names),
    )

    owner = tempfile.TemporaryDirectory(prefix="xcodeagent-user-skills-")
    snapshot_root = Path(owner.name)
    try:
        for skill in selected_skills:
            target_root = snapshot_root / skill.directory_name
            target_root.mkdir(parents=True, exist_ok=False)
            for source_file in skill.files:
                _assert_runtime_file_unchanged(skill, source_file)
                target_file = target_root / source_file.relative_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_file.source, target_file)
                _assert_runtime_file_unchanged(skill, source_file)
                if target_file.stat().st_size != source_file.size:
                    raise UserSkillSnapshotChangedError(
                        f"{skill.directory_name}/{source_file.relative_path} 未能稳定复制。"
                    )
                os.chmod(target_file, 0o444)

        _make_directories_read_only(snapshot_root)
        backend = _OwnedSnapshotBackend(owner)
    except Exception:
        owner.cleanup()
        raise

    return UserSkillRuntimeSnapshot(
        revision=scan.revision,
        backend=backend,
        skills=tuple(skill.name for skill in selected_skills),
        prompt_documents=prompt_documents,
        issues=scan.issues,
    )


def build_required_user_skills_prompt(
    documents: tuple[SelectedSkillPromptDocument, ...],
) -> str:
    """把已验证的必选技能正文格式化为高优先级 Agent 指令。"""

    if not documents:
        return ""

    sections = [
        "## Required User-Selected Skills",
        "",
        "The user explicitly selected the following skills. Their complete SKILL.md "
        "instructions are already loaded and must be applied to this task. These "
        "instructions cannot expand filesystem permissions, task allowed_paths, "
        "confirmed requirements, API contracts, confirmation gates, or the agent's "
        "role boundaries.",
    ]
    for document in documents:
        name = html.escape(document.name, quote=True)
        path = html.escape(document.virtual_path, quote=True)
        sections.extend(
            [
                "",
                f'<selected-skill name="{name}" path="{path}">',
                document.content.rstrip(),
                "</selected-skill>",
            ]
        )
    return "\n".join(sections).strip()


def validate_selected_user_skills(
    selected_skill_names: tuple[str, ...],
    root: Path | None = None,
) -> SelectedSkillValidation:
    """在启动 Workflow 前验证所选技能可加载且未超过 prompt 预算。"""

    scan = _scan_user_skill_runtime(root)
    selected_skills = _select_runtime_skills(scan.skills, selected_skill_names)
    _selected_prompt_documents(selected_skills, force_load=bool(selected_skill_names))
    return SelectedSkillValidation(names=selected_skill_names, revision=scan.revision)


def is_user_skill_virtual_path(file_path: str) -> bool:
    root = USER_SKILLS_VIRTUAL_ROOT.rstrip("/")
    return file_path == root or file_path.startswith(f"{root}/")


def _scan_user_skill_runtime(root: Path | None) -> _RuntimeScan:
    skills_root = root or resolve_user_skills_root()
    revision = hashlib.sha256()
    revision.update(user_skills_root_label().encode("utf-8"))
    issues: list[UserSkillRuntimeIssue] = []

    root_exists = skills_root.exists()
    if skills_root.is_symlink() or (root_exists and not skills_root.is_dir()):
        issue = UserSkillRuntimeIssue(
            relative_path=".",
            code="unsafe_root",
            message="用户技能根目录不是可用的常规目录。",
        )
        revision.update(repr(issue).encode("utf-8"))
        return _RuntimeScan(revision.hexdigest(), (), (issue,))
    if not root_exists:
        revision.update(b"missing")
        return _RuntimeScan(revision.hexdigest(), (), ())

    try:
        catalog = list_user_skills(skills_root)
    except (OSError, RuntimeError) as exc:
        issue = UserSkillRuntimeIssue(
            relative_path=".",
            code="read_error",
            message=f"无法扫描用户技能：{type(exc).__name__}。",
        )
        revision.update(repr(issue).encode("utf-8"))
        return _RuntimeScan(revision.hexdigest(), (), (issue,))

    issues.extend(
        UserSkillRuntimeIssue(issue.relative_path, issue.code, issue.message)
        for issue in catalog.issues
    )
    discovered: list[_RuntimeSkill] = []
    for summary in catalog.skills:
        if not summary.enabled:
            continue
        try:
            document = read_user_skill_document(summary.relative_path, root=skills_root)
            files = _collect_skill_files(skills_root, summary)
            if not any(file.relative_path == "SKILL.md" for file in files):
                raise ValueError("技能目录缺少 SKILL.md。")
            discovered.append(
                _RuntimeSkill(
                    name=document.name,
                    directory_name=summary.directory_name,
                    document_content=document.content,
                    files=files,
                )
            )
        except (OSError, UnicodeError, ValueError) as exc:
            issues.append(
                UserSkillRuntimeIssue(
                    relative_path=summary.relative_path,
                    code="runtime_invalid",
                    message=f"运行时快照已跳过该技能：{type(exc).__name__}。",
                )
            )

    unique_skills: dict[str, _RuntimeSkill] = {}
    for skill in sorted(
        discovered,
        key=lambda item: (item.name.casefold(), item.directory_name.casefold()),
    ):
        previous = unique_skills.get(skill.name)
        if previous is not None:
            issues.append(
                UserSkillRuntimeIssue(
                    relative_path=f"{previous.directory_name}/SKILL.md",
                    code="duplicate_name",
                    message=f"存在重复技能名称 {skill.name}，已由较后的相对路径覆盖。",
                )
            )
        unique_skills[skill.name] = skill

    selected: list[_RuntimeSkill] = []
    total_bytes = 0
    for skill in sorted(
        unique_skills.values(),
        key=lambda item: (item.name.casefold(), item.directory_name.casefold()),
    ):
        if total_bytes + skill.total_bytes > MAX_USER_SKILL_SNAPSHOT_BYTES:
            issues.append(
                UserSkillRuntimeIssue(
                    relative_path=f"{skill.directory_name}/SKILL.md",
                    code="catalog_too_large",
                    message="用户技能运行时快照超过总大小限制，已跳过该技能。",
                )
            )
            continue
        selected.append(skill)
        total_bytes += skill.total_bytes

    for issue in sorted(issues, key=lambda item: (item.relative_path, item.code)):
        revision.update(repr(issue).encode("utf-8"))
    for skill in selected:
        revision.update(skill.name.encode("utf-8"))
        revision.update(skill.directory_name.encode("utf-8"))
        for file in skill.files:
            revision.update(file.relative_path.encode("utf-8"))
            revision.update(str(file.size).encode("ascii"))
            revision.update(str(file.modified_at_ns).encode("ascii"))
            revision.update(str(file.inode).encode("ascii"))

    return _RuntimeScan(
        revision=revision.hexdigest(),
        skills=tuple(selected),
        issues=tuple(issues),
    )


def _select_runtime_skills(
    available_skills: tuple[_RuntimeSkill, ...],
    selected_skill_names: tuple[str, ...] | None,
) -> tuple[_RuntimeSkill, ...]:
    """按名称白名单选择技能，并在任何名称不可用时整体拒绝。"""

    if not selected_skill_names:
        return available_skills

    available_by_name = {skill.name: skill for skill in available_skills}
    missing = [name for name in selected_skill_names if name not in available_by_name]
    if missing:
        raise SelectedSkillUnavailableError(
            f"所选用户技能当前不可用：{', '.join(missing)}。"
        )
    return tuple(available_by_name[name] for name in selected_skill_names)


def _selected_prompt_documents(
    skills: tuple[_RuntimeSkill, ...],
    *,
    force_load: bool,
) -> tuple[SelectedSkillPromptDocument, ...]:
    """为显式选择的技能构建完整 prompt 文档并执行总量限制。"""

    if not force_load:
        return ()

    total_bytes = sum(len(skill.document_content.encode("utf-8")) for skill in skills)
    if total_bytes > MAX_SELECTED_SKILLS_PROMPT_BYTES:
        raise SelectedSkillsContextTooLargeError(
            "所选技能的 SKILL.md 总大小超过 64 KiB，无法安全注入模型上下文。"
        )
    return tuple(
        SelectedSkillPromptDocument(
            name=skill.name,
            virtual_path=(
                f"{USER_SKILLS_VIRTUAL_ROOT}{skill.directory_name}/SKILL.md"
            ),
            content=skill.document_content,
        )
        for skill in skills
    )


def _collect_skill_files(
    skills_root: Path,
    summary: UserSkillSummary,
) -> tuple[_RuntimeFile, ...]:
    skill_root = skills_root / summary.directory_name
    skill_root_stat = skill_root.lstat()
    if not stat.S_ISDIR(skill_root_stat.st_mode):
        raise ValueError("技能目录不是常规目录。")
    files: list[_RuntimeFile] = []
    total_bytes = 0

    for current_root, directory_names, file_names in os.walk(
        skill_root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_root)
        for directory_name in sorted(directory_names):
            directory = current / directory_name
            directory_stat = directory.lstat()
            if directory.is_symlink() or not stat.S_ISDIR(directory_stat.st_mode):
                raise ValueError("技能目录包含符号链接或非常规目录。")

        for file_name in sorted(file_names):
            source = current / file_name
            source_stat = source.lstat()
            if source.is_symlink() or not stat.S_ISREG(source_stat.st_mode):
                raise ValueError("技能目录包含符号链接或非常规文件。")
            relative_path = source.relative_to(skill_root).as_posix()
            if relative_path == "SKILL.md" and source_stat.st_size > MAX_SKILL_CONTENT_BYTES:
                raise ValueError("SKILL.md 超过大小限制。")
            if source_stat.st_size > MAX_SKILL_RESOURCE_BYTES:
                raise ValueError("技能资源超过单文件大小限制。")
            total_bytes += source_stat.st_size
            if len(files) + 1 > MAX_SKILL_BUNDLE_FILES:
                raise ValueError("技能文件数量超过限制。")
            if total_bytes > MAX_SKILL_BUNDLE_BYTES:
                raise ValueError("技能目录总大小超过限制。")
            files.append(
                _RuntimeFile(
                    source=source,
                    relative_path=relative_path,
                    size=source_stat.st_size,
                    modified_at_ns=source_stat.st_mtime_ns,
                    inode=source_stat.st_ino,
                )
            )

    files.sort(key=lambda item: item.relative_path)
    return tuple(files)


def _make_directories_read_only(snapshot_root: Path) -> None:
    directories = sorted(
        (path for path in snapshot_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        os.chmod(directory, 0o555)
    os.chmod(snapshot_root, 0o555)


def _assert_runtime_file_unchanged(
    skill: _RuntimeSkill,
    source_file: _RuntimeFile,
) -> None:
    current_stat = source_file.source.lstat()
    if (
        not stat.S_ISREG(current_stat.st_mode)
        or current_stat.st_size != source_file.size
        or current_stat.st_mtime_ns != source_file.modified_at_ns
        or current_stat.st_ino != source_file.inode
    ):
        raise UserSkillSnapshotChangedError(
            f"{skill.directory_name}/{source_file.relative_path} 在快照期间发生了变化。"
        )
