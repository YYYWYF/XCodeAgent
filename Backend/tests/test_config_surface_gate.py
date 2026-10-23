"""真实 V4 ZIP、WorkingCopy 和 journal 本地回归，不代表真实 Engine 联调。"""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import yaml
from app.services.application_config.schema import normalize_application_configuration
from app.services.template_reconcile.config_surfaces import CONTRACT, ENV, MAIN, DEV, ConfigSurfaceError
from app.services.template_reconcile.service import TemplateReconcileService
from app.services.workspace_bootstrap.models import UpdatePackageDownloadV4
from tests.test_template_update_v4 import _metadata


class ConfigSurfaceGateTests(unittest.IsolatedAsyncioTestCase):
    """验证新装能力的完整性、原有值保护和原子提交。"""

    def setUp(self):
        """创建全新 Base 工程与模拟下载设置。"""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / '.devagentstudio/template-state.json'
        self.state.parent.mkdir()
        state = _metadata()['nextTemplateState']
        state['stateSequence'] = 0
        self.state.write_text(json.dumps(state))
        self.application = self.state.with_name('application.json')
        self.config = normalize_application_configuration({
            'schemaVersion': 6, 'configRevision': 1,
            'auth': {'enable': True, 'yht': dict.fromkeys(('clientId', 'privateKey', 'publicKey', 'centerPublicKey'), 'test')},
            'authorization': {'enabled': False, 'initialAdministratorSubjects': []}})
        self.application.write_text(json.dumps(self.config))
        for target in (ENV, MAIN, DEV):
            file = self.root / target
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('USER_VALUE=keep\n' if target == ENV else '# comment\nother:\n  client-id: user\n')
        self.service = TemplateReconcileService(SimpleNamespace(
            template_reconcile_enabled=True, template_engine_base_url='http://unused',
            template_engine_connect_timeout_seconds=1, template_engine_read_timeout_seconds=1,
            template_package_max_bytes=1000000, template_package_max_files=100, template_package_max_extracted_bytes=1000000))

    def tearDown(self):
        """清理测试工作区。"""
        self.temp.cleanup()

    def package(self, caps, installed=None, mutate=None):
        """模拟 Engine 创建片段 ZIP，仅供测试，生产平台不能补模板结构。"""
        meta = _metadata()
        meta['expectedStateSequence'] = json.loads(self.state.read_text())['stateSequence']
        meta['nextTemplateState']['stateSequence'] = meta['expectedStateSequence'] + 1
        desired = {cap: {'enabled': True, 'config': {}} for cap in caps | (installed or set())}
        meta['nextTemplateState'].update(requested=desired, effective=desired)
        payloads = {}
        for target in (ENV, MAIN, DEV):
            selected = [cap for cap in sorted(caps) if target in CONTRACT[cap]]
            paths = set().union(*(CONTRACT[cap][target] for cap in selected))
            if not paths:
                continue
            if target == ENV:
                content = ''.join(f'{path}="default"\n' for path in sorted(paths))
            else:
                tree = {}
                for path in sorted(paths):
                    cursor = tree
                    parts = path.split('.')
                    for part in parts[:-1]:
                        cursor = cursor.setdefault(part, {})
                    cursor[parts[-1]] = ['/*'] if path.endswith('filter-patterns') else 'default'
                content = yaml.safe_dump(tree, sort_keys=False)
            ref = f'payload/{target}'
            payloads[ref] = content.encode()
            meta['operations'].append(dict(schemaVersion=1, operationId=f'ensure-{len(payloads)}', index=len(payloads)-1,
                type='ENSURE_CAPABILITY_CONFIG', target=target,
                parameters=dict(format='dotenv' if target == ENV else 'yaml', capabilityIds=selected), payloadRef=ref))
        if mutate:
            mutate(meta, payloads)
        for index, op in enumerate(meta['operations']):
            op['index'] = index
        refs = {op.get('payloadRef') for op in meta['operations']}
        payloads = {ref: data for ref, data in payloads.items() if ref in refs}
        meta['payloadManifest'] = {ref: {'size': len(data)} for ref, data in payloads.items()}
        archive = self.root / 'package.zip'
        with zipfile.ZipFile(archive, 'w') as handle:
            handle.writestr('extension-update-package.json', json.dumps(meta))
            for ref, data in payloads.items():
                handle.writestr(ref, data)
        return UpdatePackageDownloadV4(archive, archive.stat().st_size, 'application/zip')

    async def run_package(self, download):
        """替换网络下载，执行生产包校验与实际磁盘提交。"""
        with patch('app.services.template_reconcile.service.TemplateEngineClient') as client:
            client.return_value.update_v4 = AsyncMock(return_value=download)
            result = await self.service.reconcile_capabilities(self.root)
            client.return_value.update_v4.assert_awaited_once()
            return result

    async def test_login_then_authorization(self):
        """顺序新增能力保留已有值，重放不联网、不递增序列。"""
        await self.run_package(self.package({'login'}))
        current = (self.root / DEV).read_text().replace('client-id: default', 'client-id: custom')
        (self.root / DEV).write_text(current)
        self.config['authorization'].update(enabled=True, initialAdministratorSubjects=['subject'], api={'baseUrl': '', 'viewId': ''})
        self.application.write_text(json.dumps(self.config))
        await self.run_package(self.package({'authorization'}, installed={'login'}))
        self.assertIn('client-id: custom', (self.root / DEV).read_text())
        for target in (MAIN, DEV):
            self.assertIn('# comment\nother:\n  client-id: user\n', (self.root / target).read_text())
        before = self.state.read_bytes()
        with patch('app.services.template_reconcile.service.TemplateEngineClient', side_effect=AssertionError('unexpected network')):
            self.assertEqual(await self.service.reconcile_capabilities(self.root), 'NO_CHANGE')
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(json.loads(before)['stateSequence'], 2)

    async def test_current_maven_null_fields_survive_state_roundtrip(self):
        """当前 Engine 的 Maven spec 固定键不能被 exclude_none 擦掉。"""
        def contribution(meta, payloads):
            """模拟实际 Engine 返回的完整 Maven 描述符。"""
            meta['nextTemplateState']['managedContributions'] = {
                'maven:ZA21:bee-starter-auth': {
                    'type': 'MAVEN_DEPENDENCY', 'target': 'backend/pom.xml', 'owners': ['login'],
                    'spec': {'groupId': 'ZA21', 'artifactId': 'bee-starter-auth', 'version': None, 'scope': None},
                    'appliedRevision': '2026.10.20.1'}}
        await self.run_package(self.package({'login'}, mutate=contribution))
        spec = json.loads(self.state.read_text())['managedContributions']['maven:ZA21:bee-starter-auth']['spec']
        self.assertEqual(set(spec), {'groupId', 'artifactId', 'version', 'scope'})
        self.assertIsNone(spec['version'])
        self.assertIsNone(spec['scope'])

    async def test_combined_install(self):
        """权限自动依赖 Login 时同批确保三处目标。"""
        self.config['authorization'].update(enabled=True, initialAdministratorSubjects=['subject'], api={'baseUrl': '', 'viewId': ''})
        self.application.write_text(json.dumps(self.config))
        await self.run_package(self.package({'login', 'authorization'}))
        self.assertEqual(json.loads(self.state.read_text())['stateSequence'], 1)

    async def test_invalid_packages_never_commit(self):
        """缺目标、错误路径、重复键和别名全部拒绝。"""
        def missing(meta, payloads):
            """只保留单条 Ensure。"""
            meta['operations'] = meta['operations'][:1]
        def wrong(meta, payloads):
            """放入不属于所声明能力的路径。"""
            payloads[f'payload/{DEV}'] = b'wrong:\n  client-id: x\n'
        def duplicate(meta, payloads):
            """注入深层重复键。"""
            payloads[f'payload/{DEV}'] += b'user:\n  nested:\n    key: a\n    key: b\n'
        def unsafe(meta, payloads):
            """注入 YAML 锚点和别名。"""
            payloads[f'payload/{DEV}'] += b'user: &a custom\nother: *a\n'
        for mutate in (missing, wrong, duplicate, unsafe):
            with self.subTest(case=mutate.__name__):
                before = {target: (self.root / target).read_bytes() for target in (ENV, MAIN, DEV, '.devagentstudio/template-state.json')}
                with self.assertRaises(ConfigSurfaceError):
                    await self.run_package(self.package({'login'}, mutate=mutate))
                for target, data in before.items():
                    self.assertEqual((self.root / target).read_bytes(), data)

    async def test_post_validation_failure_and_write_fault(self):
        """后验失败与中途写盘失败均保持文件和 State 原始字节。"""
        from app.services.template_reconcile import transaction_v4
        before = {target: (self.root / target).read_bytes() for target in (ENV, MAIN, DEV, '.devagentstudio/template-state.json')}
        with patch('app.services.template_reconcile.service.validate_working_surfaces', side_effect=ConfigSurfaceError('CONFIG_STRUCTURE_MISSING', DEV)):
            with self.assertRaises(ConfigSurfaceError):
                await self.run_package(self.package({'login'}))
        original = transaction_v4._atomic_replace
        failed = False
        def fail_once(target, content, *, mode):
            """只在工程文件写盘注入一次异常，允许 journal 恢复。"""
            nonlocal failed
            if target == self.root / MAIN and not failed:
                failed = True
                raise OSError('injected')
            return original(target, content, mode=mode)
        with patch.object(transaction_v4, '_atomic_replace', side_effect=fail_once):
            with self.assertRaises(OSError):
                await self.run_package(self.package({'login'}))
        for target, data in before.items():
            self.assertEqual((self.root / target).read_bytes(), data)
        self.assertFalse((self.root / '.devagentstudio/runtime/template-update-v4-journal').exists())


