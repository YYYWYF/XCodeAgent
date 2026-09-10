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
    """创建平台路径策略依赖的最小 Runtime 目录。"""

    runtime = root / "agent-runtime"
    files = (
        "src/app/agent/factory.py",
        "src/app/agent/context.py",
        "src/app/models/factory.py",
        "src/app/settings.py",
        "src/app/persistence/checkpointer.py",
        "src/app/tools/__init__.py",
        "src/app/interaction/schemas.py",
        "src/app/interaction/service.py",
    )
    for relative_path in files:
        target = runtime / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# test\n", encoding="utf-8")
    (runtime / "tests").mkdir()
    return runtime


class AgentRuntimeTemplatePolicyTests(unittest.TestCase):
    """验证七模块权限来自平台策略而不是模板内 Manifest。"""

    def test_loads_policy_without_template_manifest(self) -> None:
        """当前模板只要存在真实入口文件即可加载完整七模块策略。"""

        with tempfile.TemporaryDirectory() as directory:
            runtime = _write_runtime(Path(directory))
            policy = load_agent_runtime_template_policy(runtime)

        self.assertEqual(set(policy["modulePaths"]), set(AGENT_RUNTIME_MODULES))
        self.assertRegex(policy["policySha256"], r"^sha256:[0-9a-f]{64}$")

    def test_rejects_missing_policy_dependency(self) -> None:
        """模板删除平台策略依赖的真实入口时必须明确失败。"""

        with tempfile.TemporaryDirectory() as directory:
            runtime = _write_runtime(Path(directory))
            (runtime / "src/app/agent/factory.py").unlink()

            with self.assertRaisesRegex(
                AgentRuntimeTemplatePolicyError,
                "src/app/agent/factory.py",
            ):
                load_agent_runtime_template_policy(runtime)


if __name__ == "__main__":
    unittest.main()
