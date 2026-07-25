from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, WriteResult

from app.services.agent_file_documents import AGENTS_FILE_NAME, read_agents_document


AGENT_MEMORY_VIRTUAL_ROOT = "/.xcodeagent/agent-memory/"
AGENT_MEMORY_VIRTUAL_PATH = f"{AGENT_MEMORY_VIRTUAL_ROOT}{AGENTS_FILE_NAME}"


@dataclass(frozen=True)
class AgentMemoryRuntimeSnapshot:
    """Immutable AGENTS.md content and backend for one Agent bundle."""

    revision: str
    backend: FilesystemBackend


class AgentMemorySnapshotChangedError(RuntimeError):
    """Raised when AGENTS.md changes while an immutable snapshot is built."""


class _OwnedAgentMemoryBackend(FilesystemBackend):
    """Keep the temporary read-only snapshot alive while the backend is used."""

    def __init__(self, owner: tempfile.TemporaryDirectory[str]) -> None:
        """持有临时目录，并把快照挂载为虚拟文件系统。"""

        self._snapshot_owner = owner
        super().__init__(root_dir=owner.name, virtual_mode=True)

    def close(self) -> None:
        """释放当前快照拥有的临时目录。"""

        self._snapshot_owner.cleanup()

    def __del__(self) -> None:
        """在对象回收时尽力清理快照，不传播解释器退出期异常。"""

        try:
            self.close()
        except Exception:
            # Destructors must not surface cleanup failures during interpreter shutdown.
            pass

    def write(self, file_path: str, content: str) -> WriteResult:
        """拒绝写入不可变内存快照，避免依赖平台 chmod 语义。"""

        return WriteResult(error=f"只读快照不允许写入：{file_path}")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """拒绝编辑不可变内存快照，确保 Windows 与 macOS 行为一致。"""

        return EditResult(error=f"只读快照不允许编辑：{file_path}")


def get_agent_memory_runtime_revision(root: Path | None = None) -> str:
    """Return the content revision used to invalidate Agent bundles."""

    return read_agents_document(root=root).revision


def create_agent_memory_runtime_snapshot(
    expected_revision: str | None = None,
    root: Path | None = None,
) -> AgentMemoryRuntimeSnapshot:
    """Create a read-only virtual AGENTS.md snapshot for one Agent bundle."""

    document = read_agents_document(root=root)
    if expected_revision is not None and document.revision != expected_revision:
        raise AgentMemorySnapshotChangedError(
            "AGENTS.md 在创建运行时快照前发生了变化。"
        )

    owner = tempfile.TemporaryDirectory(prefix="xcodeagent-agent-memory-")
    snapshot_root = Path(owner.name)
    try:
        snapshot_file = snapshot_root / AGENTS_FILE_NAME
        snapshot_file.write_bytes(document.content.encode("utf-8"))
        os.chmod(snapshot_file, 0o444)
        os.chmod(snapshot_root, 0o555)
        backend = _OwnedAgentMemoryBackend(owner)

        current_revision = get_agent_memory_runtime_revision(root=root)
        if current_revision != document.revision:
            raise AgentMemorySnapshotChangedError(
                "AGENTS.md 在创建运行时快照时发生了变化。"
            )
    except Exception:
        owner.cleanup()
        raise

    return AgentMemoryRuntimeSnapshot(
        revision=document.revision,
        backend=backend,
    )
