"""受控转入在初始化、重复执行和中断时保留真实文件与保护摘要。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.services.agent_ui_design_transfer import execute_transfer, read_transfer
from app.services.agent_ui_design_transfer_plan import prepare_transfer_plan
from app.services.agent_ui_dependency_plan import target_snapshot
from app.services.agent_ui_transfer_records import transfer_directory
from app.services.agent_ui_dependency_install import save_record
from tests.agent_ui_transfer_fixtures import transfer_fixture, verify_target


class TransferExecutionTests(unittest.TestCase):
    """通过当前正式确认与安装合同测试真实磁盘写入，隔离 SDK 和昂贵构建。"""

    def setUp(self) -> None:
        """为每项测试创建独立目标与可审阅计划。"""
        self.temp = TemporaryDirectory(prefix="T23 中文 ")
        self.workspace = Path(self.temp.name).resolve()
        self.source, self.target, self.installation = transfer_fixture(str(self.workspace))
        self.sdk = patch("app.services.agent_ui_design_transfer.run_public_tool", side_effect=self.tool)
        self.sdk.start()

    def tearDown(self) -> None:
        """释放当前夹具和替代工具。"""
        self.sdk.stop()
        self.temp.cleanup()

    def tool(self, action: str, target: Path, source_root: str, **_kwargs: object) -> dict:
        """模拟公开工具的文件边界；集成测试另消费真实打包 SDK。"""
        if action == "initialize":
            for name, content in {".agent-ui/project.json": '{"sourceRoot":"src/助手"}',
                    ".agent-ui/source-lock.json": '{"sourceRoot":"src/助手","items":{}}',
                    source_root + "/app-ui.json": '{"root":{"type":"slot","plugins":[]}}\n',
                    source_root + "/application/project-config.tsx": "// official default\n"}.items():
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        return {"status": "passed"}

    def plan(self) -> dict:
        """取得当前真实目标的服务端差异。"""
        return prepare_transfer_plan(self.workspace, installation_plan_id=self.installation, target_source_root="src/助手")

    def execute(self, plan: dict, action: str) -> dict:
        """仅提交计划摘要，不提交源码或执行命令。"""
        return execute_transfer(self.workspace, installation_plan_id=self.installation, plan_id=plan["planId"],
            target_source_root="src/助手", action=action, thread_id="thread", run_id=plan["planId"])

    def initialize(self) -> dict:
        """公开初始化后必须获得新差异，不能把默认稿当正式交接。"""
        result = self.execute(self.plan(), "initialize")
        self.assertEqual(result["state"], "initialized", result)
        return self.plan()

    def test_initialize_replan_transfer_and_duplicate(self) -> None:
        """默认应用层可在新计划中替换；重复确认只返回原事实。"""
        plan = self.initialize()
        self.assertFalse(plan["initialization"]["required"])
        self.assertEqual(plan["status"], "reviewable", plan)
        self.assertTrue(any(item["operation"] == "modify" for item in plan["changes"]))
        with patch("app.services.agent_ui_design_transfer.validate_target", return_value={"static": "passed"}):
            result = self.execute(plan, "confirm")
        self.assertEqual(result["state"], "verified", result)
        self.assertEqual((self.target / "src/助手/application/project-config.tsx").read_text(),
            (self.source / "src/agent-ui/application/project-config.tsx").read_text())
        before = target_snapshot(self.target)
        self.assertEqual(self.execute(plan, "confirm")["state"], "verified")
        self.assertEqual(target_snapshot(self.target), before)
        self.assertTrue(result["mapping"])

    def test_stale_plan_cannot_write(self) -> None:
        """批准后目标变化在写入前拒绝，保留用户编辑。"""
        plan = self.plan()
        (self.target / "src/main.tsx").write_text("// user edit\n")
        with self.assertRaisesRegex(ValueError, "过期|安装"):
            self.execute(plan, "initialize")
        self.assertFalse((self.target / ".agent-ui").exists())

    def test_initialization_does_not_authorize_later_user_edits(self) -> None:
        """初始化凭据只准许精确工具增量，后续人工编辑使桥接证据失效。"""
        self.initialize()
        path = self.target / "src/助手/application/project-config.tsx"
        path.write_text("// user customization\n")
        with self.assertRaisesRegex(ValueError, "安装|过期"):
            self.plan()
        self.assertEqual(path.read_text(), "// user customization\n")

    def test_validation_failure_preserves_partial_facts_and_backup(self) -> None:
        """已写但未通过验证不能标成功，重入不能静默重试。"""
        plan = self.initialize()
        with patch("app.services.agent_ui_design_transfer.validate_target", side_effect=ValueError("build failed")):
            result = self.execute(plan, "confirm")
        self.assertEqual(result["state"], "failed", result)
        self.assertTrue(result["applied"])
        self.assertIn("build failed", result["message"])
        self.assertEqual(self.execute(plan, "confirm")["state"], "failed")
        record = read_transfer(self.workspace, plan["planId"])
        self.assertEqual(record["after"], target_snapshot(self.target))
        directory = self.workspace / record["recordPath"]
        backup = directory.parent / "before/src/助手/application/project-config.tsx"
        self.assertEqual(backup.read_text(), "// official default\n")

    def test_initialized_base_is_preserved(self) -> None:
        """转入不复制设计工具记录，也不重置目标 BASE。"""
        plan = self.initialize()
        base = (self.target / ".agent-ui/source-lock.json").read_bytes()
        with patch("app.services.agent_ui_design_transfer.validate_target", return_value={"static": "passed"}):
            result = self.execute(plan, "confirm")
        self.assertEqual(result["state"], "verified", result)
        self.assertEqual(base, (self.target / ".agent-ui/source-lock.json").read_bytes())

    def test_target_root_change_is_rejected(self) -> None:
        """已初始化目标不能以换目录的方式重置所有权。"""
        self.initialize()
        with self.assertRaisesRegex(ValueError, "sourceRoot"):
            prepare_transfer_plan(self.workspace, installation_plan_id=self.installation, target_source_root="src/other")

    def test_default_plan_follows_existing_target_root(self) -> None:
        """界面未指定目录时沿用已有目标 ownership，不猜测默认目录。"""
        self.initialize()
        plan = prepare_transfer_plan(self.workspace, installation_plan_id=self.installation)
        self.assertEqual(plan["target"]["sourceRoot"], "src/助手")

    def test_query_rejects_stale_source_evidence(self) -> None:
        """成功后设计变化，查询也不能继续展示当前验证通过。"""
        plan = self.plan()
        self.assertEqual(self.execute(plan, "initialize")["state"], "initialized")
        (self.source / "src/agent-ui/app-ui.json").write_text('{"changed":true}')
        self.assertEqual(read_transfer(self.workspace, plan["planId"])["state"], "stale")

    def test_revalidated_installation_keeps_exact_owned_files(self) -> None:
        """重新验证依赖后仍可审阅自己生成的默认文件，用户修改继续作为冲突。"""
        self.initialize()
        path = self.target / "src/助手/application/project-config.tsx"
        path.write_text("// user's later edit\n")
        self.installation = verify_target(self.workspace)
        plan = self.plan()
        self.assertIn("src/助手/application/project-config.tsx", plan["conflicts"])
        self.assertEqual(next(item["operation"] for item in plan["changes"]
            if item["targetPath"] == "src/助手/app-ui.json"), "modify")

    def test_orphan_execution_is_unknown_and_not_replayed(self) -> None:
        """未知执行不因新调用持锁而重新运行。"""
        plan = self.plan()
        save_record(transfer_directory(self.workspace, plan["planId"]), {"planId": plan["planId"],
            "action": "initialize", "state": "running"})
        self.assertEqual(self.execute(plan, "initialize")["state"], "unknown")
        self.assertFalse((self.target / ".agent-ui").exists())

    def test_concurrent_edit_during_transfer_is_preserved(self) -> None:
        """中途外部写入导致本轮失败，后续用户编辑不被还原或覆盖。"""
        plan = self.initialize()
        from app.services.agent_ui_dependency_install import _write
        edited = self.target / "src/main.tsx"

        def write(path: Path, content: bytes) -> None:
            """第一次应用层写入后模拟编辑器保存，保留真实 CAS 行为。"""
            _write(path, content)
            if path.is_relative_to(self.target):
                edited.write_text("// concurrent user edit\n")

        with patch("app.services.agent_ui_design_transfer._write", side_effect=write):
            record = self.execute(plan, "confirm")
        self.assertEqual(record["state"], "failed", record)
        self.assertEqual(edited.read_text(), "// concurrent user edit\n")

    def test_changed_design_during_tool_execution_is_rejected(self) -> None:
        """工具成功不能替代当前设计来源复核。"""
        def tool(*args: object, **kwargs: object) -> dict:
            """模拟工具完成后设计侧刚好写入了新版本。"""
            result = self.tool(*args, **kwargs)
            (self.source / "src/agent-ui/app-ui.json").write_text('{"changed":true}')
            return result

        with patch("app.services.agent_ui_design_transfer.run_public_tool", side_effect=tool):
            record = self.execute(self.plan(), "initialize")
        self.assertEqual(record["state"], "failed", record)
