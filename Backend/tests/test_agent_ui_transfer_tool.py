"""使用真实成品公开入口验证宿主已准备依赖的单包初始化。"""

import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from app.services.agent_ui_dependency_plan import REPOSITORY, sha256


class PublicBootstrapTests(unittest.TestCase):
    """不启动目标应用单测，也不安装包或改写真实应用。"""

    def test_reviewed_dependencies_match_public_default_preset(self) -> None:
        """当前成品增加或改变直接应用层依赖时，安装合同回归必须失败。"""
        from app.services.agent_ui_source_dependencies import SOURCE_DEPENDENCIES
        script = '''
import * as b from '@za38.03/agent-ui-creator/bootstrap';
import {createAgentUIInitializationHost} from '@za38.03/agent-ui-creator/project/host';
import {loadAgentUISourceRegistry,resolveAgentUISourceItemClosure} from '@za38.03/agent-ui-creator/source-registry';
const host=createAgentUIInitializationHost();
const preset=b.createDefaultAgentUIPresetRegistry(host.parseAppUIModel).getDefaultForMode('platform',b.agentUIModeRegistry);
const registry=await loadAgentUISourceRegistry(), required={};
for(const id of preset.sourceItems??[]) for(const item of resolveAgentUISourceItemClosure(registry,id))
  for(const [name,version] of Object.entries(item.packages??{})) {
    if(name.startsWith('@za38.03/')||['react','react-dom','tailwindcss'].includes(name)) continue;
    if(required[name]&&required[name]!==version) throw new Error('conflicting source dependency');
    required[name]=version;
  }
process.stdout.write(JSON.stringify(required));
'''
        result = subprocess.run(['node', '--input-type=module', '--eval', script],
            cwd=REPOSITORY / 'tools/agent-ui', text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(SOURCE_DEPENDENCIES))

    def test_missing_runtime_packages_cannot_initialize(self) -> None:
        """宿主不再调用依赖规划器后，公开事务仍拒绝未安装的 Runtime 包。"""
        if not shutil.which('node'):
            self.skipTest('需要 Node')
        script = (REPOSITORY / 'Backend/app/services/agent_ui_transfer_tool.mjs').read_text()
        with TemporaryDirectory(prefix='Bootstrap 未安装 ') as directory:
            target = Path(directory)
            (target / 'src').mkdir()
            (target / 'package.json').write_text('{"private":true,"type":"module"}')
            result = subprocess.run(['node', '--input-type=module', '--eval', script],
                cwd=REPOSITORY / 'tools/agent-ui', text=True, capture_output=True,
                input=json.dumps({'action': 'initialize', 'projectRoot': str(target),
                                  'sourceRoot': 'src/agent-ui'}), timeout=120, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)['status'], 'failed')
            self.assertFalse((target / '.agent-ui/project.json').exists())
            self.assertFalse((target / '.agent-ui/source-lock.json').exists())
            self.assertFalse((target / 'src/agent-ui/app-ui.json').exists())

    def test_prepared_single_package_pnpm_policy_is_preserved(self) -> None:
        """已安装成品由公开 Bootstrap 检查并初始化，不重复进入依赖规划器。"""
        consumer = REPOSITORY / 'tools/agent-ui-consumer'
        if not shutil.which('node') or not (consumer / 'node_modules').is_dir():
            self.skipTest('需要本地已安装的成品消费者与 Node')
        script = (REPOSITORY / 'Backend/app/services/agent_ui_transfer_tool.mjs').read_text()
        with TemporaryDirectory(prefix='公开 Bootstrap 中文 ') as directory:
            target = Path(directory)
            (target / 'src').mkdir()
            (target / 'node_modules').symlink_to(consumer / 'node_modules', target_is_directory=True)
            package = (consumer / 'package.json').read_bytes()
            (target / 'package.json').write_bytes(package)
            policy = b"packages:\n  - '.'\nonlyBuiltDependencies: []\n"
            (target / 'pnpm-workspace.yaml').write_bytes(policy)
            result = subprocess.run(['node', '--input-type=module', '--eval', script],
                cwd=REPOSITORY / 'tools/agent-ui', text=True, capture_output=True,
                input=json.dumps({'action': 'initialize', 'projectRoot': str(target),
                                  'sourceRoot': 'src/agent-ui'}), timeout=120, check=False)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(json.loads(result.stdout)['status'], 'passed')
            self.assertEqual((target / 'pnpm-workspace.yaml').read_bytes(), policy)
            self.assertEqual(sha256((target / 'package.json').read_bytes()), sha256(package))
            self.assertTrue((target / '.agent-ui/project.json').is_file())
            self.assertTrue((target / '.agent-ui/source-lock.json').is_file())
            self.assertTrue((target / 'src/agent-ui/app-ui.json').is_file())
