from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from deepagents.backends import FilesystemBackend

from app.services.agent_file_documents import AGENTS_FILE_NAME, read_agents_document, resolve_agent_files_root
from app.services.runtime_snapshot import RuntimeSnapshotCache


AGENT_MEMORY_VIRTUAL_ROOT = "/.devagentstudio/agent-memory/"
AGENT_MEMORY_VIRTUAL_PATH = f"{AGENT_MEMORY_VIRTUAL_ROOT}{AGENTS_FILE_NAME}"
_SNAPSHOTS = RuntimeSnapshotCache("agent-memory")


@dataclass(frozen=True)
class AgentMemoryRuntimeSnapshot:
    """Immutable AGENTS.md content and backend for one Agent bundle."""

    revision: str
    backend: FilesystemBackend


class AgentMemorySnapshotChangedError(RuntimeError):
    """Raised when AGENTS.md changes while an immutable snapshot is built."""


def get_agent_memory_runtime_revision(root: Path | None = None) -> str:
    """Return the content revision used to invalidate Agent bundles."""

    return read_agents_document(root=root).revision


def create_agent_memory_runtime_snapshot(
    expected_revision: str | None = None,
    root: Path | None = None,
) -> AgentMemoryRuntimeSnapshot:
    """按规则文件来源和内容版本共享只读快照。"""

    document = read_agents_document(root=root)
    if expected_revision is not None and document.revision != expected_revision:
        raise AgentMemorySnapshotChangedError(
            "AGENTS.md 在创建运行时快照前发生了变化。"
        )

    def populate(snapshot_root: Path) -> None:
        """写入固定版本的正文，并在发布前复核来源。"""
        snapshot_file = snapshot_root / AGENTS_FILE_NAME
        snapshot_file.write_bytes(document.content.encode("utf-8"))
        os.chmod(snapshot_file, 0o444)
        os.chmod(snapshot_root, 0o555)

        current_revision = get_agent_memory_runtime_revision(root=root)
        if current_revision != document.revision:
            raise AgentMemorySnapshotChangedError(
                "AGENTS.md 在创建运行时快照时发生了变化。"
            )

    source = (root if root is not None else resolve_agent_files_root()).resolve()
    return AgentMemoryRuntimeSnapshot(
        revision=document.revision,
        backend=_SNAPSHOTS.get((source, document.revision), populate),
    )
