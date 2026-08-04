from __future__ import annotations

import json
import sys
from pathlib import Path
from pprint import pprint

from app.tools.mysql_info import create_get_mysql_table_info_tool


def run_local_mysql_info(workspace_root: str) -> None:
    """使用指定应用工作区的加密数据源配置执行一次手工结构检查。"""

    raw = create_get_mysql_table_info_tool(workspace_root).invoke({"table_name": ""})
    result = json.loads(raw)
    pprint(result)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：python tests/test_mysql_info_tool.py <workspace_root>")
    run_local_mysql_info(str(Path(sys.argv[1]).expanduser().resolve()))
