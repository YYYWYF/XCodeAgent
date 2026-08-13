"""前端代码审查的范围发现、报告持久化与结果校验。"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


CODE_AUDIT_DIRECTORY = PurePosixPath(".xcodeagent/codeAudit")
CODE_AUDIT_FILE_PATTERN = re.compile(r"^code_review_\d{8}\.md$")
CODE_AUDIT_MAX_REPORT_BYTES = 512 * 1024
CODE_AUDIT_MAX_SOURCE_FILES = 5_000

_FRONTEND_SOURCE_SUFFIXES = {
    ".cjs",
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".less",
    ".mjs",
    ".scss",
    ".ts",
    ".tsx",
    ".vue",
}
_IGNORED_DIRECTORY_NAMES = {
    ".cache",
    ".git",
    ".next",
    ".turbo",
    "build",
    "coverage",
    "dist",
    "generated",
    "node_modules",
    "out",
    "vendor",
}
_REQUIRED_REPORT_SECTIONS = (
    "# 前端代码检视报告",
    "## 报告概览",
    "## 问题统计",
    "## 文件分析详情",
    "## 总体评估与建议",
)


class CodeAnalysisScanRequest(BaseModel):
    """校验一次当前前端源码扫描请求。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["scan"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)


class CodeAnalysisReportRequest(BaseModel):
    """校验一次已生成代码审查报告读取请求。"""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["get-report"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)
    report_path: str = Field(alias="reportPath", min_length=1)


class FrontendSourceInventory(BaseModel):
    """保存确定性的前端源码根目录和文件清单。"""

    roots: list[str]
    files: list[str]


def resolve_code_analysis_workspace(workspace_root: str) -> Path:
    """解析并校验用户显式提供的代码扫描工作区。"""

    root = Path(workspace_root).expanduser().resolve()
    if not root.exists():
        raise ValueError("代码扫描工作区不存在。")
    if not root.is_dir():
        raise ValueError("代码扫描 workspaceRoot 必须是目录。")
    return root


def discover_frontend_sources(workspace_root: Path) -> FrontendSourceInventory:
    """发现受支持的前端 src 根目录，并生成有界安全文件清单。"""

    root = workspace_root.resolve()
    candidates = _frontend_root_candidates(root)
    source_roots: list[Path] = []
    source_root_keys: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_dir() or not _is_relative_to(resolved, root):
            continue
        if any(part.casefold() == "backend" for part in resolved.relative_to(root).parts):
            continue
        source_root_key = resolved.relative_to(root).as_posix().casefold()
        if source_root_key not in source_root_keys:
            source_root_keys.add(source_root_key)
            source_roots.append(resolved)

    if not source_roots:
        raise ValueError("当前 workspaceRoot 下未找到受支持的前端 src 目录。")

    files: list[str] = []
    for source_root in source_roots:
        for dirpath, dirnames, filenames in os.walk(source_root, followlinks=False):
            current = Path(dirpath)
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if name.casefold() not in _IGNORED_DIRECTORY_NAMES
                and not (current / name).is_symlink()
            ]
            for filename in sorted(filenames):
                path = current / filename
                if path.suffix.casefold() not in _FRONTEND_SOURCE_SUFFIXES:
                    continue
                if path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve()
                if not _is_relative_to(resolved, root):
                    continue
                files.append(resolved.relative_to(root).as_posix())
                if len(files) > CODE_AUDIT_MAX_SOURCE_FILES:
                    raise ValueError(
                        f"前端源码超过 {CODE_AUDIT_MAX_SOURCE_FILES} 个文件，"
                        "请缩小工作区后重试。"
                    )

    files = sorted(dict.fromkeys(files))
    if not files:
        raise ValueError("前端 src 目录中没有可审查的业务源码文件。")
    return FrontendSourceInventory(
        roots=[path.relative_to(root).as_posix() for path in source_roots],
        files=files,
    )


def code_audit_report_relative_path(now: datetime | None = None) -> str:
    """生成当天唯一正式代码审查报告的工作区相对路径。"""

    timestamp = now or datetime.now().astimezone()
    return (CODE_AUDIT_DIRECTORY / f"code_review_{timestamp:%Y%m%d}.md").as_posix()


