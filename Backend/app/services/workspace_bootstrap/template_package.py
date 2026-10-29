"""校验 Template Engine V3 `/v1/generate` ZIP 的 Bootstrap 契约。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path, PurePosixPath

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.template_reconcile.protocol_v3 import TemplateStateV3
from app.services.workspace_bootstrap.archive_security import validate_archive_entries
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError, ValidatedTemplatePackage
from app.services.workspace_bootstrap.template_layout import template_roots, validate_template_layout_state

_STATE_PATH = (WORKSPACE_ARTIFACT_DIR / "template-state.json").as_posix()
_TEMPLATE_CONTRACTS_DIR = (WORKSPACE_ARTIFACT_DIR / "template-contracts").as_posix()
_TEMPLATE_CONTRACTS_PREFIX = _TEMPLATE_CONTRACTS_DIR + "/"


def validate_template_package(
    archive_path: str | Path, limits: ArchiveLimits, *, requested_config: dict | None = None,
) -> ValidatedTemplatePackage:
    """按显式请求校验受管根、路由契约和唯一 V3 State，不推断或补造模板。"""

    path = Path(archive_path)
    try:
        with zipfile.ZipFile(path) as package:
            entries = validate_archive_entries(package, limits)

            try:
                state = json.loads(package.read(_STATE_PATH).decode("utf-8"))
            except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError) as exc:
                raise TemplatePackageError("模板 ZIP 中的 TemplateState 无法读取。") from exc

            try:
                validated = TemplateStateV3.model_validate(state)
            except ValueError as exc:
                raise TemplatePackageError("TEMPLATE_STATE_SCHEMA_UNSUPPORTED：Bootstrap Package 未提供 V3 TemplateState。") from exc
            try:
                roots = template_roots(requested_config["capabilities"] if requested_config is not None else validated.requested)
                validate_template_layout_state(validated, roots)
                if requested_config is not None and {
                    name: item.model_dump(mode="json") for name, item in validated.requested.items()
                } != requested_config["capabilities"]:
                    raise ValueError("TemplateState.requested 与当前 Generate 请求不一致。")
            except (ValueError, KeyError, TypeError) as exc:
                raise TemplatePackageError(str(exc)) from exc
            _validate_required_roots(entries, roots)
            return ValidatedTemplatePackage(path, validated)
    except zipfile.BadZipFile as exc:
        raise TemplatePackageError("模板 ZIP 已损坏或格式无效。") from exc


def _validate_required_roots(entries: list[zipfile.ZipInfo], required_roots: tuple[str, ...]) -> None:
    """仅允许请求布局的源码根、整个契约目录和唯一 State。"""

    roots = {
        PurePosixPath(entry.filename).parts[0]
        for entry in entries
        if PurePosixPath(entry.filename).parts
    }
    missing = set(required_roots) - roots
    if missing:
        raise TemplatePackageError(
            "模板 ZIP 缺少请求的受管目录："
            + "、".join(sorted(missing))
        )
    for entry in entries:
        name = entry.filename.rstrip("/")
        if name in {*required_roots, WORKSPACE_ARTIFACT_DIR.as_posix(), _TEMPLATE_CONTRACTS_DIR} and entry.is_dir():
            continue
        # 按带斜杠的目录前缀开放契约文件和子目录，避免放行同名前缀的相邻目录。
        if (
            any(name.startswith(root + "/") for root in required_roots)
            or name == _STATE_PATH
            or name.startswith(_TEMPLATE_CONTRACTS_PREFIX)
        ):
            continue
        raise TemplatePackageError("V3 Generate ZIP 包含未授权路径：" + entry.filename)
    if sum(entry.filename == _STATE_PATH for entry in entries if not entry.is_dir()) != 1:
        raise TemplatePackageError("V3 Generate ZIP 必须包含唯一 .devagentstudio/template-state.json。")
