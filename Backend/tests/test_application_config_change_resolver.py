"""验证应用配置 Resolver 的意图边界与确定性 Delta 编译。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.access_control_intent import has_explicit_capability_change, resolve_capability_intents
from app.services.application_config_change_resolver import (
    ApplicationConfigChangeResolutionError,
    resolve_application_config_changes,
)
from app.services.application_config import ApplicationConfigService


def _application(enabled: bool = False) -> dict:
    """构造当前 schema v6 的能力开关配置，用于写入测试工作区。"""

    return {
        "schemaVersion": 6,
        "configRevision": 1,
        "auth": {"enable": enabled},
        "authorization": {"enabled": enabled},
        "track": {"enable": enabled},
        "apiTrack": {"enable": enabled},
    }


def _write_application(workspace: Path, application: dict) -> None:
    """把测试应用配置写入唯一允许 Resolver 读取的 canonical 路径。"""

    config_dir = workspace / ".xcodeagent"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "application.json").write_text(
        json.dumps(application, ensure_ascii=False),
        encoding="utf-8",
    )


class ApplicationConfigChangeResolverTests(unittest.TestCase):
    """验证用户目标与当前配置分离，且仅生成白名单内的实际变化。"""

    def setUp(self) -> None:
        """为每个用例创建隔离工作区，禁止以传参方式伪造当前应用配置。"""

        self._temporary_directory = TemporaryDirectory()
        self.workspace = Path(self._temporary_directory.name)
        self.current = _application()
        _write_application(self.workspace, self.current)

    def tearDown(self) -> None:
        """清理每个用例创建的临时工作区。"""

        self._temporary_directory.cleanup()

    def _resolve(self, request: str) -> list:
        """通过当前工作区的 canonical application.json 调用公开 Resolver。"""

        return resolve_application_config_changes(request, workspace_root=self.workspace)

    def _replace_current(self, current: dict) -> None:
        """替换唯一配置文件内容，模拟当前状态而非 RequirementSpec 投影。"""

        self.current = current
        _write_application(self.workspace, current)

    def test_explicit_enable_and_disable_for_each_capability(self) -> None:
        """登录、权限和两种埋点均支持明确启停，包括自然语言后置表达。"""

        cases = [
            ("我想添加登录模块", "auth.enable", True),
            ("给应用增加登录功能", "auth.enable", True),
            ("给应用增加一个登录模块", "auth.enable", True),
            ("这个应用不需要登录了", "auth.enable", False),
            ("登录不再需要了", "auth.enable", False),
            ("将登录关闭", "auth.enable", False),
            ("开启权限管理", "authorization.enabled", True),
            ("关闭权限控制", "authorization.enabled", False),
            ("启用埋点", "track.enable", True),
            ("关闭埋点", "track.enable", False),
            ("开启接口埋点", "apiTrack.enable", True),
            ("关闭API埋点", "apiTrack.enable", False),
            ("API 埋点关闭", "apiTrack.enable", False),
            ("enable login", "auth.enable", True),
            ("disable authorization", "authorization.enabled", False),
        ]
        for request, path, enabled in cases:
            with self.subTest(request=request):
                intents = resolve_capability_intents(request)
                self.assertEqual([(item.path, item.enabled) for item in intents], [(path, enabled)])
                self.assertTrue(has_explicit_capability_change(request))

    def test_non_configuration_requests_do_not_generate_changes(self) -> None:
        """否定命令、疑问、业务流程、初始表单及界面修改不能误生成配置提案。"""

        for request in (
            "", "登录页按钮改成蓝色", "添加登录按钮", "关闭登录页提示",
            "不要添加登录模块", "不需要关闭登录", "不要将登录关闭",
            "如果开启登录会怎样", "是否需要登录", "如何关闭权限管理",
            "用户需要登录后查看人员列表", "支持登录失败重试",
            "认证：不启用。涉及权限控制：否。", "人员页面只有管理员可以访问",
            "启用审计能力", "do not enable login", "add login button", "添加登录功能按钮",
        ):
            with self.subTest(request=request):
                self.assertEqual(self._resolve(request), [])
                self.assertFalse(has_explicit_capability_change(request))

    def test_delta_uses_current_config_and_preserves_input(self) -> None:
        """Delta 当前值来自应用快照，证据保留原文，调用不修改任何输入状态。"""

        current = _application()
        current["source_request"] = "认证：不启用"
        current["authentication_requirements"] = {"enabled": True}
        before = deepcopy(current)
        self._replace_current(current)
        changes = self._resolve(" 我想添加登录模块 ")
        self.assertEqual([item.model_dump(by_alias=True) for item in changes], [{
            "path": "auth.enable", "operation": "set", "from": False, "to": True,
            "reason": "用户明确要求启用 auth.enable", "evidence": " 我想添加登录模块 ",
        }])
        self.assertEqual(current, before)
        current["auth"]["enable"] = True
        self._replace_current(current)
        self.assertEqual(self._resolve("添加登录模块"), [])
        disabled = self._resolve("不需要登录了")
        self.assertTrue(disabled[0].from_value)
        self.assertFalse(disabled[0].to_value)

    def test_authorization_dependency_and_noop_filtering(self) -> None:
        """Resolver 只解析用户目标，统一服务负责补齐权限依赖和过滤空操作。"""

        changes = self._resolve("开启权限管理")
        self.assertEqual([item.path for item in changes], ["authorization.enabled"])
        self.assertTrue(all(item.to_value for item in changes))
        self.current["datasource"] = {"type": "database"}
        self.current["authorization"]["initialAdministratorSubjects"] = ["ops@example.com"]
        self._replace_current(self.current)
        preview = ApplicationConfigService(self.workspace).preview(changes=changes)
        self.assertTrue(preview["auth"]["enable"])
        self.assertTrue(preview["authorization"]["enabled"])
        self.current["auth"]["enable"] = True
        self._replace_current(self.current)
        changes = self._resolve("开启权限管理，开启权限管理")
        self.assertEqual([item.path for item in changes], ["authorization.enabled"])
        self._replace_current(_application(True))
        self.assertEqual(self._resolve("开启权限管理"), [])

    def test_conflicting_targets_are_rejected(self) -> None:
        """同一目标冲突和权限依赖冲突必须显式失败，不能按匹配顺序覆盖。"""

        for request in ("开启登录，关闭登录", "关闭登录，开启权限管理"):
            with self.subTest(request=request), self.assertRaises(ApplicationConfigChangeResolutionError):
                self._resolve(request)

    def test_invalid_current_values_are_not_coerced_or_defaulted(self) -> None:
        """缺失或错误的应用配置不能伪装成关闭状态，也不允许旧版本回退。"""

        for value in (None, 0, "false", {}):
            current = _application()
            current["auth"]["enable"] = value
            with self.subTest(value=value), self.assertRaises(ApplicationConfigChangeResolutionError):
                self._replace_current(current)
                self._resolve("启用登录")
        for current in ({}, {"schemaVersion": 4}, {"schemaVersion": 6.0}, {"schemaVersion": 6}):
            with self.subTest(current=current), self.assertRaises(ApplicationConfigChangeResolutionError):
                self._replace_current(current)
                self._resolve("启用登录")
        (self.workspace / ".xcodeagent" / "application.json").write_text("null", encoding="utf-8")
        with self.assertRaises(ApplicationConfigChangeResolutionError):
            self._resolve("启用登录")

    def test_requires_readable_canonical_application_file(self) -> None:
        """公开入口必须从实际 canonical 文件读取，并拒绝缺失或损坏的配置。"""

        missing_workspace = self.workspace / "missing"
        with self.assertRaises(ApplicationConfigChangeResolutionError):
            resolve_application_config_changes("启用登录", workspace_root=missing_workspace)
        (self.workspace / ".xcodeagent" / "application.json").write_text("{", encoding="utf-8")
        with self.assertRaises(ApplicationConfigChangeResolutionError):
            self._resolve("启用登录")

    def test_mixed_tracking_changes_remain_independent(self) -> None:
        """同一句中的普通埋点与 API 埋点分别生成变化，不串联成同一个开关。"""

        self.current["apiTrack"]["enable"] = True
        self._replace_current(self.current)
        changes = self._resolve("开启埋点，关闭接口埋点")
        self.assertEqual([(item.path, item.to_value) for item in changes], [("track.enable", True), ("apiTrack.enable", False)])
