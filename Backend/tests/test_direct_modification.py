from __future__ import annotations

import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.direct_modification import (
    DirectModificationDecision,
    _data_source_direct_modification_prompt,
    _frontend_direct_modification_prompt,
    parse_direct_modification_agent_result,
)
from app.graph.direct_modification_workflow import (
    _route_backend,
    _route_classification,
    _route_frontend,
    _route_integration_test,
)
from app.graph.nodes.direct_modification import (
    classify_direct_modification,
    execute_backend_direct_modification,
    execute_frontend_direct_modification,
    finalize_direct_modification,
    run_direct_modification_integration_test,
)
from app.protocols.direct_modification import (
    build_direct_modification_ag_ui_stream,
    direct_modification_capabilities,
    direct_modification_input,
)


class DirectModificationPromptTests(unittest.TestCase):
    """验证快速修改 Prompt 与正式生成 Prompt 保持隔离。"""

    def test_frontend_prompt_requires_two_builtin_skills(self) -> None:
        """前端写代码前必须完整读取两个指定内置 Skill。"""

        prompt = _frontend_direct_modification_prompt(
            user_request="修改统计卡片间距",
            conversation_summary="",
            backend_handoff=None,
        )

        self.assertIn(
            "/.xcodeagent/builtin-skills/code-block-template/SKILL.md",
            prompt,
        )
        self.assertIn(
            "/.xcodeagent/builtin-skills/react-develop-specification/SKILL.md",
            prompt,
        )
        self.assertIn("read_file(limit=400)", prompt)
        self.assertIn("task and write_todos are unavailable", prompt)
        self.assertIn("appropriate to the actual change scope", prompt)
        self.assertIn("avoid unrelated repository-wide scans", prompt)
        self.assertNotIn("timeout=120", prompt)
        self.assertNotIn("at most one focused", prompt)
        self.assertNotIn("Approved frontend tasks", prompt)
        self.assertNotIn("ProjectPlan context", prompt)
        self.assertNotIn("BuildTaskPlan summary", prompt)

    def test_backend_prompt_has_no_required_builtin_skill(self) -> None:
        """后端 Prompt 保留执行约束，但不声明不存在的必读内置 Skill。"""

        prompt = _data_source_direct_modification_prompt(
            user_request="增加状态校验",
            conversation_summary="",
        )

        self.assertIn("no mandatory built-in skills", prompt)
        self.assertIn("backendHandoff", prompt)
        self.assertNotIn("/.xcodeagent/builtin-skills/", prompt)
        self.assertNotIn("Approved data-source tasks", prompt)

    def test_agent_result_requires_valid_json(self) -> None:
        """无效 Agent 文本必须被归一化为失败，而不是误报完成。"""

        result = parse_direct_modification_agent_result("done")

        self.assertEqual(result["status"], "failed")
        self.assertIn("JSON", result["failureReason"])


