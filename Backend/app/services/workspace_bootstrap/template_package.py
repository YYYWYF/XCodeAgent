"""校验 Template Engine V3 `/v1/generate-next` ZIP 的 Bootstrap 契约。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.template_reconcile.protocol_v3 import TemplateStateV3
from app.services.workspace_bootstrap.archive_security import validate_archive_entries
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError, ValidatedTemplatePackage

_STATE_PATH = (WORKSPACE_ARTIFACT_DIR / "template-state.json").as_posix()
_REQUIRED_ROOTS = frozenset({"frontend", "backend"})


def validate_template_package(archive_path: str | Path, limits: ArchiveLimits) -> ValidatedTemplatePackage:
    """校验 ZIP 只含 frontend/backend 和唯一 V3 TemplateState。"""

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
                return ValidatedTemplatePackage(path, TemplateStateV3.model_validate(state))
            except ValueError as exc:
                raise TemplatePackageError("TEMPLATE_STATE_SCHEMA_UNSUPPORTED：Bootstrap Package 未提供 V3 TemplateState。") from exc
    except zipfile.BadZipFile as exc:
        raise TemplatePackageError("模板 ZIP 已损坏或格式无效。") from exc


def _validate_required_roots(entries: list[zipfile.ZipInfo]) -> None:
    """拒绝 V3 Generate ZIP 中 frontend/backend/State 外的任何路径。"""

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
    for entry in entries:
        name = entry.filename.rstrip("/")
        if name in {"frontend", "backend", ".devagentstudio"} and entry.is_dir():
            continue
        if name.startswith("frontend/") or name.startswith("backend/") or name == _STATE_PATH:
            continue
        raise TemplatePackageError("V3 Generate ZIP 包含未授权路径：" + entry.filename)
    if sum(entry.filename == _STATE_PATH for entry in entries if not entry.is_dir()) != 1:
        raise TemplatePackageError("V3 Generate ZIP 必须包含唯一 .devagentstudio/template-state.json。")
