"""设计阶段文本调整必须支持官方 Agent 工程并保留未选页面。"""

from __future__ import annotations

import unittest
import asyncio
import threading
import time
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import ANY, AsyncMock, patch

from app.graph.nodes.ui_confirmation import _apply_adjust_pages
from tests.test_agent_ui_design_generator import formal_facts
from app.services.agent_ui_design_adjustment import _adjust_project, adjust_agent_pages
from app.services.agent_ui_design_presentation import presentation_files, validate_presentation
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path, write_ui_designs_json


class AgentUiAdjustmentRoutingTests(unittest.IsolatedAsyncioTestCase):
    """复现原 adjust_pages 对 design_asset 页直接抛错的问题。"""

    async def test_standalone_adjustment_uses_project_editor_and_waits_for_confirmation(self) -> None:
        """独立 Agent 页不能再进入单文件编辑或因没有内联 code 被跳过。"""
        requirement, product = formal_facts()
        state = {"workspace": "/workspace", "requirement_spec": requirement, "product_plan": product}
        pages = [{"pageId": page_id, "status": "ready", "design_asset": {"revision": "old"}}
                 for page_id in ("home", "chat")]
        adjusted = [pages[0], {**pages[1], "design_asset": {"revision": "new"}}]
        with patch("app.graph.nodes.ui_confirmation.adjust_agent_pages", new=AsyncMock(return_value=adjusted), create=True) as edit, \
                patch("app.graph.nodes.ui_confirmation._emit_progress") as emit, \
                patch("app.graph.nodes.ui_confirmation.generate_adjusted_page_react_code") as single:
            async def adjust(*_args, on_progress):
                """模拟服务的阶段回调，节点必须沿现有流转发页面和耗时。"""
                on_progress('Creator 正在执行设计修改（已用时 10 秒）', 'chat')
                return adjusted

            edit.side_effect = adjust
            result = await _apply_adjust_pages(state, pages, "/project", {
                "action": "adjust_pages", "pageIds": ["chat"], "instruction": "欢迎语改为你好"})
        edit.assert_awaited_once_with(state, pages, ["chat"], "欢迎语改为你好", on_progress=ANY)
        single.assert_not_called()
        self.assertEqual(result["pages"], adjusted)
        self.assertEqual(result["confirmation_status"], "pending_user_confirmation")
        stage = next(call for call in emit.call_args_list if '已用时 10 秒' in call.args[0])
        self.assertEqual(stage.kwargs['adjust_current'], 'chat')
        self.assertEqual(stage.kwargs['ready'], 0)

    async def test_implicit_targets_are_resolved_before_agent_dispatch(self) -> None:
        """未写 @页面时先识别目标，不能仅因工程含 Agent 页就拒绝整次请求。"""
        requirement, product = formal_facts()
        state = {"workspace": "/workspace", "requirement_spec": requirement, "product_plan": product}
        pages = [{"pageId": "chat", "status": "ready", "design_asset": {"revision": "old"}}]
        with patch("app.graph.nodes.ui_confirmation.resolve_adjust_target_pages", return_value=["chat"]) as resolve, \
                patch("app.graph.nodes.ui_confirmation.adjust_agent_pages", new=AsyncMock(return_value=pages), create=True) as edit, \
                patch("app.graph.nodes.ui_confirmation._emit_progress"):
            await _apply_adjust_pages(state, pages, "/project", {"instruction": "会话欢迎语改为你好"})
        resolve.assert_called_once()
        edit.assert_awaited_once()

    async def test_mixed_targets_keep_ordinary_page_on_existing_editor(self) -> None:
        """混合修改先调整 Agent 工程，再沿原路径修改普通页，不能相互吞掉。"""
        requirement, product = formal_facts()
        product["pages"].append({"pageId": "about", "name": "关于", "actions": [], "information_items": []})
        state = {"workspace": "/workspace", "requirement_spec": requirement, "product_plan": product}
        pages = [{"pageId": "chat", "status": "ready", "design_asset": {"revision": "old"}},
                 {"pageId": "about", "page_key": "About", "code": "old", "status": "confirmed"}]
        with patch("app.graph.nodes.ui_confirmation.adjust_agent_pages", new=AsyncMock(return_value=pages)) as edit, \
                patch("app.graph.nodes.ui_confirmation._emit_progress"), \
                patch("app.graph.nodes.ui_confirmation.load_page_code", return_value="old"), \
                patch("app.graph.nodes.ui_confirmation.persist_page_code", return_value="/project/About/index.tsx"), \
                patch("app.graph.nodes.ui_confirmation.generate_adjusted_page_react_code", return_value="export default function Page() { return <main>更新</main> }") as single:
            result = await _apply_adjust_pages(state, pages, "/project", {
                "pageIds": ["chat", "about"], "instruction": "调整两页标题"})
        edit.assert_awaited_once_with(state, pages, ["chat"], "调整两页标题", on_progress=ANY)
        single.assert_called_once()
        self.assertEqual(result["pages"][0]["design_asset"], {"revision": "old"})
        self.assertIn("更新", result["pages"][1]["code"])


