"""V4 增量包的无摘要严格合同测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

from app.services.template_reconcile.executor_v3 import UpdatePackageExecutorV3, WorkingCopyStoreV3
from app.services.template_reconcile.protocol_v3 import EnsureMavenDependencyOperationV3
from app.services.template_reconcile.protocol_v4 import EnsureCapabilityConfigOperationV4
from app.services.template_reconcile.update_package_v4 import validate_update_package_v4
from app.services.template_reconcile.yaml_config_merge import CapabilityYamlMergeError, merge_capability_yaml
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError


def _metadata() -> dict:
    """构造最小 V4 元数据样本。"""

    return {
        "protocolVersion": "4", "packageId": "pkg-550e8400-e29b-41d4-a716-446655440000",
        "mode": "APPLY", "sourceRevision": "2026.10.20.1", "expectedStateSequence": 0,
        "operations": [], "validationPlan": [], "payloadManifest": {}, "diagnostics": [],
        "nextTemplateState": {
            "schemaVersion": 3, "templateRevision": "2026.10.20.1", "stateSequence": 1,
            "requested": {}, "effective": {}, "installedArtifacts": {}, "managedContributions": {},
        },
    }


class TemplateUpdateV4Tests(unittest.TestCase):
    """验证 V4 包不接受 V3 摘要字段。"""

    def setUp(self) -> None:
        """创建临时 ZIP 目录。"""

        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "update.zip"

    def tearDown(self) -> None:
        """清理临时目录。"""

        self.temporary.cleanup()

    def _write(self, metadata: dict) -> None:
        """以真实 ZIP 格式写入元数据文件。"""

        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("extension-update-package.json", json.dumps(metadata))

    def test_accepts_size_only_v4(self) -> None:
        """合法无摘要 V4 包可通过严格预检。"""

        self._write(_metadata())
        validated = validate_update_package_v4(self.path, ArchiveLimits(100000, 10, 100000))
        self.assertEqual(validated.package.expectedStateSequence, 0)

    def test_rejects_v3_digest_fields(self) -> None:
        """摘要字段不可混入 V4 包。"""

        metadata = _metadata()
        metadata["currentStateDigest"] = "sha256:" + "0" * 64
        self._write(metadata)
        with self.assertRaises(TemplatePackageError):
            validate_update_package_v4(self.path, ArchiveLimits(100000, 10, 100000))

    def test_rejects_managed_configuration_snapshot(self) -> None:
        """下一状态不能携带旧敏感参数快照。"""

        metadata = _metadata()
        metadata["nextTemplateState"]["managedConfiguration"] = {"privateKey": "secret"}
        self._write(metadata)
        with self.assertRaises(TemplatePackageError):
            validate_update_package_v4(self.path, ArchiveLimits(100000, 10, 100000))

    def test_accepts_capability_config_ensure_payload(self) -> None:
        """登录能力的配置结构 Ensure 及其 payload 引用必须可通过 V4 校验。"""

        metadata = _metadata()
        metadata["operations"] = [{
            "schemaVersion": 1,
            "operationId": "ensure-login-dotenv",
            "index": 0,
            "type": "ENSURE_CAPABILITY_CONFIG",
            "target": "frontend/.env",
            "parameters": {"format": "dotenv", "capabilityIds": ["login"]},
            "payloadRef": "payload/login.env",
        }]
        metadata["payloadManifest"] = {"payload/login.env": {"size": 24}}
        metadata["nextTemplateState"]["requested"] = {"login": {"enabled": True, "config": {}}}
        metadata["nextTemplateState"]["effective"] = {"login": {"enabled": True, "config": {}}}
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("extension-update-package.json", json.dumps(metadata))
            archive.writestr("payload/login.env", "PUBLIC_YHT_CLIENT_ID=\"\"\n")

        validated = validate_update_package_v4(self.path, ArchiveLimits(100000, 10, 100000))

        self.assertEqual(validated.package.operations[0].type, "ENSURE_CAPABILITY_CONFIG")

    def test_accepts_login_application_yaml_ensure_payload(self) -> None:
        """Login 的固定 application.yml 占位配置属于 V4 能力安装目标。"""

        metadata = _metadata()
        payload = b"bee:\n  auth:\n    client-id: ${yht.authentication.client-id}\n"
        target = "backend/src/main/resources/application.yml"
        payload_ref = f"payload/{target}"
        metadata["operations"] = [{
            "schemaVersion": 1,
            "operationId": f"ensure-config:{target}",
            "index": 0,
            "type": "ENSURE_CAPABILITY_CONFIG",
            "target": target,
            "parameters": {"format": "yaml", "capabilityIds": ["login"]},
            "payloadRef": payload_ref,
        }]
        metadata["payloadManifest"] = {payload_ref: {"size": len(payload)}}
        metadata["nextTemplateState"]["requested"] = {"login": {"enabled": True, "config": {}}}
        metadata["nextTemplateState"]["effective"] = {"login": {"enabled": True, "config": {}}}
        with zipfile.ZipFile(self.path, "w") as archive:
            archive.writestr("extension-update-package.json", json.dumps(metadata))
            archive.writestr(payload_ref, payload)

        validated = validate_update_package_v4(self.path, ArchiveLimits(100000, 10, 100000))

        self.assertEqual(validated.package.operations[0].target, target)

    def test_yaml_ensure_merges_login_under_existing_bee_root(self) -> None:
        """新增 Login 占位配置不得追加第二个 bee 根或改写已有字段。"""

        current = "server:\n  port: 8080\nbee:\n  persistence:\n    ds1: existing\nmodule:\n  enabled: true\n"
        fragment = "bee:\n  auth:\n    client-id: ${yht.authentication.client-id}\n"

        merged = merge_capability_yaml(current, fragment)

        self.assertEqual(merged.count("\nbee:\n"), 1)
        self.assertEqual(yaml.safe_load(merged)["bee"]["persistence"]["ds1"], "existing")
        self.assertEqual(yaml.safe_load(merged)["bee"]["auth"]["client-id"], "${yht.authentication.client-id}")
        self.assertEqual(merge_capability_yaml(merged, fragment), merged)

    def test_yaml_ensure_rejects_existing_scalar_parent(self) -> None:
        """模板片段要求映射时，已有标量配置必须明确报冲突。"""

        with self.assertRaises(CapabilityYamlMergeError):
            merge_capability_yaml("bee: custom\n", "bee:\n  auth:\n    client-id: placeholder\n")

    def test_yaml_ensure_preserves_existing_leaf_when_adding_authorization(self) -> None:
        """追加权限结构时保留已有 Login 参数和同一 yht 根。"""

        current = "yht:\n  authentication:\n    client-id: custom\n"
        fragment = "yht:\n  authentication:\n    client-id: default\n  api:\n    base-url: http://example.test\n"

        merged = merge_capability_yaml(current, fragment)
        parsed = yaml.safe_load(merged)

        self.assertEqual(merged.count("yht:"), 1)
        self.assertEqual(parsed["yht"]["authentication"]["client-id"], "custom")
        self.assertEqual(parsed["yht"]["api"]["base-url"], "http://example.test")

    def test_deep_duplicates_aliases_and_type_conflicts_are_rejected(self) -> None:
        """即使冲突位于非受管深层节点也不能静默接受。"""
        for current, fragment in (
            ("other:\n  nested:\n    key: a\n    key: b\n", "bee:\n  auth: value\n"),
            ("other: &a value\ncopy: *a\n", "bee:\n  auth: value\n"),
            ("bee:\n  auth: [user]\n", "bee:\n  auth: template\n"),
        ):
            with self.subTest(case=current), self.assertRaises(CapabilityYamlMergeError):
                merge_capability_yaml(current, fragment)

    def test_merge_without_trailing_newline_is_idempotent(self) -> None:
        """没有 EOF 换行的片段与目标也能安全追加，不丢最后一个叶子。"""
        current = "bee:\n  custom: user"
        fragment = "bee:\n  auth:\n    client-id: template"
        merged = merge_capability_yaml(current, fragment)
        self.assertEqual(yaml.safe_load(merged), {'bee': {'custom': 'user', 'auth': {'client-id': 'template'}}})
        self.assertEqual(merge_capability_yaml(merged, fragment), merged)

    def test_dotenv_fragment_comments_do_not_break_idempotence(self) -> None:
        """重复执行 Ensure 不重复追加片段注释，也不覆盖已有用户值。"""
        root = Path(self.temporary.name)
        target = root / 'frontend/.env'
        target.parent.mkdir()
        target.write_bytes(b'# user comment\r\nUSER_VALUE=keep\r\nPUBLIC_YHT_CLIENT_ID=custom\r\n')
        operation = EnsureCapabilityConfigOperationV4.model_validate({
            'schemaVersion': 1, 'operationId': 'dotenv-comment', 'index': 0, 'type': 'ENSURE_CAPABILITY_CONFIG',
            'target': 'frontend/.env', 'parameters': {'format': 'dotenv', 'capabilityIds': ['login']}, 'payloadRef': 'payload/env'})
        store = WorkingCopyStoreV3(root)
        executor = UpdatePackageExecutorV3()
        payload = b'# template comment\nPUBLIC_YHT_CLIENT_ID=default\nPUBLIC_YHT_AUTHORIZE_URI=default\n'
        executor._ensure_capability_config(operation, store, payload)
        first = store.entry('frontend/.env').working
        executor._ensure_capability_config(operation, store, payload)
        self.assertEqual(first, store.entry('frontend/.env').working)
        self.assertTrue(first.startswith(target.read_bytes()))

    def test_maven_ensure_does_not_create_whitespace_only_line(self) -> None:
        """能力安装新增 Maven 依赖时不在结束标签前留下尾随空格。"""

        root = Path(self.temporary.name)
        target = root / "backend/pom.xml"
        target.parent.mkdir(parents=True)
        target.write_text("<project>\n    <dependencies>\n    </dependencies>\n</project>\n", encoding="utf-8")
        operation = EnsureMavenDependencyOperationV3.model_validate({
            "schemaVersion": 1,
            "operationId": "ensure-maven-login",
            "index": 0,
            "type": "ENSURE_MAVEN_DEPENDENCY",
            "target": "backend/pom.xml",
            "parameters": {"groupId": "ZA21", "artifactId": "bee-starter-auth"},
            "payloadRef": None,
        })

        store = WorkingCopyStoreV3(root)
        UpdatePackageExecutorV3()._maven(operation, store, False)
        content = store.entry("backend/pom.xml").working.decode("utf-8")

        self.assertIn("<artifactId>bee-starter-auth</artifactId>", content)
        self.assertFalse(any(line != line.rstrip() for line in content.splitlines()))


if __name__ == "__main__":
    unittest.main()
