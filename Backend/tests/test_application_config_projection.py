from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.application_config.projection import (
    APP_CONFIG_TARGET,
    SUPPORTED_APPLICATION_ICONS,
    ApplicationConfigProjectionError,
    compile_public_app_config,
    project_template_app_config,
    project_workspace_app_config,
    render_public_app_config,
    validate_projected_app_config,
)


def _application(*, revision: int = 1, color: str = "#6b3cf0") -> dict[str, object]:
    """构造包含敏感及非投影字段的当前应用配置。"""

    return {
        "schemaVersion": 6,
        "configRevision": revision,
        "appName": "人员管理系统",
        "appIcon": "AppstoreOutlined",
        "theme": {"primaryColor": color},
        "layout": {"type": "side", "useHeader": True, "useFooter": False},
        "auth": {"enable": False, "yht": {"clientId": "secret-client"}},
        "authorization": {"enabled": False, "initialAdministratorSubjects": []},
        "environment": {"dev": [{"key": "TOKEN", "value": "secret"}]},
    }


def _prepare_target(root: Path, content: str = "// template default\n") -> Path:
    """创建由模板预先提供的唯一投影目标。"""

    target = root / APP_CONFIG_TARGET
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    return target


def _write_application(root: Path, application: dict[str, object]) -> None:
    """写入当前品牌目录下的 canonical application.json。"""

    target = root / ".devagentstudio/application.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(application, ensure_ascii=False), encoding="utf-8")


