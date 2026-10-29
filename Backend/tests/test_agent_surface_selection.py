from __future__ import annotations

from app.services.runtime_agent_contracts import agent_fact_digest

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.protocols.agent_planning_actions import build_agent_planning_action_stream
from app.services.agent_surface_selection import (
    GetAgentSurfaceSelectionRequest,
    SaveAgentSurfaceSelectionRequest,
    get_agent_surface_selection_draft,
    save_agent_surface_selection_draft,
)
from app.services.product_plan import create_product_plan
from app.services.requirement_spec import create_requirement_spec
from app.workspace.product_plan_documents import write_product_plan_documents
from app.workspace.spec_documents import write_requirement_spec_draft_document


class AgentSurfaceSelectionTests(unittest.TestCase):
    """验证用户可在联合确认前启停候选页面的智能体浮窗。"""

    def _write_pending_artifacts(self, workspace: Path) -> tuple[dict, dict, str, str]:
        """写入包含一个默认开启浮窗的 RequirementSpec 与 ProductPlan 草稿。"""

        state = {"workspace": str(workspace)}
        spec = create_requirement_spec("创建库存管理应用，并提供库存问答助手")
        page_id = spec["pages"][0]["pageId"]
        spec["agent_requirements"] = [
            {
                "agentId": "inventory_assistant",
                "name": "库存助手",
                "purpose": "帮助用户理解库存状态。",
                "capabilities": ["解释库存状态"],
                "entryPageIds": [page_id],
                "interactionMode": "conversation",
                "boundaries": ["不得直接修改库存"],
            }
        ]
        write_requirement_spec_draft_document(state, spec)
        plan = create_product_plan(spec)
        markdown_path, json_path = write_product_plan_documents(state, plan)
        return spec, plan, markdown_path, json_path

    def test_get_returns_disk_snapshot_and_identity_without_writing(self) -> None:
        """读取必须交付磁盘草稿和匹配摘要，不重写用户草稿内容。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _spec, _plan, markdown_path, json_path = self._write_pending_artifacts(workspace)
            before = {path: Path(path).read_bytes() for path in [markdown_path, json_path]}
            result = get_agent_surface_selection_draft(
                GetAgentSurfaceSelectionRequest(action="get", workspaceRoot=directory)
            )
            self.assertEqual(result["productPlan"], json.loads(before[json_path]))
            self.assertEqual(result["productPlanHash"], agent_fact_digest(result["productPlan"]))
            for path, content in before.items():
                self.assertEqual(Path(path).read_bytes(), content)

    def test_get_stream_completes_success_and_business_failure_lifecycles(self) -> None:
        """查询沿现有 AG-UI 端点交付完整生命周期，缺少草稿也返回结构化失败。"""

        async def read_frames(directory: str) -> list[dict]:
            """消费真实动作流，仅解析测试回执中的标准 SSE 数据帧。"""

            raw = {"action": "get", "workspaceRoot": directory}
            stream = build_agent_planning_action_stream(
                {
                    "threadId": "surface-read",
                    "runId": "surface-read-run",
                    "forwardedProps": {"agentSurfaceSelection": raw},
                },
                raw=raw,
                kind="agentSurfaceSelection",
                accept=None,
            )
            return [
                json.loads(line[6:])
                async for chunk in stream
                for line in chunk.splitlines()
                if line.startswith("data: ")
            ]

        for has_draft in [True, False]:
            with self.subTest(has_draft=has_draft), tempfile.TemporaryDirectory() as directory:
                if has_draft:
                    self._write_pending_artifacts(Path(directory))
                frames = asyncio.run(read_frames(directory))
                types = [frame["type"] for frame in frames]
                self.assertEqual(types[0], "RUN_STARTED")
                self.assertEqual(types[-1], "RUN_FINISHED")
                for expected in [
                    "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT",
                    "TEXT_MESSAGE_END", "STATE_SNAPSHOT",
                ]:
                    self.assertIn(expected, types)
                result = next(frame["value"] for frame in frames if frame["type"] == "CUSTOM")
                self.assertEqual(result["status"], "completed" if has_draft else "failed")
                if has_draft:
                    self.assertEqual(result["productPlanHash"], agent_fact_digest(result["productPlan"]))
                else:
                    self.assertIn("草稿", result["error"]["message"])

    def test_save_result_is_next_baseline_and_old_baseline_is_rejected(self) -> None:
        """连续关闭再开启使用保存回执的新摘要，旧页面的摘要仍须被拒绝。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_pending_artifacts(workspace)
            original = get_agent_surface_selection_draft(
                GetAgentSurfaceSelectionRequest(action="get", workspaceRoot=directory)
            )
            binding = original["productPlan"]["agents"][0]["pageActionBindings"][0]
            request = {
                "action": "save",
                "workspaceRoot": directory,
                "agentId": "inventory_assistant",
                "pageId": binding["pageId"],
                "enabled": False,
                "basedOnProductPlanHash": original["productPlanHash"],
            }
            saved = save_agent_surface_selection_draft(SaveAgentSurfaceSelectionRequest(**request))
            self.assertNotEqual(saved["productPlanHash"], original["productPlanHash"])
            self.assertEqual(saved["productPlanHash"], agent_fact_digest(saved["productPlan"]))
            self.assertEqual(saved["productPlan"]["confirmation_status"], "pending_user_confirmation")
            with self.assertRaisesRegex(ValueError, "产品草稿已变化"):
                save_agent_surface_selection_draft(SaveAgentSurfaceSelectionRequest(**request))
            reopened = save_agent_surface_selection_draft(
                SaveAgentSurfaceSelectionRequest(
                    **{**request, "enabled": True, "basedOnProductPlanHash": saved["productPlanHash"]}
                )
            )
            self.assertTrue(reopened["productPlan"]["agents"][0]["pageActionBindings"][0]["surface"]["enabled"])

    def test_save_disables_only_selected_floating_surface(self) -> None:
        """关闭浮窗后应保存显式选择，并同步重写 ProductPlan JSON 与 Markdown。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _spec, plan, markdown_path, json_path = self._write_pending_artifacts(workspace)
            binding = plan["agents"][0]["pageActionBindings"][0]

            result = save_agent_surface_selection_draft(
                SaveAgentSurfaceSelectionRequest(
                    action="save",
                    basedOnProductPlanHash=agent_fact_digest(plan),
                    workspaceRoot=str(workspace),
                    agentId="inventory_assistant",
                    pageId=binding["pageId"],
                    enabled=False,
                )
            )

            saved = result["productPlan"]
            self.assertFalse(saved["agents"][0]["pageActionBindings"][0]["surface"]["enabled"])
            self.assertFalse(
                json.loads(Path(json_path).read_text(encoding="utf-8"))["agents"][0][
                    "pageActionBindings"
                ][0]["surface"]["enabled"]
            )
            self.assertIn("集成状态 关闭", Path(markdown_path).read_text(encoding="utf-8"))

    def test_save_rejects_standalone_surface_and_confirmed_plan(self) -> None:
        """独立页面或已确认规划都不得被浮窗开关修改。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _spec, plan, _markdown_path, _json_path = self._write_pending_artifacts(workspace)
            binding = plan["agents"][0]["pageActionBindings"][0]
            binding["surface"]["type"] = "standalone_page"
            write_product_plan_documents({"workspace": str(workspace)}, plan)
            request = SaveAgentSurfaceSelectionRequest(
                action="save",
                basedOnProductPlanHash=agent_fact_digest(plan),
                workspaceRoot=str(workspace),
                agentId="inventory_assistant",
                pageId=binding["pageId"],
                enabled=False,
            )

            with self.assertRaisesRegex(ValueError, "只有悬浮问答面板"):
                save_agent_surface_selection_draft(request)

            binding["surface"].update({"type": "floating_panel", "enabled": True})
            plan["confirmation_status"] = "confirmed"
            write_product_plan_documents({"workspace": str(workspace)}, plan)
            with self.assertRaisesRegex(ValueError, "待确认"):
                save_agent_surface_selection_draft(request)


if __name__ == "__main__":
    unittest.main()
