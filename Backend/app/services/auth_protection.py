"""使用系统安全存储保护本次桌面会话的登录凭据。"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from cryptography.fernet import Fernet


def protect(data: bytes, *, decrypt: bool = False) -> bytes:
    """Windows 使用当前用户 DPAPI，其他平台使用系统钥匙串保存加密密钥。"""
    if os.name == "nt":
        return _windows_protect(data, decrypt=decrypt)
    import keyring
    backend = keyring.get_keyring()
    # 禁止把认证密钥降级保存到明文文件后端。
    if not type(backend).__module__.startswith(("keyring.backends.macOS", "keyring.backends.SecretService", "keyring.backends.kwallet")):
        raise RuntimeError("未找到系统安全钥匙串，无法保存登录凭据。")
    service, account = "devagentstudio-yst-login", "credential-encryption"
    key = keyring.get_password(service, account)
    if not key:
        if decrypt:
            raise RuntimeError("登录凭据的系统密钥不存在，请重新登录。")
        key = Fernet.generate_key().decode("ascii")
        keyring.set_password(service, account, key)
    codec = Fernet(key.encode("ascii"))
    return codec.decrypt(data) if decrypt else codec.encrypt(data)


def _windows_protect(data: bytes, *, decrypt: bool) -> bytes:
    """调用 Windows DPAPI，并释放系统分配的输出内存。"""
    class Blob(ctypes.Structure):
        """描述 DPAPI 输入输出的二进制缓冲区。"""
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.restype = wintypes.BOOL
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError("系统无法保护或解密登录凭据，请重新登录。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)
