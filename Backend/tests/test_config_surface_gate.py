"""真实 V4 ZIP、WorkingCopy 和 journal 本地回归，不代表真实 Engine 联调。"""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import yaml
from app.services.template_reconcile.executor_v3 import UpdatePackageExecutorV3, UpdateExecutionV3Error
from app.services.workspace_bootstrap.models import TemplatePackageError
from app.services.application_config.schema import normalize_application_configuration
from app.services.template_reconcile.config_surfaces import ENV, MAIN, DEV, ConfigSurfaceError
from app.services.template_reconcile.service import TemplateReconcileService, TemplateStateError
from app.services.workspace_bootstrap.models import UpdatePackageDownloadV4
from tests.test_template_update_v4 import _metadata


LOGIN_FRAGMENTS = [
    (ENV, ['login'], b"""PUBLIC_YHT_CLIENT_ID="default"
PUBLIC_YHT_AUTHORIZE_URI="default"
PUBLIC_YHT_END_URL="default"
PUBLIC_YHT_LOGOUT_SHARE_JS="default"
"""),
    (MAIN, ['login'], b'bee:\n  auth:\n    client-id: ${yht.authentication.client-id}\n'),
    (DEV, ['login'], b"""yht:
  authentication:
    client-id: default
    private-key: default
    public-key: default
    center-public-key: default
    center-uri: default
    authorize-uri: default
    redirect-uri: default
"""),
]
AUTHORIZATION_FRAGMENTS = [
    (MAIN, ['authorization'], b"""yht:
  api:
    user-query-path: /v2/users/query
bee:
  persistence:
    xcode-agent:
      mybatis:
        mapper-locations: "classpath*:mapper/**/*.xml"
"""),
    (DEV, ['authorization'], b'yht:\n  api:\n    base-url: default\n    view-id: default\n'),
]


class _V4WorkspaceFixture(unittest.IsolatedAsyncioTestCase):
    """准备可执行真实 V4 ZIP 的临时工作区。"""

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

    def package(self, caps, installed=None, mutate=None, *, fragments=None):
        """模拟 Engine 创建片段 ZIP，仅供测试，生产平台不能补模板结构。"""
        meta = _metadata()
        meta['expectedStateSequence'] = json.loads(self.state.read_text())['stateSequence']
        meta['nextTemplateState']['stateSequence'] = meta['expectedStateSequence'] + 1
        desired = {cap: {'enabled': True, 'config': {}} for cap in caps | (installed or set())}
        meta['nextTemplateState'].update(requested=desired, effective=desired)
        payloads = {}
        if fragments is None:
            fragments = [
                fragment for fragment in LOGIN_FRAGMENTS + AUTHORIZATION_FRAGMENTS
                if fragment[1][0] in caps
            ]
        for index, (target, owners, content) in enumerate(fragments):
            ref = f'payload/config-{index}'
            payloads[ref] = content
            meta['operations'].append(dict(schemaVersion=1, operationId=f'ensure-{index}', index=index,
                type='ENSURE_CAPABILITY_CONFIG', target=target,
                parameters=dict(format='dotenv' if target == ENV else 'yaml', capabilityIds=owners), payloadRef=ref))
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

    def snapshot(self):
        """读取受管配置及 State 的 before-image。"""
        return {path: path.read_bytes() for path in (self.root / ENV, self.root / MAIN, self.root / DEV, self.state)}

    async def assert_rejected(self, download, error):
        """确认失败未发布工程文件或 State。"""
        before = self.snapshot()
        with self.assertRaises(error):
            await self.run_package(download)
        self.assertEqual(self.snapshot(), before)


