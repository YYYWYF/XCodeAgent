from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services.agent_build_tasks import compile_agent_build_tasks
from app.services.agent_runtime_template_policy import AGENT_RUNTIME_MODULES


def _contract() -> dict:
    """构造七模块任务编译使用的正式 Agent Contract。"""

    return {
        "agentId": "inventory_assistant",
        "source": {"productPlanSha256": "sha256:" + "1" * 64},
        "identity": {"name": "库存助手", "purpose": "解释库存", "boundaries": []},
        "capabilities": [],
        "interaction": {"supportsMultiTurn": True},
        "agentSettings": {
            "prompt": {"systemPrompt": "解释库存", "persona": {}, "constraints": []},
            "model": {"selection": "project_default"},
            "memory": {"shortTerm": {"enabled": True, "store": "sqlite"}},
            "tools": {"enabled": False, "bindings": []},
            "skills": {"enabled": False, "bindings": []},
            "knowledge": {"enabled": False, "sources": []},
            "context": {"compression": {"strategy": "none"}},
        },
        "invocation": {"transport": "ag-ui-sse"},
        "runtime": {"framework": "DeepAgents"},
        "security": {"platformPromptMode": "locked_prefix"},
        "evaluation": {},
        "artifacts": {
            "compositionPath": "agent-runtime/src/app/agent/factory.py",
            "testRoot": "agent-runtime/tests",
        },
    }


def _write_workspace(root: Path) -> None:
    """写入任务编译依赖的 Runtime 入口和模板生成 manifest。"""

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
    metadata = root / ".xcodeagent"
    metadata.mkdir()
    (metadata / "template-generation-manifest.json").write_text(
        json.dumps(
            {
                "steps": {
                    "download": {
                        "targets": {
                            "agentRuntime": {
                                "required": True,
                                "status": "succeeded",
                                "commitSha": "abc123",
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )


class AgentBuildTasksTests(unittest.TestCase):
    """验证七模块任务由平台稳定编译而不是由规划模型生成。"""

    def test_compiles_seven_serial_agent_code_tasks(self) -> None:
        """单个 Agent 必须得到顺序稳定的七个 agent.code 任务。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root)
            tasks = compile_agent_build_tasks(
                [_contract()],
                unit_ids={"agent:inventory_assistant"},
                workspace=root,
            )

        self.assertEqual(
            [task["source_refs"]["agent_module"] for task in tasks],
            list(AGENT_RUNTIME_MODULES),
        )
        self.assertTrue(all(task["task_type"] == "agent.code" for task in tasks))
        self.assertEqual(tasks[0]["dependencies"], [])
        self.assertEqual(tasks[1]["dependencies"], [tasks[0]["id"]])
        self.assertTrue(
            all(
                path.startswith("agent-runtime/")
                for task in tasks
                for path in task["allowed_paths"]
            )
        )

    def test_disabled_optional_modules_are_skipped_without_completing_others(self) -> None:
        """关闭的 Tools/Skills/Knowledge 必须单独跳过，其余模块仍保持 pending。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root)
            tasks = compile_agent_build_tasks(
                [_contract()],
                unit_ids={"agent:inventory_assistant"},
                workspace=root,
            )
        by_module = {task["source_refs"]["agent_module"]: task for task in tasks}
        self.assertEqual(by_module["tools"]["status"], "already_satisfied")
        self.assertEqual(by_module["skills"]["status"], "already_satisfied")
        self.assertEqual(by_module["knowledge"]["status"], "already_satisfied")
        self.assertEqual(by_module["prompt"]["status"], "pending")
        self.assertFalse((root / "agent-runtime/config").exists())
        self.assertTrue(
            all("template_policy_sha256" in task["source_refs"] for task in tasks)
        )

    def test_ignores_agents_outside_requested_units(self) -> None:
        """当前 Build 范围外的 Agent 不得产生七模块任务或额外配置文件。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_workspace(root)
            tasks = compile_agent_build_tasks(
                [_contract()],
                unit_ids={"agent:another_agent"},
                workspace=root,
            )

        self.assertEqual(tasks, [])
        self.assertFalse((root / "agent-runtime/config").exists())


if __name__ == "__main__":
    unittest.main()
