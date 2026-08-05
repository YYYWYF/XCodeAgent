from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.services.user_skills import user_skills_working_dir


logger = logging.getLogger(__name__)


SECRET_PREFIX = "xcodeagent-secret"
SECRET_VERSION = "v1"
SECRET_ALGORITHM = "rsa-oaep-256"
PUBLIC_ALGORITHM = "RSA-OAEP-256"
PLATFORM_KEY_ID = "platform-key-v1"
KEY_FILE_NAME = "database-platform-key.json"
_MAX_KEY_FILE_BYTES = 32 * 1024


class DatabaseCryptoError(RuntimeError):
    """表示平台数据库密钥或密文无法安全使用。"""


@dataclass(frozen=True)
class PlatformKeyMaterial:
    """保存经过完整校验的平台密钥材料。"""

    private_key: rsa.RSAPrivateKey
    public_key_pem: str


def database_key_file_path() -> Path:
    """返回当前运行环境隔离的平台数据库密钥文件路径。"""

    return Path.home() / user_skills_working_dir() / "keys" / KEY_FILE_NAME


def ensure_database_platform_key(*, key_file: Path | None = None) -> PlatformKeyMaterial:
    """首次启动时原子生成平台密钥，后续只加载同一份密钥。"""

    target = key_file or database_key_file_path()
    if target.exists() or target.is_symlink():
        return load_database_platform_key(key_file=target)

    _ensure_key_directory(target.parent)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    payload = _serialize_key_file(private_key)
    _write_key_file_atomic(target, payload)
    return load_database_platform_key(key_file=target)


def load_database_platform_key(*, key_file: Path | None = None) -> PlatformKeyMaterial:
    """读取并校验现有平台密钥，缺失或损坏时拒绝自动覆盖。"""

    target = key_file or database_key_file_path()
    if not target.exists() and not target.is_symlink():
        raise DatabaseCryptoError(
            "平台数据库密钥缺失；旧应用中的加密密码无法解密，请恢复原密钥文件。"
        )
    raw = _read_key_file(target)
    if not raw or len(raw) > _MAX_KEY_FILE_BYTES:
        raise DatabaseCryptoError("平台数据库密钥文件已损坏。")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatabaseCryptoError("平台数据库密钥文件已损坏。") from exc
    return _parse_key_file(payload)


def database_encryption_metadata(*, key_file: Path | None = None) -> dict[str, Any]:
    """导出 Renderer 加密所需的公开元数据，绝不包含私钥。"""

    material = ensure_database_platform_key(key_file=key_file)
    return {
        "enabled": True,
        "algorithm": PUBLIC_ALGORITHM,
        "key_id": PLATFORM_KEY_ID,
        "public_key": material.public_key_pem,
    }


def is_encrypted_password(value: str) -> bool:
    """判断字符串是否声明为 XCodeAgent 版本化密文。"""

    return value.startswith(f"{SECRET_PREFIX}:")


