from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.agent_surface_selection import (
    SaveAgentSurfaceSelectionRequest,
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

    def test_save_disables_only_selected_floating_surface(self) -> None:
        """关闭浮窗后应保存显式选择，并同步重写 ProductPlan JSON 与 Markdown。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _spec, plan, markdown_path, json_path = self._write_pending_artifacts(workspace)
            binding = plan["agents"][0]["pageActionBindings"][0]

            result = save_agent_surface_selection_draft(
                SaveAgentSurfaceSelectionRequest(
                    action="save",
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
