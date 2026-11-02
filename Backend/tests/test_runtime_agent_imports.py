"""实际文件验证 Agent 实现摘要支持合法 Python 包导入。"""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.services.runtime_agent_implementation import agent_implementation_facts


class RuntimeAgentImportTests(TestCase):
    """覆盖命名空间包、普通包、别名、相对导入和真实缺失边界。"""

    def _write(self, root, name, content):
        """仅创建用例声明的精确模块文件。"""
        path = root / "agent-runtime/src/app" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def _facts(self, root):
        """运行真实静态解析器，不执行目标模块。"""
        return agent_implementation_facts(root, {"assistant"})

    def test_package_import_variants_track_service_changes(self):
        """合法包写法均可解析，且子模块内容变化确实改变实现摘要。"""
        for regular in (False, True):
            for statement in ("from app.leave_balance import service", "from app.leave_balance import service as svc",
                              "import app.leave_balance.service as service", "from ..leave_balance import service"):
                with self.subTest(regular=regular, statement=statement), TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    name = "svc" if " as svc" in statement else "service"
                    self._write(root, "agent/factory.py", f"""{statement}
def builder():
    return {name}.query()
_AGENT_BUILDERS = {{'assistant': builder}}
def create_agent():
    return builder()
""")
                    if regular:
                        self._write(root, "leave_balance/__init__.py", "")
                    self._write(root, "leave_balance/service.py", "def query():\n    return 1\n")
                    before = self._facts(root)
                    self._write(root, "leave_balance/service.py", "def query():\n    return 2\n")
                    self.assertNotEqual(before, self._facts(root))

    def test_missing_submodule_reports_exact_module(self):
        """真正缺失的子模块仍报精确身份，不把空包误当作完成实现。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "agent/factory.py", "from app.leave_balance import service\ndef builder():\n return service.query()\n_AGENT_BUILDERS={'assistant': builder}\ndef create_agent():\n return builder()\n")
            self._write(root, "leave_balance/__init__.py", "")
            with self.assertRaisesRegex(ValueError, r"app.leave_balance.service"):
                self._facts(root)

    def test_symlink_submodule_is_rejected(self):
        """子模块解析继续遵守源码读取边界。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "agent/factory.py", "from app.leave_balance import service\ndef builder():\n return service.query()\n_AGENT_BUILDERS={'assistant': builder}\ndef create_agent():\n return builder()\n")
            self._write(root, "leave_balance/__init__.py", "")
            target = root / "outside.py"
            target.write_text("def query(): return 1")
            (root / "agent-runtime/src/app/leave_balance/service.py").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "符号链接"):
                self._facts(root)
