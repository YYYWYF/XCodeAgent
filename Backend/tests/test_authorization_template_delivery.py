"""合成 ZIP 的消费端集成回归；真实模板交付由手动联调入口另行验收。"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.services.authorization_bootstrap import _command_for_workspace, run_authorization_bootstrap
from app.services.template_reconcile.executor_v3 import UpdatePackageExecutorV3, WorkingCopyStoreV3
from app.services.template_reconcile.transaction_v4 import apply_working_copy_v4
from app.services.template_reconcile.update_package_v4 import validate_update_package_v4
from app.services.workspace_bootstrap.materializer import WorkspaceMaterializer
from app.services.workspace_bootstrap.models import ArchiveLimits
from app.services.workspace_bootstrap.template_package import validate_template_package

SCHEMA = "backend/sql/authorization/schema.sql"
STATE = ".devagentstudio/template-state.json"
ASSETS = {
    SCHEMA: b"-- synthetic SQL fixture\nselect 1;\n",
    "backend/scripts/bootstrap-authorization.sh": b"#!/bin/bash\nexit 0\n",
    "backend/scripts/bootstrap-authorization.ps1": b"exit 0\n",
}
LIMITS = ArchiveLimits(1024 * 1024, 30, 1024 * 1024)


def _state(*capabilities: str, sequence: int = 0) -> dict:
    """构造仅供包传输测试使用的最小当前 State。"""

    enabled = {name: {"enabled": True, "config": {}} for name in capabilities}
    return {"schemaVersion": 3, "templateRevision": "2026.10.22.2", "stateSequence": sequence,
            "requested": enabled, "effective": enabled, "installedArtifacts": {}, "managedContributions": {}}


class AuthorizationTemplateDeliveryTests(unittest.TestCase):
    """验证既有通用物化器和 ADD_FILE 事务原样交付新 SQL，无专用安装分支。"""

    def test_generate_zip_materializes_assets_then_bootstraps(self) -> None:
        """Generate ZIP 经真实校验与 Git 物化后满足新的 Bootstrap 预检。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            archive = Path(directory) / "generate.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("frontend/package.json", "{}\n")
                package.writestr("backend/pom.xml", "<project/>\n")
                package.writestr(STATE, json.dumps(_state("login", "authorization")))
                for name, content in ASSETS.items():
                    package.writestr(name, content)
            validated = validate_template_package(archive, LIMITS)
            WorkspaceMaterializer().materialize(workspace=root, archive_path=archive,
                                                 template_state=validated.template_state)
            for name, content in ASSETS.items():
                self.assertEqual((root / name).read_bytes(), content)
            # 仅在物化完成后替换 Bootstrap 进程，保留 Git baseline 的真实执行。
            with patch("app.services.authorization_bootstrap.workspace_process_registry.run") as run_mock, \
                    patch("app.services.authorization_bootstrap._bootstrap_environment", return_value={}):
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = b"ok"
                run_mock.return_value.stderr = b""
                result = run_authorization_bootstrap(root, {
                    "artifact_type": "technical-plan", "confirmation_status": "confirmed",
                    "authorization_manifest": {"fingerprint": "sha256:delivery-test"},
                }, application_config={"authorization": {"enabled": True}})
                self.assertEqual(result["status"], "executed")
                self.assertEqual(run_mock.call_count, 1)

    def test_v4_add_file_commits_schema_without_touching_user_configuration(self) -> None:
        """Base/Login 两种起点通过通用 V4 ADD_FILE 和事务交付相同 SQL。"""

        for installed in ((), ("login",)):
            with self.subTest(installed=installed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "workspace"
                before = {
                    "backend/src/main/resources/application.yml": b"# user\nserver:\n  port: 9001\n",
                    "backend/src/main/resources/application-dev.yml": b"# user\ncustom: keep\n",
                    "frontend/.env": b"# user\nUSER_VALUE=keep\n",
                    STATE: json.dumps(_state(*installed)).encode(),
                }
                for name, content in before.items():
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                next_state = _state("login", "authorization", sequence=1)
                operations = [{"schemaVersion": 1, "operationId": f"asset-{index}", "index": index,
                    "type": "ADD_FILE", "target": name, "parameters": {}, "payloadRef": f"payload/{name}"}
                    for index, name in enumerate(ASSETS)]
                metadata = {"protocolVersion": "4", "packageId": "pkg-550e8400-e29b-41d4-a716-446655440000",
                    "mode": "APPLY", "sourceRevision": next_state["templateRevision"], "expectedStateSequence": 0,
                    "operations": operations, "validationPlan": [], "diagnostics": [], "nextTemplateState": next_state,
                    "payloadManifest": {f"payload/{name}": {"size": len(content)} for name, content in ASSETS.items()}}
                archive = Path(directory) / "update.zip"
                with zipfile.ZipFile(archive, "w") as package:
                    package.writestr("extension-update-package.json", json.dumps(metadata))
                    for name, content in ASSETS.items():
                        package.writestr(f"payload/{name}", content)
                validated = validate_update_package_v4(archive, LIMITS)
                store = WorkingCopyStoreV3(root)
                UpdatePackageExecutorV3().execute(validated.package, store, validated.payloads)
                store.entry(STATE).working = json.dumps(next_state).encode()
                self.assertFalse((root / SCHEMA).exists())
                apply_working_copy_v4(root, store)
                for name, content in ASSETS.items():
                    self.assertEqual((root / name).read_bytes(), content)
                for name, content in before.items():
                    if name != STATE:
                        self.assertEqual((root / name).read_bytes(), content)
                self.assertEqual(json.loads((root / STATE).read_text()), next_state)
                self.assertIsNone(_command_for_workspace(root)[1])