class ConfigSurfaceGateTests(_V4WorkspaceFixture):
    """验证新装能力的完整性、原有值保护和原子提交。"""

    async def test_login_then_authorization(self):
        """顺序新增能力保留已有值，重放不联网、不递增序列。"""
        await self.run_package(self.package({'login'}))
        current = (self.root / DEV).read_text().replace('client-id: default', 'client-id: custom')
        (self.root / DEV).write_text(current)
        self.config['authorization'].update(enabled=True, initialAdministratorSubjects=['subject'], api={'baseUrl': '', 'viewId': ''})
        self.application.write_text(json.dumps(self.config))
        await self.run_package(self.package({'authorization'}, installed={'login'}))
        self.assertIn('client-id: custom', (self.root / DEV).read_text())
        main = yaml.safe_load((self.root / MAIN).read_bytes())
        self.assertEqual(main['bee']['auth']['client-id'], '${yht.authentication.client-id}')
        self.assertEqual(main['yht']['api']['user-query-path'], '/v2/users/query')
        self.assertEqual(main['bee']['persistence']['xcode-agent']['mybatis']['mapper-locations'],
                         'classpath*:mapper/**/*.xml')
        for target in (MAIN, DEV):
            self.assertIn('# comment\nother:\n  client-id: user\n', (self.root / target).read_text())
        before = self.state.read_bytes()
        with patch('app.services.template_reconcile.service.TemplateEngineClient', side_effect=AssertionError('unexpected network')):
            self.assertEqual(await self.service.reconcile_capabilities(self.root), 'NO_CHANGE')
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(json.loads(before)['stateSequence'], 2)

    async def test_existing_capability_cannot_claim_new_config(self):
        """只有实际新增的能力可以声明 Ensure 片段。"""
        await self.run_package(self.package({'login'}))
        self.config['authorization'].update(enabled=True, initialAdministratorSubjects=['subject'],
                                            api={'baseUrl': '', 'viewId': ''})
        self.application.write_text(json.dumps(self.config))
        await self.assert_rejected(self.package({'authorization'}, installed={'login'}, fragments=LOGIN_FRAGMENTS),
                                   ConfigSurfaceError)

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

    async def test_invalid_fragments_never_commit(self):
        """拒绝非法 UTF-8、空片段、重复键和危险 YAML，以及非法 dotenv。"""
        for target, content in (
            (MAIN, b'key: [invalid'), (MAIN, b'\xff'), (MAIN, b'# empty\n'),
            (MAIN, b'key: a\nkey: b\n'), (MAIN, b'key: &a value\ncopy: *a\n'),
            (MAIN, b'key: !danger value\n'), (MAIN, b'key: {nested: value}\n'),
            (ENV, b'KEY=one\nKEY=two\n'), (ENV, b'not an assignment\n'),
        ):
            with self.subTest(target=target, content=content):
                await self.assert_rejected(
                    self.package({'login'}, fragments=[(target, ['login'], content)]), ConfigSurfaceError)

    async def test_invalid_original_never_commits(self):
        """整份原 YAML 都必须可安全合并。"""
        for content in (
            b'old: one\nold: two\n', b'old: &a value\n', b'old: *a\n',
            b'old: !danger value\n', b'old: {key: value}\n', b'bee: scalar\n',
        ):
            with self.subTest(content=content):
                (self.root / MAIN).write_bytes(content)
                await self.assert_rejected(self.package({'login'}), UpdateExecutionV3Error)

    async def test_operation_boundary(self):
        """禁止绕过 Ensure 或为本次未安装的能力声明片段。"""
        for target in (ENV, MAIN, DEV, 'frontend/./.env', 'FRONTEND/.ENV'):
            def use_add_file(meta, payloads):
                """将合法 Ensure 换成同目标的 ADD_FILE。"""
                meta['operations'][0].update(type='ADD_FILE', parameters={}, target=target)
            with self.subTest(target=target):
                await self.assert_rejected(self.package({'login'}, mutate=use_add_file), ConfigSurfaceError)
        for owners, error in (
            (['authorization'], ConfigSurfaceError), (['future-addon'], ConfigSurfaceError),
            (['login', 'login'], TemplatePackageError), ([], TemplatePackageError),
        ):
            with self.subTest(owners=owners):
                await self.assert_rejected(self.package({'login'}, fragments=[(ENV, owners, b'KEY=value\n')]), error)

    async def test_package_state_and_reference_checks(self):
        """保持 State 序列与 ZIP 双向引用约束。"""
        def wrong_sequence(meta, payloads):
            """使用与当前 State 不一致的包序列。"""
            meta['expectedStateSequence'] += 1

        def missing_payload(meta, payloads):
            """保留操作中的引用，但删除对应 payload。"""
            payloads.pop(meta['operations'][0]['payloadRef'])

        await self.assert_rejected(self.package({'login'}, mutate=wrong_sequence), TemplateStateError)
        await self.assert_rejected(self.package({'login'}, mutate=missing_payload), TemplatePackageError)
        download = self.package({'login'})
        with zipfile.ZipFile(download.temporary_path, 'a') as archive:
            archive.writestr('payload/../../outside', b'out of bounds')
        await self.assert_rejected(download, TemplatePackageError)

    async def test_install_without_config_fragment(self):
        """能力可以不贡献受管配置。"""
        before = self.snapshot()
        self.assertEqual(await self.run_package(self.package({'login'}, fragments=[])), 'CHANGED')
        self.assertEqual(json.loads(self.state.read_bytes())['stateSequence'], 1)
        before.pop(self.state)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    async def test_multiple_fragments_preserve_bytes_and_user_values(self):
        """允许新类型和含点号的键，保留用户值、注释、CRLF，重复片段幂等。"""
        original = (
            b'# user comment\r\n'
            b'bee:\r\n'
            b'  persistence:\r\n'
            b'    xcode-agent:\r\n'
            b'      mybatis:\r\n'
            b'        mapper-locations: custom # keep\r\n'
        )
        (self.root / MAIN).write_bytes(original)
        (self.root / ENV).write_bytes(b'# user env\r\nUSER_VALUE=keep\r\n')
        arbitrary = b'"a.b": true\na:\n  b: 42\nitems:\n  - name: first\n    nested:\n      enabled: false\n'
        fragments = [
            (MAIN, ['login'], AUTHORIZATION_FRAGMENTS[0][2]),
            (MAIN, ['login'], arbitrary),
            (MAIN, ['login'], arbitrary),
            (ENV, ['login'], b'USER_VALUE=default\nFUTURE_KEY=1\n'),
        ]
        await self.run_package(self.package({'login'}, fragments=fragments))
        actual = (self.root / MAIN).read_bytes()
        self.assertTrue(actual.startswith(original))
        self.assertNotIn(b'\n', actual.replace(b'\r\n', b''))
        parsed = yaml.safe_load(actual)
        self.assertEqual(parsed['bee']['persistence']['xcode-agent']['mybatis']['mapper-locations'], 'custom')
        self.assertIs(parsed['a.b'], True)
        self.assertEqual(parsed['a']['b'], 42)
        self.assertEqual(parsed['items'], [{'name': 'first', 'nested': {'enabled': False}}])
        self.assertEqual((self.root / ENV).read_bytes(), b'# user env\r\nUSER_VALUE=keep\r\nFUTURE_KEY=1\r\n')

    async def test_conflicting_fragments_never_commit(self):
        """拒绝相同新路径的不同值、父子冲突和 dotenv 冲突。"""
        for target, first, second in (
            (MAIN, b'new: [1]\n', b'new: [2]\n'),
            (MAIN, b'new: one\n', b'new:\n  child: two\n'),
            (ENV, b'NEW=one\n', b'NEW=two\n'),
        ):
            with self.subTest(target=target, second=second):
                fragments = [(target, ['login'], item) for item in (first, second)]
                await self.assert_rejected(self.package({'login'}, fragments=fragments), ConfigSurfaceError)

    async def test_post_validation_checks_actual_working_copy(self):
        """实际内存副本漏写、改写、删除或额外写入时拒绝提交。"""
        execute = UpdatePackageExecutorV3.execute
        for old, new in (
            (b'client-id: user', b'client-id: overwritten'),
            (b'# comment\n', b''),
            (b'other:\n  client-id: user\n', b''),
            (b'bee:\n', b'injected: value\nbee:\n'),
            (b'FUTURE_KEY=default\n', b''),
        ):
            def corrupt(executor, package, store, payloads):
                """在执行完成后破坏待提交副本。"""
                execute(executor, package, store, payloads)
                entry = store.entry(ENV if old.startswith(b'FUTURE_KEY') else MAIN)
                entry.working = entry.working.replace(old, new)

            with self.subTest(old=old, new=new), patch.object(UpdatePackageExecutorV3, 'execute', corrupt):
                fragments = LOGIN_FRAGMENTS + [(ENV, ['login'], b'FUTURE_KEY=default\n')]
                await self.assert_rejected(self.package({'login'}, fragments=fragments), ConfigSurfaceError)

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


class ConfigurationRecoveryTests(_V4WorkspaceFixture):
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
        for target in (self.root / DEV, self.state):
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
