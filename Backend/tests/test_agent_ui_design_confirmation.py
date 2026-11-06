"""多文件设计必须复核真实资产，再沿原显式确认门提交。"""

from __future__ import annotations

from copy import deepcopy
import asyncio
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command

from tests.test_agent_ui_design_generator import formal_facts
from app.graph.nodes.ui_confirmation import _product_plan_hash, _verified_ui_designs_for_confirmation, ui_confirmation
from app.graph.application_planning_interrupts import planning_stage_entry, ui_confirmation_review
from app.graph.application_planning_workflow import _route_ui_confirmation
from app.graph.state import ProjectState
from app.services.agent_ui_design_project import SEED_FILES, source_digest, tree_digest
from app.services.ui_design_manifest import build_ui_page_manifest
from app.services.ui_design_generation_pool import get_ui_design_generation_pool
from app.services.ui_design_agent_surfaces import project_ui_design_pages
from app.services.product_plan import create_product_plan
from app.services.requirement_spec import create_requirement_spec
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path, write_ui_designs_json


def confirmation_fixture(directory: str) -> tuple[dict, dict, Path]:
    """构造真实文件摘要与安装 metadata，模型和昂贵构建不参与确认测试。"""
    requirement, product = formal_facts()
    requirement = create_requirement_spec("创建一个文本助手") | requirement
    requirement["pages"] = [{"pageId": page["pageId"], "name": page["name"],
        "path": "/" + page["pageId"], "description": page["name"]} for page in product["pages"]]
    requirement["agent_requirements"] = [{"agentId": "assistant", "name": "通用助手", "purpose": "通用文本对话",
        "capabilities": ["文本对话"], "entryPageIds": ["home", "chat"], "interactionMode": "conversation",
        "boundaries": ["只提供文本回复"]}]
    for page in product["pages"]:
        for action in page["actions"]:
            action.update(description="使用助手", requiresConfirmation=False,
                          behavior={"type": "interface", "expectedResult": "展示助手"})
    product = {**create_product_plan(requirement, agent_plan=product), "confirmation_status": "confirmed"}
    state = {"workspace": directory, "project_id": "test", "workflow_scope": "application_planning",
             "requirement_spec": requirement, "product_plan": product,
             "application_planning_interaction": {"action": "confirm"}}
    root = Path(directory) / ".devagentstudio/ui-design/drafts" / ("a" * 32) / "agent-ui-project"
    project = root / "tools/agent-ui-consumer"
    (project / "src").mkdir(parents=True)
    (project / "src/pages").mkdir()
    (project / "src/pages/page-0.tsx").write_text(
        'export default function Page() { return <p data-information-item-id="home-information-1" '
        'data-control-id="welcome">首页</p> }', encoding="utf-8")
    (project / "dist").mkdir()
    (project / "src/App.tsx").write_text("export const App = 'current'", encoding="utf-8")
    (project / "dist/index.html").write_text("current build", encoding="utf-8")
    for name in (*SEED_FILES, "vite.config.ts", "index.html"):
        (project / name).write_text("{}", encoding="utf-8")
    vendor = Path(__file__).resolve().parents[2] / "vendor/agent-ui/manifest.json"
    for package in json.loads(vendor.read_text())["packages"]:
        if package["name"].endswith(("creator", "web-component")):
            continue
        path = project / "node_modules" / package["name"] / "package.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"name": package["name"], "version": package["version"]}), encoding="utf-8")
    facts = hashlib.sha256(json.dumps({"requirement": requirement, "product": product},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    source = source_digest(project)
    identity = "b" * 64
    revision = hashlib.sha256((facts + identity + source).encode()).hexdigest()
    asset = {"kind": "agent-ui-project", "draft": "a" * 32, "revision": revision,
             "factsDigest": facts, "environmentIdentity": identity, "sourceDigest": source,
             "buildDigest": tree_digest(project / "dist"), "pageIds": ["home", "chat"]}
    (root / "generation.json").write_text(json.dumps({**asset, "status": "built",
        "pages": [{"pageId": "home"}, {"pageId": "chat"}]}), encoding="utf-8")
    pages = []
    for page in project_ui_design_pages(product):
        entry = build_ui_page_manifest(page, page_key=page["pageId"].title(), status="ready")
        entry.update(design_asset=asset, verification={"status": "pending_user_confirmation",
            "checks": [{"id": name, "status": "passed"} for name in ("lint", "typecheck", "build")], "errors": []})
        pages.append(entry)
    manifest = {"confirmation_status": "pending_user_confirmation", "product_plan_sha256": _product_plan_hash(state),
                "pages": pages}
    write_ui_designs_json(state, manifest)
    state["ui_designs"] = load_ui_designs_json(ui_designs_json_path(state))
    return state, state["ui_designs"], project


class AgentUiDesignConfirmationTests(unittest.IsolatedAsyncioTestCase):
    """确认只写当前正式证据，不重跑模型、构建或 Mock 服务。"""

    async def test_current_ready_project_confirms_only_on_explicit_submission(self) -> None:
        """有效 ready 资产经用户动作确认，随后仍停在计划阶段入口。"""
        with TemporaryDirectory() as directory:
            state, manifest, _ = confirmation_fixture(directory)
            before = deepcopy(manifest)
            result = await ui_confirmation(state)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["ui_designs"]["confirmation_status"], "confirmed")
            for page in result["ui_designs"]["pages"]:
                self.assertEqual(page["status"], "confirmed")
                self.assertEqual(page["asset_confirmation"]["pageId"], page["pageId"])
                self.assertEqual(len(page["asset_confirmation"]["pageContentDigest"]), 64)
                self.assertTrue(page["asset_confirmation"]["packages"])
            self.assertEqual(manifest, before)

    def test_page_confirmation_depends_on_shared_code_and_own_presentation(self) -> None:
        """一页欢迎语不使另一页确认失效，共享源码修改仍须全体重新确认。"""
        from app.services.agent_ui_design_confirmation import agent_design_evidence

        with TemporaryDirectory() as directory:
            state, manifest, project = confirmation_fixture(directory)
            project = project.resolve()
            design = project / 'src/design'
            design.mkdir()
            presentation = design / 'presentation.json'
            presentation.write_text('{}')
            # 当前资产校验在单独测试中覆盖；这里只隔离已经验证的构建，检查确认范围。
            from types import SimpleNamespace
            with patch('app.services.agent_ui_design_projection.current_project',
                       side_effect=lambda _state, page: (SimpleNamespace(project_dir=project), page['design_asset'])):
                before = [agent_design_evidence(state, page) for page in manifest['pages']]
                presentation.write_text('{"chat":{"props":{"welcome":{"title":"你好"}},"styles":[]}}')
                after = [agent_design_evidence(state, page) for page in manifest['pages']]
                self.assertEqual(before[0], after[0])
                self.assertNotEqual(before[1], after[1])
                (project / 'src/App.tsx').write_text('changed shared host')
                final = [agent_design_evidence(state, page) for page in manifest['pages']]
                self.assertTrue(all(left != right for left, right in zip(after, final, strict=True)))

    def test_changed_source_build_lock_package_and_facts_are_rejected(self) -> None:
        """源码、构建、依赖或正式事实变化时，旧资产不能被确认。"""
        for target in ("source", "build", "lock", "package", "facts", "missing", "duplicate", "page-order"):
            with self.subTest(target=target), TemporaryDirectory() as directory:
                state, manifest, project = confirmation_fixture(directory)
                if target == "facts":
                    state["requirement_spec"]["app"]["name"] = "changed"
                elif target == "missing":
                    manifest["pages"][1].pop("design_asset")
                elif target == "duplicate":
                    manifest["pages"].append(deepcopy(manifest["pages"][0]))
                elif target == "page-order":
                    manifest['pages'][0]['design_asset']['pageIds'].reverse()
                else:
                    path = {"source": project / "src/App.tsx", "build": project / "dist/index.html",
                        "lock": project / "pnpm-lock.yaml", "package": project / "node_modules/@za38.03/agent-ui-runtime/package.json"}[target]
                    path.write_text('{"name":"changed","version":"99.0.0"}', encoding="utf-8")
                result, errors = _verified_ui_designs_for_confirmation(state, manifest)
                self.assertTrue(errors)
                self.assertEqual(result["confirmation_status"], "pending_user_confirmation")

    async def test_disk_manifest_changed_since_review_cannot_confirm(self) -> None:
        """旧 checkpoint 的确认不能批准后台刚刚写入的新修订。"""
        with TemporaryDirectory() as directory:
            state, manifest, _ = confirmation_fixture(directory)
            latest = deepcopy(manifest)
            latest["pages"][0]["status"] = "generation_failed"
            write_ui_designs_json(state, latest)
            result = await ui_confirmation(state)
            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["ui_designs"]["confirmation_status"], "pending_user_confirmation")
            self.assertTrue(result["clarification"]["validation_errors"])
            self.assertFalse(any(page.get("asset_confirmation") for page in result["ui_designs"]["pages"]))

    async def test_native_review_confirms_then_stops_before_technical_planning(self) -> None:
        """真实 Graph interrupt 往返后仍需独立 enter_planning，不自动调用下游。"""
        with TemporaryDirectory() as directory:
            state, _manifest, _ = confirmation_fixture(directory)
            state["application_planning_interaction"] = {}
            builder = StateGraph(ProjectState)
            builder.add_node("ui_confirmation", ui_confirmation)
            builder.add_node("ui_confirmation_review", ui_confirmation_review)
            builder.add_node("planning_stage_entry", planning_stage_entry)
            def unexpected_revision(_state: ProjectState) -> dict:
                """测试确认动作不能误入设计修订或技术生成。"""
                raise AssertionError("confirmation entered an unexpected stage")
            builder.add_node("design_intent_analysis", unexpected_revision)
            builder.add_edge(START, "ui_confirmation")
            builder.add_conditional_edges("ui_confirmation", _route_ui_confirmation)
            graph = builder.compile(checkpointer=InMemorySaver())
            config = {"configurable": {"thread_id": "t20-test"}}
            first = await graph.ainvoke(state, config)
            review = first["__interrupt__"][0].value
            self.assertEqual(review["artifact"], "ui_designs")
            submission = {key: review[key] for key in ("gateId", "artifact", "artifactRevision")}
            final = await graph.ainvoke(Command(resume={**submission, "action": "confirm"}), config)
            self.assertEqual(final["ui_designs"]["confirmation_status"], "confirmed")
            self.assertEqual(final["__interrupt__"][0].value["phase"], "planning_stage_entry")
            self.assertFalse(final.get("technical_plan"))

    async def test_failed_formal_write_does_not_report_confirmation(self) -> None:
        """正式 JSON 提交失败必须失败关闭，并恢复原 Markdown。"""
        with TemporaryDirectory() as directory:
            state, manifest, _ = confirmation_fixture(directory)
            markdown = ui_designs_json_path(state).with_suffix(".md")
            before = markdown.read_text()
            with patch("app.utils.atomic_json.atomic_write_json", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    await ui_confirmation(state)
            self.assertEqual(load_ui_designs_json(ui_designs_json_path(state)), manifest)
            self.assertEqual(markdown.read_text(), before)

    async def test_confirmation_waits_for_project_writer_then_rejects_stale_review(self) -> None:
        """Creator 持锁期间确认不能覆盖写入；等待后按最新清单拒绝旧版本。"""
        with TemporaryDirectory() as directory:
            state, manifest, _ = confirmation_fixture(directory)
            lock = get_ui_design_generation_pool().agent_project_lock(str(Path(directory).resolve()))
            async with lock:
                confirmation = asyncio.create_task(ui_confirmation(state))
                await asyncio.sleep(0)
                self.assertFalse(confirmation.done())
                latest = deepcopy(manifest)
                latest["pages"][0]["status"] = "generation_failed"
                write_ui_designs_json(state, latest)
            result = await confirmation
            self.assertEqual(result["status"], "requires_user_input")
            self.assertEqual(result["ui_designs"]["confirmation_status"], "pending_user_confirmation")


if __name__ == "__main__":
    unittest.main()
