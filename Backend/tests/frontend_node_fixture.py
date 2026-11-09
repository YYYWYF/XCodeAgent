"""为只验证命令编排的用例隔离本机 Node 和包管理器探测。"""

import os
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from app.services.frontend_node_runtime import FrontendNodeRuntime


def stub_frontend_node(test: TestCase, module: str) -> FrontendNodeRuntime:
    """提供可比较的 Node 身份，避免命令编排用例依赖本机安装。"""
    runtime = FrontendNodeRuntime(Path(sys.executable).resolve(), "v22.12.0", "PATH", dict(os.environ))
    test.enterContext(patch(f"{module}.resolve_frontend_node", return_value=runtime))
    test.enterContext(patch.object(FrontendNodeRuntime, "verify"))
    return runtime