class AgentUiPresentationTests(unittest.TestCase):
    """展示 JSON 不允许修改 Runtime，也不能通过 CSS 逃出页面。"""

    def test_rejects_transport_and_external_or_unscoped_css(self) -> None:
        """任意连接字段、外部资源、规则注入和选择器列表都应在写入前拒绝。"""
        for value in (
            {"props": {"endpoint": "https://invalid.test"}, "styles": []},
            {"props": {}, "styles": [{"selector": "p, body", "declarations": {"color": "red"}}]},
            {"props": {}, "styles": [{"selector": "p", "declarations": {"background": "url(https://invalid.test)"}}]},
            {"props": {}, "styles": [{"selector": "p", "declarations": {"color": "red;}body{display:none"}}]},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_presentation(value)

    def test_style_rules_are_scoped_to_each_page(self) -> None:
        """两页的同名选择器不能相互污染，根节点规则也保持页面身份。"""
        rule = {"props": {}, "styles": [{"selector": ":scope", "declarations": {"padding": "8px"}},
            {"selector": "textarea", "declarations": {"min-height": "100px"}}]}
        source = presentation_files({"chat": rule})["src/design/presentation.ts"]
        self.assertIn('data-design-page=\\"chat\\"', source)
        self.assertNotIn('"css": "textarea', source)


class AgentUiAdjustmentFailureTests(unittest.IsolatedAsyncioTestCase):
    """失败与停止保留旧资产，并复用原工程串行边界。"""

    async def test_waiting_worker_reports_stage_and_elapsed_without_resubmission(self) -> None:
        """长任务定期显示真实阶段和耗时，终态后停止进度且不重复提交 Creator。"""
        pool = SimpleNamespace(agent_project_lock=lambda _: asyncio.Lock())
        reports: list[tuple[str, str]] = []
        loop = asyncio.get_running_loop()

        def report(message: str, page_id: str) -> None:
            """验证工作线程的通知在原事件循环交给工作流 writer。"""
            self.assertIs(asyncio.get_running_loop(), loop)
            reports.append((message, page_id))

        def edit(_state, pages, _targets, _instruction, _cancelled, on_progress):
            """模拟无法立即返回的 Creator；等待期间不得被当成超时失败或重新执行。"""
            on_progress('Creator 正在执行设计修改', 'chat')
            time.sleep(0.09)
            on_progress('正在启动设计预览', 'chat')
            return pages

        with patch('app.services.agent_ui_design_adjustment.get_ui_design_generation_pool', return_value=pool), \
                patch('app.services.agent_ui_design_adjustment._PROGRESS_INTERVAL', 0.02), \
                patch('app.services.agent_ui_design_adjustment._adjust_project', side_effect=edit) as worker:
            self.assertEqual(await adjust_agent_pages({'workspace': '/workspace'}, [], ['chat'], '调整',
                on_progress=report), [])
        worker.assert_called_once()
        self.assertGreaterEqual(sum('Creator 正在执行' in message for message, _ in reports), 2)
        self.assertTrue(all('已用时' in message and page == 'chat' for message, page in reports))
        count = len(reports)
        await asyncio.sleep(0.04)
        self.assertEqual(len(reports), count)

    async def test_failed_adjustment_keeps_asset_and_unrelated_pages(self) -> None:
        """构建失败只标记目标页，不抛成整个工作流中断或销毁原设计。"""
        pages = [{"pageId": page_id, "status": "ready", "design_asset": {"revision": "old"}}
                 for page_id in ("home", "chat")]
        pool = SimpleNamespace(agent_project_lock=lambda _: asyncio.Lock())
        with patch("app.services.agent_ui_design_adjustment.get_ui_design_generation_pool", return_value=pool), \
                patch("app.services.agent_ui_design_adjustment._adjust_project", side_effect=ValueError("构建未通过")):
            result = await adjust_agent_pages({"workspace": "/workspace"}, pages, ["chat"], "调整欢迎语")
        self.assertEqual(result[0], pages[0])
        self.assertEqual(result[1]["design_asset"], pages[1]["design_asset"])
        self.assertEqual(result[1]["status"], "generation_failed")
        self.assertEqual(result[1]["error"], "构建未通过")

    async def test_cancel_waits_for_worker_to_observe_cancellation(self) -> None:
        """断开 Graph 时等待线程取消边界，不能让后台线程迟到写入或切换预览。"""
        entered, stopped = threading.Event(), threading.Event()
        pool = SimpleNamespace(agent_project_lock=lambda _: asyncio.Lock())

        def edit(_state, _pages, _targets, _instruction, cancelled, _on_progress):
            """模拟模型等待，取消后释放执行线程。"""
            entered.set()
            cancelled.wait(2)
            stopped.set()
            return []

        with patch("app.services.agent_ui_design_adjustment.get_ui_design_generation_pool", return_value=pool), \
                patch("app.services.agent_ui_design_adjustment._adjust_project", side_effect=edit):
            task = asyncio.create_task(adjust_agent_pages({"workspace": "/workspace"}, [], ["chat"], "调整"))
            await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stopped.is_set())


