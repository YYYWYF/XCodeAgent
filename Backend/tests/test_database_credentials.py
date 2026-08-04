from __future__ import annotations

import base64
import json
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.services.database_credentials import (
    DatabaseCredentialError,
    MySQLConnectionConfig,
    resolve_application_mysql_config,
)
from app.services.database_crypto import (
    DatabaseCryptoError,
    PLATFORM_KEY_ID,
    database_key_file_path,
    database_encryption_metadata,
    decrypt_password,
    ensure_database_platform_key,
    load_database_platform_key,
)
from app.tools.mysql_info import (
    create_get_mysql_table_info_tool,
    get_mysql_table_info,
    get_mysql_table_info_for_workspace,
)


def _secret_envelope(public_key_pem: str, password: str) -> str:
    """使用与 Renderer 相同的协议生成测试密文。"""

    public_key = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
    ciphertext = public_key.encrypt(
        password.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    encoded = base64.urlsafe_b64encode(ciphertext).decode("ascii").rstrip("=")
    return f"xcodeagent-secret:v1:rsa-oaep-256:{PLATFORM_KEY_ID}:{encoded}"


def _write_application(workspace: Path, password: str, *, schema: str) -> None:
    """写入只包含本测试所需 plantMode 字段的应用配置。"""

    target = workspace / ".xcodeagent" / "application.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "datasource": {
                    "db": {
                        "plantMode": {
                            "domain": f"{schema}.mysql.local",
                            "port": 3306,
                            "userName": f"{schema}_user",
                            "pwd": password,
                            "schema": schema,
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class DatabasePlatformKeyTests(unittest.TestCase):
    """验证平台密钥持久化、安全权限和跨语言解密。"""

    def test_key_is_generated_once_and_reloaded_with_secure_permissions(self) -> None:
        """首次生成后再次加载必须保持同一公钥和 0700/0600 权限。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            key_file = Path(temporary_root) / "runtime" / "keys" / "database-platform-key.json"
            first = ensure_database_platform_key(key_file=key_file)
            second = ensure_database_platform_key(key_file=key_file)
            self.assertEqual(first.public_key_pem, second.public_key_pem)
            self.assertEqual(stat.S_IMODE(key_file.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(key_file.stat().st_mode), 0o600)

    def test_public_metadata_never_contains_private_key(self) -> None:
        """健康检查使用的元数据只能暴露公钥和协议标识。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            key_file = Path(temporary_root) / "runtime" / "keys" / "database-platform-key.json"
            metadata = database_encryption_metadata(key_file=key_file)
            self.assertEqual(
                set(metadata),
                {"enabled", "algorithm", "key_id", "public_key"},
            )
            self.assertIn("BEGIN PUBLIC KEY", metadata["public_key"])
            self.assertNotIn("PRIVATE", json.dumps(metadata))

    def test_corrupt_key_is_not_replaced(self) -> None:
        """损坏私钥必须失败，并保留损坏内容供人工恢复。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            key_file = Path(temporary_root) / "runtime" / "keys" / "database-platform-key.json"
            key_file.parent.mkdir(parents=True)
            key_file.write_text("broken-key", encoding="utf-8")
            with self.assertRaises(DatabaseCryptoError):
                ensure_database_platform_key(key_file=key_file)
            self.assertEqual(key_file.read_text(encoding="utf-8"), "broken-key")

    def test_symlink_key_file_is_rejected(self) -> None:
        """私钥路径为符号链接时必须拒绝读取或覆盖。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            actual_key = root / "actual.json"
            actual_key.write_text("not-a-key", encoding="utf-8")
            linked_key = root / "database-platform-key.json"
            linked_key.symlink_to(actual_key)
            with self.assertRaisesRegex(DatabaseCryptoError, "符号链接"):
                ensure_database_platform_key(key_file=linked_key)

    def test_environment_working_directory_selects_stable_key_path(self) -> None:
        """开发与生产环境必须映射到各自稳定的用户级密钥目录。"""

        with patch.dict("os.environ", {"XCODEAGENT_WORKING_DIR": ".xcodeagent_dev"}):
            development_path = database_key_file_path()
        with patch.dict("os.environ", {"XCODEAGENT_WORKING_DIR": ".xcodeagent"}):
            production_path = database_key_file_path()
        self.assertEqual(
            development_path.parts[-3:],
            (".xcodeagent_dev", "keys", "database-platform-key.json"),
        )
        self.assertEqual(
            production_path.parts[-3:],
            (".xcodeagent", "keys", "database-platform-key.json"),
        )

    def test_missing_key_reports_old_ciphertext_recovery_error(self) -> None:
        """显式加载缺失密钥时提示旧密文无法解密。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            missing = Path(temporary_root) / "missing.json"
            with self.assertRaisesRegex(DatabaseCryptoError, "旧应用中的加密密码无法解密"):
                load_database_platform_key(key_file=missing)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for cross-language crypto")
    def test_renderer_algorithm_encrypts_unicode_for_python_decryption(self) -> None:
        """Node WebCrypto 生成的 Unicode 密文必须可被 Python 解密。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            key_file = Path(temporary_root) / "runtime" / "keys" / "database-platform-key.json"
            material = ensure_database_platform_key(key_file=key_file)
            password = "数据库-密码-🔐"
            script = r"""
const { webcrypto } = require('node:crypto');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', async () => {
  const payload = JSON.parse(input);
  const der = Buffer.from(payload.publicKey.replace(/-----[^-]+-----|\s/g, ''), 'base64');
  const key = await webcrypto.subtle.importKey(
    'spki', der, { name: 'RSA-OAEP', hash: 'SHA-256' }, false, ['encrypt']
  );
  const ciphertext = await webcrypto.subtle.encrypt(
    { name: 'RSA-OAEP' }, key, new TextEncoder().encode(payload.password)
  );
  const encoded = Buffer.from(ciphertext).toString('base64url');
  process.stdout.write(`xcodeagent-secret:v1:rsa-oaep-256:platform-key-v1:${encoded}`);
});
"""
            completed = subprocess.run(
                [shutil.which("node") or "node", "-e", script],
                input=json.dumps(
                    {"publicKey": material.public_key_pem, "password": password}
                ),
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(decrypt_password(completed.stdout, key_file=key_file), password)

    def test_tampered_ciphertext_and_wrong_key_id_fail_without_plaintext(self) -> None:
        """篡改密文和错误 keyId 都必须失败且异常不得包含密码。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            key_file = Path(temporary_root) / "runtime" / "keys" / "database-platform-key.json"
            material = ensure_database_platform_key(key_file=key_file)
            password = "never-log-this"
            encrypted = _secret_envelope(material.public_key_pem, password)
            tampered = encrypted[:-1] + ("A" if encrypted[-1] != "A" else "B")
            for candidate in (
                tampered,
                encrypted.replace(PLATFORM_KEY_ID, "platform-key-v2"),
                "xcodeagent-secret:v1:broken",
            ):
                with self.assertRaises(DatabaseCryptoError) as raised:
                    decrypt_password(candidate, key_file=key_file)
                self.assertNotIn(password, str(raised.exception))


class ApplicationDatabaseCredentialTests(unittest.TestCase):
    """验证工作区级配置映射、旧明文兼容和工具协议。"""

    def test_two_workspaces_resolve_distinct_encrypted_configs(self) -> None:
        """A、B 应用必须分别读取各自的 application.json。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            key_file = root / "runtime" / "keys" / "database-platform-key.json"
            material = ensure_database_platform_key(key_file=key_file)
            workspace_a = root / "app-a"
            workspace_b = root / "app-b"
            _write_application(
                workspace_a,
                _secret_envelope(material.public_key_pem, "密码-A"),
                schema="schema_a",
            )
            _write_application(
                workspace_b,
                _secret_envelope(material.public_key_pem, "密码-B"),
                schema="schema_b",
            )
            config_a = resolve_application_mysql_config(workspace_a, key_file=key_file)
            config_b = resolve_application_mysql_config(workspace_b, key_file=key_file)
            self.assertEqual((config_a.database, config_a.password), ("schema_a", "密码-A"))
            self.assertEqual((config_b.database, config_b.password), ("schema_b", "密码-B"))

    def test_legacy_plaintext_is_read_without_rewriting_application(self) -> None:
        """旧明文配置可读，但解析操作不得改写 application.json。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root) / "legacy"
            _write_application(workspace, "legacy-password", schema="legacy_schema")
            application_file = workspace / ".xcodeagent" / "application.json"
            before = application_file.read_bytes()
            config = resolve_application_mysql_config(workspace)
            self.assertEqual(config.password, "legacy-password")
            self.assertEqual(application_file.read_bytes(), before)

    def test_invalid_fields_fail_without_echoing_values(self) -> None:
        """非法端口和空密码必须返回脱敏配置错误。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root) / "invalid"
            _write_application(workspace, "secret-value", schema="invalid_schema")
            target = workspace / ".xcodeagent" / "application.json"
            payload = json.loads(target.read_text(encoding="utf-8"))
            payload["datasource"]["db"]["plantMode"]["port"] = "bad-port"
            target.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(DatabaseCredentialError) as raised:
                resolve_application_mysql_config(workspace)
            self.assertNotIn("secret-value", str(raised.exception))

    def test_workspace_tool_preserves_result_and_model_input_schema(self) -> None:
        """工具只暴露 table_name，并原样返回 mysql_table_info 的 JSON。"""

        config = MySQLConnectionConfig(
            host="db.local",
            port=3307,
            user="reader",
            password="private-password",
            database="inventory",
        )
        expected = json.dumps(
            {
                "tool": "mysql_table_info",
                "status": "ok",
                "database": "inventory",
                "database_exists": True,
                "tables": [],
                "schemas": {},
                "indexes": {},
                "foreign_keys": {},
            }
        )
        with patch(
            "app.tools.mysql_info.resolve_application_mysql_config",
            return_value=config,
        ), patch("app.tools.mysql_info.mysql_table_info", return_value=expected) as mysql_info:
            result = get_mysql_table_info_for_workspace("/workspace-a", table_name=None)
        self.assertEqual(result, expected)
        mysql_info.assert_called_once_with(
            host="db.local",
            port=3307,
            user="reader",
            password="private-password",
            database="inventory",
            table_name=None,
        )
        bound_tool = create_get_mysql_table_info_tool("/workspace-a")
        self.assertEqual(set(bound_tool.args_schema.model_fields), {"table_name"})

    def test_configuration_error_keeps_existing_error_envelope(self) -> None:
        """工具忽略全局 MYSQL_*，配置错误仍使用既有错误结构。"""

        with patch.dict(
            "os.environ",
            {
                "MYSQL_HOST": "ignored-host",
                "MYSQL_PORT": "3306",
                "MYSQL_USER": "ignored-user",
                "MYSQL_PWD": "ignored-password",
                "MYSQL_DATABASE": "ignored-database",
            },
        ):
            result = json.loads(get_mysql_table_info.invoke({"table_name": None}))
        self.assertEqual(result["tool"], "get_mysql_table_info")
        self.assertEqual(result["status"], "error")
        self.assertEqual(set(result), {"tool", "status", "error"})


if __name__ == "__main__":
    unittest.main()
