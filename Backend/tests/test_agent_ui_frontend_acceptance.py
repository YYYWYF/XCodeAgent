"""共享页面静态验收读取当前磁盘来源，并拒绝重复会话及伪造入口。"""

from copy import deepcopy
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.services.business_acceptance_verifiers.frontend_agent_ui import verify_agent_ui_runtime_source
from app.services.workspace_process_registry import workspace_process_registry
from tests.test_agent_ui_build_contract import build_evidence


class SharedPageAcceptanceTests(TestCase):
    """使用真实 TypeScript AST，不把注释或字符串当成 JSX 和公开调用。"""

    def setUp(self) -> None:
        """准备独立页面源码及独立于待测源码的当前合同。"""
        self.temp = TemporaryDirectory(prefix='T25 页面 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.page = self.root / 'frontend/src/pages/Chat.tsx'
        self.page.parent.mkdir(parents=True)
        self.contract = {'version': 'agent-ui-shared-build.v1', 'mode': 'shared_host',
            'pageId': 'chat', 'agentId': 'assistant', 'surface': 'standalone_page',
            'host': build_evidence('chat')['host'], 'evidence': build_evidence('chat'),
            'connection': 'blocked_T27', 'verificationScope': 'static_source', 'sha256': 'a' * 64}
        self.current = patch('app.services.agent_ui_build_contract.current_agent_ui_build_contract',
                             side_effect=lambda *_args: deepcopy(self.contract))
        self.current.start()
        self.addCleanup(self.current.stop)

    def verify(self, source: str, *, expected: dict | None = None) -> dict:
        """写入本次实现，执行真实静态分析；不修改任何用户工程。"""
        self.page.write_text(source)
        return verify_agent_ui_runtime_source({'frontend/src/pages/Chat.tsx': source},
            {'agent_ui': expected or self.contract}, workspace_root=self.root)

    def page_source(self) -> str:
        """合法页面可使用别名和业务布局，但只挂载共享占位。"""
        return ("import {SharedAgentPage as Slot} from '@/正式助手/design/SharedApplication'\n"
                "export default function Page(){return <main><Slot pageId='chat'/></main>}")

    def test_alias_and_business_layout_pass_only_static_scope(self) -> None:
        """共享占位可保留业务布局，静态通过不批准真实连接或业务结果。"""
        result = self.verify(self.page_source())
        self.assertEqual(result['status'], 'passed', result)
        self.assertEqual(result['facts']['verification_scope'], 'static_source')
        self.assertEqual(result['facts']['real_connection'], 'blocked_T27')
        self.assertEqual(result['facts']['business_acceptance'], 'not_run')

    def test_large_source_survives_slow_analyzer_startup(self) -> None:
        """子进程晚于取消轮询启动时仍收到完整大输入，不挂在未关闭的管道上。"""
        run = workspace_process_registry.run

        def delayed(argv: list[str], **kwargs: object) -> object:
            """延迟真实 Node 读取，覆盖输入超过管道缓冲区的生产情形。"""
            kwargs['timeout'] = 2
            code = 'import os,sys,time; time.sleep(0.4); os.execv(sys.argv[1],sys.argv[1:])'
            return run([sys.executable, '-c', code, *argv], **kwargs)

        source = self.page_source() + "\nexport const padding='" + 'x' * 150_000 + "'\n"
        with patch.object(workspace_process_registry, 'run', side_effect=delayed):
            self.assertEqual(self.verify(source)['status'], 'passed')

    def test_analyzer_timeout_is_blocked_without_claiming_success(self) -> None:
        """分析器真正超时时给出阻断结果，不能抛出未处理异常或计为通过。"""
        with patch.object(workspace_process_registry, 'run', side_effect=subprocess.TimeoutExpired(['node'], 30)):
            result = self.verify(self.page_source())
            self.assertEqual(result['status'], 'blocked')
            self.assertIn('超时', str(result))

    def test_string_comment_type_only_or_wrong_page_never_prove_mount(self) -> None:
        """同名文本、类型导入和错误 pageId 都不构成共享页面入口。"""
        for source in ("// " + self.page_source().replace('\n', '\n// '),
                "export const text = \"<SharedAgentPage pageId='chat'/>\"",
                self.page_source().replace('import {', 'import type {'),
                self.page_source().replace("pageId='chat'", "pageId='foreign'"),
                self.page_source().replace("pageId='chat'", "{...props}")):
            with self.subTest(source=source):
                self.assertEqual(self.verify(source)['status'], 'failed')

    def test_forged_expected_source_is_blocked(self) -> None:
        """验收重新读取当前合同，不能让任务凭自己的哈希自证来源。"""
        expected = deepcopy(self.contract)
        expected['evidence']['installation']['lockSha256'] = '9' * 64
        self.assertEqual(self.verify(self.page_source(), expected=expected)['status'], 'blocked')

    def test_duplicate_runtime_legacy_or_transport_in_other_file_is_rejected(self) -> None:
        """其他页面/组件也不能偷偷挂第二个公开 Runtime 或旧 Adapter。"""
        other = self.page.with_name('Other.tsx')
        for source in (
            "import {Agent as Copy} from '@za38.03/agent-ui-runtime/ui'; export const x=<Copy/>",
            "import {AgentUIProvider as Copy} from '@za38.03/agent-ui-runtime'; export const x=<Copy/>",
            "import {AgentConversationPage} from '@/components/AgentConversation'",
            "export const create=()=>import('@za38.03/agent-ui-runtime/ui')",
            "export function AgentChatCore(){return null}",
            "import {SharedAgentHostProvider as Copy} from '@/extensions/AgentUiHostProvider'; export const x=<Copy/>",
        ):
            other.write_text(source)
            with self.subTest(source=source):
                self.assertEqual(self.verify(self.page_source())['status'], 'failed')

    def test_reachable_wrapper_cannot_hide_transport_or_second_provider(self) -> None:
        """页面依赖图中的辅助文件同样受会话边界限制。"""
        helper = self.page.with_name('helper.ts')
        helper.write_text("export const send = () => fetch('/agents/assistant/run')")
        source = "import {send} from './helper'\n" + self.page_source()
        self.assertEqual(self.verify(source)['status'], 'failed')

    def test_floating_page_uses_controls_and_preserves_business_body(self) -> None:
        """浮窗只操作共享控制器，单独导入或空页面不能通过。"""
        self.contract['surface'] = 'floating_panel'
        source = ("import {useSharedAgentPanel as usePanel} from '@/正式助手/design/SharedApplication'\n"
                  "export default function Page(){const {openPanel:open}=usePanel();"
                  "return <main><h1>业务</h1><button onClick={open}>打开助手</button></main>}")
        self.assertEqual(self.verify(source)['status'], 'passed')
        self.assertEqual(self.verify(source.replace('onClick={open}', ''))['status'], 'failed')

    def test_missing_workspace_or_changed_read_is_blocked(self) -> None:
        """没有当前磁盘或传入内容已落后时不能出具静态通过。"""
        self.assertEqual(verify_agent_ui_runtime_source({}, {'agent_ui': self.contract})['status'], 'blocked')
        self.page.write_text('export {}')
        result = verify_agent_ui_runtime_source({'frontend/src/pages/Chat.tsx': self.page_source()},
            {'agent_ui': self.contract}, workspace_root=self.root)
        self.assertEqual(result['status'], 'blocked')

    def test_separate_business_agui_call_keeps_its_confirmed_endpoint(self) -> None:
        """普通业务 AG-UI SDK 不被误判为第二个共享会话，但不能改连 /agents。"""
        self.contract['businessAguiPaths'] = ['/api/report/run']
        api = self.root / 'frontend/src/apis/report.ts'
        api.parent.mkdir()
        api.write_text("import {HttpAgent as Client} from '@ag-ui/client'\n"
                       "import {EventType} from '@ag-ui/core'\n"
                       "export const report=new Client({url:'/api/report/run'})")
        source = "import {report} from '../apis/report'\n" + self.page_source()
        self.assertEqual(self.verify(source)['status'], 'passed')
        api.write_text(api.read_text().replace('/api/report/run', '/agents/assistant/run'))
        self.assertEqual(self.verify(source)['status'], 'failed')
