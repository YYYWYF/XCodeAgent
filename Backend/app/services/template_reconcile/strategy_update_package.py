"""校验 V2 Strategy Update Package 的 ZIP 布局与 payload 完整性。"""

from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from app.services.template_reconcile.protocol_v2 import StrategyUpdatePackageV2
from app.services.workspace_bootstrap.archive_security import validate_archive_entries
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError

_PACKAGE_ENTRY = "strategy-update-package.json"
_PAYLOAD_PREFIX = "payload/"


@dataclass(frozen=True)
class ValidatedStrategyUpdatePackage:
    """保存已验证的不可变 V2 Package 及其权威 DTO。"""

    archive_path: Path
    package: StrategyUpdatePackageV2


def validate_strategy_update_package(
    archive_path: str | Path, limits: ArchiveLimits
) -> ValidatedStrategyUpdatePackage:
    """校验 ZIP 固定元数据、严格 DTO 和每个声明 payload 的字节摘要。"""

    path = Path(archive_path)
    try:
        with zipfile.ZipFile(path) as archive:
            files = [entry for entry in validate_archive_entries(archive, limits) if not entry.is_dir()]
            names = {entry.filename for entry in files}
            if sum(entry.filename == _PACKAGE_ENTRY for entry in files) != 1:
                raise TemplatePackageError("V2 Strategy Package 必须且只能包含一个元数据条目。")
            package = _parse_package(archive)
            _validate_entry_names(names)
            _validate_payloads(archive, names, package)
            return ValidatedStrategyUpdatePackage(archive_path=path, package=package)
    except zipfile.BadZipFile as exc:
        raise TemplatePackageError("V2 Strategy Package ZIP 已损坏或格式无效。") from exc


def _parse_package(archive: zipfile.ZipFile) -> StrategyUpdatePackageV2:
    """解析并严格校验唯一的 Strategy Package 元数据。"""

    try:
        return StrategyUpdatePackageV2.model_validate_json(archive.read(_PACKAGE_ENTRY))
    except (KeyError, ValidationError, UnicodeError, ValueError) as exc:
        raise TemplatePackageError("V2 Strategy Package 元数据不符合冻结协议。") from exc


def _validate_entry_names(names: set[str]) -> None:
    """拒绝元数据及其显式 payload 之外的任意 ZIP 文件。"""

    for name in names:
        if name == _PACKAGE_ENTRY:
            continue
        if not name.startswith(_PAYLOAD_PREFIX):
            raise TemplatePackageError("V2 Strategy Package 包含未授权条目。")
        relative = name.removeprefix(_PAYLOAD_PREFIX)
        path = PurePosixPath(relative)
        if not relative or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise TemplatePackageError("V2 Strategy Package payload 路径无效。")


def _validate_payloads(
    archive: zipfile.ZipFile, names: set[str], package: StrategyUpdatePackageV2
) -> None:
    """要求 manifest、Strategy 引用和 ZIP payload 集合完全一致。"""

    expected = set(package.payloadManifest)
    actual = {name for name in names if name.startswith(_PAYLOAD_PREFIX)}
    if any(not payload_ref.startswith(_PAYLOAD_PREFIX) for payload_ref in expected):
        raise TemplatePackageError("V2 Strategy Package 的 payloadRef 必须位于 payload/ 下。")
    if expected != actual:
        raise TemplatePackageError("V2 Strategy Package 的 payloadManifest 与 ZIP 条目不一致。")
    for payload_ref, descriptor in package.payloadManifest.items():
        content = archive.read(payload_ref)
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        if len(content) != descriptor.size or digest != descriptor.sha256:
            raise TemplatePackageError("V2 Strategy Package payload 摘要或大小不匹配。")
