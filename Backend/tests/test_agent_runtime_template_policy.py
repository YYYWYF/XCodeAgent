"""验证迁入的七模块策略与模板文件边界，不调用模型或真实模板服务。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.services.agent_runtime_template_policy import (
    AGENT_RUNTIME_MODULES,
    AgentRuntimeTemplatePolicyError,
    load_agent_runtime_template_policy,
)


def _write_runtime(root: Path) -> Path:
    """创建旧分支七模块策略要求的真实文件，用于验证迁入合同。"""

    runtime = root / "agent-runtime"
    for relative_path in (
        "src/app/agent/factory.py", "src/app/agent/context.py",
        "src/app/models/factory.py", "src/app/settings.py",
        "src/app/persistence/checkpointer.py", "src/app/tools/__init__.py",
        "src/app/interaction/schemas.py", "src/app/interaction/service.py",
    ):
        target = runtime / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# fixture\n", encoding="utf-8")
    (runtime / "tests").mkdir()
    return runtime


class AgentRuntimeTemplatePolicyTests(unittest.TestCase):
    """检查缺件、类型、引用隔离及中间目录越界，不接入未实现的生成流程。"""

    def test_loads_platform_policy_and_returns_isolated_copy(self) -> None:
        """模板不能改变权限；调用方修改返回值不能污染后续生成器范围。"""

        with tempfile.TemporaryDirectory() as directory:
            runtime = _write_runtime(Path(directory))
            policy = load_agent_runtime_template_policy(runtime)
            expected_digest = policy["policySha256"]
            policy["modulePaths"]["tools"]["addRoots"].append("../../outside")
            current = load_agent_runtime_template_policy(runtime)
        self.assertEqual(set(current["modulePaths"]), set(AGENT_RUNTIME_MODULES))
        self.assertEqual(current["policySha256"], expected_digest)
        self.assertNotIn("../../outside", current["modulePaths"]["tools"]["addRoots"])

    def test_rejects_missing_dependency(self) -> None:
        """缺少模型工厂时公开准确缺件路径，不能继续生成。"""

        with tempfile.TemporaryDirectory() as directory:
            runtime = _write_runtime(Path(directory))
            (runtime / "src/app/models/factory.py").unlink()
            with self.assertRaisesRegex(AgentRuntimeTemplatePolicyError, "src/app/models/factory.py"):
                load_agent_runtime_template_policy(runtime)

    def test_rejects_directory_disguised_as_source(self) -> None:
        """同名目录不能满足 Python 入口文件的就绪条件。"""

        with tempfile.TemporaryDirectory() as directory:
            runtime = _write_runtime(Path(directory))
            path = runtime / "src/app/settings.py"
            path.unlink()
            path.mkdir()
            with self.assertRaisesRegex(AgentRuntimeTemplatePolicyError, "类型无效"):
                load_agent_runtime_template_policy(runtime)

    def test_rejects_file_disguised_as_tests_root(self) -> None:
        """测试根必须是真实目录，普通文件不能成为授权写范围。"""

        with tempfile.TemporaryDirectory() as directory:
            runtime = _write_runtime(Path(directory))
            path = runtime / "tests"
            path.rmdir()
            path.write_text("fixture", encoding="utf-8")
            with self.assertRaisesRegex(AgentRuntimeTemplatePolicyError, "类型无效"):
                load_agent_runtime_template_policy(runtime)

    def test_rejects_intermediate_directory_symlink(self) -> None:
        """外部工厂即使存在，父目录链接也不能扩大生成器写入权限。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = _write_runtime(root)
            models = runtime / "src/app/models"
            outside = root / "outside-models"
            models.rename(outside)
            self._symlink_or_skip(models, outside)
            with self.assertRaisesRegex(AgentRuntimeTemplatePolicyError, "符号链接"):
                load_agent_runtime_template_policy(runtime)

    def test_rejects_optional_add_root_symlink(self) -> None:
        """可选新增目录也必须受限，不能通过知识或技能根写到工程外。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = _write_runtime(root)
            outside = root / "outside-skills"
            outside.mkdir()
            self._symlink_or_skip(runtime / "src/app/skills", outside)
            with self.assertRaisesRegex(AgentRuntimeTemplatePolicyError, "符号链接"):
                load_agent_runtime_template_policy(runtime)

    def _symlink_or_skip(self, path: Path, target: Path) -> None:
        """无链接权限的平台明确跳过链接用例，其余校验继续保持真实文件系统行为。"""

        try:
            path.symlink_to(target, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"当前平台无法创建目录链接：{exc}")
