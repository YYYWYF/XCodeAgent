"""V4 受管文件与 TemplateState 的无摘要持久事务。"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.template_reconcile.executor_v3 import WorkingCopyStoreV3, _target
from app.services.workspace_bootstrap.models import TemplateStateError
from app.utils.atomic_json import atomic_write_json

_JOURNAL = WORKSPACE_ARTIFACT_DIR / "runtime/template-update-v4-journal"


def recover_v4_transaction(workspace: str | Path) -> None:
    """在新请求前恢复未提交事务，或清除已提交的残留备份。"""

    root = Path(workspace).expanduser().resolve()
    journal = root / _JOURNAL
    if not journal.exists():
        return
    manifest = journal / "manifest.json"
    if not manifest.exists():
        # 清单只有在全部备份落盘后才创建；缺清单意味着尚未触碰工程文件。
        for backup in journal.iterdir():
            suffix = backup.suffix
            if backup.is_symlink() or suffix not in {".before", ".after"} or not backup.stem.isdigit():
                raise TemplateStateError("TEMPLATE_UPDATE_RECOVERY_REQUIRED：V4 备份目录含未知文件。")
            backup.unlink()
        journal.rmdir()
        return
    try:
        record = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TemplateStateError("TEMPLATE_UPDATE_RECOVERY_REQUIRED：V4 事务记录损坏，不能继续更新。") from exc
    if not isinstance(record, dict) or record.get("version") != 1 or record.get("phase") not in {"PREPARED", "COMMITTED"} or not isinstance(record.get("files"), list):
        raise TemplateStateError("TEMPLATE_UPDATE_RECOVERY_REQUIRED：V4 事务记录无效。")
    if record["phase"] == "PREPARED":
        for index, entry in reversed(list(enumerate(record["files"]))):
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or type(entry.get("existed")) is not bool:
                raise TemplateStateError("TEMPLATE_UPDATE_RECOVERY_REQUIRED：V4 恢复目标无效。")
            target = _target(root, entry["path"])
            backup = journal / f"{index}.before"
            after = journal / f"{index}.after"
            if not after.is_file() or after.is_symlink():
                raise TemplateStateError("TEMPLATE_UPDATE_RECOVERY_REQUIRED：V4 after-image 缺失。")
            if entry["existed"] and (not backup.is_file() or backup.is_symlink()):
                raise TemplateStateError("TEMPLATE_UPDATE_RECOVERY_REQUIRED：V4 before-image 缺失。")
            actual = target.read_bytes() if target.exists() else None
            before = backup.read_bytes() if entry["existed"] and backup.is_file() else None
            if actual not in {before, after.read_bytes()}:
                raise TemplateStateError("MANAGED_SURFACE_CONFLICT：恢复期间目标已被外部修改。")
            if entry["existed"]:
                _atomic_replace(target, before, mode=entry.get("mode"))
            elif target.exists():
                target.unlink()
    _clear_journal(journal, len(record["files"]))


def apply_working_copy_v4(workspace: str | Path, store: WorkingCopyStoreV3) -> None:
    """先持久化全部 before-image，再原子替换受管文件和 State。"""

    root = Path(workspace).expanduser().resolve()
    recover_v4_transaction(root)
    changes = store.changed()
    if not changes:
        return
    for entry in changes:
        target = _target(root, entry.path)
        actual = target.read_bytes() if target.exists() else None
        if actual != entry.original:
            raise TemplateStateError("MANAGED_SURFACE_CONFLICT：V4 目标在预检后被修改。")
    journal = root / _JOURNAL
    journal.mkdir(parents=True, mode=0o700, exist_ok=False)
    files: list[dict[str, object]] = []
    try:
        for index, entry in enumerate(changes):
            target = _target(root, entry.path)
            file_mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
            files.append({"path": entry.path, "existed": entry.original is not None, "mode": file_mode})
            if entry.original is not None:
                _atomic_replace(journal / f"{index}.before", entry.original, mode=0o600)
            _atomic_replace(journal / f"{index}.after", entry.working or b"", mode=0o600)
        record = {"version": 1, "phase": "PREPARED", "files": files}
        atomic_write_json(journal / "manifest.json", record)
        for entry, metadata in zip(changes, files, strict=True):
            target = _target(root, entry.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.working is None:
                target.unlink(missing_ok=True)
            else:
                _atomic_replace(target, entry.working, mode=metadata["mode"])
        record["phase"] = "COMMITTED"
        atomic_write_json(journal / "manifest.json", record)
        _clear_journal(journal, len(files))
    except Exception:
        if (journal / "manifest.json").is_file():
            recover_v4_transaction(root)
        else:
            _clear_journal(journal, len(files))
        raise


def _atomic_replace(target: Path, content: bytes, *, mode: int | None) -> None:
    """用权限受限的同目录文件持久写入指定原始字节。"""

    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.v4.", dir=target.parent)
    try:
        if mode is not None:
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _clear_journal(journal: Path, file_count: int) -> None:
    """仅删除本次事务命名的备份和清单。"""

    for index in range(file_count):
        (journal / f"{index}.before").unlink(missing_ok=True)
        (journal / f"{index}.after").unlink(missing_ok=True)
    (journal / "manifest.json").unlink(missing_ok=True)
    journal.rmdir()
