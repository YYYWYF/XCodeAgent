"""提供不会向 Windows 进程发送终止信号的存活检查。"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


def pid_is_running(pid: int) -> bool:
    """跨平台只读检查 PID；权限不足时保守视为仍存活。"""

    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_is_running(pid: int) -> bool:
    """用进程句柄的零等待状态判断退出，始终关闭查询句柄。"""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 显式声明 HANDLE 返回类型，避免 Windows x64 的句柄被默认 int 截断。
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    # 只申请 SYNCHRONIZE，不申请终止权限，也不启动外部命令轮询。
    handle = kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER：PID 已不存在。
            return False
        if error == 5:  # ERROR_ACCESS_DENIED：不能把无权限误判为已退出。
            return True
        raise ctypes.WinError(error)
    try:
        status = kernel32.WaitForSingleObject(handle, 0)
        if status == 0:  # WAIT_OBJECT_0：进程已退出，句柄可能尚未释放。
            return False
        if status == 258:  # WAIT_TIMEOUT：进程仍在运行。
            return True
        raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)
