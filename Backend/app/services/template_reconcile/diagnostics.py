"""为失败的 Template Reconcile 保存不含文件正文的更新包诊断摘要。"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from app.services.template_reconcile.models import ChangeSetBody, TemplateState
from app.utils.atomic_json import atomic_write_json
from app.services.workspace_bootstrap.models import TemplatePackageDownload

TEMPLATE_UPDATE_DIAGNOSTIC_RELATIVE_PATH = Path(
    ".xcodeagent/runtime/template-update-diagnostic.json"
)


def template_update_diagnostic_path(workspace: str | Path) -> Path:
    """返回失败更新包诊断摘要的唯一工作区路径。"""

    return Path(workspace).expanduser().resolve() / TEMPLATE_UPDATE_DIAGNOSTIC_RELATIVE_PATH


def capture_template_update_diagnostic(
    workspace: str | Path,
    *,
    change_id: str,
    technical_plan_sha256: str,
    download: TemplatePackageDownload,
    current_state: TemplateState,
    next_state: TemplateState | None,
    change_set: ChangeSetBody | None,
    failure_type: str,
    before: dict[str, dict[str, str | None]] | None = None,
) -> dict[str, Any]:
    """生成失败摘要，保留更新前和失败回滚前的内容哈希而不保留正文。"""

    root = Path(workspace).expanduser().resolve()
    paths = diagnostic_paths(current_state, next_state, change_set)
    before_snapshot = before or capture_workspace_snapshot(root, paths)
    after_snapshot = capture_workspace_snapshot(root, paths)
    return {
        "changeId": change_id,
        "technicalPlanSha256": technical_plan_sha256,
        "package": {
            "sha256": download.sha256,
            "bytes": download.size,
            "validated": next_state is not None and change_set is not None,
        },
        "failureType": failure_type,
        "operations": _operation_summary(change_set) if change_set is not None else [],
        "managedFiles": _managed_file_summary(
            current_state,
            next_state,
            paths,
            before_snapshot,
            after_snapshot,
        ),
    }


def save_template_update_diagnostic(workspace: str | Path, diagnostic: dict[str, Any]) -> None:
    """原子保存失败诊断；成功更新不会删除既有失败证据。"""

    atomic_write_json(template_update_diagnostic_path(workspace), diagnostic)


def diagnostic_paths(
    current_state: TemplateState,
    next_state: TemplateState | None,
    change_set: ChangeSetBody | None,
) -> tuple[str, ...]:
    """汇总当前、目标 State 和操作涉及的路径，保证摘要排序稳定。"""

    return tuple(
        sorted(
            set(current_state.managedFiles)
            | (set(next_state.managedFiles) if next_state is not None else set())
            | ({operation.path for operation in change_set.operations} if change_set is not None else set())
        )
    )


def capture_workspace_snapshot(root: Path, paths: tuple[str, ...]) -> dict[str, dict[str, str | None]]:
    """读取每个路径的原始字节哈希；无效路径和非普通文件不会被跟随。"""

    return {raw_path: _file_hash(root, raw_path) for raw_path in paths}


def _file_hash(root: Path, raw_path: str) -> dict[str, str | None]:
    """返回普通文件的 SHA-256，或可判定的缺失/无效状态，绝不读取文件正文。"""

    relative = _safe_relative_path(raw_path)
    if relative is None:
        return {"status": "INVALID_PATH", "sha256": None}
    target = root / relative
    if not target.exists():
        return {"status": "MISSING", "sha256": None}
    if not target.is_file() or target.is_symlink():
        return {"status": "NOT_REGULAR_FILE", "sha256": None}
    try:
        with target.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        # 与健康检查一致地保留 CRLF/LF，按原始 UTF-8 文本语义计算比较哈希。
        health_check_digest = _text_sha256(target.read_bytes().decode("utf-8"))
        return {
            "status": "PRESENT",
            "sha256": digest,
            "healthCheckContentSha256": health_check_digest,
        }
    except (OSError, UnicodeError):
        return {"status": "UNREADABLE", "sha256": None}


def _safe_relative_path(raw_path: str) -> Path | None:
    """限制诊断读取范围到 Workspace，避免异常包路径逃逸。"""

    if not raw_path or "\\" in raw_path:
        return None
    path = PurePosixPath(raw_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.parts[0] in {".git", ".xcodeagent"}:
        return None
    return Path(*path.parts)


def _operation_summary(change_set: ChangeSetBody) -> list[dict[str, str | int | None]]:
    """输出每个操作的路径、类型和正文哈希，DELETE 的内容哈希显式为空。"""

    summary: list[dict[str, str | int | None]] = []
    for index, operation in enumerate(change_set.operations):
        content = getattr(operation, "content", None)
        summary.append(
            {
                "index": index,
                "path": operation.path,
                "type": operation.type,
                "contentSha256": _text_sha256(content) if isinstance(content, str) else None,
            }
        )
    return summary


def _managed_file_summary(
    current_state: TemplateState,
    next_state: TemplateState | None,
    paths: tuple[str, ...],
    before: dict[str, dict[str, str | None]],
    after: dict[str, dict[str, str | None]],
) -> list[dict[str, Any]]:
    """关联当前/目标 State 与实际文件哈希，供定位基线或包契约偏差。"""

    return [
        {
            "path": raw_path,
            "currentStateContentSha256": _text_sha256(current_state.managedFiles[raw_path])
            if raw_path in current_state.managedFiles
            else None,
            "nextStateContentSha256": _text_sha256(next_state.managedFiles[raw_path])
            if next_state is not None and raw_path in next_state.managedFiles
            else None,
            "actualBefore": before[raw_path],
            "actualAfterFailure": after[raw_path],
        }
        for raw_path in paths
    ]


def _text_sha256(content: str) -> str:
    """按 Engine UTF-8 文本契约计算内容 SHA-256。"""

    return hashlib.sha256(content.encode("utf-8")).hexdigest()
