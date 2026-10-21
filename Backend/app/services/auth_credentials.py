"""文件是用户 token 的唯一持久来源；会话栅栏阻止迟到的登录写入。"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Callable
from pydantic import BaseModel, ConfigDict, Field
from app.branding import USER_DATA_DIRECTORY_NAMES, DEFAULT_USER_DATA_DIRECTORY_NAME
from app.services.auth_protection import protect


class AuthCredentials(BaseModel):
    """校验保存在加密文件中的当前认证凭据。"""
    model_config = ConfigDict(extra="forbid")
    access_token: str = Field(min_length=1)
    refresh_token: str = ""
    token_type: str = "Bearer"
    expires_at_ms: int = Field(gt=0)
    refresh_expires_at_ms: int = 0
    user_id: str = Field(min_length=1)
    user_name: str = Field(min_length=1)
    yst_id_token: str = ""
    yst_refresh_token: str = ""


class CredentialStore:
    """集中处理凭据文件、环境校验和桌面会话所有权。"""
    def __init__(self, root: Path | None = None, codec: Callable = protect):
        """定位独立的认证子目录，允许测试注入临时目录和加密器。"""
        self.working_dir = os.getenv("DEVAGENTSTUDIO_WORKING_DIR", DEFAULT_USER_DATA_DIRECTORY_NAME)
        if self.working_dir not in USER_DATA_DIRECTORY_NAMES:
            raise RuntimeError("DEVAGENTSTUDIO_WORKING_DIR 不是受支持的用户目录。")
        self.root = root if root is not None else Path.home() / self.working_dir / "yst-login"
        self.path = self.root / "auth.json"
        self.session_path = self.root / "session.json"
        self.codec = codec

    def _check_directory(self) -> None:
        """拒绝符号链接路径，并确保认证目录存在。"""
        if any(item.is_symlink() for item in (self.root, *self.root.parents)):
            raise RuntimeError("认证目录不允许使用符号链接。")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink() or self.session_path.is_symlink():
            raise RuntimeError("认证文件不允许使用符号链接。")

    def _atomic_write(self, path: Path, payload: dict) -> None:
        """先完整写入同目录临时文件，再原子替换正式文件。"""
        self._check_directory()
        descriptor, name = tempfile.mkstemp(prefix=".auth-", suffix=".tmp", dir=self.root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def initialize(self, session_id: str) -> None:
        """新 Electron 会话先建立栅栏，再删除上次异常退出残留。"""
        self._atomic_write(self.session_path, {"sessionId": session_id, "active": True})
        self.clear()

    def require_session(self, session_id: str) -> None:
        """每次操作和最终写入前校验当前桌面会话仍有效。"""
        self._check_directory()
        try:
            session = json.loads(self.session_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise RuntimeError("桌面认证会话未初始化，请重新启动应用。") from None
        if session != {"sessionId": session_id, "active": True}:
            raise RuntimeError("桌面认证会话已结束或已被替换。")

    def save(self, session_id: str, credentials: AuthCredentials) -> None:
        """凭据加密后原子落盘，不在实例属性中缓存 token。"""
        encrypted = self.codec(credentials.model_dump_json().encode("utf-8"))
        self.require_session(session_id)
        self._atomic_write(self.path, {"ciphertext": base64.b64encode(encrypted).decode("ascii")})

    def read(self, session_id: str) -> AuthCredentials | None:
        """读取当前文件；缺失、损坏或即将过期时要求重新登录。"""
        self.require_session(session_id)
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            encrypted = base64.b64decode(payload["ciphertext"], validate=True)
            credentials = AuthCredentials.model_validate_json(self.codec(encrypted, decrypt=True))
            if credentials.expires_at_ms <= int(time.time() * 1000) + 30_000:
                self.clear()
                return None
            return credentials
        except Exception:
            self.clear()
            return None

    def clear(self) -> None:
        """删除 token 和本模块创建的临时文件，不影响其他应用数据。"""
        self._check_directory()
        self.path.unlink(missing_ok=True)
        for temporary in self.root.glob(".auth-*.tmp"):
            temporary.unlink(missing_ok=True)

    def invalidate(self, session_id: str) -> None:
        """退出时先撤销写入权限，再删除凭据；保留不含秘密的栅栏。"""
        self.require_session(session_id)
        self._atomic_write(self.session_path, {"sessionId": session_id, "active": False})
        self.clear()
