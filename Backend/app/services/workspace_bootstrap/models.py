"""Workspace Bootstrap 基础层共享的数据模型与稳定错误。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class WorkspaceBootstrapError(ValueError):
    """表示可安全投影给 AG-UI 的 Bootstrap 领域错误。"""

    code = "WORKSPACE_BOOTSTRAP_ERROR"


class TemplateConfigError(WorkspaceBootstrapError):
    """表示 Application 与 TechnicalPlan 无法编译为模板请求。"""

    code = "TEMPLATE_CONFIG_INVALID"


class TemplateStateError(WorkspaceBootstrapError):
    """表示 Engine TemplateState 不符合冻结契约。"""

    code = "TEMPLATE_STATE_INVALID"


class TemplatePackageError(WorkspaceBootstrapError):
    """表示 ZIP 安全性或 Package 根结构不满足契约。"""

    code = "TEMPLATE_PACKAGE_CONTRACT_INVALID"


class TemplateEngineError(WorkspaceBootstrapError):
    """表示模板引擎调用、超时或下载失败。"""

    code = "TEMPLATE_ENGINE_UNAVAILABLE"


@dataclass(frozen=True)
class ArchiveLimits:
    """限制不可信 ZIP 的压缩包、条目和展开体积。"""

    max_package_bytes: int
    max_files: int
    max_extracted_bytes: int


@dataclass(frozen=True)
class TemplatePackageDownload:
    """保存已完整流式写入临时目录的 Engine ZIP 下载结果。"""

    temporary_path: Path
    sha256: str
    size: int
    content_type: str | None


@dataclass(frozen=True)
class ValidatedTemplatePackage:
    """保存通过安全和根目录契约检查的 Package 与 TemplateState。"""

    archive_path: Path
    template_state: dict[str, object]
