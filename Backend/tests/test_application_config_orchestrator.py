"""统一配置提交的 CAS、幂等与非敏感持久状态测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from app.protocols.application_config_operation import ApplicationConfigAction, _request_validation_message
from app.services.application_config_orchestrator import ApplicationConfigOperationError, ApplicationConfigOrchestrator, _changes_digest
from app.services.workspace_bootstrap.models import TemplateEngineError


class ApplicationConfigOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    """覆盖首次 Generate 前只保存 Desired 的事务分支。"""

    def setUp(self) -> None:
        """创建不含 TemplateState 的当前配置工作区。"""

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / ".devagentstudio/application.json"
        self.config_path.parent.mkdir()
        self.config_path.write_text(json.dumps({
            "schemaVersion": 6, "configRevision": 1,
            "appName": "原应用", "appIcon": "app", "theme": {"primaryColor": "#123456"},
            "auth": {"enable": False, "yht": {"clientId": "", "privateKey": "", "publicKey": "", "centerPublicKey": ""}},
            "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database"},
        }, ensure_ascii=False), encoding="utf-8")
        self.service = ApplicationConfigOrchestrator(SimpleNamespace())

    def tearDown(self) -> None:
        """释放临时工作区。"""

        self.temporary.cleanup()

    def test_request_validation_message_exposes_only_invalid_field_path(self) -> None:
        """请求校验失败应定位字段，不得在错误正文中回显私钥内容。"""

        with self.assertRaises(ValidationError) as captured:
            ApplicationConfigAction.model_validate({
                "action": "apply",
                "workspaceRoot": str(self.root),
                "expectedConfigRevision": 1,
                "operationId": "operation-request-error",
                "changes": {"auth.yht.privateKey": "sensitive-secret-value"},
                "unexpected": True,
            })

        message = _request_validation_message(captured.exception)
        self.assertIn("unexpected", message)
        self.assertNotIn("sensitive-secret-value", message)

    async def test_preview_apply_and_replay(self) -> None:
        """仅名称变更只修改 Desired，重复 operationId 不再增加修订号。"""

        changes = {"appName": "新应用"}
        projection = self.service.preview(self.root, expected_revision=1, changes=changes)
        self.assertEqual(projection["changedKeys"], ["appName"])
        self.assertFalse(projection["generated"])
        first = await self.service.apply(self.root, operation_id="operation-001", expected_revision=1, changes=changes)
        second = await self.service.apply(self.root, operation_id="operation-001", expected_revision=1, changes=changes)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "APPLIED")
        self.assertEqual(json.loads(self.config_path.read_text())["configRevision"], 2)
        self.assertEqual(first["changedFiles"], [])

    async def test_noop_operation_id_is_still_bound_to_proposal(self) -> None:
        """无变化操作也保留摘要，不能把同一 ID 随后改绑到另一份提案。"""
        first = await self.service.apply(self.root, operation_id='noop-operation', expected_revision=1, changes={'appName': '原应用'})
        self.assertEqual(first['configRevision'], 1)
        with self.assertRaisesRegex(ApplicationConfigOperationError, 'CONFIG_OPERATION_CONFLICT'):
            await self.service.apply(self.root, operation_id='noop-operation', expected_revision=1, changes={'appName': '其他应用'})

    async def test_crash_after_desired_save_still_blocks_another_proposal(self) -> None:
        """Desired 已写盘而阶段尚未记录的崩溃窗口不能被其他提案跨过。"""
        await self.service.apply(self.root, operation_id='crash-window', expected_revision=1, changes={'appName': 'New'})
        record = self.service.get_operation(self.root, 'crash-window')
        record['status'] = 'APPLYING'
        record.pop('progressStage')
        self.service._write_operation(self.root, record)
        with self.assertRaisesRegex(ApplicationConfigOperationError, 'CONFIG_OPERATION_PENDING'):
            await self.service.apply(self.root, operation_id='after-crash-proposal', expected_revision=2, changes={'appName': 'Other'})
        recovered = await self.service.apply(self.root, operation_id='crash-window', expected_revision=1, changes={'appName': 'New'})
        self.assertEqual(recovered['status'], 'APPLIED')

    async def test_stale_revision_and_secret_not_logged(self) -> None:
        """过期版本被拒绝，操作记录不含用户密钥值。"""

        with self.assertRaisesRegex(ApplicationConfigOperationError, "CONFIG_REVISION_CONFLICT"):
            self.service.preview(self.root, expected_revision=2, changes={"appName": "新应用"})
        changes = {"auth.yht.privateKey": "sensitive-secret-value"}
        await self.service.apply(self.root, operation_id="operation-002", expected_revision=1, changes=changes)
        record = (self.root / ".devagentstudio/runtime/application-config/operation-002.json").read_text()
        self.assertNotIn("sensitive-secret-value", record)
        self.assertIn("auth.yht.privateKey", record)

    async def test_successful_retry_clears_previous_error_code(self) -> None:
        """原操作恢复成功后不保留上一轮模板失败码。"""

        operation_path = self.root / ".devagentstudio/runtime/application-config/retry-operation.json"
        operation_path.parent.mkdir(parents=True)
        operation_path.write_text(json.dumps({
            "operationId": "retry-operation",
            "generated": False,
            "status": "FAILED",
            "expectedConfigRevision": 1,
            "targetConfigRevision": 2,
            "changedKeys": ["appName"],
            "changedFiles": [],
            "installedCapabilities": [],
            "requestId": None,
            "changesDigest": _changes_digest({"appName": "恢复后名称"}),
            "errorCode": "TEMPLATE_PACKAGE_CONTRACT_INVALID",
        }), encoding="utf-8")

        result = await self.service.apply(
            self.root, operation_id="retry-operation", expected_revision=1,
            changes={"appName": "恢复后名称"},
        )

        self.assertEqual(result["status"], "APPLIED")
        self.assertNotIn("errorCode", result)
        self.assertNotIn("errorCode", json.loads(operation_path.read_text(encoding="utf-8")))

    async def test_retry_rejects_an_operation_id_with_different_secret_value(self) -> None:
        """重试不得因字段名相同而把新输入误绑定到旧的敏感提案。"""

        original = {"auth.yht.privateKey": "first-private-key"}
        await self.service.apply(
            self.root, operation_id="operation-digest", expected_revision=1, changes=original,
        )

        with self.assertRaisesRegex(ApplicationConfigOperationError, "CONFIG_OPERATION_CONFLICT"):
            await self.service.apply(
                self.root,
                operation_id="operation-digest",
                expected_revision=1,
                changes={"auth.yht.privateKey": "second-private-key"},
            )

    async def test_capability_install_failure_records_safe_stage_and_code(self) -> None:
        """模板引擎不可用时应明确记录失败环节，不把上游详情或配置值写入状态。"""

        state_path = self.root / ".devagentstudio/template-state.json"
        state_path.write_text(json.dumps({
            "schemaVersion": 3, "templateRevision": "2026.10.22.2",
            "requested": {}, "effective": {}, "installedArtifacts": {},
            "managedContributions": {}, "stateSequence": 0,
        }), encoding="utf-8")
        env_path = self.root / "frontend/.env"
        env_path.parent.mkdir()
        env_path.write_text(
            'PUBLIC_APP_NAME="原应用"\nPUBLIC_APP_ICON="app"\nPUBLIC_THEME_PRIMARY_COLOR="#123456"\n',
            encoding="utf-8",
        )

        async def unavailable(_root: Path) -> None:
            """模拟不可达模板引擎。"""

            raise TemplateEngineError("调用 V4 模板更新超时。")

        self.service._reconcile.reconcile_capabilities = unavailable  # type: ignore[method-assign]
        with self.assertRaisesRegex(ApplicationConfigOperationError, "模板能力代码安装失败.*TEMPLATE_ENGINE_UNAVAILABLE"):
            await self.service.apply(
                self.root, operation_id="operation-engine-down", expected_revision=1,
                changes={
                    "auth.enable": True,
                    "auth.yht.clientId": "client-id",
                    "auth.yht.privateKey": "private-key",
                    "auth.yht.publicKey": "public-key",
                    "auth.yht.centerPublicKey": "center-public-key",
                },
            )

        record = json.loads((self.root / ".devagentstudio/runtime/application-config/operation-engine-down.json").read_text(encoding="utf-8"))
        self.assertEqual(record["failureStage"], "template_capability_reconcile")
        self.assertEqual(record["errorCode"], "TEMPLATE_ENGINE_UNAVAILABLE")
        self.assertIn("模板引擎不可用", record["failureSummary"])

    async def test_desired_write_failure_retains_original_operation(self) -> None:
        """Desired 写盘失败保留阶段、错误码与原提案身份，重试不丢修订。"""
        from unittest.mock import patch
        from app.services.application_config.repository import ApplicationConfigRepository
        with patch.object(ApplicationConfigRepository, 'save', side_effect=OSError('injected')):
            with self.assertRaises(ApplicationConfigOperationError) as captured:
                await self.service.apply(self.root, operation_id='desired-write-fault', expected_revision=1, changes={'appName': 'New'})
        self.assertEqual(captured.exception.failure_stage, 'desired_save')
        record = self.service.get_operation(self.root, 'desired-write-fault')
        self.assertEqual(record['status'], 'FAILED')
        self.assertEqual(record['failureCode'], 'CONFIG_SAVE_FAILED')
        self.assertNotIn('progressStage', record)
        result = await self.service.apply(self.root, operation_id='desired-write-fault', expected_revision=1, changes={'appName': 'New'})
        self.assertEqual(result['status'], 'APPLIED')
        self.assertEqual(result['configRevision'], 2)

    def test_read_does_not_normalize_persistent_configuration(self) -> None:
        """读取当前合法 Base 的可选字段时不创建配置或修改文件。"""
        from app.services.application_config.repository import ApplicationConfigRepository
        current = json.loads(self.config_path.read_text())
        del current['auth']['yht']
        self.config_path.write_text(json.dumps(current))
        before = self.config_path.read_bytes()
        loaded = ApplicationConfigRepository(self.root).load()
        self.assertNotIn('yht', loaded['auth'])
        self.assertNotIn('api', loaded['authorization'])
        self.assertEqual(before, self.config_path.read_bytes())

    async def test_resume_requires_original_request_and_persisted_desired(self) -> None:
        """恢复只接受原请求身份和未变化的 Desired，不能采用编辑后的值。"""
        await self.service.apply(self.root, operation_id='resume-request', expected_revision=1, changes={'appName': 'New'}, request_id='request-original')
        with self.assertRaisesRegex(ApplicationConfigOperationError, 'CONFIG_OPERATION_CONFLICT'):
            await self.service.resume(self.root, operation_id='resume-request', request_id='request-other')
        record = self.service.get_operation(self.root, 'resume-request')
        record['status'] = 'FAILED'
        self.service._write_operation(self.root, record)
        content = json.loads(self.config_path.read_text())
        content['appName'] = 'external-change'
        self.config_path.write_text(json.dumps(content))
        with self.assertRaisesRegex(ApplicationConfigOperationError, 'CONFIG_OPERATION_CONFLICT'):
            await self.service.resume(self.root, operation_id='resume-request', request_id='request-original')

    def test_resume_protocol_rejects_values_and_revision(self) -> None:
        """显式恢复动作不得携带旧密钥提案或客户端指定的修订。"""
        action = {'action': 'resume', 'workspaceRoot': str(self.root), 'operationId': 'operation-resume'}
        ApplicationConfigAction.model_validate(action)
        for extra in ({'changes': {'auth.yht.privateKey': 'synthetic-secret'}}, {'expectedConfigRevision': 1}):
            with self.subTest(field=list(extra)), self.assertRaises(ValidationError):
                ApplicationConfigAction.model_validate({**action, **extra})

    async def test_optional_login_addresses_can_be_cleared(self) -> None:
        """可选地址允许显式留空，且仍参与正常的 revision 事务。"""

        result = await self.service.apply(
            self.root, operation_id="operation-empty-urls", expected_revision=1,
            changes={"auth.yht.centerUri": "", "auth.yht.authorizeUri": ""},
        )
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(saved["auth"]["yht"]["centerUri"], "")
        self.assertEqual(saved["auth"]["yht"]["authorizeUri"], "")

    async def test_enable_login_initializes_missing_yht_container(self) -> None:
        """关闭登录时缺失 yht 的配置可在首次启用时写入完整凭据。"""

        current = json.loads(self.config_path.read_text(encoding="utf-8"))
        del current["auth"]["yht"]
        self.config_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        changes = {
            "auth.enable": True,
            "auth.yht.clientId": "client-id",
            "auth.yht.privateKey": "encrypted-private-key",
            "auth.yht.publicKey": "public-key",
            "auth.yht.centerPublicKey": "center-public-key",
        }

        result = await self.service.apply(
            self.root,
            operation_id="operation-003",
            expected_revision=1,
            changes=changes,
        )

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "APPLIED")
        self.assertTrue(saved["auth"]["enable"])
        self.assertEqual(saved["auth"]["yht"]["clientId"], "client-id")
        self.assertEqual(saved["auth"]["yht"]["authorizeUri"], "https://one-account-gateway.paasuat.cmbchina.cn/auth-server/auth")


if __name__ == "__main__":
    unittest.main()
