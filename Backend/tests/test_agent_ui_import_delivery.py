"""覆盖真实 TypeScript 分析器晚于取消轮询读取大源码的场景。"""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.services.agent_ui_dependency_plan import REPOSITORY
from app.services.agent_ui_transfer_files import scan_imports
from app.services.workspace_process_registry import workspace_process_registry


class AgentUiImportDeliveryTests(TestCase):
    """使用真实进程和 AST 验证完整输入及真实超时。"""

    def test_large_source_survives_delayed_reader(self) -> None:
        """源码超过管道缓冲区且子进程晚启动时，仍可完整解析所有 import。"""
        run = workspace_process_registry.run

        def delayed(argv: list[str], **kwargs: object) -> object:
            """延迟真实 Node 读入，确保跨过首轮取消轮询。"""
            kwargs['timeout'] = 3
            code = 'import os,sys,time; time.sleep(0.4); os.execv(sys.argv[1],sys.argv[1:])'
            return run([sys.executable, '-c', code, *argv], **kwargs)

        source = "import React from 'react';\nexport const padding='" + 'x' * 200_000 + "';\n"
        with TemporaryDirectory() as directory, patch.object(workspace_process_registry, 'run', side_effect=delayed):
            result = scan_imports(REPOSITORY / 'Frontend', {'src/example.tsx': source}, Path(directory))
        self.assertEqual(result['src/example.tsx']['imports'][0]['specifier'], 'react')
        self.assertEqual(result['src/example.tsx']['errors'], [])

    def test_real_timeout_does_not_return_partial_analysis(self) -> None:
        """真正超时仍交回原调用方处理，不产生伪造的成功分析。"""
        with TemporaryDirectory() as directory, patch.object(
            workspace_process_registry, 'run', side_effect=subprocess.TimeoutExpired(['node'], 30),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                scan_imports(REPOSITORY / 'Frontend', {'src/example.tsx': 'export {}'}, Path(directory))
