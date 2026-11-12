"""为本地联调提供可选的模拟用户身份。"""

from __future__ import annotations

import os


def current_user() -> tuple[str, str]:
    """读取模拟用户 ID 和姓名，未配置的字段返回空字符串。"""

    return (
        os.getenv("DEVAGENTSTUDIO_MOCK_USER_ID", "00000001").strip(),
        os.getenv("DEVAGENTSTUDIO_MOCK_USER_NAME", "hello").strip(),
    )
