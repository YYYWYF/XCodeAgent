"""校验 V3 Extension Update Package 的 ZIP、Operation 和 payload。"""
from __future__ import annotations
import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from pydantic import ValidationError
from app.services.template_reconcile.digest_v3 import template_state_digest_v3
from app.services.template_reconcile.protocol_v3 import AddFileOperationV3, ExtensionUpdatePackageV3, ReplaceManagedFileOperationV3
from app.services.workspace_bootstrap.archive_security import validate_archive_entries
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError

_PACKAGE_ENTRY = "extension-update-package.json"
_PAYLOAD_PREFIX = "payload/"

@dataclass(frozen=True)
class ValidatedUpdatePackageV3:
    """保存已通过 V3 预检的 Package 和原始 payload 字节。"""
    archive_path: Path
    package: ExtensionUpdatePackageV3
    payloads: dict[str, bytes]

def validate_update_package_v3(archive_path: str | Path, limits: ArchiveLimits) -> ValidatedUpdatePackageV3:
    """验证 ZIP 布局、严格元数据和全部 payload digest。"""
    path = Path(archive_path)
    try:
        if path.stat().st_size > limits.max_package_bytes:
            raise TemplatePackageError("V3 Update ZIP 超过压缩包大小限制。")
        with zipfile.ZipFile(path) as archive:
            entries = validate_archive_entries(archive, limits)
            files = [entry for entry in entries if not entry.is_dir()]
            if sum(entry.filename == _PACKAGE_ENTRY for entry in files) != 1:
                raise TemplatePackageError("V3 Update ZIP 必须包含唯一 extension-update-package.json。")
            try:
                package = ExtensionUpdatePackageV3.model_validate_json(archive.read(_PACKAGE_ENTRY))
            except (KeyError, ValidationError, UnicodeError, ValueError) as exc:
                raise TemplatePackageError("V3 Extension Update Package 元数据不符合冻结协议。") from exc
            names = {entry.filename for entry in files}
            for entry in entries:
                name = entry.filename.rstrip("/")
                if name == _PACKAGE_ENTRY or (name == "payload" and entry.is_dir()):
                    continue
                if not name.startswith(_PAYLOAD_PREFIX):
                    raise TemplatePackageError("V3 Update ZIP 包含未授权条目。")
                relative = name.removeprefix(_PAYLOAD_PREFIX)
                parsed = PurePosixPath(relative)
                if not relative or parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
                    raise TemplatePackageError("V3 Update ZIP payload 路径无效。")
            refs = {item.payloadRef for item in package.operations if isinstance(item, (AddFileOperationV3, ReplaceManagedFileOperationV3))}
            actual = {name for name in names if name.startswith(_PAYLOAD_PREFIX)}
            if refs != set(package.payloadManifest) or refs != actual:
                raise TemplatePackageError("payloadRef、payloadManifest 与 ZIP 条目必须完全一致。")
            payloads: dict[str, bytes] = {}
            for ref, descriptor in package.payloadManifest.items():
                content = archive.read(ref)
                if len(content) != descriptor.size or "sha256:" + hashlib.sha256(content).hexdigest() != descriptor.sha256:
                    raise TemplatePackageError("V3 payload 的 size 或 sha256 不匹配。")
                payloads[ref] = content
            if package.nextStateDigest != template_state_digest_v3(package.nextTemplateState):
                raise TemplatePackageError("V3 Update Package 的 nextStateDigest 未绑定 nextTemplateState。")
            return ValidatedUpdatePackageV3(path, package, payloads)
    except zipfile.BadZipFile as exc:
        raise TemplatePackageError("V3 Update ZIP 已损坏或格式无效。") from exc
