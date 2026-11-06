"""设计稿修改必须真实委托 Creator，并以目标文件差异作为完成依据。"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.config import Settings
from app.services.agent_ui_design_creator import run_design_creator
from app.services.agent_ui_tool_host import inspect_agent_ui_tool_host


@unittest.skipUnless(shutil.which("node"), "本机未提供 Node")
class DesignCreatorBridgeTests(unittest.TestCase):
    """用临时公开 SDK 夹具验证真实跨进程调用，不把模型文字当文件回执。"""

    def setUp(self) -> None:
        """创建独立工具和草稿，SDK 只提供正式公开的 run/dispose 接口。"""
        temporary = TemporaryDirectory(prefix="设计 Creator 中文 ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "draft"
        self.project.mkdir()
        self.package = self.root / "tool/node_modules/@za38.03/agent-ui-creator"
        self.package.mkdir(parents=True)
        (self.package / "package.json").write_text(json.dumps({
            "name": "@za38.03/agent-ui-creator", "version": "1.2.3", "type": "module",
            "engines": {"node": "^22.0.0 || ^24.0.0"},
            "exports": {".": "./index.mjs", "./package.json": "./package.json"},
        }))
        (self.package / "index.mjs").write_text('''
import {writeFile, readFile} from 'node:fs/promises';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';
export function createCreatorClient(options) {
  return {
    async run(request) {
      await writeFile(new URL('./observed.json', import.meta.url), JSON.stringify({request, options}));
      const behavior = JSON.parse(await readFile(new URL('./behavior.json', import.meta.url), 'utf8'));
      if (behavior.fail) throw new Error(behavior.errorMessage ?? 'fixture-secret');
      if (behavior.wait) await new Promise(resolve => setTimeout(resolve, 10000));
      if (behavior.sourceFile) {
        await mkdir(path.join(options.projectRoot,'src/agent-ui'),{recursive:true});
        await writeFile(path.join(options.projectRoot,'src/agent-ui/Component.tsx'),'export const View=()=> <div>显示</div>');
      }
      return {message:'已完成 fixture-secret',result:{completion:behavior.completion ?? 'success',executionPolicy:behavior.policy ?? 'domain-write',receipt:{files:behavior.noFiles ? [] : [{path:behavior.sourceFile ? 'src/agent-ui/Component.tsx' : 'src/agent-ui/app-ui.json'}],verification:{status:'changed-and-statically-verified'}}}};
    },
    async dispose() { await writeFile(new URL('./disposed.json', import.meta.url), '{}'); }
  };
}
''')
        self.behavior()
        self.inspection = inspect_agent_ui_tool_host(workspace=self.project,
            installation_root=self.root / "tool", node_executable=Path(shutil.which("node")).resolve(),
            creator_version="1.2.3")
        self.enterContext(patch("app.services.agent_ui_design_creator.Settings.from_env", return_value=Settings(
            model_base_url="https://fixture.example/v1", model_api_key="fixture-secret", model_name="fixture-model")))

    def behavior(self, **values: object) -> None:
        """设置夹具成功、失败或等待行为。"""
        (self.package / "behavior.json").write_text(json.dumps(values))

    def test_arbitrary_request_reaches_creator_once_with_bound_project(self) -> None:
        """不同 UI 需求均交公开 Creator，不在 X 内硬编码搜索栏或猜 CSS。"""
        instruction = "侧栏移到右边，输入框加高，并调整建议卡片"
        receipt = run_design_creator(self.inspection, self.project, instruction,
            run_id="test-design", should_cancel=lambda: False)
        observed = json.loads((self.package / "observed.json").read_text())
        self.assertEqual(observed["request"], instruction)
        self.assertEqual(observed["options"]["projectRoot"], str(self.project))
        self.assertFalse(observed["options"]["allowExternalEndpoint"])
        self.assertEqual(observed["options"]["environment"]["CREATOR_MODEL_NAME"], "fixture-model")
        self.assertNotIn("fixture-secret", repr(receipt))
        self.assertTrue((self.package / "disposed.json").exists())

    def test_sdk_failure_is_not_success_or_a_generic_model_fallback(self) -> None:
        """SDK 报错保持明确失败，异常和模型文字不能泄漏凭据。"""
        self.behavior(fail=True)
        with self.assertRaisesRegex(ValueError, "Creator") as caught:
            run_design_creator(self.inspection, self.project, "修改欢迎语", run_id="failure")
        self.assertNotIn("fixture-secret", str(caught.exception))
        self.assertTrue((self.package / "disposed.json").exists())

    def test_sdk_stage_is_reported_without_raw_model_content(self) -> None:
        """公开 SDK 执行阶段进入进度，模型回复和凭据不能进入状态文案。"""
        messages: list[str] = []
        run_design_creator(self.inspection, self.project, '调整布局', run_id='progress',
                           on_progress=messages.append)
        self.assertEqual(messages, ['Creator 正在执行设计修改'])
        self.assertNotIn('fixture-secret', repr(messages))

    def test_cancel_disposes_the_actual_creator(self) -> None:
        """取消在任务已提交后仍调用公开 dispose，并等待包装进程结束。"""
        from app.services.ui_design_generator import UiDesignStreamCancelled

        self.behavior(wait=True)
        with self.assertRaises(UiDesignStreamCancelled):
            run_design_creator(self.inspection, self.project, "修改布局", run_id="cancel",
                should_cancel=lambda: (self.package / "observed.json").exists())
        self.assertTrue((self.package / "disposed.json").exists())

    def test_cancelled_request_is_never_submitted_to_creator(self) -> None:
        """草稿准备期间已收到取消时，不能迟到启动 Creator 或提交模型任务。"""
        from app.services.ui_design_generator import UiDesignStreamCancelled

        with self.assertRaises(UiDesignStreamCancelled):
            run_design_creator(self.inspection, self.project, '修改布局', run_id='pre-cancelled',
                should_cancel=lambda: True)
        self.assertFalse((self.package / 'observed.json').exists())

    def test_pending_question_cannot_publish_partial_files_as_success(self) -> None:
        """Creator 有部分修改但仍需用户输入时，不能假装当前草稿已完成。"""
        self.behavior(completion='needs_user_input')
        with self.assertRaisesRegex(ValueError, '补充信息'):
            run_design_creator(self.inspection, self.project, '修改插件展示', run_id='question')

    def test_model_timeout_rate_limit_and_auth_errors_have_distinct_safe_messages(self) -> None:
        """模型服务失败显示可处理的类型，不向界面回显 SDK 原始异常。"""
        for message, expected in [('request timeout fixture-secret', '模型请求超时'),
                                  ('429 rate limit fixture-secret', '模型服务正在限流'),
                                  ('401 unauthorized fixture-secret', '模型服务鉴权失败'),
                                  ('Connection error fixture-secret', '模型服务连接失败')]:
            with self.subTest(message=message):
                self.behavior(fail=True, errorMessage=message)
                with self.assertRaisesRegex(ValueError, expected) as caught:
                    run_design_creator(self.inspection, self.project, '调整界面', run_id='model-error')
                self.assertNotIn('fixture-secret', str(caught.exception))

    def test_successful_model_reply_without_receipt_is_not_a_file_change(self) -> None:
        """不允许平台自动生成的隔离 CSS 掩盖 Creator 实际没有改文件。"""
        self.behavior(noFiles=True)
        with self.assertRaisesRegex(ValueError, '实际设计文件变更回执'):
            run_design_creator(self.inspection, self.project, '修改布局', run_id='no-change')

    def test_read_only_route_is_not_reported_as_a_successful_design_change(self) -> None:
        """正常终态仍须区分检查和修改，平台不能替 Creator 提升权限。"""
        self.behavior(policy='read-only')
        with self.assertRaisesRegex(ValueError, '识别为检查'):
            run_design_creator(self.inspection, self.project, '检查当前布局', run_id='inspect')

    def test_application_source_accepts_public_props_but_rejects_new_transport(self) -> None:
        """真实 TypeScript AST 检查组件展示修改，不能被别名导入或注释误导。"""
        repository = Path(__file__).resolve().parents[2]
        validator = repository / 'Backend/app/services/agent_ui_design_creator_source.mjs'
        consumer = repository / 'tools/agent-ui-consumer/package.json'
        script = '''
import {createRequire} from 'node:module';
const {validateCreatorSource}=await import(process.argv[1]);
const ts=createRequire(process.argv[2])('typescript');
validateCreatorSource(ts,'application.tsx',process.argv[3]);
'''
        for source, valid in (
            ('import {ConversationThreadListPlugin as UI} from "@za38.03/agent-ui-plugins/conversation-thread-list"; export const View=()=> <UI showSearch={false}/>;', True),
            ('// fetch is only a comment\nexport const label="fetch";', True),
            ('import {HttpAgent as Hidden} from "@ag-ui/client"; export const agent=new Hidden({});', False),
            ('export const read=()=>fetch("/private");', False),
            ('export const hidden=import("node:fs");', False),
            ('export const View=()=> <ConversationRuntimeProvider />;', False),
            ('export const View=()=> <style>{"body{display:none}"}</style>;', False),
        ):
            with self.subTest(source=source):
                result = subprocess.run(['node', '--input-type=module', '-e', script,
                    validator.as_uri(), str(consumer), source], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode == 0, valid, result.stderr)

    def test_source_validation_uses_the_target_compiler_not_the_creator_compiler(self) -> None:
        """工具包的 TypeScript 入口没有 AST API 时，目标工程校验仍使用自己的真实编译器。"""
        repository = Path(__file__).resolve().parents[2]
        resolved = subprocess.run(['node', '-e',
            'console.log(require("node:module").createRequire(process.argv[1]).resolve("typescript"))',
            str(repository / 'tools/agent-ui-consumer/package.json')], capture_output=True, text=True, check=True)
        for root, content in (
            (self.project, 'module.exports=require(' + json.dumps(resolved.stdout.strip()) + ')'),
            (self.package, 'module.exports={}'),
        ):
            module = root / 'node_modules/typescript'
            module.mkdir(parents=True)
            (module / 'index.js').write_text(content)
        module = self.package / 'node_modules/postcss'
        module.mkdir(parents=True)
        (module / 'index.js').write_text('module.exports={parse(){throw new Error("unexpected CSS")}}')
        metadata = json.loads((self.package / 'package.json').read_text())
        metadata['exports']['./project/dev'] = './dev.mjs'
        (self.package / 'package.json').write_text(json.dumps(metadata))
        (self.package / 'dev.mjs').write_text('export async function verifyUIProject(){return {status:"passed"}}')
        self.inspection = inspect_agent_ui_tool_host(workspace=self.project,
            installation_root=self.root / 'tool', node_executable=Path(shutil.which('node')).resolve(),
            creator_version='1.2.3')
        self.behavior(sourceFile=True)
        receipt = run_design_creator(self.inspection, self.project, '修改控件展示', run_id='target-typescript',
            source_root='src/agent-ui')
        self.assertEqual(receipt['type'], 'completed')
        postcss = subprocess.run(['node', '-e',
            'const r=require("node:module").createRequire(process.argv[1]); console.log(r.resolve("postcss"))',
            str((repository / 'tools/agent-ui/node_modules/@za38.03/agent-ui-creator/package.json').resolve())],
            capture_output=True, text=True, check=True)
        (module / 'index.js').write_text('module.exports=require(' + json.dumps(postcss.stdout.strip()) + ')')
        style = self.project / 'src/agent-ui/application/shared.css'
        style.parent.mkdir(parents=True, exist_ok=True)
        style.write_text('.card {color:inherit}')
        run_design_creator(self.inspection, self.project, '修改共享样式', run_id='shared-styles',
                           source_root='src/agent-ui')
        self.assertIn('.agent-design .card', style.read_text())
        self.assertNotIn('data-design-page', style.read_text())
        scoped = style.read_text()
        run_design_creator(self.inspection, self.project, '继续修改共享样式', run_id='shared-styles-again',
                           source_root='src/agent-ui')
        self.assertEqual(scoped, style.read_text())
        (self.project / 'node_modules/typescript/index.js').write_text('module.exports={}')
        with self.assertRaisesRegex(ValueError, '校验工具不可用'):
            run_design_creator(self.inspection, self.project, '修改控件展示', run_id='target-compiler-unavailable',
                source_root='src/agent-ui')