class AgentUiAdjustmentProjectTests(unittest.TestCase):
    """使用真实清单与源码，验证成功交接、连续编辑输入以及陈旧清单保护。"""

    def setUp(self) -> None:
        """仅隔离昂贵的工程构建和进程启动，保留真实输入读取和清单保存。"""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspace = Path(temporary.name).resolve()
        requirement, product = formal_facts()
        self.state = {"workspace": str(workspace), "requirement_spec": requirement, "product_plan": product}
        project_dir = workspace / "current"
        (project_dir / "src/pages").mkdir(parents=True)
        (project_dir / "src/pages/page-0.tsx").write_text("keep current home", encoding="utf-8")
        (project_dir / "src/design").mkdir()
        (project_dir / "src/design/presentation.json").write_text(
            '{"home":{"props":{"welcome":{"title":"上轮定制"}},"styles":[]}}', encoding="utf-8")
        self.asset = {"kind": "agent-ui-project", "revision": "old", "pageIds": ["home", "chat"]}
        self.pages = [{"pageId": page_id, "status": "ready", "page_key": page_id.title(),
                       "design_asset": self.asset, "asset_confirmation": {"revision": "old"},
                       "custom": "keep"} for page_id in ("home", "chat")]
        write_ui_designs_json(self.state, {"pages": self.pages})
        self.pages = load_ui_designs_json(ui_designs_json_path(self.state))["pages"]
        project = SimpleNamespace(workspace=workspace, project_dir=project_dir,
            identity="environment", source_digest="new-source", build_digest="new-build")
        self.enterContext(patch("app.services.agent_ui_design_projection.current_project", return_value=(project, self.asset)))
        self.enterContext(patch("app.services.agent_ui_design_adjustment.validate_design_project"))
        self.enterContext(patch("app.services.agent_ui_design_adjustment.design_environment",
                                return_value=(SimpleNamespace(node_executable=Path("/node")), Path("/pnpm.cjs"))))
        self.generate = self.enterContext(patch("app.services.agent_ui_design_adjustment.revise_agent_ui_design",
            return_value=SimpleNamespace(project=project, revision="new", facts_digest="facts", affected_page_ids=('chat',))))
        self.preview = self.enterContext(patch("app.services.agent_ui_design_adjustment.agent_ui_design_previews.start"))

    def test_success_changes_shared_reference_but_preserves_non_target_metadata(self) -> None:
        """只撤销目标页旧确认，其他页面源码和展示定制继续作为新设计输入。"""
        result = _adjust_project(self.state, self.pages, ["chat"], "欢迎语改为你好", threading.Event())
        options = self.generate.call_args.kwargs
        self.assertEqual(options["targets"], ["chat"])
        self.assertEqual(options["instruction"], "欢迎语改为你好")
        self.assertEqual((options["current_project"].project_dir / "src/pages/page-0.tsx").read_text(), "keep current home")
        self.assertEqual(result[0], {**self.pages[0], "design_asset": result[1]["design_asset"]})
        self.assertEqual(result[1]["verification"]["status"], "pending_user_confirmation")
        self.assertNotIn("asset_confirmation", result[1])
        self.assertEqual(result[1]["design_asset"]["revision"], "new")
        self.assertEqual(load_ui_designs_json(ui_designs_json_path(self.state))["pages"], result)
        self.preview.assert_called_once()

    def test_shared_layout_revokes_confirmation_for_every_agent_page(self) -> None:
        """共享布局或组件变化时不能保留任何使用页的旧确认。"""
        self.generate.return_value.affected_page_ids = ('home', 'chat')
        result = _adjust_project(self.state, self.pages, ['chat'], '修改共享布局', threading.Event())
        self.assertTrue(all(page['status'] == 'ready' and not page.get('asset_confirmation') for page in result))

    def test_adjusted_business_source_refreshes_effects_before_confirmation(self) -> None:
        """调整后的本地效果随新资产落盘，不能沿用上一稿的交互绑定。"""
        self.state["product_plan"]["pages"][0]["actions"].append({
            "actionId": "open_panel", "behavior": {"type": "interface", "expectedResult": "产品预期打开"}})
        project = self.generate.return_value.project
        (project.project_dir / "src/pages/page-0.tsx").write_text(
            'export default function Page({onOpenAgentPanel}) { return <button data-action-id="open_panel" '
            'data-control-id="open" data-ui-effect="展开调整后的浮窗" onClick={onOpenAgentPanel}>打开</button> }',
            encoding="utf-8")
        self.generate.return_value.affected_page_ids = ('home',)
        result = _adjust_project(self.state, self.pages, ['home'], '调整打开入口', threading.Event())
        binding = next(item for item in result[0]["bindings"]["actions"] if item["actionId"] == "open_panel")
        self.assertEqual(binding["uiEffect"], "展开调整后的浮窗")
        self.assertNotIn("asset_confirmation", result[0])
        self.assertEqual(result[1], {**self.pages[1], "design_asset": result[0]["design_asset"]})
        self.assertEqual(load_ui_designs_json(ui_designs_json_path(self.state))["pages"], result)

    def test_changed_manifest_prevents_preview_switch_and_preserves_current_disk(self) -> None:
        """构建期间有新状态落盘时，不以旧快照覆盖它，也不切换到过期预览。"""
        changed = [{**page, "status": "queued"} for page in self.pages]

        def generate(*_args, **_options):
            """模拟模型构建期间另一条合法动作更新当前清单。"""
            write_ui_designs_json(self.state, {"pages": changed})
            return self.generate.return_value

        self.generate.side_effect = generate
        with self.assertRaisesRegex(ValueError, "状态已变化"):
            _adjust_project(self.state, self.pages, ["chat"], "欢迎语改为你好", threading.Event())
        self.preview.assert_not_called()
        self.assertEqual(load_ui_designs_json(ui_designs_json_path(self.state))["pages"], changed)

    def test_invalid_business_binding_preserves_preview_and_manifest(self) -> None:
        """交互源码不完整时不能切换到无法确认的新工程或覆盖现有清单。"""
        self.state["product_plan"]["pages"][0]["actions"].append({
            "actionId": "open_panel", "behavior": {"type": "interface", "expectedResult": "展开浮窗"}})
        self.generate.return_value.affected_page_ids = ('home',)
        with self.assertRaisesRegex(ValueError, "业务页面"):
            _adjust_project(self.state, self.pages, ['home'], '调整入口', threading.Event())
        self.preview.assert_not_called()
        self.assertEqual(load_ui_designs_json(ui_designs_json_path(self.state))["pages"], self.pages)