class ApplicationConfigProjectionTests(unittest.TestCase):
    """验证公开配置投影的白名单、安全性、幂等性和并发边界。"""

    def test_compile_uses_only_three_public_fields(self) -> None:
        """投影对象不得包含布局、认证、环境或修订元数据。"""

        compiled = compile_public_app_config(_application())

        self.assertEqual(
            compiled,
            {
                "app": {"name": "人员管理系统", "icon": "AppstoreOutlined"},
                "theme": {"primaryColor": "#6b3cf0"},
            },
        )
        serialized = json.dumps(compiled, ensure_ascii=False)
        for forbidden in ("layout", "clientId", "TOKEN", "configRevision", "schemaVersion"):
            self.assertNotIn(forbidden, serialized)

    def test_backend_icon_allowlist_matches_creation_form_options(self) -> None:
        """Backend 白名单必须与新建/设置页共享的前端选项一一对应。"""

        constants = (
            Path(__file__).resolve().parents[2]
            / "Frontend/src/renderer/src/components/Welcome/constants.ts"
        ).read_text(encoding="utf-8")
        frontend_icons = set(re.findall(r"value: '([A-Za-z]+Outlined)'", constants))

        self.assertEqual(frontend_icons, set(SUPPORTED_APPLICATION_ICONS))

    def test_render_is_stable_and_escapes_typescript_line_separators(self) -> None:
        """特殊字符必须走 JSON 转义，同一输入逐字节稳定。"""

        application = _application()
        application["appName"] = "人'员\n管理\\系统\u2028下一行\u2029"

        first = render_public_app_config(application)
        second = render_public_app_config(application)

        self.assertEqual(first, second)
        self.assertIn('"name": "人\'员\\n管理\\\\系统\\u2028下一行\\u2029"', first)
        self.assertNotIn("secret-client", first)
        self.assertTrue(first.endswith(" satisfies AppConfig;\n"))

    def test_rejects_invalid_color_icon_and_missing_name(self) -> None:
        """三个公开字段任一非法时必须指出 application.json 字段。"""

        cases = (
            ({**_application(), "theme": {"primaryColor": "red"}}, "theme.primaryColor"),
            ({**_application(), "appIcon": "ArbitraryExpression"}, "appIcon"),
            ({**_application(), "appName": "  "}, "appName"),
        )
        for application, field in cases:
            with self.subTest(field=field), self.assertRaisesRegex(
                ApplicationConfigProjectionError, field.replace(".", r"\.")
            ):
                compile_public_app_config(application)

    def test_projection_is_atomic_idempotent_and_verifiable(self) -> None:
        """首次写入返回 CHANGED，相同内容返回 NO_CHANGE 且摘要稳定。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = _prepare_target(root)
            application = _application()

            changed = project_template_app_config(root, application)
            changed_stat = target.stat().st_mtime_ns
            unchanged = project_template_app_config(root, application)

            self.assertEqual(changed.status, "CHANGED")
            self.assertEqual(unchanged.status, "NO_CHANGE")
            self.assertEqual(changed.content_sha256, unchanged.content_sha256)
            self.assertEqual(target.stat().st_mtime_ns, changed_stat)
            validate_projected_app_config(root, application)

    def test_missing_old_template_and_symlink_are_rejected_without_damage(self) -> None:
        """旧模板缺目标或目标为符号链接时不得创建或覆盖文件。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "frontend").mkdir()
            with self.assertRaisesRegex(ApplicationConfigProjectionError, "APP_CONFIG_TARGET_MISSING"):
                project_template_app_config(root, _application())
            self.assertFalse((root / APP_CONFIG_TARGET).exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real.ts"
            real.write_text("keep\n", encoding="utf-8")
            target = root / APP_CONFIG_TARGET
            target.parent.mkdir(parents=True)
            target.symlink_to(real)
            with self.assertRaisesRegex(ApplicationConfigProjectionError, "APP_CONFIG_TARGET_INVALID"):
                project_template_app_config(root, _application())
            self.assertEqual(real.read_text(encoding="utf-8"), "keep\n")

    def test_workspace_projection_skips_absent_template_and_rejects_stale_revision(self) -> None:
        """尚未生成模板应跳过，陈旧请求不得触碰已有目标。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_application(root, _application(revision=3))
            skipped = project_workspace_app_config(root, expected_config_revision=3)
            self.assertEqual(skipped.status, "SKIPPED_NO_TEMPLATE")

            target = _prepare_target(root, "keep\n")
            with self.assertRaisesRegex(ApplicationConfigProjectionError, "REVISION_CONFLICT"):
                project_workspace_app_config(root, expected_config_revision=2)
            self.assertEqual(target.read_text(encoding="utf-8"), "keep\n")

    def test_unrelated_config_change_does_not_create_generated_file_diff(self) -> None:
        """非白名单字段和修订号变化不得造成 app.generated.ts 无意义改写。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _application(revision=1)
            _write_application(root, first)
            target = _prepare_target(root)
            self.assertEqual(
                project_workspace_app_config(root, expected_config_revision=1).status,
                "CHANGED",
            )
            content = target.read_bytes()
            modified_at = target.stat().st_mtime_ns

            second = _application(revision=2)
            second["layout"] = {"type": "top", "useHeader": False, "useFooter": True}
            _write_application(root, second)
            result = project_workspace_app_config(root, expected_config_revision=2)

            self.assertEqual(result.status, "NO_CHANGE")
            self.assertEqual(target.read_bytes(), content)
            self.assertEqual(target.stat().st_mtime_ns, modified_at)

    def test_revision_change_before_replace_preserves_original_target(self) -> None:
        """替换前 CAS 发现更新时必须保留旧目标，避免旧请求覆盖新配置。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = _application(revision=1)
            newer = _application(revision=2, color="#1677ff")
            _write_application(root, initial)
            target = _prepare_target(root, "keep\n")
            with patch(
                "app.services.application_config.projection.ApplicationConfigRepository.load",
                side_effect=[initial, newer],
            ):
                with self.assertRaisesRegex(ApplicationConfigProjectionError, "REVISION_CONFLICT"):
                    project_workspace_app_config(root, expected_config_revision=1)
            self.assertEqual(target.read_text(encoding="utf-8"), "keep\n")


if __name__ == "__main__":
    unittest.main()