class ConfigurationRecoveryTests(ConfigSurfaceGateTests):
    """复用真实 V4 包执行现场，验证跨阶段非 ACID 恢复。"""

    async def test_parameter_failure_resumes_without_reinstall(self):
        """V4 完成后参数失败保留安装，原 ID 只重试参数且清除诊断。"""
        from app.services.application_config_orchestrator import ApplicationConfigOrchestrator, ApplicationConfigOperationError
        from app.services.application_parameter_updater import ParameterUpdateError
        self.config.update(appName='Name', appIcon='app', theme={'primaryColor': '#123456'})
        self.config['auth']['enable'] = False
        self.application.write_text(json.dumps(self.config))
        (self.root / ENV).write_text('PUBLIC_APP_NAME="Name"\nPUBLIC_APP_ICON="app"\nPUBLIC_THEME_PRIMARY_COLOR="#123456"\n')
        orchestrator = ApplicationConfigOrchestrator(self.service._settings)
        download = self.package({'login'})
        with patch('app.services.template_reconcile.service.TemplateEngineClient') as client:
            client.return_value.update_v4 = AsyncMock(return_value=download)
            with patch.object(orchestrator._parameters, 'apply', side_effect=ParameterUpdateError('CONFIG_APPLY_FAILED：写入故障。')):
                with self.assertRaises(ApplicationConfigOperationError):
                    await orchestrator.apply(self.root, operation_id='operation-recover', expected_revision=1, changes={'auth.enable': True})
            client.return_value.update_v4.assert_awaited_once()
        record = orchestrator.get_operation(self.root, 'operation-recover')
        self.assertEqual(record['status'], 'FAILED')
        self.assertEqual(record['progressStage'], 'CAPABILITY_APPLIED')
        self.assertEqual(record['failureStage'], 'application_parameter_update')
        self.assertEqual(record['failureCode'], 'CONFIG_APPLY_FAILED')
        with self.assertRaisesRegex(ApplicationConfigOperationError, 'CONFIG_OPERATION_PENDING'):
            await orchestrator.apply(self.root, operation_id='new-proposal-during-failure', expected_revision=2, changes={'appName': 'Other'})
        with patch('app.services.template_reconcile.service.TemplateEngineClient', side_effect=AssertionError('reinstalled')):
            result = await orchestrator.resume(self.root, operation_id='operation-recover')
        self.assertEqual(result['status'], 'APPLIED')
        self.assertEqual(result['progressStage'], 'PARAMETERS_APPLIED')
        self.assertEqual(result['installedCapabilities'], ['login'])
        self.assertNotIn('failureCode', result)
        self.assertEqual(json.loads(self.state.read_text())['stateSequence'], 1)

    async def test_external_deletion_after_install_stops_recovery(self):
        """安装完成后外部删除配置或 State，原操作不得自动补文件或伪报成功。"""
        from app.services.application_config_orchestrator import ApplicationConfigOrchestrator, ApplicationConfigOperationError
        from app.services.application_parameter_updater import ParameterUpdateError
        self.config.update(appName='Name', appIcon='app', theme={'primaryColor': '#123456'})
        self.application.write_text(json.dumps(self.config))
        with (self.root / ENV).open('a') as handle:
            handle.write('PUBLIC_APP_NAME="Name"\nPUBLIC_APP_ICON="app"\nPUBLIC_THEME_PRIMARY_COLOR="#123456"\n')
        await self.run_package(self.package({'login'}))
        service = ApplicationConfigOrchestrator(self.service._settings)
        changes = {'theme.primaryColor': '#654321'}
        with patch.object(service._parameters, 'apply', side_effect=ParameterUpdateError('CONFIG_APPLY_FAILED：synthetic-secret')):
            with self.assertRaises(ApplicationConfigOperationError):
                await service.apply(self.root, operation_id='external-deletion', expected_revision=1, changes=changes)
        record = service.get_operation(self.root, 'external-deletion')
        self.assertNotIn('synthetic-secret', json.dumps(record))
        for target in (self.root / MAIN, self.state):
            before = target.read_bytes()
            target.unlink()
            try:
                with patch('app.services.template_reconcile.service.TemplateEngineClient', side_effect=AssertionError('reinstalled')):
                    with self.assertRaisesRegex(ApplicationConfigOperationError, 'CONFIG_STRUCTURE_MISSING'):
                        await service.apply(self.root, operation_id='external-deletion', expected_revision=1, changes=changes)
                self.assertFalse(target.exists())
                failed = service.get_operation(self.root, 'external-deletion')
                self.assertEqual(failed['status'], 'FAILED')
                self.assertEqual(failed['failureTarget'], target.relative_to(self.root).as_posix())
            finally:
                target.write_bytes(before)
