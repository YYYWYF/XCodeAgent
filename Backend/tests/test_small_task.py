from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from deepagents.backends import StateBackend
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.agents.small_task.agent import create_small_task_agent
from app.agents.small_task.scope import is_small_task_path_allowed, small_task_path_scope
from app.agents.small_task.scope import ScopedSmallTaskBackend
from app.agents.small_task.runner import build_small_task_prompt
from app.agents.workspace_scope import create_workspace_backend
from app.graph.nodes.small_task import _initial_tasks, small_task_repair
from app.services.small_task import execute_small_task_batch
from app.services.small_task_scope import (
    select_parallel_small_task_batch,
    small_task_preflight,
    workflow_target_for_small_task,
)
from app.agents.small_task.runner import normalize_small_task_result


class _ToolBindableFakeChatModel(FakeListChatModel):
    """提供最小 bind_tools 行为，便于只测试 Agent 初始化链路。"""

    def bind_tools(self, tools, **kwargs):
        """返回自身以模拟真实聊天模型的工具绑定接口。"""

        return self


def _task(task_id: str, path: str, *, status: str = "pending") -> dict:
    """构造测试用的最小局部修复任务。"""

    return {
        "id": task_id,
        "owner": "frontend",
        "title": task_id,
        "description": "修复指定代码问题",
        "allowed_paths": [path],
        "target_files": [path],
        "acceptance_criteria": ["相关检查通过"],
        "dependencies": [],
        "status": status,
    }


class SmallTaskScopeTests(unittest.TestCase):
    """验证 SmallTask 的并行边界、升级判定和输出协议。"""

    def test_disjoint_tasks_are_selected_together_but_shared_config_is_serialized(self) -> None:
        """不重叠的两个任务可以并行，共享配置文件必须避免并行。"""

        batch = select_parallel_small_task_batch(
            [
                _task("page-a", "Frontend/src/PageA.tsx"),
                _task("page-b", "Frontend/src/PageB.tsx"),
                _task("config", "Frontend/package.json"),
            ]
        )

        self.assertEqual([task["id"] for task in batch], ["page-a", "page-b"])

    def test_preflight_routes_database_and_formal_changes_to_workflow(self) -> None:
        """数据库迁移和正式工件修改不得进入 SmallTask 执行器。"""

        database = small_task_preflight(
            {
                "owner": "backend",
                "allowed_paths": ["Backend/app/migrations/orders.py"],
            }
        )
        formal = small_task_preflight(
            {
                "owner": "frontend",
                "allowed_paths": [".xcodeagent/project-plan.json"],
            }
        )
        command_only = small_task_preflight(
            {
                "owner": "frontend",
                "allowed_paths": ["<no file paths — repair is a command-level operation>"],
            }
        )

        self.assertEqual(database["workflowIntent"], "detail_confirmation")
        self.assertEqual(formal["workflowIntent"], "prepare_build_tasks")
        self.assertEqual(command_only["workflowIntent"], "prepare_build_tasks")
        self.assertEqual(
            workflow_target_for_small_task({"reasonCode": "new_page"}),
            "detail_confirmation",
        )

    def test_file_scope_allows_only_current_task_paths(self) -> None:
        """SmallTask 文件工具只能写入当前任务明确授权的路径。"""

        with small_task_path_scope(["Frontend/src/**"]):
            self.assertTrue(is_small_task_path_allowed("/Frontend/src/pages/Home.tsx"))
            self.assertFalse(is_small_task_path_allowed("/Backend/app/main.py"))
            self.assertFalse(is_small_task_path_allowed("/Frontend/.env.local"))
            self.assertFalse(
                is_small_task_path_allowed("/Frontend/node_modules/pkg/index.js")
            )

    def test_scoped_backend_prioritizes_source_and_blocks_installed_dependencies(self) -> None:
        """默认搜索只进入源码根，显式读取 node_modules 也必须被拒绝。"""

        with tempfile.TemporaryDirectory() as workspace:
            source = Path(workspace) / "Frontend" / "src" / "Page.tsx"
            dependency = (
                Path(workspace) / "Frontend" / "node_modules" / "pkg" / "index.tsx"
            )
            source.parent.mkdir(parents=True)
            dependency.parent.mkdir(parents=True)
            source.write_text("export const Page = 'source'\n", encoding="utf-8")
            dependency.write_text("export const Page = 'dependency'\n", encoding="utf-8")
            backend = ScopedSmallTaskBackend(create_workspace_backend(workspace))

            with small_task_path_scope(["Frontend/src/**"]):
                globbed = backend.glob("**/*.tsx")
                searched = backend.grep("Page")
                denied = backend.read("/Frontend/node_modules/pkg/index.tsx")
                listed = backend.ls("/Frontend")

        self.assertEqual(
            [item["path"] for item in globbed.matches or []],
            ["/Frontend/src/Page.tsx"],
        )
        self.assertEqual(
            {item["path"] for item in searched.matches or []},
            {"/Frontend/src/Page.tsx"},
        )
        self.assertIn("不读取安装依赖", str(denied.error))
        self.assertNotIn(
            "/Frontend/node_modules/",
            [item["path"] for item in listed.entries or []],
        )

    def test_normalizer_preserves_escalation_without_trusting_free_text(self) -> None:
        """Agent 的升级状态和路径被保留在有界结构中。"""

        result = normalize_small_task_result(
            json.dumps(
                {
                    "status": "requires_workflow",
                    "summary": "需要新增接口",
                    "escalation": {
                        "reasonCode": "new_api",
                        "workflowIntent": "detail_confirmation",
                        "requestedPaths": ["Backend/app/api.py"],
                    },
                }
            )
        )

        self.assertEqual(result["status"], "requires_workflow")
        self.assertEqual(result["escalation"]["workflowIntent"], "detail_confirmation")
        self.assertEqual(result["escalation"]["requestedPaths"], ["Backend/app/api.py"])

    def test_acceptance_local_fix_replaces_stale_repair_tasks(self) -> None:
        """验收局部修改必须创建新任务，不能重复执行旧修复列表。"""

        tasks = _initial_tasks(
            {
                "acceptance_adjustment": {
                    "type": "local_fix",
                    "feedback": "调整按钮间距。",
                },
                "tasks": [
                    {
                        "target_files": ["Frontend/src/pages/Inventory.tsx"],
                        "allowed_paths": ["Frontend/src/pages/Inventory.tsx"],
                    }
                ],
                "small_task_tasks": [_task("old-repair", "Frontend/src/Old.tsx")],
                "repair_tasks": [_task("older-repair", "Frontend/src/Older.tsx")],
            }
        )

        self.assertEqual([task["id"] for task in tasks], ["acceptance-local-fix"])
        self.assertEqual(tasks[0]["allowed_paths"], ["Frontend/src/pages/Inventory.tsx"])


