"""V4 Capability Ensure 的本地判定与开关边界回归测试。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.services.template_reconcile.service import TemplateReconcileService
from app.services.template_reconcile.state_v3 import write_template_state_v3
from app.services.template_reconcile.protocol_v3 import TemplateStateV3
from app.services.workspace_bootstrap.models import TemplateStateError


def _application(login: bool, authorization: bool) -> dict[str, object]:
    """构造满足当前 application.json schema 的最小能力配置。"""

    return {
        "schemaVersion": 6,
        "configRevision": 1,
        "appName": "测试应用",
        "appIcon": "AppstoreOutlined",
        "theme": {"primaryColor": "#722ED1"},
        "auth": {"enable": login, "yht": {"clientId": "", "privateKey": "", "publicKey": "", "centerPublicKey": ""}},
        "authorization": {"enabled": authorization, "initialAdministratorSubjects": []},
        "datasource": {"type": "database"},
    }


def _state(capabilities: dict[str, dict[str, object]]) -> TemplateStateV3:
    """构造只含已应用能力的最小 V3 State。"""

    return TemplateStateV3.model_validate({
        "schemaVersion": 3,
        "templateRevision": "2026.10.20.1",
        "stateSequence": 3,
        "requested": capabilities,
        "effective": capabilities,
        "installedArtifacts": {},
        "managedContributions": {},
    })


class TemplateCapabilityReconcileTests(unittest.TestCase):
    """验证 V4 Ensure 不会回退到 V3 或在本地幂等路径触网。"""

    def _workspace(self, root: Path, *, login: bool, authorization: bool, applied: dict[str, dict[str, object]]) -> None:
        """写入每次 Ensure 都必须读取的 Desired 与 Applied 两个 canonical 文件。"""

        artifact = root / ".devagentstudio"
        artifact.mkdir()
        (artifact / "application.json").write_text(json.dumps(_application(login, authorization)), encoding="utf-8")
        write_template_state_v3(root, _state(applied))

    def test_satisfied_capabilities_skip_engine_even_when_switch_is_disabled(self) -> None:
        """已满足时开关关闭仍应本地返回 NO_CHANGE，且不得写 State。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            applied = {"login": {"enabled": True, "config": {}}, "authorization": {"enabled": True, "config": {}}}
            self._workspace(root, login=True, authorization=True, applied=applied)
            state_path = root / ".devagentstudio/template-state.json"
            before = state_path.read_bytes()
            service = TemplateReconcileService(Settings("", "", "", template_reconcile_enabled=False))
            with patch("app.services.template_reconcile.service.TemplateEngineClient", side_effect=AssertionError("NO_CHANGE 不得创建 Engine Client")):
                result = asyncio.run(service.reconcile_capabilities(root))
            self.assertEqual("NO_CHANGE", result)
            self.assertEqual(before, state_path.read_bytes())

    def test_missing_capability_fails_when_switch_is_disabled(self) -> None:
        """缺失能力不能被关闭的 V4 开关伪装为 NO_CHANGE。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root, login=True, authorization=True, applied={"login": {"enabled": True, "config": {}}})
            service = TemplateReconcileService(Settings("", "", "", template_reconcile_enabled=False))
            with self.assertRaisesRegex(TemplateStateError, "TEMPLATE_RECONCILE_DISABLED"):
                asyncio.run(service.reconcile_capabilities(root))

    def test_same_name_different_configuration_is_not_no_change(self) -> None:
        """同名 Capability 配置冲突必须进入收敛判断，不能只比较能力名称。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root, login=True, authorization=False, applied={"login": {"enabled": True, "config": {"provider": "stale"}}})
            service = TemplateReconcileService(Settings("", "", "", template_reconcile_enabled=False))
            with self.assertRaisesRegex(TemplateStateError, "TEMPLATE_RECONCILE_DISABLED"):
                asyncio.run(service.reconcile_capabilities(root))

    def test_applied_capability_cannot_be_disabled(self) -> None:
        """Applied 包含未启用能力时先明确拒绝卸载。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root, login=False, authorization=False, applied={"login": {"enabled": True, "config": {}}})
            service = TemplateReconcileService(Settings("", "", "", template_reconcile_enabled=False))
            with self.assertRaisesRegex(TemplateStateError, "CAPABILITY_REMOVAL_UNSUPPORTED"):
                asyncio.run(service.reconcile_capabilities(root))

