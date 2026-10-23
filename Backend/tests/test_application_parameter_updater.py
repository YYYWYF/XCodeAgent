"""参数文件定点更新的保真与故障验证。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.application_parameter_updater import ApplicationParameterUpdater, ParameterUpdateError


class ParameterUpdaterTests(unittest.TestCase):
    """覆盖无变化、精确写入、冲突和同进程回滚。"""

    def setUp(self) -> None:
        """构造保留用户行和 CRLF 的前后端参数文件。"""

        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.env = self.root / "frontend/.env"
        self.yaml = self.root / "backend/src/main/resources/application-dev.yml"
        self.env.parent.mkdir(parents=True)
        self.yaml.parent.mkdir(parents=True)
        self.env.write_bytes(b'OTHER=value\r\nPUBLIC_APP_NAME="Before"\r\nPUBLIC_APP_ICON="old"\r\nPUBLIC_THEME_PRIMARY_COLOR="#000000"\r\nPUBLIC_YHT_CLIENT_ID="old"\r\nPUBLIC_YHT_AUTHORIZE_URI="old"\r\nPUBLIC_YHT_END_URL="old"\r\nPUBLIC_YHT_LOGOUT_SHARE_JS="old"\r\n')
        self.yaml.write_bytes(
            b'server:\r\n  port: 8080\r\nyht:\r\n  authentication:\r\n'
            b'    client-id: "old"\r\n    private-key: "old"\r\n    public-key: "old"\r\n    center-public-key: "old"\r\n    center-uri: "old"\r\n    authorize-uri: "old"\r\n    redirect-uri: "old"\r\n  api:\r\n    base-url: "old"\r\n    view-id: "old"\r\n'
        )
        self.config = {
            "appName": "After", "appIcon": "app", "theme": {"primaryColor": "#123456"},
            "auth": {"yht": {"clientId": "new", "privateKey": "secret-1", "publicKey": "pub", "centerPublicKey": "center", "centerUri": "http://center", "authorizeUri": "http://authorize", "redirectUri": "http://redirect", "endUrl": "http://end", "logoutShareJs": "http://logout"}},
            "authorization": {"api": {"baseUrl": "http://api", "viewId": "view"}},
        }
        self.updater = ApplicationParameterUpdater()

    def tearDown(self) -> None:
        """释放临时工程。"""

        self.temporary.cleanup()

    def test_full_paths_preserve_unmanaged_names_and_inline_comments(self) -> None:
        """同名叶子不影响其他树，CRLF 与行尾注释均保持原字节。"""
        other = b'other:\r\n  client-id: user-client # keep\r\n  base-url: https://user.test\r\n'
        self.yaml.write_bytes(self.yaml.read_bytes().replace(b'client-id: "old"', b'client-id: "old" # managed comment') + other)
        plan = self.updater.preview(self.root, self.config, login_applied=True, authorization_applied=True)
        self.updater.apply(self.root, plan)
        self.assertTrue(self.yaml.read_bytes().endswith(other))
        self.assertIn(b'client-id: "new" # managed comment\r\n', self.yaml.read_bytes())
        self.assertIn('auth.yht.clientId', plan.changed_keys)
        self.assertIn('authorization.api.baseUrl', plan.changed_keys)

    def test_rejects_ambiguous_yaml_without_writing(self) -> None:
        """重复键、别名、流式映射、多行标量均不得猜测更新。"""
        original = self.yaml.read_bytes()
        cases = [
            original + b'other:\n  key: a\n  key: b\n',
            original + b'other: &a value\ncopy: *a\n',
            original.replace(b'client-id: "old"', b'client-id: |\r\n      old'),
            b'yht: {authentication: {client-id: old}}\n',
        ]
        for content in cases:
            with self.subTest(case=cases.index(content)):
                self.yaml.write_bytes(content)
                before = self.env.read_bytes()
                with self.assertRaisesRegex(ParameterUpdateError, 'CONFIG_TARGET_CONFLICT'):
                    self.updater.preview(self.root, self.config, login_applied=True)
                self.assertEqual(self.env.read_bytes(), before)
                self.assertEqual(self.yaml.read_bytes(), content)

    def test_missing_path_does_not_create_nodes(self) -> None:
        """受管叶子缺失时停止，不新增结构。"""
        self.yaml.write_bytes(b'yht:\n  authentication:\n    client-id: "old"\n')
        before = self.yaml.read_bytes()
        with self.assertRaisesRegex(ParameterUpdateError, 'CONFIG_STRUCTURE_MISSING'):
            self.updater.preview(self.root, self.config, login_applied=True)
        self.assertEqual(self.yaml.read_bytes(), before)

    def test_theme_only_keeps_backend(self) -> None:
        """未安装 Login 时只修改前端文件，并保持幂等。"""

        original = self.yaml.read_bytes()
        plan = self.updater.preview(self.root, self.config, login_applied=False)
        self.assertEqual(plan.changed_files, ("frontend/.env",))
        self.updater.apply(self.root, plan)
        self.assertEqual(self.yaml.read_bytes(), original)
        self.assertIn(b'OTHER=value\r\n', self.env.read_bytes())
        self.assertEqual(self.updater.preview(self.root, self.config, login_applied=False).changed_files, ())

    def test_auth_leaves_preserve_yaml_and_secret(self) -> None:
        """多文件更新保留其他 YAML，结果不回显密钥。"""

        plan = self.updater.preview(self.root, self.config, login_applied=True)
        self.assertEqual(plan.changed_files, ("frontend/.env", "backend/src/main/resources/application-dev.yml"))
        self.assertNotIn("secret-1", repr(plan))
        self.updater.apply(self.root, plan)
        content = self.yaml.read_bytes()
        self.assertTrue(content.startswith(b'server:\r\n  port: 8080\r\n'))
        self.assertIn(b'    private-key: "secret-1"\r\n', content)
        self.assertEqual(self.updater.preview(self.root, self.config, login_applied=True).changed_files, ())

    def test_optional_login_address_can_be_empty(self) -> None:
        """用户清空可选地址时同时更新现有 env/YAML 叶子。"""

        self.config["auth"]["yht"]["authorizeUri"] = ""
        plan = self.updater.preview(self.root, self.config, login_applied=True)
        self.updater.apply(self.root, plan)
        self.assertIn(b'PUBLIC_YHT_AUTHORIZE_URI=""\r\n', self.env.read_bytes())
        self.assertIn(b'    authorize-uri: ""\r\n', self.yaml.read_bytes())

    def test_rejects_duplicates_invalid_yaml_and_symlink(self) -> None:
        """在写入前拒绝重复 Key、重复 auth、非法 YAML 和符号链接。"""

        original_env, original_yaml = self.env.read_bytes(), self.yaml.read_bytes()
        for target, content in (
            (self.env, b'PUBLIC_APP_NAME=x\nPUBLIC_APP_NAME=y\n'),
            (self.yaml, b'auth:\n  client-id: a\nauth:\n  client-id: b\n'),
            (self.yaml, b'auth: [\n'),
        ):
            with self.subTest(target=target, content=content):
                self.env.write_bytes(original_env)
                self.yaml.write_bytes(original_yaml)
                target.write_bytes(content)
                with self.assertRaises(ParameterUpdateError):
                    self.updater.preview(self.root, self.config, login_applied=True)
        self.env.unlink()
        self.env.symlink_to(self.yaml)
        with self.assertRaises(ParameterUpdateError):
            self.updater.preview(self.root, self.config, login_applied=False)

    def test_second_file_failure_rolls_back(self) -> None:
        """第二目标故障时恢复已写入的前端原始字节。"""

        plan = self.updater.preview(self.root, self.config, login_applied=True)
        before = self.env.read_bytes()
        original_replace = self.updater._replace

        def fail_backend(root: Path, relative: str, content: bytes) -> None:
            """只为第二个目标注入写入失败。"""

            if relative.startswith("backend/"):
                raise OSError("injected")
            original_replace(root, relative, content)

        with patch.object(self.updater, "_replace", side_effect=fail_backend):
            with self.assertRaisesRegex(ParameterUpdateError, "CONFIG_APPLY_FAILED"):
                self.updater.apply(self.root, plan)
        self.assertEqual(self.env.read_bytes(), before)

    def test_interrupted_update_recovers_from_journal(self) -> None:
        """进程在首个文件落盘后退出时，下次预览先恢复 before-image。"""

        plan = self.updater.preview(self.root, self.config, login_applied=True)
        before = self.env.read_bytes()
        self.updater._write_journal(self.root, plan._changes)
        first = plan._changes[0]
        self.updater._replace(self.root, first.path, first.after)

        self.updater.recover(self.root)

        self.assertEqual(self.env.read_bytes(), before)
        self.assertFalse((self.root / ".devagentstudio/runtime/application-parameter-update-journal.json").exists())


if __name__ == "__main__":
    unittest.main()
