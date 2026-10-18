"""生成入口隔离自身损坏的旧 JSON，保留原文件供诊断，不影响必要前置输入。"""

import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

_lock = RLock()


def prepare_generation_baseline(path: Path) -> bool:
    """检查可选生成基线；损坏时原地改名留档，权限和其他 I/O 故障仍向上报告。"""
    with _lock:
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return False
        except UnicodeError:
            content = None
        try:
            valid = content is not None and isinstance(json.loads(content), dict)
        except json.JSONDecodeError:
            valid = False
        if valid:
            return True
        # 只隔离本节点将重建的主文件；Markdown 与有效上游产物始终保留。
        path.rename(path.with_name(f"{path.name}.corrupt-{uuid4().hex}.bak"))
        return False
