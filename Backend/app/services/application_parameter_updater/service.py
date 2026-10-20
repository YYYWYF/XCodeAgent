"""配置文件定点预览、原始字节比较与失败恢复。"""

from __future__ import annotations

import os
import re
import stat
import tempfile
import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.application_parameter_updater.editors import ParameterUpdateError, edit_auth_yaml, edit_dotenv

_FRONTEND = "frontend/.env"
_BACKEND = "backend/src/main/resources/application.yml"
_JOURNAL = ".devagentstudio/runtime/application-parameter-update-journal.json"
_ENV_PATHS = {
    "PUBLIC_APP_NAME": "appName", "PUBLIC_APP_ICON": "appIcon",
    "PUBLIC_THEME_PRIMARY_COLOR": "theme.primaryColor", "PUBLIC_YHT_CLIENT_ID": "auth.yht.clientId",
}
_YAML_PATHS = {
    "client-id": "auth.yht.clientId", "private-key": "auth.yht.privateKey",
    "public-key": "auth.yht.publicKey", "center-public-key": "auth.yht.centerPublicKey",
}


@dataclass(frozen=True)
class _FileChange:
    """只在内存中保存目标文件的原始字节和目标字节。"""

    path: str
    before: bytes
    after: bytes


@dataclass(frozen=True)
class ParameterUpdatePlan:
    """表示一个基于原始字节快照的参数变更计划。"""

    changed_files: tuple[str, ...]
    changed_keys: tuple[str, ...]
    _changes: tuple[_FileChange, ...] = field(repr=False)


