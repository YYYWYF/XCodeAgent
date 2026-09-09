"""校验当前 Engine `/v1/update` 返回的受限 ZIP Package。"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from app.services.template_reconcile.models import ChangeSetBody, TemplateState
from app.services.workspace_bootstrap.archive_security import validate_archive_entries
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError

_CHANGE_SET_PATH = "change-set.json"
_NEXT_STATE_PATH = "next-template-state.json"
_PAYLOAD_PREFIX = "payload/"


@dataclass(frozen=True)
class ValidatedUpdatePackage:
    """保存已校验 Update Package 的权威操作、目标 State 与下载摘要。"""

    archive_path: Path
    change_set: ChangeSetBody
    next_template_state: TemplateState


def validate_update_package(archive_path: str | Path, limits: ArchiveLimits) -> ValidatedUpdatePackage:
    """校验 ZIP 安全性、固定条目、Payload 与操作内容的一致性。"""

    path = Path(archive_path)
    try:
        with zipfile.ZipFile(path) as package:
            files = [entry for entry in validate_archive_entries(package, limits) if not entry.is_dir()]
            names = {entry.filename for entry in files}
            if _CHANGE_SET_PATH not in names or _NEXT_STATE_PATH not in names:
                raise TemplatePackageError("模板更新 ZIP 缺少 change-set.json 或 next-template-state.json。")
            if sum(entry.filename == _CHANGE_SET_PATH for entry in files) != 1 or sum(entry.filename == _NEXT_STATE_PATH for entry in files) != 1:
                raise TemplatePackageError("模板更新 ZIP 的元数据条目必须唯一。")
            _validate_entry_names(names)
            change_set = _parse_change_set(package)
            next_state = _parse_next_state(package)
            _validate_payloads(package, names, change_set)
            return ValidatedUpdatePackage(path, change_set, next_state)
    except zipfile.BadZipFile as exc:
        raise TemplatePackageError("模板更新 ZIP 已损坏或格式无效。") from exc


def _parse_change_set(package: zipfile.ZipFile) -> ChangeSetBody:
    """读取并按当前三类文件操作模型校验 ChangeSet。"""

    try:
        return ChangeSetBody.model_validate_json(package.read(_CHANGE_SET_PATH))
    except (KeyError, ValidationError, UnicodeError) as exc:
        raise TemplatePackageError("模板更新 ChangeSet 不符合当前 Engine 契约。") from exc


def _parse_next_state(package: zipfile.ZipFile) -> TemplateState:
    """读取并按当前四字段模型校验目标 TemplateState。"""

    try:
        return TemplateState.model_validate_json(package.read(_NEXT_STATE_PATH))
    except (KeyError, ValidationError, UnicodeError) as exc:
        raise TemplatePackageError("模板更新目标 TemplateState 不符合当前 Engine 契约。") from exc


def _validate_entry_names(names: set[str]) -> None:
    """拒绝固定元数据之外的根目录文件与非 payload 条目。"""

    for name in names:
        if name in {_CHANGE_SET_PATH, _NEXT_STATE_PATH}:
            continue
        if not name.startswith(_PAYLOAD_PREFIX):
            raise TemplatePackageError("模板更新 ZIP 只允许元数据和 payload 文件。")
        relative = name.removeprefix(_PAYLOAD_PREFIX)
        path = PurePosixPath(relative)
        if not relative or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise TemplatePackageError("模板更新 Payload 路径无效。")


def _validate_payloads(package: zipfile.ZipFile, names: set[str], change_set: ChangeSetBody) -> None:
    """要求 ADD/UPDATE 有同内容 Payload，DELETE 绝不携带 Payload。"""

    expected_payloads: set[str] = set()
    for operation in change_set.operations:
        payload_name = f"{_PAYLOAD_PREFIX}{operation.path}"
        if operation.type == "DELETE_FILE":
            if payload_name in names:
                raise TemplatePackageError("DELETE_FILE 不允许携带 Payload。")
            continue
        expected_payloads.add(payload_name)
        if payload_name not in names:
            raise TemplatePackageError("ADD_FILE 或 UPDATE_FILE 缺少对应 Payload。")
        try:
            content = package.read(payload_name).decode("utf-8")
        except (KeyError, UnicodeError) as exc:
            raise TemplatePackageError("模板更新 Payload 必须是 UTF-8 文本。") from exc
        if content != operation.content:
            raise TemplatePackageError("模板更新 Payload 与 ChangeSet 内容不一致。")
    actual_payloads = {name for name in names if name.startswith(_PAYLOAD_PREFIX)}
    if actual_payloads != expected_payloads:
        raise TemplatePackageError("模板更新 ZIP 包含未声明的 Payload。")