class SmallTaskExecutionTests(unittest.TestCase):
    """验证局部 Agent 的并行执行和主图升级状态。"""

    def test_scoped_backend_forwards_batch_downloads_for_skill_loading(self) -> None:
        """动态范围 backend 必须转发 DeepAgents Skill 初始化所需的批量读取。"""

        with tempfile.TemporaryDirectory() as workspace:
            skill_file = Path(workspace) / "skill" / "SKILL.md"
            skill_file.parent.mkdir()
            skill_file.write_text("---\nname: sample\ndescription: test\n---\n", encoding="utf-8")
            backend = ScopedSmallTaskBackend(create_workspace_backend(workspace))

            responses = backend.download_files(["/skill/SKILL.md"])

        self.assertEqual(len(responses), 1)
        self.assertIsNone(responses[0].error)
        self.assertEqual(responses[0].content, b"---\nname: sample\ndescription: test\n---\n")

    def test_small_task_agent_passes_skill_initialization(self) -> None:
        """SmallTask Agent 应能完成 Skill 初始化并返回模型结果。"""

        with tempfile.TemporaryDirectory() as workspace:
            agent = create_small_task_agent(
                _ToolBindableFakeChatModel(
                    responses=[
                        '{"status":"already_satisfied","summary":"已验证",'
                        '"changedFiles":[],"verification":[],"failureReason":null,'
                        '"escalation":{}}'
                    ]
                ),
                workspace,
                user_skills_backend=StateBackend(),
                agent_memory_backend=StateBackend(),
            )
            result = agent.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": build_small_task_prompt(
                                {
                                    "taskId": "skill-init",
                                    "allowedPaths": ["src/Page.tsx"],
                                    "description": "验证局部任务 Agent",
                                    "acceptanceCriteria": ["返回结构化结果"],
                                }
                            ),
                        }
                    ]
                }
            )

        self.assertIn("已验证", str(result["messages"][-1].content))

    def test_small_task_prompt_declares_source_first_read_order(self) -> None:
        """任务 Prompt 必须要求先读源码候选并禁止探索安装依赖。"""

        prompt = build_small_task_prompt(
            {
                "candidateFiles": ["Frontend/src/pages/PetPhotoList/index.tsx"],
                "allowedPaths": ["Frontend/src/**"],
                "description": "修改卡片宽度",
            }
        )

        self.assertIn("packet.candidateFiles first", prompt)
        self.assertIn("Never inspect node_modules", prompt)

    def test_batch_executes_disjoint_tasks_in_parallel_and_attributes_changes(self) -> None:
        """两个不冲突任务应并行运行，并各自只认领自己的实际文件。"""

        barrier = threading.Barrier(2)
        tasks = [
            _task("page-a", "Frontend/src/PageA.tsx"),
            _task("page-b", "Frontend/src/PageB.tsx"),
        ]

        with tempfile.TemporaryDirectory() as workspace:
            workspace_path = Path(workspace)
            files = {
                task["id"]: workspace_path / task["allowed_paths"][0]
                for task in tasks
            }

            def write_for_task(*, packet, **_kwargs) -> str:
                """按任务 ID 写入各自文件，避免测试共享文件产生交叉归属。"""

                barrier.wait(timeout=3)
                target = files[packet["taskId"]]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(packet["taskId"], encoding="utf-8")
                return json.dumps({"status": "completed", "summary": "已完成"})

            with patch(
                "app.services.small_task.invoke_small_task_agent",
                side_effect=write_for_task,
            ):
                execution = execute_small_task_batch(
                    state={"workspace": workspace},
                    tasks=tasks,
                )

        results = {item["taskId"]: item for item in execution["results"]}
        self.assertEqual(results["page-a"]["status"], "completed")
        self.assertEqual(results["page-b"]["status"], "completed")
        self.assertEqual(results["page-a"]["changedFiles"], ["Frontend/src/PageA.tsx"])
        self.assertEqual(results["page-b"]["changedFiles"], ["Frontend/src/PageB.tsx"])
        self.assertEqual(len(execution["codeChangeSets"]), 2)

    def test_directory_authorized_repair_accepts_nested_source_change(self) -> None:
        """目录级授权内的真实源码修复必须保持 completed，供主图继续回测。"""

        task = {
            **_task("frontend-build-repair", "frontend/package.json"),
            "allowed_paths": ["frontend"],
        }
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "frontend/src/index.tsx"
            target.parent.mkdir(parents=True)
            target.write_text("impor ReactDOM\n", encoding="utf-8")

            def repair_source(*, packet, **_kwargs) -> str:
                """模拟 Agent 修复目录授权内、但不在 target_files 中的源码。"""

                target.write_text("import ReactDOM\n", encoding="utf-8")
                return json.dumps({"status": "completed", "summary": "已修复拼写"})

            with patch(
                "app.services.small_task.invoke_small_task_agent",
                side_effect=repair_source,
            ):
                execution = execute_small_task_batch(
                    state={"workspace": workspace},
                    tasks=[task],
                )

        result = execution["results"][0]
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["changedFiles"], ["frontend/src/index.tsx"])
        self.assertEqual(execution["unauthorizedPaths"], [])

    def test_successful_repair_node_clears_failed_status_and_requests_retest(self) -> None:
        """SmallTask 成功必须覆盖上一轮 failed 状态并显式请求重新集成测试。"""

        task = _task("frontend-build-repair", "frontend/src/index.tsx")
        with patch(
            "app.graph.nodes.small_task.execute_small_task_batch",
            return_value={
                "results": [
                    {
                        "taskId": task["id"],
                        "owner": "frontend",
                        "status": "completed",
                        "summary": "已修复前端构建错误",
                        "changedFiles": ["frontend/src/index.tsx"],
                        "verification": [],
                        "alreadySatisfied": False,
                        "failureReason": None,
                        "escalation": {},
                    }
                ],
                "codeChangeSets": [],
            },
        ):
            result = small_task_repair(
                {
                    "status": "failed",
                    "small_task_tasks": [task],
                    "small_task_results": [],
                    "small_task_code_change_sets": [],
                    "repair_iteration": 0,
                }
            )

        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["small_task_route"], "integration_test")
        self.assertEqual(result["integration_next_action"], "integration_test")
        self.assertEqual(result["repair_iteration"], 1)

    def test_main_node_stops_for_complex_task_confirmation(self) -> None:
        """SmallTask 返回复杂升级时主图必须暂停并生成正式工作流确认卡。"""

        tasks = [_task("new-api", "Backend/app/api.py")]
        with patch(
            "app.graph.nodes.small_task.execute_small_task_batch",
            return_value={
                "results": [
                    {
                        "taskId": "new-api",
                        "owner": "backend",
                        "status": "requires_workflow",
                        "summary": "需要新增 API",
                        "escalation": {
                            "reasonCode": "new_api",
                            "workflowIntent": "detail_confirmation",
                            "reason": "需要确认接口契约",
                        },
                        "packet": {},
                    }
                ],
                "codeChangeSets": [],
            },
        ):
            result = small_task_repair(
                {
                    "workspace": "",
                    "small_task_tasks": tasks,
                    "small_task_results": [],
                    "small_task_code_change_sets": [],
                }
            )

        self.assertEqual(result["status"], "requires_user_input")
        self.assertEqual(
            result["clarification"]["mode"],
            "small_task_workflow_handoff",
        )
        self.assertEqual(
            result["clarification"]["workflowIntent"],
            "detail_confirmation",
        )


if __name__ == "__main__":
    unittest.main()
