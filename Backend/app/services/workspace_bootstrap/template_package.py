"""校验 Engine 或 Git Bootstrap ZIP 的安全内容与动态必需根目录契约。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath

from app.services.template_reconcile.protocol_v2 import TemplateStateV2
from app.services.workspace_bootstrap.archive_security import validate_archive_entries
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError, ValidatedTemplatePackage

_STATE_PATH = ".xcodeagent/template-state.json"
_BASE_MANAGED_ROOTS = ("frontend", "backend")


def validate_template_package(
    archive_path: str | Path,
    limits: ArchiveLimits,
    managed_roots: tuple[str, ...] = _BASE_MANAGED_ROOTS,
) -> ValidatedTemplatePackage:
    """校验 ZIP 安全性、唯一 State 和本轮必需 roots，其他安全文件完整保留。"""

    path = Path(archive_path)
    try:
        if path.stat().st_size > limits.max_package_bytes:
            raise TemplatePackageError("模板 ZIP 超过下载大小限制。")
    except OSError as exc:
        raise TemplatePackageError("模板 ZIP 无法读取。") from exc
    try:
        with zipfile.ZipFile(path) as package:
            entries = validate_archive_entries(package, limits)
            files = [entry for entry in entries if not entry.is_dir()]
            _validate_required_roots(files, managed_roots)
            state_entries = [entry for entry in files if entry.filename == _STATE_PATH]
            if len(state_entries) != 1:
                raise TemplatePackageError("模板 ZIP 必须且只能包含 .xcodeagent/template-state.json。")
            try:
                state = json.loads(package.read(_STATE_PATH).decode("utf-8"))
            except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as exc:
                raise TemplatePackageError("模板 ZIP 中的 TemplateState 无法读取。") from exc

            try:
                return ValidatedTemplatePackage(path, TemplateStateV2.model_validate(state))
            except ValueError as exc:
                raise TemplatePackageError("TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED：Bootstrap Package 未提供 V2 TemplateState。") from exc
    except zipfile.BadZipFile as exc:
        raise TemplatePackageError("模板 ZIP 已损坏或格式无效。") from exc


def _validate_required_roots(
    entries: list[zipfile.ZipInfo], managed_roots: tuple[str, ...]
) -> None:
    """要求本轮动态受管 roots 都含普通文件，不拦截其他已通过安全校验的路径。"""

    expected_roots = frozenset(managed_roots)
    if not expected_roots or len(expected_roots) != len(managed_roots):
        raise TemplatePackageError("Bootstrap managed roots 配置无效。")
    seen_roots: set[str] = set()
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if entry.filename == _STATE_PATH:
            continue
        root = path.parts[0] if path.parts else ""
        if root in expected_roots and len(path.parts) < 2:
            raise TemplatePackageError("模板 ZIP 不允许在 managed root 放置顶层普通文件。")
        if root in expected_roots:
            seen_roots.add(root)
    missing = expected_roots - seen_roots
    if missing:
        raise TemplatePackageError(
            "模板 ZIP 必须包含本轮全部 managed roots 文件，缺少："
            + "、".join(sorted(missing))
        )
