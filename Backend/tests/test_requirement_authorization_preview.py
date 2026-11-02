"""需求权限预览必须只读、无模型，并保持完整 AG-UI 生命周期。"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.protocols.application_page_planning import build_application_page_planning_ag_ui_stream
from tests import test_authorization_manifest as fixtures


class RequirementAuthorizationPreviewTests(unittest.IsolatedAsyncioTestCase):
    """通过真实规划入口验证预览和失败都不会进入 Graph 或改变正式产物。"""

    async def _preview(self, config=None, *, product=None, extra=None):
        """在隔离工作区读取唯一应用配置，并捕获标准 AG-UI 帧及文件快照。"""

        helper = fixtures.AuthorizationManifestTests()
        spec, default_product, _, _ = helper._inputs()
        with tempfile.TemporaryDirectory() as workspace:
            metadata = Path(workspace) / '.devagentstudio'
            metadata.mkdir()
            config_file = metadata / 'application.json'
            current_config = deepcopy(config or helper._application_config())
            current_config.update(schemaVersion=6, configRevision=1)
            current_config['authorization'].setdefault('initialAdministratorSubjects', [])
            if current_config['auth']['enable']:
                current_config['auth']['yht'] = {key: 'test-only' for key in ('clientId', 'privateKey', 'publicKey', 'centerPublicKey')}
            if current_config['authorization']['enabled']:
                current_config['authorization']['api'] = {'baseUrl': 'http://localhost', 'viewId': 'test-only'}
            config_file.write_text(json.dumps(current_config))
            before = config_file.read_bytes()
            raw = {"action": "preview", "workspaceRoot": workspace,
                   "requirementSpec": spec, "productPlan": default_product if product is None else product, **(extra or {})}
            with patch('app.agents.model_factory.create_chat_model') as model:
                frames = [frame async for frame in build_application_page_planning_ag_ui_stream(
                    graph=None, payload={"threadId": "read-only", "forwardedProps": {"requirementAuthorization": raw}},
                )]
            model.assert_not_called()
            self.assertEqual(config_file.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in metadata.iterdir()), ['application.json'])
        events = [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith('data:')]
        self.assertEqual(events[0]['type'], 'RUN_STARTED')
        self.assertEqual(events[-1]['type'], 'RUN_FINISHED')
        self.assertIn('TEXT_MESSAGE_END', [event['type'] for event in events])
        self.assertIn('STATE_SNAPSHOT', [event['type'] for event in events])
        return next(event['value'] for event in events if event.get('name') == 'requirement-authorization')

    async def test_preview_contains_resources_without_technical_plan(self):
        """首阶段没有 TechnicalPlan 时也可展示真实资源键和默认角色授权。"""
        result = await self._preview()
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['enabled'])
        self.assertTrue(result['ready'])
        self.assertIn('people_edit_person', [item['resourceKey'] for item in result['manifest']['resources']])
        self.assertEqual(result['manifest']['bindings']['endpoints'], [])

    async def test_configuration_disable_cannot_be_overridden_by_spec(self):
        """需求草稿即使声明权限，预览也只能服从真实工作区能力开关。"""
        result = await self._preview({'auth': {'enable': False}, 'authorization': {'enabled': False}})
        self.assertFalse(result['enabled'])
        self.assertEqual(result['manifest']['resources'], [])

    async def test_incomplete_product_stays_pending_without_inventing_resources(self):
        """产品页面尚未生成时展示待完成，不能将其误报为没有权限。"""
        result = await self._preview(product={})
        self.assertFalse(result['ready'])
        self.assertNotIn('manifest', result)

    async def test_invalid_mapping_and_extra_config_return_handled_failure(self):
        """不匹配目标与客户端试图提交开关都返回标准业务失败，不进入 Graph。"""
        _, product, _, _ = fixtures.AuthorizationManifestTests()._inputs()
        product = deepcopy(product)
        product['authorizationTargets']['operationRules'][0]['actionId'] = 'missing_action'
        self.assertEqual((await self._preview(product=product))['status'], 'failed')
        self.assertEqual((await self._preview(extra={'applicationConfig': {}}))['status'], 'failed')
