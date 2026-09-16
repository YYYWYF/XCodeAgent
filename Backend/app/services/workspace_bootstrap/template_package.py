"""校验 Template Engine `/v1/generate` ZIP 的最小 Bootstrap 契约。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath

from app.services.template_reconcile.protocol_v2 import TemplateStateV2
from app.services.workspace_bootstrap.archive_security import validate_archive_entries
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError, ValidatedTemplatePackage

_STATE_PATH = ".xcodeagent/template-state.json"
_REQUIRED_ROOTS = frozenset({"frontend", "backend"})


def validate_template_package(archive_path: str | Path, limits: ArchiveLimits) -> ValidatedTemplatePackage:
    """仅要求 ZIP 顶层存在 frontend/backend，并读取 TemplateState。"""

    path = Path(archive_path)
    try:
        with zipfile.ZipFile(path) as package:
            entries = validate_archive_entries(package, limits)
            _validate_required_roots(entries)

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


def _validate_required_roots(entries: list[zipfile.ZipInfo]) -> None:
    """仅校验 ZIP 顶层存在 frontend 和 backend 目录；其它路径不做限制。"""

    roots = {
        PurePosixPath(entry.filename).parts[0]
        for entry in entries
        if PurePosixPath(entry.filename).parts
    }
    missing = _REQUIRED_ROOTS - roots
    if missing:
        raise TemplatePackageError(
            "模板 ZIP 顶层必须包含 frontend 和 backend 目录，缺少："
            + "、".join(sorted(missing))
        )
