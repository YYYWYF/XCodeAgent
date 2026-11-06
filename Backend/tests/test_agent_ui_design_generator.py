"""包消费设计必须绑定正式事实，模型只能生成业务页面文件。"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.agent_ui_design_generator import (
    _host_files, _validate_files, generate_agent_ui_design,
)
from app.services.agent_ui_design_response import parse_design_files
from app.services.agent_ui_design_project import (
    AgentUiDesignProject, _PLACEHOLDER, source_digest, tree_digest, validate_design_project,
)
from app.services.agent_ui_tool_host import AgentUiToolHostInspection
from app.services.ui_design_generator import UiDesignStreamCancelled
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from app.services.product_plan import requirement_spec_sha256


def formal_facts() -> tuple[dict, dict]:
    """构造同一文本 Agent 的浮窗和独立页正式输入。"""
    requirement = {"confirmation_status": "confirmed", "app": {"name": "助手示例"}}
    product = {
        "confirmation_status": "confirmed",
        "requirement_spec_sha256": requirement_spec_sha256(requirement),
        "pages": [
            {"pageId": "home", "name": "首页", "information_items": [], "actions": [
                {"actionId": "open_chat", "name": "打开助手"},
            ]},
            {"pageId": "chat", "name": "会话", "information_items": [], "actions": [
                {"actionId": "chat", "name": "对话"},
            ]},
        ],
        "agents": [{
            "agentId": "assistant", "name": "通用助手", "purpose": "通用文本对话",
            "pageActionBindings": [
                {"pageId": "home", "actionIds": ["open_chat"], "surface": {
                    "type": "floating_panel", "enabled": True, "contextItemIds": [],
                }},
                {"pageId": "chat", "actionIds": ["chat"], "surface": {
                    "type": "standalone_page", "enabled": True, "contextItemIds": [],
                }},
            ],
        }],
    }
    return requirement, product


class AgentUiDesignInputTests(unittest.TestCase):
    """事实投影与输出文件边界必须在写入或模型调用前生效。"""

    def test_joint_confirmation_and_digest_are_required(self) -> None:
        """澄清、待确认或陈旧 ProductPlan 不能启动设计生成。"""
        requirement, product = formal_facts()
        for invalid in ({**requirement, "confirmation_status": "pending_user_confirmation"},
                        {**requirement, "app": {"name": "新版本"}}):
            with self.assertRaises(ValueError):
                project_agent_design_pages(invalid, product)
        with self.assertRaises(ValueError):
            project_agent_design_pages(requirement, {**product, "confirmation_status": "pending"})

    def test_one_agent_is_shared_by_both_surface_inputs(self) -> None:
        """按稳定身份保留两种 Surface，不能另造浮窗 Agent 或线程。"""
        pages = project_agent_design_pages(*formal_facts())
        self.assertEqual([page["pageId"] for page in pages], ["home", "chat"])
        self.assertEqual({page["agent_surfaces"][0]["agentId"] for page in pages}, {"assistant"})
        self.assertEqual(pages[0]["agent_surfaces"][0]["actionIds"], ["open_chat"])

    def test_ordinary_pages_stay_with_existing_generator(self) -> None:
        """普通页面不被包消费生产者重新生成。"""
        requirement, product = formal_facts()
        product["pages"].append({"pageId": "about", "name": "关于", "actions": [], "information_items": []})
        self.assertEqual([page["pageId"] for page in project_agent_design_pages(requirement, product)], ["home", "chat"])

    def test_official_welcome_uses_confirmed_agent_without_demo_suggestions(self) -> None:
        """共享会话使用正式身份和职责，不向通用助手注入银行示例业务。"""
        source = _host_files(project_agent_design_pages(*formal_facts()))["src/design/props.ts"]
        props = json.loads(source.split("export const agentProps = ", 1)[1].split(" satisfies", 1)[0])
        self.assertEqual(props["welcome"], {"title": "通用助手", "description": "通用文本对话"})
        self.assertEqual(props["suggestions"], [])

    def test_file_set_is_exact_and_rejects_duplicate_or_foreign_paths(self) -> None:
        """模型不能写配置、依赖、包源码或通过重复 JSON key 隐藏覆盖。"""
        allowed = ("src/pages/page-0.tsx", "src/pages/page-1.tsx")
        files = {path: "export default function Page() { return <main /> }" for path in allowed}
        self.assertEqual(parse_design_files(json.dumps({"files": files}), allowed), files)
        for output in (
            json.dumps({"files": {**files, "../escape.ts": "bad"}}),
            json.dumps({"files": {allowed[0]: files[allowed[0]]}}),
            '{"files": {}, "files": {}}',
            json.dumps({"files": {**files, "package.json": "{}"}}),
        ):
            with self.assertRaises(ValueError):
                parse_design_files(output, allowed)

    def test_invalid_json_reports_location_and_escaping_guidance(self) -> None:
        """源码字符串中的未转义引号必须被拒绝，并提供可用于模型修复的位置。"""
        broken = '{"files":{"src/pages/page-0.tsx":"const title = "欢迎";"}}'
        with self.assertRaisesRegex(ValueError, r"设计响应 JSON 格式错误.*行 1.*列.*转义"):
            parse_design_files(broken, ("src/pages/page-0.tsx",))

    def test_standalone_registry_has_no_generated_business_component(self) -> None:
        """独立会话完全交给官方组件，不能再导入或挂载模型生成的聊天正文。"""
        pages = project_agent_design_pages(*formal_facts())
        registry = _host_files(pages)["src/design/pages.tsx"]
        self.assertNotIn("import Page1", registry)
        self.assertIn('component:null', registry)
        self.assertNotIn("locale: 'zh-CN'", _host_files(pages)['src/design/props.ts'])

    def test_standalone_navigation_has_a_real_page_body(self) -> None:
        """返回首页属于页面导航，不能使官方会话设计失败或被静默丢弃。"""
        requirement, product = formal_facts()
        product["pages"][1]["actions"].append({"actionId": "back_home", "name": "返回首页",
            "behavior": {"type": "navigation", "targetPageId": "home"}})
        pages = project_agent_design_pages(requirement, product)
        registry = _host_files(pages)["src/design/pages.tsx"]
        self.assertIn("import Page1", registry)
        self.assertIn("component:Page1", registry)

    def test_floating_business_bindings_use_host_callbacks(self) -> None:
        """保留业务信息与界面动作；开关实际操作同一个官方浮窗。"""
        requirement, product = formal_facts()
        product["pages"][0]["information_items"] = [{"itemId": "welcome"}]
        product["pages"][0]["actions"] += [
            {"actionId": "open_panel", "behavior": {"type": "interface"}},
            {"actionId": "close_panel", "behavior": {"type": "interface"}},
        ]
        page = project_agent_design_pages(requirement, product)[0]
        code = '''import type { ReactElement } from 'react'
/** 通过平台回调打开关闭会话，业务正文不持有聊天状态。 */
export default function Page({onOpenAgentPanel, onCloseAgentPanel}: {
  onOpenAgentPanel: () => void; onCloseAgentPanel: () => void
}): ReactElement {
  return <main><p data-information-item-id="welcome" data-control-id="welcome">欢迎</p>
    <button data-action-id="open_panel" data-control-id="open" data-ui-effect="open"
      onClick={onOpenAgentPanel}>打开</button>
    <button data-action-id="close_panel" data-control-id="close" data-ui-effect="close"
      onClick={onCloseAgentPanel}>关闭</button></main>
}'''
        repository = Path(__file__).resolve().parents[2]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "logs").mkdir()
            project = SimpleNamespace(root=root, project_dir=repository / "tools/agent-ui-consumer", workspace=root)
            panel_actions = _validate_files(project, {"src/pages/page-0.tsx": code}, [page], Path("node"), "")
            self.assertEqual(panel_actions, {"home": {"open": ["open_panel"], "close": ["close_panel"], "navigate": []}})
            registry = _host_files([page], panel_actions)["src/design/pages.tsx"]
            self.assertIn('"hasInlineLauncher":true', registry)
            with self.assertRaisesRegex(ValueError, "业务信息标记"):
                _validate_files(project, {"src/pages/page-0.tsx": code.replace(
                    'data-information-item-id="welcome"', '')}, [page], Path("node"), "")
            invalid = {**page, "actions": [
                {**action, "behavior": {"type": "business"}} if action["actionId"] == "open_panel" else action
                for action in page["actions"]
            ]}
            with self.assertRaisesRegex(ValueError, "只能绑定.*界面动作"):
                _validate_files(project, {"src/pages/page-0.tsx": code}, [invalid], Path("node"), "")

    def test_ast_boundary_rejects_transport_and_module_escape(self) -> None:
        """使用实际 TS 解析器拒绝别名导入、动态导入和隐藏的浏览器能力。"""
        repository = Path(__file__).resolve().parents[2]
        validator = repository / "Backend/app/services/agent_ui_design_validate.mjs"
        target = repository / "tools/agent-ui-consumer"
        with TemporaryDirectory() as directory:
            candidate = Path(directory) / "files.json"
            for source, expected in (
                ('import {useState} from "react"; export default function Page() {return <h1>首页</h1>}', 0),
                ('import {HttpAgent as Hidden} from "@ag-ui/client"; export default Hidden', 1),
                ('const hidden = import("node:fs"); export default hidden', 1),
                ('const hidden = window; export default hidden', 1),
                ('const hidden = globalThis["fetch"]; export default hidden', 1),
                ('import * as React from "react"; export default React', 1),
                ('export {Agent} from "../agent-ui"', 1),
                ('export default function Page(){return <iframe src="https://example.test"/>}', 1),
            ):
                candidate.write_text(json.dumps({"src/pages/page-0.tsx": source}), encoding="utf-8")
                completed = subprocess.run(
                    ["node", str(validator), str(target), str(candidate)], capture_output=True, text=True,
                    timeout=15, check=False,
                )
                self.assertEqual(completed.returncode, expected, completed.stderr)


class AgentUiDesignGenerationTests(unittest.TestCase):
    """隔离模型和安装开销，验证生成失败、取消、人工编辑与本轮结果登记。"""

    def setUp(self) -> None:
        """准备真实磁盘草稿和源码摘要，模型与外部命令在各用例中控制。"""
        temporary = TemporaryDirectory(prefix="Agent UI 生成 ")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        root = self.workspace / ".devagentstudio/drafts/current/agent-ui-project"
        target = root / "tools/agent-ui-consumer"
        (target / "src").mkdir(parents=True)
        (root / "logs").mkdir()
        (target / "src/App.tsx").write_text(_PLACEHOLDER, encoding="utf-8")
        for name in ("package.json", "pnpm-lock.yaml", ".pnpmfile.cjs", "tsconfig.json",
                     "eslint.config.mjs", "vite.config.ts", "index.html"):
            (target / name).write_text("{}", encoding="utf-8")
        (target / "dist").mkdir()
        (target / "dist/index.html").write_text("placeholder", encoding="utf-8")
        self.project = AgentUiDesignProject(self.workspace, root, target, "environment",
                                           source_digest(target), tree_digest(target / "dist"))
        self.inspection = AgentUiToolHostInspection(self.workspace, self.workspace,
                                                  Path("/node"), "22.16.0", "0.0.1", "^22.0.0", "hash")
        self.requirement, self.product = formal_facts()
        self.files = {"src/pages/page-0.tsx": "export default function Page() { return <h1>首页</h1> }"}
        self.prepare = self.enterContext(patch("app.services.agent_ui_design_generator.prepare_agent_ui_design_project",
                                               return_value=self.project))
        self.enterContext(patch("app.services.agent_ui_design_generator._validate_files", return_value={}))
        self.creator = self.enterContext(patch("app.services.agent_ui_design_creator.run_design_creator"))
        self.enterContext(patch("app.services.agent_ui_design_generator.Settings.from_env",
                                return_value=SimpleNamespace(ui_design_max_retries=1)))

    def generate(self, model: object, **options: object):
        """通过同一公开生成入口调用测试模型，保留实际文件写入与摘要验证。"""
        with patch("app.services.ui_design_generator._create_ui_design_model", return_value=model):
            return generate_agent_ui_design(self.inspection, draft_root=self.project.root.parent,
                pnpm_cli=Path("/pnpm.cjs"), requirement_spec=self.requirement, product_plan=self.product, **options)

    def test_missing_confirmation_does_not_prepare_or_call_model(self) -> None:
        """未确认输入不会安装依赖、调用设计模型或写草稿生成记录。"""
        self.requirement["confirmation_status"] = "pending_user_confirmation"
        with self.assertRaises(ValueError):
            self.generate(None)
        self.prepare.assert_not_called()
        self.assertFalse((self.project.root / "generation.json").exists())

    def test_standalone_chat_information_never_requires_a_second_ui(self) -> None:
        """正式计划中的消息列表、输入框、发送按钮均由官方独立页实现。"""
        self.product["pages"] = [self.product["pages"][1]]
        self.product["agents"][0]["pageActionBindings"] = [self.product["agents"][0]["pageActionBindings"][1]]
        self.product["pages"][0]["information_items"] = [
            {"itemId": "messages", "label": "消息列表"},
            {"itemId": "input", "label": "文本输入框"},
            {"itemId": "send", "label": "发送按钮"},
        ]

        def build(project: AgentUiDesignProject, **_options: object) -> AgentUiDesignProject:
            """基于实际源码模拟成功构建，检查生成器未创建额外业务文件。"""
            return replace(project, source_digest=source_digest(project.project_dir))

        with patch("app.services.ui_design_generator._create_ui_design_model") as model_factory, \
                patch("app.services.agent_ui_design_generator.build_agent_ui_design_project", side_effect=build):
            design = generate_agent_ui_design(self.inspection, draft_root=self.project.root.parent,
                pnpm_cli=Path("/pnpm.cjs"), requirement_spec=self.requirement, product_plan=self.product)
        model_factory.assert_not_called()
        self.creator.assert_not_called()
        self.assertFalse((self.project.project_dir / "src/pages").exists())
        self.assertEqual(design.pages[0]["pageId"], "chat")
        self.assertIsNone(design.pages[0]["path"])

    def test_cancel_during_model_discards_result_and_blocks_old_preview(self) -> None:
        """首个模型块到达前取消后，不落页面文件，也不能预览旧占位构建。"""
        cancelled = False

        def stream(_prompt: str):
            """在模型返回时模拟用户取消。"""
            nonlocal cancelled
            cancelled = True
            yield SimpleNamespace(content=json.dumps({"files": self.files}))

        with self.assertRaises(UiDesignStreamCancelled):
            self.generate(SimpleNamespace(stream=stream), should_cancel=lambda: cancelled)
        self.assertFalse((self.project.project_dir / "src/pages").exists())
        self.assertEqual(json.loads((self.project.root / "generation.json").read_text())["status"], "cancelled")
        with self.assertRaises(ValueError):
            validate_design_project(self.project)

    def test_initial_generation_does_not_start_autonomous_creator(self) -> None:
        """首轮业务生成使用既有快速链路，不额外启动长时间的 Creator 任务。"""
        model = SimpleNamespace(stream=lambda _: [SimpleNamespace(content=json.dumps({"files": self.files}))])
        with patch("app.services.agent_ui_design_generator.build_agent_ui_design_project",
                   side_effect=lambda project, **_options: replace(project, source_digest=source_digest(project.project_dir))):
            self.generate(model)
        self.creator.assert_not_called()
        record = json.loads((self.project.root / "generation.json").read_text())
        self.assertEqual(record["status"], "built")

    def test_standalone_regeneration_preserves_home_without_calling_model(self) -> None:
        """已有首页源码时，刷新独立页只装配官方组件，不重新绘制首页。"""
        retained = dict(self.files)

        def build(project: AgentUiDesignProject, **_options: object) -> AgentUiDesignProject:
            """模拟构建边界，使用真实文件摘要检查保留源码未被改写。"""
            return replace(project, source_digest=source_digest(project.project_dir))

        with patch("app.services.ui_design_generator._create_ui_design_model") as model_factory, \
                patch("app.services.agent_ui_design_generator.build_agent_ui_design_project", side_effect=build):
            design = generate_agent_ui_design(self.inspection, draft_root=self.project.root.parent,
                pnpm_cli=Path("/pnpm.cjs"), requirement_spec=self.requirement, product_plan=self.product,
                retained_sources=retained)
        model_factory.assert_not_called()
        self.assertEqual((design.project.project_dir / "src/pages/page-0.tsx").read_text(), retained["src/pages/page-0.tsx"])
        self.assertEqual([page["pageId"] for page in design.pages], ["home", "chat"])

    def test_model_only_receives_target_business_page(self) -> None:
        """多个浮窗页面共用工程时，模型只接收目标页，其余页面源码原样保留。"""
        second = {"pageId": "second", "name": "第二首页", "information_items": [],
                  "actions": [{"actionId": "open_second", "name": "打开助手"}]}
        self.product["pages"].append(second)
        self.product["agents"][0]["pageActionBindings"].append({
            "pageId": "second", "actionIds": ["open_second"], "surface": {
                "type": "floating_panel", "enabled": True, "contextItemIds": []}})
        prompts: list[str] = []

        def stream(prompt: str):
            """只返回目标页面文件，多余路径必须被生成器拒绝。"""
            prompts.append(prompt)
            yield SimpleNamespace(content=json.dumps({"files": self.files}))

        def build(project: AgentUiDesignProject, **_options: object) -> AgentUiDesignProject:
            """外部构建由测试隔离，真实源码仍进入摘要和生成记录。"""
            return replace(project, source_digest=source_digest(project.project_dir))

        retained = {"src/pages/page-2.tsx": "export default function Second() { return <h1>保留的内容</h1> }"}
        with patch("app.services.agent_ui_design_generator.build_agent_ui_design_project", side_effect=build):
            design = self.generate(SimpleNamespace(stream=stream), retained_sources=retained)
        self.assertEqual(len(prompts), 1)
        self.assertNotIn('"pageId":"second"', prompts[0])
        self.assertNotIn("src/pages/page-2.tsx", prompts[0])
        self.assertEqual((design.project.project_dir / "src/pages/page-2.tsx").read_text(), retained["src/pages/page-2.tsx"])

    def test_regeneration_preserves_non_target_creator_application(self) -> None:
        """刷新业务页时保留其他页面的 Creator JSON 和应用层，不交通用模型重画。"""
        retained = dict(self.files)
        previous = {"home": {"props": {"welcome": {"title": "保留首页定制"}}, "styles": []}}
        agent_sources = {'src/agent-ui/application/project-config.tsx':
            'export const agentUIProject = {}', 'src/agent-ui/app-ui.json': '{"root":{}}',
            'src/agent-ui/empty.css': ''}

        def build(project: AgentUiDesignProject, **_options: object) -> AgentUiDesignProject:
            """保留真实写入及源码摘要，构建命令另由隔离工程验证。"""
            return replace(project, source_digest=source_digest(project.project_dir))

        with patch("app.services.agent_ui_design_generator.build_agent_ui_design_project", side_effect=build):
            design = self.generate(None, retained_sources=retained,
                presentations=previous, retained_agent_sources=agent_sources)
        self.assertEqual((design.project.project_dir / "src/pages/page-0.tsx").read_text(), retained["src/pages/page-0.tsx"])
        stored = json.loads((design.project.project_dir / "src/design/presentation.json").read_text())
        self.assertEqual(stored, previous)
        for path, source in agent_sources.items():
            self.assertEqual((design.project.project_dir / path).read_text(), source)
        self.assertFalse((design.project.project_dir / 'src/design/agent-pages').exists())
        self.assertEqual(json.loads((design.project.root / "generation.json").read_text())["confirmation_status"],
                         "pending_user_confirmation")

    def test_human_edit_during_generation_is_preserved(self) -> None:
        """模型运行期间人工改动会使本轮失效，不写入或复用旧设计。"""
        source = self.project.project_dir / "src/App.tsx"

        def stream(_prompt: str):
            """在模型返回前模拟人工修改。"""
            source.write_text("user customization", encoding="utf-8")
            yield SimpleNamespace(content=json.dumps({"files": self.files}))

        with self.assertRaisesRegex(RuntimeError, "保留用户修改"):
            self.generate(SimpleNamespace(stream=stream))
        self.assertEqual(source.read_text(), "user customization")
        self.assertFalse((self.project.project_dir / "src/pages").exists())

    def test_failed_build_never_registers_success(self) -> None:
        """完整工程编译失败时保留诊断和候选，不能登记源码或预览成功。"""
        model = SimpleNamespace(stream=lambda _: [SimpleNamespace(content=json.dumps({"files": self.files}))])
        with patch("app.services.agent_ui_design_generator.build_agent_ui_design_project", side_effect=ValueError("typecheck failed")):
            with self.assertRaisesRegex(ValueError, "typecheck failed"):
                self.generate(model)
        record = json.loads((self.project.root / "generation.json").read_text())
        self.assertEqual(record["status"], "generation_failed")
        self.assertNotIn("revision", record)
        with self.assertRaises(ValueError):
            validate_design_project(self.project)

    def test_repair_writes_current_files_and_waits_for_formal_confirmation(self) -> None:
        """第一次响应不合法时在现有预算内修复，结果仍不是正式 UiDesign 确认。"""
        responses = iter(["not json", json.dumps({"files": dict(reversed(list(self.files.items())))})])
        model = SimpleNamespace(stream=lambda _: [SimpleNamespace(content=next(responses))])

        def build(project: AgentUiDesignProject, **_options: object) -> AgentUiDesignProject:
            """模拟外部编译成功；源码记录与登记读取实际磁盘。"""
            (project.project_dir / "dist/index.html").write_text("current build", encoding="utf-8")
            return replace(project, source_digest=source_digest(project.project_dir),
                           build_digest=tree_digest(project.project_dir / "dist"))

        with patch("app.services.agent_ui_design_generator.build_agent_ui_design_project", side_effect=build):
            design = self.generate(model)
        validate_design_project(design.project)
        record = json.loads((self.project.root / "generation.json").read_text())
        self.assertEqual(record["confirmation_status"], "pending_user_confirmation")
        self.assertEqual([page["pageId"] for page in record["pages"]], ["home", "chat"])
        for path, code in self.files.items():
            self.assertEqual((self.project.project_dir / path).read_text(), code)
        self.assertEqual(len(design.revision), 64)

    def test_invalid_json_retry_keeps_diagnostics_and_original_confirmation_gate(self) -> None:
        """损坏 JSON 的诊断在成功重试后仍保留，后续只写本轮合格源码。"""
        broken = '{"files":{"src/pages/page-0.tsx":"const title = "欢迎";"}}'
        prompts: list[str] = []
        responses = iter([broken, json.dumps({"files": self.files})])

        def stream(prompt: str):
            """记录真实修复提示并返回坏候选及其完整修正版。"""
            prompts.append(prompt)
            yield SimpleNamespace(content=next(responses))

        def build(project: AgentUiDesignProject, **_options: object) -> AgentUiDesignProject:
            """仅隔离外部构建，保留当前源码摘要的真实校验。"""
            return replace(project, source_digest=source_digest(project.project_dir))

        with patch("app.services.agent_ui_design_generator.build_agent_ui_design_project", side_effect=build) as compiler:
            design = self.generate(SimpleNamespace(stream=stream))
        self.assertEqual(len(prompts), 2)
        self.assertIn("设计响应 JSON 格式错误", prompts[1])
        self.assertIn("转义", prompts[1])
        self.assertIn(broken, prompts[1])
        self.assertEqual(compiler.call_count, 1)
        diagnostic = json.loads((self.project.root / "logs/generation-attempt-1.json").read_text())
        self.assertEqual(diagnostic["attempt"], 1)
        self.assertIn("设计响应 JSON 格式错误", diagnostic["error"])
        self.assertEqual(len(diagnostic["responseSha256"]), 64)
        self.assertNotIn("欢迎", json.dumps(diagnostic, ensure_ascii=False))
        record = json.loads((design.project.root / "generation.json").read_text())
        self.assertEqual(record["status"], "built")
        self.assertEqual(record["confirmation_status"], "pending_user_confirmation")

    def test_invalid_json_exhaustion_keeps_each_failure_without_writing_source(self) -> None:
        """修复预算耗尽时保存每次错误，不写页面源码或登记成功。"""
        model = SimpleNamespace(stream=lambda _: [SimpleNamespace(content="not json")])
        with patch("app.services.agent_ui_design_generator.build_agent_ui_design_project") as compiler:
            with self.assertRaisesRegex(ValueError, "设计响应 JSON 格式错误"):
                self.generate(model)
        compiler.assert_not_called()
        self.assertFalse((self.project.project_dir / "src/pages").exists())
        diagnostics = list((self.project.root / "logs").glob("generation-attempt-*.json"))
        self.assertEqual(len(diagnostics), 2)
        record = json.loads((self.project.root / "generation.json").read_text())
        self.assertEqual(record["status"], "generation_failed")
        self.assertNotIn("revision", record)


if __name__ == "__main__":
    unittest.main()
