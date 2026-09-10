"""管理生成应用 Agent UI 固定资产的路径、摘要与安全写入。"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

AGENT_UI_FRONTEND_TEMPLATE_VERSION = "agent-ui.v1"
AGENT_UI_FRONTEND_SOURCE_RELATIVE_PATHS = (
    "src/components/AgentConversation/AgentChatCore.tsx",
    "src/components/AgentConversation/AgentMessageBubble.tsx",
    "src/components/AgentConversation/AgentConversationPage.tsx",
    "src/components/AgentConversation/AgentFloatingPanel.tsx",
    "src/components/AgentConversation/AgentMobileChatDrawer.tsx",
    "src/components/AgentConversation/iconCompatibility.ts",
    "src/components/AgentConversation/styles.ts",
    "src/components/AgentConversation/index.ts",
    "src/apis/agentConversationMock.ts",
    "src/typings/agentConversation.ts",
    "tests/agentConversationMock.test.ts",
)
AGENT_UI_FRONTEND_TARGET_PATHS = tuple(
    f"frontend/{relative_path}"
    for relative_path in AGENT_UI_FRONTEND_SOURCE_RELATIVE_PATHS
)


class FrontendAgentUiAssetError(ValueError):
    """表示固定 Agent UI 资产本身缺失或无法安全写入。"""


def resolve_agent_ui_frontend_template_root() -> Path:
    """在源码与 PyInstaller 冻结布局中解析内置 Agent UI 资产目录。"""

    module_relative = (
        Path(__file__).resolve().parent.parent
        / "builtin_templates"
        / "agent-ui-frontend"
    )
    candidates = [module_relative]
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(
            Path(frozen_root).resolve()
            / "app"
            / "builtin_templates"
            / "agent-ui-frontend"
        )
    for candidate in candidates:
        if candidate.is_dir() and not candidate.is_symlink():
            return candidate
    return module_relative


def calculate_sha256(content: bytes) -> str:
    """计算单个固定资产的 SHA-256 摘要。"""

    return hashlib.sha256(content).hexdigest()


def load_agent_ui_frontend_source_assets() -> tuple[dict[str, bytes], str]:
    """读取完整内置资产集合并计算包含路径的聚合源码摘要。"""

    root = resolve_agent_ui_frontend_template_root()
    assets: dict[str, bytes] = {}
    digest = hashlib.sha256()
    for relative_path in AGENT_UI_FRONTEND_SOURCE_RELATIVE_PATHS:
        source = root / relative_path
        if not source.is_file() or source.is_symlink():
            raise FrontendAgentUiAssetError(
                f"内置 Agent UI 固定资产缺失或不是普通文件：{relative_path}"
            )
        try:
            content = source.read_bytes()
            content.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise FrontendAgentUiAssetError(
                f"内置 Agent UI 固定资产无法读取：{relative_path}"
            ) from exc
        assets[relative_path] = content
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return assets, digest.hexdigest()


def assert_safe_agent_ui_target(workspace: Path, target: Path) -> None:
    """拒绝目标文件或工作区内任一已有父目录为符号链接。"""

    relative = target.relative_to(workspace)
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise FrontendAgentUiAssetError(
                f"Agent UI 注入目标不能经过符号链接：{relative.as_posix()}"
            )


def write_missing_agent_ui_file_atomically(path: Path, content: bytes) -> None:
    """以同目录完整临时文件和原子硬链接创建目标，绝不覆盖竞态文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_name, path)
        except FileExistsError as exc:
            raise FrontendAgentUiAssetError(
                f"Agent UI 注入目标在写入期间发生冲突：{path}"
            ) from exc
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
