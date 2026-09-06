import tempfile
import unittest
import json
import subprocess
from pathlib import Path

from app.services.application_template_generation import (
    AGENT_RUNTIME_TEMPLATE_REPOSITORY_URL,
    ApplicationTemplateGenerationError,
    begin_application_template_deletion,
    inspect_template_generation_readiness,
    normalize_git_repository_identity,
    prepare_application_template_generation,
    validate_application_template_generation,
)


class ApplicationTemplateGenerationTests(unittest.TestCase):
    """验证 main 与 auth 模板初始化严格按下载分支隔离。"""

    def test_github_https_and_ssh_repository_urls_are_equivalent(self) -> None:
        """模板门禁应把同一 GitHub 仓库的 HTTPS 与 SSH 地址视为同一来源。"""

        self.assertEqual(
            normalize_git_repository_identity(AGENT_RUNTIME_TEMPLATE_REPOSITORY_URL),
            normalize_git_repository_identity(
                "git@github.com:Bettetman/agent-runtime-template.git"
            ),
        )

    def _workspace(self, root: Path) -> None:
        """创建满足 auth 前端模板契约的最小工作区。"""
        (root / "frontend/src/constants").mkdir(parents=True)
        (root / "frontend/package.json").write_text("{}", encoding="utf-8")
        (root / "frontend/src/constants/resources.ts").write_text("export const RESOURCES = { SYSTEM: {} } as const;\n", encoding="utf-8")
        (root / "frontend/src/constants/routes.tsx").write_text("// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n", encoding="utf-8")
        (root / "backend").mkdir()
        (root / "backend/pom.xml").write_text("<project/>", encoding="utf-8")
        (root / ".xcodeagent/plans").mkdir(parents=True)
        (root / ".xcodeagent/plans/technical-plan.json").write_text(
            json.dumps(
                {
                    "artifact_type": "technical-plan",
                    "confirmation_status": "confirmed",
                    "agent_contracts": [],
                }
            ),
            encoding="utf-8",
        )

    def _download(self, branch: str = "auth") -> dict:
        """返回成功下载的最小结果。"""
        return {
            "targets": {
                "frontend": {
                    "required": True,
                    "status": "succeeded",
                    "attempt": 1,
                    "branch": branch,
                },
                "backend": {
                    "required": True,
                    "status": "succeeded",
                    "attempt": 1,
                    "branch": branch,
                },
                "agentRuntime": {
                    "required": False,
                    "status": "skipped",
                    "attempt": 0,
                    "path": "agent-runtime",
                },
            }
        }

    def _agent_runtime(self, root: Path) -> str:
        """创建满足当前模板门禁且带可验证来源的本地 Git Runtime。"""

        runtime = root / "agent-runtime"
        required_files = (
            "README.md",
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            ".env.example",
            "src/app/main.py",
            "src/app/settings.py",
            "src/app/server/app.py",
            "src/app/server/health.py",
            "src/app/server/agui.py",
            "src/app/agent/factory.py",
            "src/app/agent/context.py",
            "src/app/models/factory.py",
            "src/app/tools/__init__.py",
            "src/app/interaction/service.py",
            "src/app/interaction/schemas.py",
            "src/app/persistence/checkpointer.py",
            "tests/.gitkeep",
        )
        for relative_path in required_files:
            target = runtime / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# test\n", encoding="utf-8")
        commands = (
            ["git", "init", "-b", "master"],
            ["git", "config", "user.email", "tests@example.invalid"],
            ["git", "config", "user.name", "Tests"],
            ["git", "remote", "add", "origin", AGENT_RUNTIME_TEMPLATE_REPOSITORY_URL],
            ["git", "add", "."],
            ["git", "commit", "-m", "test runtime"],
        )
        for command in commands:
            subprocess.run(command, cwd=runtime, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=runtime,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_contract_gate_does_not_create_business_pages(self) -> None:
        """业务页面必须留给后续前端 Build 任务创建。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            manifest = prepare_application_template_generation(root, self._download())
            validate_application_template_generation(root)
            self.assertEqual(manifest["steps"]["templateContract"]["status"], "succeeded")
            self.assertTrue(inspect_template_generation_readiness(root)["ready"])
            self.assertFalse((root / "frontend/src/pages").exists())
            self.assertEqual(
                manifest["steps"]["download"]["targets"]["agentRuntime"]["status"],
                "skipped",
            )

    def test_contract_gate_rejects_missing_markers(self) -> None:
        """模板缺少托管区时不得进入 Build。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            (root / "frontend/src/constants/routes.tsx").write_text("export {};", encoding="utf-8")
            with self.assertRaisesRegex(ApplicationTemplateGenerationError, "托管标记"):
                prepare_application_template_generation(root, self._download())

    def test_rejects_mixed_template_branches(self) -> None:
        """前后端分支不匹配时不能静默选择任一生成流程。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            download = self._download("auth")
            download["targets"]["backend"]["branch"] = "main"
            with self.assertRaisesRegex(ApplicationTemplateGenerationError, "分支不一致"):
                prepare_application_template_generation(root, download)

    def test_main_keeps_page_and_menu_initialization(self) -> None:
        """main 模板不依赖 auth 插槽，仍生成占位页和 BIZ_MENUS。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "frontend/src/constants").mkdir(parents=True)
            (root / "frontend/package.json").write_text("{}", encoding="utf-8")
            (root / "frontend/src/constants/menus.ts").write_text("export const BIZ_MENUS = [];\n", encoding="utf-8")
            (root / "backend").mkdir()
            (root / "backend/pom.xml").write_text("<project/>", encoding="utf-8")
            (root / ".xcodeagent/plans").mkdir(parents=True)
            (root / ".xcodeagent/specs").mkdir(parents=True)
            (root / ".xcodeagent/plans/product-plan.json").write_text('{"confirmation_status":"confirmed","schema_version":"product-plan.v6","pages":[{"pageId":"dashboard","name":"首页","path":"/dashboard"}]}', encoding="utf-8")
            (root / ".xcodeagent/specs/ui-designs.json").write_text('{"schema_version":"ui-manifest.v3","confirmation_status":"skipped"}', encoding="utf-8")
            (root / ".xcodeagent/plans/technical-plan.json").write_text('{"artifact_type":"technical-plan","confirmation_status":"confirmed","agent_contracts":[]}', encoding="utf-8")
            manifest = prepare_application_template_generation(root, self._download("main"))
            validate_application_template_generation(root)
            self.assertEqual(manifest["templateVariant"], "main")
            self.assertTrue((root / "frontend/src/pages/Dashboard/index.tsx").is_file())
            self.assertIn('key: "Dashboard"', (root / "frontend/src/constants/menus.ts").read_text(encoding="utf-8"))

    def test_required_agent_runtime_is_verified_before_readiness(self) -> None:
        """含 Agent 的已确认 TechnicalPlan 必须下载并校验指定 Runtime Git 模板。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            (root / ".xcodeagent/plans/technical-plan.json").write_text(
                '{"artifact_type":"technical-plan","confirmation_status":"confirmed","agent_contracts":[{"agentId":"assistant"}]}',
                encoding="utf-8",
            )
            commit_sha = self._agent_runtime(root)
            download = self._download()
            download["targets"]["agentRuntime"] = {
                "required": True,
                "status": "succeeded",
                "attempt": 1,
                "path": "agent-runtime",
                "repositoryUrl": AGENT_RUNTIME_TEMPLATE_REPOSITORY_URL,
                "branch": "master",
                "commitSha": commit_sha,
            }

            manifest = prepare_application_template_generation(root, download)
            validate_application_template_generation(root)

            self.assertTrue(inspect_template_generation_readiness(root)["ready"])
            self.assertTrue(
                manifest["steps"]["download"]["targets"]["agentRuntime"]["required"]
            )

    def test_agent_runtime_required_flag_cannot_bypass_technical_plan(self) -> None:
        """Renderer 不能把含 Agent 的正式计划伪装成不需要 Runtime。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            (root / ".xcodeagent/plans/technical-plan.json").write_text(
                '{"artifact_type":"technical-plan","confirmation_status":"confirmed","agent_contracts":[{"agentId":"assistant"}]}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ApplicationTemplateGenerationError,
                "agentRuntime.required.*不一致",
            ):
                prepare_application_template_generation(root, self._download())

    def test_deletion_fence_rejects_new_template_writes(self) -> None:
        """应用开始删除后不得再次执行模板初始化或完成门禁写入。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._workspace(root)
            begin_application_template_deletion(root)

            with self.assertRaisesRegex(ApplicationTemplateGenerationError, "正在删除"):
                prepare_application_template_generation(root, self._download())


if __name__ == "__main__":
    unittest.main()
