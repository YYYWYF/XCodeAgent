from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


def write_json_atomic(path: Path, payload: Any) -> None:
    """将 JSON 数据通过同目录临时文件原子写入目标路径。"""

    target = Path(path)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    descriptor_is_open = True
    try:
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor_is_open = False
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if descriptor_is_open:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # 临时文件清理只能尽力执行，不能覆盖原始写入或替换异常。
            pass