def atomic_write_code_audit_report(
    workspace_root: Path,
    report_relative_path: str,
    content: str,
    *,
    cancellation_requested: Callable[[], bool] | None = None,
) -> Path:
    """校验报告后在 codeAudit 目录内原子覆盖当天正式文件。"""

    validate_code_audit_report_content(content)
    target = resolve_code_audit_report_path(
        workspace_root,
        report_relative_path,
        require_exists=False,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=".code-review-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_file.name)
        if cancellation_requested is not None and cancellation_requested():
            raise RuntimeError("代码审查运行已取消，报告未替换。")
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return target


def read_code_audit_report(
    workspace_root: Path,
    report_relative_path: str,
) -> tuple[str, dict[str, object]]:
    """安全读取正式 Markdown 报告，并返回校验后的统计摘要。"""

    target = resolve_code_audit_report_path(
        workspace_root,
        report_relative_path,
        require_exists=True,
    )
    size_bytes = target.stat().st_size
    if size_bytes > CODE_AUDIT_MAX_REPORT_BYTES:
        raise ValueError("代码审查报告超过允许读取的大小。")
    content = target.read_text(encoding="utf-8")
    summary = summarize_code_audit_report(content)
    return content, {**summary, "sizeBytes": size_bytes}


def resolve_code_audit_report_path(
    workspace_root: Path,
    report_relative_path: str,
    *,
    require_exists: bool,
) -> Path:
    """把公开报告相对路径限制在固定 codeAudit 目录和文件名格式内。"""

    if not report_relative_path or "\\" in report_relative_path:
        raise ValueError("代码审查报告路径格式无效。")
    relative = PurePosixPath(report_relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("代码审查报告路径不能逃逸工作区。")
    if relative.parent != CODE_AUDIT_DIRECTORY:
        raise ValueError("代码审查报告必须位于 .xcodeagent/codeAudit。")
    if not CODE_AUDIT_FILE_PATTERN.fullmatch(relative.name):
        raise ValueError("代码审查报告文件名格式无效。")

    root = workspace_root.resolve()
    target = root.joinpath(*relative.parts)
    if target.is_symlink():
        raise ValueError("代码审查报告不能是符号链接。")
    resolved_parent = target.parent.resolve()
    if not _is_relative_to(resolved_parent, root):
        raise ValueError("代码审查报告目录逃逸工作区。")
    if require_exists:
        if not target.is_file():
            raise ValueError("代码审查报告不存在。")
        if not _is_relative_to(target.resolve(), root):
            raise ValueError("代码审查报告逃逸工作区。")
    return target


def validate_code_audit_report_content(content: str) -> None:
    """拒绝空白、过大、缺少模板章节或仍含占位符的报告。"""

    encoded = content.encode("utf-8")
    if not content.strip():
        raise ValueError("代码审查报告不能为空。")
    if len(encoded) > CODE_AUDIT_MAX_REPORT_BYTES:
        raise ValueError("代码审查报告超过允许写入的大小。")
    missing = [section for section in _REQUIRED_REPORT_SECTIONS if section not in content]
    if missing:
        raise ValueError(f"代码审查报告缺少模板章节：{', '.join(missing)}。")
    if any(
        placeholder in content
        for placeholder in (
            "[项目名称]",
            "[检视时间]",
            "[前端源码根目录]",
            "[检视文件数]",
            "[有问题文件数]",
            "[无问题文件数]",
            "[总检视问题数量]",
            "[严重问题数]",
            "[高风险问题数]",
            "[中风险问题数]",
            "[低风险问题数]",
            "[工作区相对路径]",
            "[问题数量]",
            "[问题标题]",
            "[总体评价内容]",
        )
    ):
        raise ValueError("代码审查报告仍包含未替换的模板占位符。")
    required_metrics = (
        "检视文件数",
        "有问题文件数",
        "无问题文件数",
        "总检视问题数量",
        "严重问题",
        "高风险问题",
        "中风险问题",
        "低风险问题",
    )
    missing_metrics = [label for label in required_metrics if _report_metric_match(content, label) is None]
    if missing_metrics:
        raise ValueError(f"代码审查报告缺少可解析统计：{', '.join(missing_metrics)}。")
    if "**问题**:" not in content and "未发现有证据支持的安全问题" not in content:
        raise ValueError("代码审查报告缺少问题详情或无问题结论。")


def summarize_code_audit_report(content: str) -> dict[str, object]:
    """从已校验 Markdown 中提取稳定且有界的公开统计。"""

    validate_code_audit_report_content(content)
    issue_count = _report_metric(content, "总检视问题数量")
    problem_file_count = _report_metric(content, "有问题文件数")
    severity_counts = {
        "critical": _report_metric(content, "严重问题"),
        "high": _report_metric(content, "高风险问题"),
        "medium": _report_metric(content, "中风险问题"),
        "low": _report_metric(content, "低风险问题"),
    }
    return {
        "reportedFileCount": _report_metric(content, "检视文件数"),
        "issueCount": issue_count,
        "problemFileCount": problem_file_count,
        "severityCounts": severity_counts,
    }


def _frontend_root_candidates(workspace_root: Path) -> list[Path]:
    """返回显式常见根目录和有界深度内发现的 frontend/src 目录。"""

    candidates = [
        workspace_root / "Frontend" / "src",
        workspace_root / "frontend" / "src",
        workspace_root / "app" / "frontend" / "src",
    ]
    for dirpath, dirnames, _filenames in os.walk(workspace_root, followlinks=False):
        current = Path(dirpath)
        depth = len(current.relative_to(workspace_root).parts)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name.casefold() not in _IGNORED_DIRECTORY_NAMES
            and name.casefold() != "backend"
            and not (current / name).is_symlink()
            and depth < 3
        ]
        if current.name.casefold() == "frontend" and (current / "src").is_dir():
            candidates.append(current / "src")
    return candidates


def _report_metric(content: str, label: str) -> int:
    """读取报告统计表中的一个非负整数，缺失时返回零。"""

    match = _report_metric_match(content, label)
    return int(match.group(1)) if match else 0


def _report_metric_match(content: str, label: str) -> re.Match[str] | None:
    """查找报告表格中的一个整数统计单元格。"""

    return re.search(
        rf"\|\s*{re.escape(label)}\s*\|\s*(\d+)\s*(?:个|个文件)?\s*\|",
        content,
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    """兼容判断规范化路径是否仍位于工作区根目录内。"""

    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
