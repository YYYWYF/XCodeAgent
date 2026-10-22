"""无 SHA 的 V4 增量包安全校验。"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from app.services.template_reconcile.protocol_v3 import AddFileOperationV3, ReplaceManagedFileOperationV3
from app.services.template_reconcile.protocol_v4 import EnsureCapabilityConfigOperationV4, ExtensionUpdatePackageV4
from app.services.workspace_bootstrap.archive_security import validate_archive_entries
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError


@dataclass(frozen=True)
class ValidatedUpdatePackageV4:
    """保留已通过 V4 安全校验的包和 payload 字节。"""

    archive_path: Path
    package: ExtensionUpdatePackageV4
    payloads: dict[str, bytes]


def validate_update_package_v4(archive_path: str | Path, limits: ArchiveLimits) -> ValidatedUpdatePackageV4:
    """验证 ZIP 边界、V4 严格字段和 payload 大小/双向引用。"""

    path = Path(archive_path)
    try:
        if path.stat().st_size > limits.max_package_bytes:
            raise TemplatePackageError("V4 Update ZIP 超过压缩包大小限制。")
        with zipfile.ZipFile(path) as archive:
            entries = validate_archive_entries(archive, limits)
            files = [entry for entry in entries if not entry.is_dir()]
            names = {entry.filename for entry in files}
            if sum(entry.filename == "extension-update-package.json" for entry in files) != 1:
                raise TemplatePackageError("V4 Update ZIP 缺少唯一元数据文件。")
            try:
                package = ExtensionUpdatePackageV4.model_validate_json(archive.read("extension-update-package.json"))
            except (KeyError, ValidationError, ValueError, UnicodeError) as exc:
                raise TemplatePackageError("V4 Update Package 元数据无效。") from exc
            for entry in entries:
                name = entry.filename.rstrip("/")
                if name == "extension-update-package.json" or (name == "payload" and entry.is_dir()):
                    continue
                if not name.startswith("payload/"):
                    raise TemplatePackageError("V4 Update ZIP 包含未授权条目。")
                suffix = name.removeprefix("payload/")
                parts = PurePosixPath(suffix)
                if not suffix or parts.is_absolute() or any(part in {"", ".", ".."} for part in parts.parts):
                    raise TemplatePackageError("V4 payload 路径无效。")
            refs = {item.payloadRef for item in package.operations if isinstance(item, (AddFileOperationV3, ReplaceManagedFileOperationV3, EnsureCapabilityConfigOperationV4))}
            actual = {name for name in names if name.startswith("payload/")}
            if refs != set(package.payloadManifest) or refs != actual:
                raise TemplatePackageError("V4 payloadRef、manifest 与 ZIP 条目不一致。")
            payloads: dict[str, bytes] = {}
            for ref, descriptor in package.payloadManifest.items():
                content = archive.read(ref)
                if len(content) != descriptor.size:
                    raise TemplatePackageError("V4 payload 大小不匹配。")
                payloads[ref] = content
            return ValidatedUpdatePackageV4(path, package, payloads)
    except zipfile.BadZipFile as exc:
        raise TemplatePackageError("V4 Update ZIP 损坏或格式无效。") from exc
