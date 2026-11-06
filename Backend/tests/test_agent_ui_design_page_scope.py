"""单页重生成必须保持目标范围、非目标源码与共享工程串行提交。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.agent_ui_design_sources import retained_page_sources, retained_agent_sources
from app.services.agent_ui_design_workflow import group_agent_tasks
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from app.services.ui_design_generation_pool import UiDesignGenerationPool, UiDesignGenerationTask
from tests.test_agent_ui_design_generator import formal_facts


def page_tasks(workspace: str) -> list[UiDesignGenerationTask]:
    """构造共享同一 Agent 的首页与独立页请求。"""
    requirement, product = formal_facts()
    return [UiDesignGenerationTask(workspace=workspace, project_id="test", project_dir=workspace,
        page_id=page["pageId"], spec_page=page, page_key=page["pageId"].title(), action="regenerate",
        requirement_spec=requirement, product_plan=product)
        for page in project_agent_design_pages(requirement, product)]


class AgentUiPageScopeTests(unittest.IsolatedAsyncioTestCase):
    """隔离外部生成操作，检查真实任务范围、源码选择与清单写入。"""

    def test_single_page_does_not_expand_and_batch_keeps_requested_pages(self) -> None:
        """单页只标记自身，全量操作仍合并为一次共享工程构建。"""
        tasks = page_tasks("/workspace")
        for task in tasks:
            grouped = group_agent_tasks([task])
            self.assertEqual([member.page_id for member in grouped[0].members], [task.page_id])
        grouped = group_agent_tasks(tasks)
        self.assertEqual(len(grouped), 1)
        self.assertEqual([member.page_id for member in grouped[0].members], ["home", "chat"])

    def test_retains_only_non_target_source_and_rejects_stale_project(self) -> None:
        """刷新独立页从当前资产保留首页，当前资产校验失败时不能偷偷全量重画。"""
        with TemporaryDirectory() as directory:
            tasks = page_tasks(directory)
            task = group_agent_tasks([tasks[1]])[0]
            pages = project_agent_design_pages(task.requirement_spec, task.product_plan)
            project_dir = Path(directory)
            source = project_dir / "src/pages/page-0.tsx"
            source.parent.mkdir(parents=True)
            source.write_text("preserved home", encoding="utf-8")
            asset = {"kind": "agent-ui-project", "revision": "current"}
            manifest = {"pages": [{"pageId": "home", "design_asset": asset}]}
            with patch("app.services.agent_ui_design_sources.load_ui_designs_json", return_value=manifest), \
                    patch("app.services.agent_ui_design_sources.validate_design_project") as validate, \
                    patch("app.services.agent_ui_design_projection.current_project",
                          return_value=(SimpleNamespace(project_dir=project_dir), asset)) as current:
                self.assertEqual(retained_page_sources(task, pages), {"src/pages/page-0.tsx": "preserved home"})
                validate.assert_called_once()
                current.side_effect = ValueError("stale project")
                with self.assertRaisesRegex(ValueError, "stale project"):
                    retained_page_sources(task, pages)
            with patch("app.services.agent_ui_design_sources.load_ui_designs_json", return_value={}):
                self.assertEqual(retained_page_sources(task, pages), {})

    def test_regeneration_keeps_non_target_creator_json_and_component_sources(self) -> None:
        """刷新首页时，独立页经 Creator 修改的 JSON 和组件包装保持原样。"""
        with TemporaryDirectory() as directory:
            task = group_agent_tasks([page_tasks(directory)[0]])[0]
            pages = project_agent_design_pages(task.requirement_spec, task.product_plan)
            root = Path(directory)
            sources = {'src/agent-ui/app-ui.json': '{"root":{"type":"row"}}',
                       'src/agent-ui/application/project-config.tsx': 'export const project = {}'}
            for path, source in sources.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source)
            asset = {'kind': 'agent-ui-project'}
            with patch('app.services.agent_ui_design_sources.load_ui_designs_json',
                       return_value={'pages': [{'pageId': 'chat', 'design_asset': asset}]}), \
                    patch('app.services.agent_ui_design_projection.current_project',
                          return_value=(SimpleNamespace(project_dir=root), asset)), \
                    patch('app.services.agent_ui_design_sources.validate_design_project'):
                self.assertEqual(retained_agent_sources(task, pages), sources)

    async def test_only_target_status_changes_and_shared_asset_updates_atomically(self) -> None:
        """目标页进入生成，未操作页保留状态与元数据，仅切换共享构建引用。"""
        with TemporaryDirectory() as directory:
            task = group_agent_tasks([page_tasks(directory)[1]])[0]
            pool = UiDesignGenerationPool(1)
            asset = {"kind": "agent-ui-project", "revision": "old"}
            manifest = {"pages": [
                {"pageId": "home", "status": "ready", "page_key": "KeptHome", "custom": "keep",
                 "design_asset": asset},
                {"pageId": "chat", "status": "ready", "design_asset": asset}]}
            with patch.object(pool, "_read_manifest", return_value=manifest), \
                    patch("app.services.ui_design_generation_pool.write_ui_designs_json"):
                await pool._write_result(task, {"pageId": "chat", "status": "generating"})
                self.assertEqual(manifest["pages"][0]["status"], "ready")
                self.assertEqual(manifest["pages"][1]["design_asset"], asset)
                updated = {"kind": "agent-ui-project", "revision": "new"}
                await pool._write_result(task, {"project_pages": [
                    {"pageId": page_id, "status": "ready", "design_asset": updated}
                    for page_id in ("home", "chat")]})
            self.assertEqual(manifest["pages"][0], {"pageId": "home", "status": "ready",
                "page_key": "KeptHome", "custom": "keep", "design_asset": updated})
            self.assertEqual(manifest["pages"][1]["design_asset"], updated)

    async def test_same_project_updates_are_serialized(self) -> None:
        """不同目标页可以排队，但必须等前一共享工程提交后才读取最新源码。"""
        pool = UiDesignGenerationPool(2)
        first, second = [group_agent_tasks([task])[0] for task in page_tasks("/workspace")]
        entered = asyncio.Event()
        release = asyncio.Event()
        calls: list[str] = []

        async def process(task: UiDesignGenerationTask) -> None:
            """暂停首页生成，确认独立页不能并发进入源码读取与写入。"""
            calls.append(task.page_id)
            if task.page_id == "home":
                entered.set()
                await release.wait()

        with patch.object(pool, "_process_page", side_effect=process):
            one = asyncio.create_task(pool._process(first))
            await entered.wait()
            two = asyncio.create_task(pool._process(second))
            await asyncio.sleep(0)
            self.assertEqual(calls, ["home"])
            release.set()
            await asyncio.gather(one, two)
        self.assertEqual(calls, ["home", "chat"])

    async def test_shared_creator_change_revokes_non_target_confirmation(self) -> None:
        """重新生成独立页改变共享 UI 后，首页旧确认不可随新资产继续生效。"""
        with TemporaryDirectory() as directory:
            task = group_agent_tasks([page_tasks(directory)[1]])[0]
            pool = UiDesignGenerationPool(1)
            manifest = {"pages": [{"pageId": "home", "status": "confirmed", "custom": "keep",
                "asset_confirmation": {"revision": "old"}, "design_asset": {"revision": "old"}},
                {"pageId": "chat", "status": "ready"}]}
            with patch.object(pool, "_read_manifest", return_value=manifest), \
                    patch("app.services.ui_design_generation_pool.write_ui_designs_json"):
                await pool._write_result(task, {"affected_page_ids": ["home", "chat"], "project_pages": [
                    {"pageId": page_id, "status": "ready", "design_asset": {"revision": "new"},
                     "verification": {"status": "pending_user_confirmation"}} for page_id in ("home", "chat")]})
            home = manifest["pages"][0]
            self.assertEqual(home["status"], "ready")
            self.assertEqual(home["custom"], "keep")
            self.assertNotIn("asset_confirmation", home)
