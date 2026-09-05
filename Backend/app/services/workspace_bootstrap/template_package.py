"""校验 Template Engine `/v1/generate` ZIP 的固定根目录契约。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath

from app.services.template_state import validate_template_state
from app.services.workspace_bootstrap.archive_security import validate_archive_entries
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError, ValidatedTemplatePackage

_STATE_PATH = ".xcodeagent/template-state.json"
_MANAGED_ROOTS = frozenset({"frontend", "backend"})


def validate_template_package(archive_path: str | Path, limits: ArchiveLimits) -> ValidatedTemplatePackage:
    """校验 ZIP 安全性、唯一 State 和 frontend/backend 固定顶层根。"""

    path = Path(archive_path)
    try:
        with zipfile.ZipFile(path) as package:
            entries = validate_archive_entries(package, limits)
            files = [entry for entry in entries if not entry.is_dir()]
            _validate_roots(files)
            state_entries = [entry for entry in files if entry.filename == _STATE_PATH]
            if len(state_entries) != 1:
                raise TemplatePackageError("模板 ZIP 必须且只能包含 .xcodeagent/template-state.json。")
            try:
                state = json.loads(package.read(state_entries[0]).decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as exc:
                raise TemplatePackageError("模板 ZIP 中的 TemplateState 无法读取。") from exc
            return ValidatedTemplatePackage(path, validate_template_state(state))
    except zipfile.BadZipFile as exc:
        raise TemplatePackageError("模板 ZIP 已损坏或格式无效。") from exc


def _validate_roots(entries: list[zipfile.ZipInfo]) -> None:
    """以 exact allow-list 限制初次 Bootstrap 可物化的全部根路径。"""

    seen_roots: set[str] = set()
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if entry.filename == _STATE_PATH:
            continue
        root = path.parts[0] if path.parts else ""
        if root not in _MANAGED_ROOTS:
            raise TemplatePackageError("模板 ZIP 仅允许 frontend、backend 和唯一 TemplateState。")
        if len(path.parts) < 2:
            raise TemplatePackageError("模板 ZIP 不允许在 managed root 放置顶层普通文件。")
        seen_roots.add(root)
    if seen_roots != _MANAGED_ROOTS:
        raise TemplatePackageError("模板 ZIP 必须同时包含 frontend 和 backend 文件。")