def decrypt_password(value: str, *, key_file: Path | None = None) -> str:
    """使用平台私钥解密密码，并把所有失败收敛为不泄密的错误。"""

    ciphertext = _parse_secret_envelope(value)
    material = load_database_platform_key(key_file=key_file)
    try:
        plaintext = material.private_key.decrypt(
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        password = plaintext.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise DatabaseCryptoError(
            "数据库密码解密失败；密文可能已损坏或不属于当前平台密钥。"
        ) from exc
    if not password:
        raise DatabaseCryptoError("数据库密码解密结果为空。")
    return password


def _ensure_key_directory(key_directory: Path) -> None:
    """创建权限为 0700 的密钥目录，并拒绝符号链接目录。"""

    environment_root = key_directory.parent
    if environment_root.is_symlink():
        raise DatabaseCryptoError("XCodeAgent 用户级密钥目录不允许使用符号链接。")
    try:
        environment_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if key_directory.is_symlink():
            raise DatabaseCryptoError("XCodeAgent 用户级密钥目录不允许使用符号链接。")
        key_directory.mkdir(mode=0o700, exist_ok=True)
        key_directory.chmod(0o700)
    except DatabaseCryptoError:
        raise
    except OSError as exc:
        raise DatabaseCryptoError("无法安全创建平台数据库密钥目录。") from exc


def _serialize_key_file(private_key: rsa.RSAPrivateKey) -> bytes:
    """把 RSA-3072 私钥和对应公钥编码为版本化 JSON。"""

    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_key_pem = _public_key_pem(private_key)
    return (
        json.dumps(
            {
                "version": SECRET_VERSION,
                "algorithm": PUBLIC_ALGORITHM,
                "keyId": PLATFORM_KEY_ID,
                "privateKey": private_key_pem,
                "publicKey": public_key_pem,
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _write_key_file_atomic(target: Path, payload: bytes) -> None:
    """使用同目录临时文件和原子替换写入权限为 0600 的私钥文件。"""

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.chmod(temporary.name, 0o600)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        if target.is_symlink():
            raise DatabaseCryptoError("平台数据库密钥文件不允许使用符号链接。")
        os.replace(temporary_path, target)
        temporary_path = None
        target.chmod(0o600)
    except (OSError, DatabaseCryptoError) as exc:
        raise DatabaseCryptoError("无法安全写入平台数据库密钥文件。") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _read_key_file(target: Path) -> bytes:
    """通过 no-follow 文件描述符读取私钥，并收紧文件权限。"""

    try:
        path_stat = target.lstat()
    except OSError as exc:
        raise DatabaseCryptoError("平台数据库密钥文件无法访问。") from exc
    if stat.S_ISLNK(path_stat.st_mode):
        raise DatabaseCryptoError("平台数据库密钥文件不允许使用符号链接。")
    if not stat.S_ISREG(path_stat.st_mode):
        raise DatabaseCryptoError("平台数据库密钥路径不是普通文件。")
    file_descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(target, flags)
        file_stat = os.fstat(file_descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise DatabaseCryptoError("平台数据库密钥路径不是普通文件。")
        if (path_stat.st_dev, path_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino):
            raise DatabaseCryptoError("平台数据库密钥文件在读取期间发生变化。")
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(file_descriptor, 0o600)
            else:
                target.chmod(0o600)
        except OSError as exc:
            # Windows 没有 POSIX 权限位：Python 3.13 起 os.fchmod 存在但必然抛
            # PermissionError(WinError 5)。收紧失败不应阻断密钥读取，记录告警即可。
            logger.warning("无法收紧平台数据库密钥文件权限：%s", exc)
        with os.fdopen(file_descriptor, "rb", closefd=True) as key_stream:
            file_descriptor = None
            return key_stream.read(_MAX_KEY_FILE_BYTES + 1)
    except DatabaseCryptoError:
        raise
    except OSError as exc:
        raise DatabaseCryptoError("平台数据库密钥文件无法安全读取。") from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)


def _parse_key_file(payload: Any) -> PlatformKeyMaterial:
    """校验密钥版本、算法、标识以及公私钥一致性。"""

    if not isinstance(payload, dict):
        raise DatabaseCryptoError("平台数据库密钥文件已损坏。")
    if (
        payload.get("version") != SECRET_VERSION
        or payload.get("algorithm") != PUBLIC_ALGORITHM
        or payload.get("keyId") != PLATFORM_KEY_ID
    ):
        raise DatabaseCryptoError("平台数据库密钥版本或算法不受支持。")
    private_key_pem = payload.get("privateKey")
    public_key_pem = payload.get("publicKey")
    if not isinstance(private_key_pem, str) or not isinstance(public_key_pem, str):
        raise DatabaseCryptoError("平台数据库密钥文件已损坏。")
    try:
        private_key = serialization.load_pem_private_key(
            private_key_pem.encode("ascii"),
            password=None,
        )
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise DatabaseCryptoError("平台数据库密钥文件已损坏。") from exc
    if not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size != 3072:
        raise DatabaseCryptoError("平台数据库密钥必须是 RSA-3072 私钥。")
    if _public_key_pem(private_key) != public_key_pem:
        raise DatabaseCryptoError("平台数据库公钥与私钥不匹配。")
    return PlatformKeyMaterial(private_key=private_key, public_key_pem=public_key_pem)


def _public_key_pem(private_key: rsa.RSAPrivateKey) -> str:
    """从私钥稳定导出 SubjectPublicKeyInfo PEM 公钥。"""

    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _parse_secret_envelope(value: str) -> bytes:
    """解析并严格校验版本化密码密文信封。"""

    parts = value.split(":", 4)
    if len(parts) != 5:
        raise DatabaseCryptoError("数据库密码密文格式无效。")
    prefix, version, algorithm, key_id, encoded_ciphertext = parts
    if prefix != SECRET_PREFIX or version != SECRET_VERSION:
        raise DatabaseCryptoError("数据库密码密文版本不受支持。")
    if algorithm != SECRET_ALGORITHM:
        raise DatabaseCryptoError("数据库密码密文算法不受支持。")
    if key_id != PLATFORM_KEY_ID:
        raise DatabaseCryptoError("数据库密码密文 keyId 不受当前后端支持。")
    if not encoded_ciphertext:
        raise DatabaseCryptoError("数据库密码密文格式无效。")
    padded = encoded_ciphertext + "=" * (-len(encoded_ciphertext) % 4)
    try:
        ciphertext = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise DatabaseCryptoError("数据库密码密文格式无效。") from exc
    if len(ciphertext) != 3072 // 8:
        raise DatabaseCryptoError("数据库密码密文长度无效。")
    return ciphertext
