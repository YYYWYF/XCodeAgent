"""生成应用 Agent UI 固定资产注入服务测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.frontend_agent_ui_assets import (
    FrontendAgentUiAssetError,
    write_missing_agent_ui_file_atomically,
)
from app.services.frontend_agent_ui_scaffold import (
    AGENT_UI_FRONTEND_TARGET_PATHS,
    AGENT_UI_FRONTEND_TEMPLATE_VERSION,
    FrontendAgentUiScaffoldError,
    inject_frontend_agent_ui_scaffold,
    validate_frontend_agent_ui_scaffold,
)


class FrontendAgentUiScaffoldTests(unittest.TestCase):
    """验证生成应用只按已确认 ProductPlan 条件式获得固定 Agent UI 资产。"""

    def _workspace(
        self,
        root: Path,
        *,
        surface_type: str | None = "standalone_page",
        enabled: bool = True,
        confirmation_status: str = "confirmed",
    ) -> None:
        """创建带最小前端目录和正式 ProductPlan 的测试工作区。"""

        (root / "frontend/src").mkdir(parents=True)
        (root / ".xcodeagent/plans").mkdir(parents=True)
        bindings = []
        if surface_type is not None:
            bindings.append(
                {
                    "pageId": "assistant",
                    "actionIds": ["assistant.ask"],
                    "surface": {
                        "type": surface_type,
                        "enabled": enabled,
                        "contextItemIds": ["assistant.summary"],
                    },
                }
            )
        product_plan = {
            "schema_version": "product-plan.v8",
            "confirmation_status": confirmation_status,
            "agents": [
                {
                    "agentId": "assistant",
                    "pageActionBindings": bindings,
                }
            ],
        }
        (root / ".xcodeagent/plans/product-plan.json").write_text(
            json.dumps(product_plan, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_injects_the_same_fixed_assets_for_an_enabled_surface(self) -> None:
        """任一启用 Surface 都应注入唯一一套共享组件和 Mock Adapter。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)

            result = inject_frontend_agent_ui_scaffold(root)

            self.assertTrue(result["required"])
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(
                result["templateVersion"], AGENT_UI_FRONTEND_TEMPLATE_VERSION
            )
            self.assertEqual(result["createdCount"], len(AGENT_UI_FRONTEND_TARGET_PATHS))
            self.assertEqual(result["reusedCount"], 0)
            self.assertEqual(
                [target["path"] for target in result["targets"]],
                list(AGENT_UI_FRONTEND_TARGET_PATHS),
            )
            for relative_path in AGENT_UI_FRONTEND_TARGET_PATHS:
                self.assertTrue((root / relative_path).is_file(), relative_path)

            core_source = (
                root / "frontend/src/components/AgentConversation/AgentChatCore.tsx"
            ).read_text(encoding="utf-8")
            page_source = (
                root
                / "frontend/src/components/AgentConversation/AgentConversationPage.tsx"
            ).read_text(encoding="utf-8")
            floating_source = (
                root
                / "frontend/src/components/AgentConversation/AgentFloatingPanel.tsx"
            ).read_text(encoding="utf-8")
            adapter_source = (
                root / "frontend/src/apis/agentConversationMock.ts"
            ).read_text(encoding="utf-8")
            self.assertIn("模拟运行", core_source)
            self.assertIn("AgentChatCore", page_source)
            self.assertIn("AgentChatCore", floating_source)
            self.assertIn("x-agent-floating__panel", floating_source)
            self.assertNotIn("AgentMobileChatDrawer", floating_source)
            self.assertIn("class MockAgentConversationAdapter", adapter_source)
            self.assertNotIn("fetch(", adapter_source)
            self.assertNotIn("axios", adapter_source)
            self.assertNotIn("EventSource", adapter_source)
            self.assertEqual(validate_frontend_agent_ui_scaffold(root, result), [])

    def test_disabled_or_missing_surfaces_do_not_create_agent_ui_files(self) -> None:
        """无启用 Surface 时必须记录 skipped，且不能创建任何 Agent UI 资产。"""

        for surface_type, enabled in (("floating_panel", False), (None, False)):
            with (
                self.subTest(surface_type=surface_type),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self._workspace(root, surface_type=surface_type, enabled=enabled)

                result = inject_frontend_agent_ui_scaffold(root)

                self.assertFalse(result["required"])
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["targets"], [])
                self.assertFalse(
                    (root / "frontend/src/components/AgentConversation").exists()
                )
                self.assertEqual(validate_frontend_agent_ui_scaffold(root, result), [])

    def test_repeated_injection_reuses_matching_files_without_rewriting(self) -> None:
        """内容与摘要一致时第二次注入只复用，不改写目标文件。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root, surface_type="floating_panel")
            first = inject_frontend_agent_ui_scaffold(root)
            mtimes = {
                path: (root / path).stat().st_mtime_ns
                for path in AGENT_UI_FRONTEND_TARGET_PATHS
            }

            second = inject_frontend_agent_ui_scaffold(root)

            self.assertEqual(first["sourceSha256"], second["sourceSha256"])
            self.assertEqual(second["createdCount"], 0)
            self.assertEqual(
                second["reusedCount"], len(AGENT_UI_FRONTEND_TARGET_PATHS)
            )
            self.assertEqual(
                mtimes,
                {
                    path: (root / path).stat().st_mtime_ns
                    for path in AGENT_UI_FRONTEND_TARGET_PATHS
                },
            )

    def test_conflict_fails_before_writing_any_other_asset(self) -> None:
        """同路径非一致文件必须保留，且预检失败前不能产生部分注入。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            conflict_path = root / AGENT_UI_FRONTEND_TARGET_PATHS[0]
            conflict_path.parent.mkdir(parents=True)
            conflict_path.write_text("// user-owned file\n", encoding="utf-8")

            with self.assertRaisesRegex(
                FrontendAgentUiScaffoldError, "已有文件与固定资产不一致"
            ):
                inject_frontend_agent_ui_scaffold(root)

            self.assertEqual(
                conflict_path.read_text(encoding="utf-8"), "// user-owned file\n"
            )
            for relative_path in AGENT_UI_FRONTEND_TARGET_PATHS[1:]:
                self.assertFalse((root / relative_path).exists(), relative_path)

    def test_atomic_writer_never_replaces_a_racing_target(self) -> None:
        """原子提交遇到已存在目标时必须保留内容并清理临时文件。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "AgentChatCore.tsx"
            target.write_text("// racing file\n", encoding="utf-8")

            with self.assertRaisesRegex(
                FrontendAgentUiAssetError, "写入期间发生冲突"
            ):
                write_missing_agent_ui_file_atomically(target, b"// fixed asset\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "// racing file\n")
            self.assertEqual(list(root.glob(".AgentChatCore.tsx.*.tmp")), [])

    def test_rejects_unconfirmed_or_unknown_surface_product_plan(self) -> None:
        """注入条件只能来自当前已确认且 Surface 类型合法的 ProductPlan。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root, confirmation_status="pending_user_confirmation")
            with self.assertRaisesRegex(FrontendAgentUiScaffoldError, "尚未确认"):
                inject_frontend_agent_ui_scaffold(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root, surface_type="modal")
            with self.assertRaisesRegex(FrontendAgentUiScaffoldError, "Surface 类型无效"):
                inject_frontend_agent_ui_scaffold(root)


if __name__ == "__main__":
    unittest.main()