class ApplicationParameterUpdater:
    """仅管理模板冻结的 dotenv 和 auth YAML 字段。"""

    def preview(self, workspace: str | Path, desired_config: dict[str, Any], *, login_applied: bool) -> ParameterUpdatePlan:
        """不写磁盘地验证目标文件并生成目标字节。"""

        root = Path(workspace).expanduser().resolve()
        self.recover(root)
        theme = desired_config.get("theme")
        auth = desired_config.get("auth")
        if not isinstance(theme, dict) or not isinstance(auth, dict):
            raise ParameterUpdateError("CONFIG_VALUE_INVALID：应用配置结构无效。")
        env = {
            "PUBLIC_APP_NAME": desired_config.get("appName"),
            "PUBLIC_APP_ICON": desired_config.get("appIcon"),
            "PUBLIC_THEME_PRIMARY_COLOR": theme.get("primaryColor"),
        }
        if login_applied:
            yht = auth.get("yht")
            if not isinstance(yht, dict):
                raise ParameterUpdateError("CONFIG_VALUE_INVALID：一号通配置缺失。")
            env["PUBLIC_YHT_CLIENT_ID"] = yht.get("clientId")
        env_before = self._read(root, _FRONTEND)
        env_after = edit_dotenv(env_before, env)
        changes: list[_FileChange] = []
        keys: list[str] = []
        if env_after != env_before:
            changes.append(_FileChange(_FRONTEND, env_before, env_after))
            keys.extend(_changed_leaf_keys(env_before, env_after, _ENV_PATHS, indent=""))
        if login_applied:
            yht = auth["yht"]
            yaml_values = {
                "client-id": yht.get("clientId"),
                "private-key": yht.get("privateKey"),
                "public-key": yht.get("publicKey"),
                "center-public-key": yht.get("centerPublicKey"),
            }
            yaml_before = self._read(root, _BACKEND)
            yaml_after = edit_auth_yaml(yaml_before, yaml_values)
            if yaml_after != yaml_before:
                changes.append(_FileChange(_BACKEND, yaml_before, yaml_after))
                keys.extend(_changed_leaf_keys(yaml_before, yaml_after, _YAML_PATHS, indent="  "))
        return ParameterUpdatePlan(tuple(change.path for change in changes), tuple(dict.fromkeys(keys)), tuple(changes))

    def apply(self, workspace: str | Path, plan: ParameterUpdatePlan) -> None:
        """提交预检文件；任一步失败均用 before-image 恢复已写文件。"""

        root = Path(workspace).expanduser().resolve()
        self.recover(root)
        for change in plan._changes:
            if self._read(root, change.path) != change.before:
                raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：参数目标文件已变化，请重新预览。")
        committed: list[_FileChange] = []
        self._write_journal(root, plan._changes)
        try:
            for change in plan._changes:
                self._replace(root, change.path, change.after)
                committed.append(change)
        except Exception as exc:
            try:
                for change in reversed(committed):
                    self._replace(root, change.path, change.before)
                self._clear_journal(root)
            except Exception as rollback_error:
                raise ParameterUpdateError("CONFIG_RECOVERY_REQUIRED：参数文件写入中断，等待下次安全恢复。") from rollback_error
            raise ParameterUpdateError("CONFIG_APPLY_FAILED：参数文件写入失败，已恢复原始内容。") from exc
        self._clear_journal(root)

    def recover(self, workspace: str | Path) -> None:
        """恢复异常退出留下的参数 before-image，拒绝覆盖外部后续修改。"""

        root = Path(workspace).expanduser().resolve()
        journal = root / _JOURNAL
        if not journal.exists():
            return
        if journal.is_symlink() or not journal.is_file():
            raise ParameterUpdateError("CONFIG_RECOVERY_REQUIRED：参数更新 journal 无效。")
        try:
            payload = json.loads(journal.read_text(encoding="utf-8"))
            entries = payload["files"]
            if payload.get("version") != 1 or payload.get("phase") != "PREPARED" or not isinstance(entries, list):
                raise ValueError("invalid journal")
            changes = tuple(self._journal_change(entry) for entry in entries)
        except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise ParameterUpdateError("CONFIG_RECOVERY_REQUIRED：参数更新 journal 损坏。") from exc
        for change in changes:
            current = self._read(root, change.path)
            if current not in {change.before, change.after}:
                raise ParameterUpdateError("CONFIG_RECOVERY_REQUIRED：参数目标已被外部修改。")
        try:
            for change in reversed(changes):
                if self._read(root, change.path) == change.after:
                    self._replace(root, change.path, change.before)
        except Exception as exc:
            raise ParameterUpdateError("CONFIG_RECOVERY_REQUIRED：参数更新恢复失败。") from exc
        self._clear_journal(root)

    def _read(self, root: Path, relative: str) -> bytes:
        """拒绝符号链接和缺失目标，不跟随工作区外路径。"""

        target = root / relative
        if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent != root and root in parent.parents):
            raise ParameterUpdateError("CONFIG_TARGET_CONFLICT：参数目标路径包含符号链接。")
        if not target.is_file() or root not in target.resolve().parents:
            raise ParameterUpdateError(f"CONFIG_TARGET_CONFLICT：{relative} 不是工作区内普通文件。")
        return target.read_bytes()

    def _replace(self, root: Path, relative: str, content: bytes) -> None:
        """以同目录临时文件和原子替换写入目标字节。"""

        target = root / relative
        descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            os.fchmod(descriptor, stat.S_IMODE(target.stat().st_mode))
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _write_journal(self, root: Path, changes: tuple[_FileChange, ...]) -> None:
        """在任何参数文件写入前落盘仅本机可读的恢复记录。"""

        if not changes:
            return
        journal = root / _JOURNAL
        if journal.exists():
            raise ParameterUpdateError("CONFIG_RECOVERY_REQUIRED：存在未恢复的参数更新 journal。")
        journal.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "phase": "PREPARED",
            "files": [
                {
                    "path": change.path,
                    "before": base64.b64encode(change.before).decode("ascii"),
                    "after": base64.b64encode(change.after).decode("ascii"),
                }
                for change in changes
            ],
        }
        descriptor, name = tempfile.mkstemp(prefix=".application-parameter-", suffix=".tmp", dir=journal.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, journal)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _journal_change(self, entry: Any) -> _FileChange:
        """从受限 journal 解码白名单文件的 before/after image。"""

        if not isinstance(entry, dict) or entry.get("path") not in {_FRONTEND, _BACKEND}:
            raise ValueError("invalid journal entry")
        before = base64.b64decode(entry["before"], validate=True)
        after = base64.b64decode(entry["after"], validate=True)
        return _FileChange(str(entry["path"]), before, after)

    def _clear_journal(self, root: Path) -> None:
        """在提交或恢复完成后删除本次短暂的密钥材料副本。"""

        journal = root / _JOURNAL
        if journal.exists():
            if journal.is_symlink() or not journal.is_file():
                raise ParameterUpdateError("CONFIG_RECOVERY_REQUIRED：参数更新 journal 无效。")
            journal.unlink()


def _changed_leaf_keys(before: bytes, after: bytes, mapping: dict[str, str], *, indent: str) -> list[str]:
    """比较白名单字段原行，仅向结果暴露变化字段名称。"""

    def extract(content: bytes) -> dict[str, str]:
        """从本次编辑器已验证的文本中提取受管字段行。"""

        result: dict[str, str] = {}
        for line in content.decode("utf-8").splitlines():
            for key in mapping:
                separator = ":" if indent else "="
                match = re.match(rf"^{re.escape(indent + key)}\s*{re.escape(separator)}(.*)$", line)
                if match:
                    result[key] = match.group(1)
        return result

    old, new = extract(before), extract(after)
    return [path for key, path in mapping.items() if old.get(key) != new.get(key)]