class DirectModificationNodeTests(unittest.TestCase):
    """验证分类、Agent 执行、测试和收口节点的快速模式语义。"""

    def test_classifier_accepts_fullstack_direct_request(self) -> None:
        """跨端局部需求应继续执行后端阶段，而不是转正式工作流。"""

        decision = DirectModificationDecision(
            owner="fullstack",
            scope="direct",
            confidence=0.95,
            reason="需要新增接口并展示结果。",
            clarification_question="",
        )
        with patch(
            "app.graph.nodes.direct_modification.classify_direct_modification_intent",
            return_value=decision,
        ):
            update = classify_direct_modification({"request": "新增统计接口并展示"})

        self.assertEqual(update["status"], "in_progress")
        self.assertEqual(update["direct_modification_owner"], "fullstack")
        self.assertEqual(update["launch_result"], {})
        self.assertIs(update["quality_gate_passed"], False)
        self.assertEqual(_route_classification(update), "execute_backend")

    def test_frontend_execution_uses_real_workspace_diff(self) -> None:
        """前端阶段以工作区快照为权威变更清单。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "frontend" / "src" / "Page.tsx"

            def fake_invoke(**_kwargs) -> str:
                """模拟 Agent 写入前端文件并返回结构化结果。"""

                target.parent.mkdir(parents=True)
                target.write_text("export default null\n", encoding="utf-8")
                return json.dumps(
                    {
                        "status": "completed",
                        "summary": "完成页面修改",
                        "changedFiles": ["model-invented.tsx"],
                        "verification": ["pnpm build"],
                        "alreadySatisfied": False,
                        "failureReason": None,
                    }
                )

            with patch(
                "app.graph.nodes.direct_modification.invoke_frontend_direct_modification",
                side_effect=fake_invoke,
            ):
                update = execute_frontend_direct_modification(
                    {
                        "request": "修改页面",
                        "workspace": workspace,
                        "direct_stage_results": {},
                        "direct_code_change_sets": [],
                    }
                )

        self.assertEqual(update["status"], "in_progress")
        self.assertEqual(
            update["direct_stage_results"]["frontend"]["changedFiles"],
            ["frontend/src/Page.tsx"],
        )

    def test_backend_execution_builds_handoff_from_actual_diff(self) -> None:
        """后端阶段应把真实改动文件补充到前端交接信息。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "backend" / "src" / "Api.java"

            def fake_invoke(**_kwargs) -> str:
                """模拟 Agent 写入后端文件并返回接口交接。"""

                target.parent.mkdir(parents=True)
                target.write_text("class Api {}\n", encoding="utf-8")
                return json.dumps(
                    {
                        "status": "completed",
                        "summary": "新增接口",
                        "changedFiles": [],
                        "verification": ["mvn test"],
                        "alreadySatisfied": False,
                        "failureReason": None,
                        "backendHandoff": {
                            "summary": "新增统计接口",
                            "endpoints": [
                                {
                                    "method": "GET",
                                    "path": "/api/statistics",
                                    "request": None,
                                    "response": {"total": "number"},
                                }
                            ],
                            "notes": [],
                        },
                    }
                )

            with patch(
                "app.graph.nodes.direct_modification.invoke_data_source_direct_modification",
                side_effect=fake_invoke,
            ):
                update = execute_backend_direct_modification(
                    {
                        "request": "新增统计接口",
                        "workspace": workspace,
                        "direct_stage_results": {},
                        "direct_code_change_sets": [],
                    }
                )

        self.assertEqual(update["status"], "in_progress")
        self.assertEqual(update["backend_handoff"]["changedFiles"], ["backend/src/Api.java"])

    def test_frontend_failure_preserves_changes_created_before_exception(self) -> None:
        """Agent 运行异常时仍应返回可审核、可撤销的真实差异。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "frontend" / "src" / "Page.tsx"

            def failing_invoke(**_kwargs) -> str:
                """模拟 Agent 修改文件后在验证阶段异常退出。"""

                target.parent.mkdir(parents=True)
                target.write_text("export default null\n", encoding="utf-8")
                raise TimeoutError("model verification timed out")

            with patch(
                "app.graph.nodes.direct_modification.invoke_frontend_direct_modification",
                side_effect=failing_invoke,
            ):
                update = execute_frontend_direct_modification(
                    {
                        "request": "修改页面颜色",
                        "workspace": workspace,
                        "direct_stage_results": {},
                        "direct_code_change_sets": [],
                    }
                )

        result = update["direct_stage_results"]["frontend"]
        self.assertEqual(update["status"], "failed")
        self.assertEqual(result["changedFiles"], ["frontend/src/Page.tsx"])
        self.assertIn("TimeoutError", result["failureReason"])
        self.assertEqual(update["code_changes"]["summary"]["files"], 1)

    def test_integration_disables_contract_and_repair(self) -> None:
        """快速测试必须复用节点但关闭正式契约校验和 RepairPlanner。"""

        captured_state: dict = {}

        def fake_integration(state):
            """记录集成测试收到的快速模式开关。"""

            captured_state.update(state)
            return {
                "quality_gate_passed": False,
                "test_results": [],
                "code_change_sets": [],
            }

        with patch(
            "app.graph.nodes.direct_modification.integration_test",
            side_effect=fake_integration,
        ):
            update = run_direct_modification_integration_test(
                {"direct_code_change_sets": []}
            )

        self.assertIs(captured_state["integration_contract_check_enabled"], False)
        self.assertIs(captured_state["integration_repair_enabled"], False)
        self.assertEqual(update["status"], "failed")
        self.assertEqual(_route_integration_test(update), "finalize")

    def test_launch_success_is_finalized_without_acceptance_gate(self) -> None:
        """启动成功后快速通道直接完成并清除正式验收字段。"""

        update = finalize_direct_modification(
            {
                "request": "修改页面",
                "status": "requires_user_input",
                "direct_modification_owner": "frontend",
                "direct_modification_scope": "direct",
                "direct_stage_results": {
                    "frontend": {"status": "completed", "summary": "完成页面修改"}
                },
                "direct_code_change_sets": [],
                "launch_result": {"status": "running"},
                "preview_url": "http://127.0.0.1:3000",
            }
        )

        self.assertEqual(update["status"], "completed")
        self.assertEqual(update["acceptance_request"], {})
        self.assertEqual(update["clarification"], {})

    def test_fullstack_routes_backend_then_frontend(self) -> None:
        """fullstack 成功路径必须固定后端优先，再执行前端和测试。"""

        state = {"status": "in_progress", "direct_modification_owner": "fullstack"}
        self.assertEqual(_route_backend(state), "execute_frontend")
        self.assertEqual(_route_frontend(state), "integration_test")


class DirectModificationProtocolTests(unittest.TestCase):
    """验证快速修改公开 AG-UI 契约。"""

    def test_capabilities_publish_independent_targetless_endpoint(self) -> None:
        """健康检查应声明独立端点且请求不需要 target。"""

        capability = direct_modification_capabilities()

        self.assertEqual(capability["endpoint"], "/direct-modification/run")
        self.assertEqual(capability["customEventName"], "direct-modification")
        self.assertEqual(capability["stateSnapshotKey"], "directModification")
        self.assertIs(capability["targetRequired"], False)
        self.assertEqual(
            capability["executionPolicy"],
            {"subagentsEnabled": False, "todoPlanningEnabled": False},
        )

    def test_input_reads_only_direct_modification_payload(self) -> None:
        """协议只读取嵌套业务字段，不要求页面或接口身份。"""

        value = direct_modification_input(
            {
                "forwardedProps": {
                    "directModification": {
                        "workspaceRoot": "/workspace",
                        "selectedSkillNames": [],
                    }
                }
            }
        )

        self.assertEqual(value["workspaceRoot"], "/workspace")
        self.assertNotIn("target", value)

    def test_stream_emits_complete_ag_ui_lifecycle(self) -> None:
        """独立 Graph 结果必须发送自定义事件、快照和正常完成事件。"""

        final_state = {
            "phase": "direct_modification",
            "status": "completed",
            "message": "快速修改完成",
            "direct_modification_owner": "frontend",
            "direct_modification_scope": "direct",
            "direct_modification_result": {
                "status": "completed",
                "summary": "快速修改完成",
            },
            "code_changes": {},
        }

        class FakeGraph:
            """提供协议测试所需的最小异步 Graph 接口。"""

            async def astream(self, *_args, **_kwargs):
                """发送一个最终节点更新。"""

                yield "updates", {"finalize_direct_modification": final_state}

            async def aget_state(self, _config):
                """返回与更新一致的最终状态。"""

                return SimpleNamespace(values=final_state)

        with tempfile.TemporaryDirectory() as workspace:
            with (
                patch(
                    "app.protocols.direct_modification.direct_modification_graph_for_request",
                    new=AsyncMock(return_value=FakeGraph()),
                ),
                patch(
                    "app.protocols.direct_modification.cleanup_workflow_checkpoints",
                    new=AsyncMock(return_value=0),
                ),
            ):
                stream = build_direct_modification_ag_ui_stream(
                    payload={
                        "threadId": "direct-thread",
                        "runId": "direct-run",
                        "messages": [
                            {"id": "message-1", "role": "user", "content": "修改页面"}
                        ],
                        "forwardedProps": {
                            "directModification": {
                                "workspaceRoot": workspace,
                                "selectedSkillNames": [],
                            }
                        },
                    }
                )

                async def collect() -> str:
                    """消费完整快速修改事件流。"""

                    return "".join([frame async for frame in stream])

                frames = asyncio.run(collect())

        self.assertIn("direct-modification", frames)
        self.assertIn("directModification", frames)
        self.assertIn("RUN_STARTED", frames)
        self.assertIn("STATE_SNAPSHOT", frames)
        self.assertIn("RUN_FINISHED", frames)
        self.assertEqual(frames.count("TEXT_MESSAGE_CONTENT"), 1)


if __name__ == "__main__":
    unittest.main()
