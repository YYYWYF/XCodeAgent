"""ApplicationConfigMutationService 的原子提交与约束测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.domain.application_config_change import ApplicationConfigChange
from app.services.application_config_mutation import (
    ApplicationConfigMutationError,
    apply_application_config_changes,
)


def _change(path: str, before: bool, after: bool) -> ApplicationConfigChange:
    """构造已由 Resolver 生成的最小合法 Delta。"""

    return ApplicationConfigChange(path=path, operation="set", **{"from": before, "to": after}, reason="测试", evidence="测试")


def _write_application(workspace: str, *, datasource: str = "static", subjects: list[str] | None = None) -> Path:
    """写入包含四个当前可变能力字段的 schema v5 测试配置。"""

    target = Path(workspace) / ".xcodeagent" / "application.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"schemaVersion": 5, "datasource": {"type": datasource}, "auth": {"enable": False}, "authorization": {"enabled": False, "initialAdministratorSubjects": subjects or []}, "track": {"enable": False}, "apiTrack": {"enable": False}}), encoding="utf-8")
    return target


class ApplicationConfigMutationTests(unittest.TestCase):
    """验证服务只接受当前快照 Delta，并在全部校验完成后才写入。"""

    def test_applies_whitelisted_changes_atomically(self) -> None:
        """合法登录与埋点 Delta 写入完整对象，未触及字段保持原状。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = _write_application(workspace)
            updated = apply_application_config_changes(workspace, changes=[_change("auth.enable", False, True), _change("track.enable", False, True)])
            self.assertTrue(updated["auth"]["enable"])
            self.assertTrue(json.loads(target.read_text(encoding="utf-8"))["track"]["enable"])

    def test_rejects_stale_or_duplicate_changes_without_writing(self) -> None:
        """from 不匹配或重复路径必须拒绝，文件保持原配置。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = _write_application(workspace)
            for changes in ([_change("auth.enable", True, False)], [_change("auth.enable", False, True), _change("auth.enable", False, True)]):
                with self.subTest(changes=changes), self.assertRaises(ApplicationConfigMutationError):
                    apply_application_config_changes(workspace, changes=changes)
            self.assertFalse(json.loads(target.read_text(encoding="utf-8"))["auth"]["enable"])

    def test_enforces_authorization_dependencies_before_writing(self) -> None:
        """权限启用必须同时满足登录、数据库和真实初始管理员前置条件。"""

        with tempfile.TemporaryDirectory() as workspace:
            target = _write_application(workspace, datasource="database")
            with self.assertRaisesRegex(ApplicationConfigMutationError, "初始管理员"):
                apply_application_config_changes(workspace, changes=[_change("auth.enable", False, True), _change("authorization.enabled", False, True)])
            self.assertFalse(json.loads(target.read_text(encoding="utf-8"))["authorization"]["enabled"])
        with tempfile.TemporaryDirectory() as workspace:
            _write_application(workspace, datasource="database", subjects=["ops@example.com"])
            updated = apply_application_config_changes(workspace, changes=[_change("auth.enable", False, True), _change("authorization.enabled", False, True)])
            self.assertTrue(updated["authorization"]["enabled"])

