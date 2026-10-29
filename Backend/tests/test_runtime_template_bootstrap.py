"""验证 Runtime 模板沿当前 Engine 合同的请求、布局、物化与正式就绪边界。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app.services.template_reconcile.protocol_v3 import TemplateStateV3
from app.services.workspace_bootstrap.materializer import WorkspaceMaterializer
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplateConfigError, TemplatePackageError
from app.services.workspace_bootstrap.readiness import validate_workspace_bootstrap_readiness
from app.services.workspace_bootstrap.requested_config import compile_template_requested_config
from app.services.workspace_bootstrap.template_layout import (
    RUNTIME_CAPABILITIES, compile_workspace_template_capabilities,
)
from app.services.workspace_bootstrap.template_package import validate_template_package


def _application() -> dict:
    """返回无登录与授权的当前应用配置，不包含数据库或模型凭据。"""

    return {"schemaVersion": 6, "configRevision": 1, "appName": "政策助手", "appIcon": "AppstoreOutlined",
            "theme": {"primaryColor": "#6b3cf0"}, "datasource": {"type": "database"},
            "auth": {"enable": False}, "authorization": {"enabled": False, "initialAdministratorSubjects": []}}


def _plan() -> dict:
    """返回明确确认的 Direct 服务图；不按实体或目录数量选择拓扑。"""

    return {"artifact_type": "technical-plan", "confirmation_status": "confirmed", "sourceConfigRevision": 1,
            "topology": {"type": "agent_runtime_direct", "serviceIds": ["agent-runtime"],
                         "publicEdgeServiceId": "agent-runtime", "authenticationTermination": "anonymous-session"}}


def _capabilities() -> dict:
    """使用单一编译器获取模板服务应原样回写的实现能力。"""

    return compile_workspace_template_capabilities(Path("unused"), _application(), plan=_plan())


def _state() -> dict:
    """构造当前 V3 状态，不增加 schema 版本或第二份模板状态。"""

    return {"schemaVersion": 3, "templateRevision": "2026.10.28.1",
            "requested": _capabilities(), "effective": _capabilities(),
            "installedArtifacts": {}, "managedContributions": {}}


def _entries() -> dict[str, str]:
    """构造具有真实启动入口、七模块路径和路由描述的候选模板。"""

    entries = {
        "frontend/package.json": "{}",
        "agent-runtime/pyproject.toml": '[project]\nname="agent-runtime"\nversion="0.1.0"\n[project.scripts]\nagent-runtime="app.main:main"\n',
        "agent-runtime/uv.lock": "version = 1\n",
        "agent-runtime/src/app/main.py": "# 只验证静态入口，不启动 Runtime。\n",
        "agent-runtime/tests/test_contract.py": "# fixture\n",
        ".devagentstudio/template-state.json": json.dumps(_state()),
        ".devagentstudio/template-contracts/route-projector.json": '{"schemaVersion":"route-projector-contract.v2"}',
    }
    for relative in (
        "agent/factory.py", "agent/context.py", "models/factory.py", "settings.py",
        "persistence/checkpointer.py", "tools/__init__.py", "interaction/schemas.py", "interaction/service.py",
    ):
        entries[f"agent-runtime/src/app/{relative}"] = "# fixture\n"
    return entries


def _archive(root: Path, entries: dict[str, str]) -> Path:
    """写入指定 ZIP，避免真实模板网络或进程调用。"""

    path = root / "template.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


def _formal_artifacts(root: Path) -> None:
    """准备当前正式确认产物与应用配置，保留原来的确认门。"""

    for name, body in {
        "application.json": _application(), "plans/technical-plan.json": _plan(),
        "specs/requirement-spec.json": {"confirmation_status": "confirmed"},
        "plans/product-plan.json": {"confirmation_status": "confirmed"},
        "specs/ui-designs.json": {"confirmation_status": "skipped"},
    }.items():
        path = root / ".devagentstudio" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body), encoding="utf-8")


class _GitBoundary:
    """替代真实提交命令，检查物化事务在基线与 readiness 之间的时序。"""

    def initialize_baseline(self, workspace: str | Path) -> str:
        """模拟本次创建的 Git 元数据，不写真实用户仓库。"""

        Path(workspace, ".git").mkdir()
        return "fixture"

    def verify_baseline(self, workspace: str | Path) -> str:
        """只有事务已经准备了完整目录与状态才允许验证。"""

        root = Path(workspace)
        if not (root / ".git").is_dir() or not (root / "agent-runtime/pyproject.toml").is_file():
            raise ValueError("fixture baseline missing")
        return "fixture"


class RuntimeTemplateBootstrapTests(unittest.TestCase):
    """覆盖请求权威、双布局隔离、真实入口、状态身份和事务回滚。"""

    def test_runtime_request_uses_existing_engine_envelope(self) -> None:
        """只扩展 capability，不改变 Generate 的现有请求字段或配置来源。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _formal_artifacts(root)
            requested = compile_template_requested_config(root, include_application_config=True)
            self.assertEqual(set(requested), {"capabilities", "applicationConfig"})
            self.assertEqual(set(requested["capabilities"]), set(RUNTIME_CAPABILITIES))
            self.assertIs(requested["applicationConfig"]["auth"]["enable"], False)

    def test_java_request_keeps_current_capabilities(self) -> None:
        """没有 Runtime 选择的当前 Java 应用继续只消费应用能力开关。"""

        application = _application()
        application["auth"]["enable"] = True
        expected = {"login": {"enabled": True, "config": {}}}
        self.assertEqual(compile_workspace_template_capabilities(Path("unused"), application, plan={}), expected)

    def test_runtime_does_not_silently_disable_login(self) -> None:
        """不支持的 Python 登录配置必须拒绝，不能覆盖用户已经开启的能力。"""

        application = _application()
        application["auth"]["enable"] = True
        with self.assertRaisesRegex(TemplateConfigError, "尚未交付"):
            compile_workspace_template_capabilities(Path("unused"), application, plan=_plan())
        self.assertIs(application["auth"]["enable"], True)

    def test_package_rejects_java_root_inside_runtime_layout(self) -> None:
        """Runtime 模板不能夹带 backend 根，避免混合两套启动与生成范围。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = _entries()
            entries["backend/pom.xml"] = "<project/>"
            with self.assertRaisesRegex(TemplatePackageError, "未授权路径"):
                validate_template_package(_archive(root, entries), self._limits(), requested_config={"capabilities": _capabilities()})

    def test_runtime_files_need_runtime_artifact_identity(self) -> None:
        """Runtime Artifact 必须显式归属于已安装能力，不能借 Java State 越权。"""

        state = _state()
        state["installedArtifacts"] = {"agent-runtime:python_application_runtime:src/app/main.py": {
            "capabilityId": "python_application_runtime", "target": "agent-runtime/src/app/main.py",
            "installedRevision": "2026.10.28.1"}}
        self.assertIn("agent-runtime:python_application_runtime:src/app/main.py", TemplateStateV3.model_validate(state).installedArtifacts)
        state["requested"] = {}
        state["effective"] = {}
        with self.assertRaises(ValueError):
            TemplateStateV3.model_validate(state)

    def test_runtime_materializes_then_passes_current_confirmation_gate(self) -> None:
        """Runtime 沿原物化事务提交，保留应用配置、确认门和真实模板字节。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            _formal_artifacts(root)
            archive = _archive(Path(directory), _entries())
            requested = {"capabilities": _capabilities()}
            validated = validate_template_package(archive, self._limits(), requested_config=requested)
            boundary = _GitBoundary()

            def readiness(workspace: Path) -> None:
                """在事务内验证本次 Runtime 与正式事实一致，不提前登记完成。"""
                validate_workspace_bootstrap_readiness(workspace, requested_config=requested, git_manager=boundary)

            WorkspaceMaterializer(git_manager=boundary).materialize(
                workspace=root, archive_path=archive, template_state=validated.template_state, readiness=readiness,
            )
            self.assertTrue((root / "agent-runtime/uv.lock").is_file())
            self.assertFalse((root / "backend").exists())
            self.assertFalse((root / ".devagentstudio/bootstrap-staging").exists())
            self.assertEqual(json.loads((root / ".devagentstudio/application.json").read_text()), _application())

    def test_missing_entrypoint_rolls_back_roots_and_preserves_formal_inputs(self) -> None:
        """缺少锁文件导致 readiness 失败时，不能遗留半个 Runtime 或清除规划。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            _formal_artifacts(root)
            entries = _entries()
            del entries["agent-runtime/uv.lock"]
            archive = _archive(Path(directory), entries)
            requested = {"capabilities": _capabilities()}
            validated = validate_template_package(archive, self._limits(), requested_config=requested)

            def readiness(workspace: Path) -> None:
                """用生产 readiness 证明空壳 Runtime 不能提交成功。"""
                validate_workspace_bootstrap_readiness(workspace, requested_config=requested, git_manager=_GitBoundary())

            with self.assertRaisesRegex(ValueError, "uv.lock"):
                WorkspaceMaterializer(git_manager=_GitBoundary()).materialize(
                    workspace=root, archive_path=archive, template_state=validated.template_state, readiness=readiness,
                )
            self.assertFalse((root / "agent-runtime").exists())
            self.assertFalse((root / "frontend").exists())
            self.assertFalse((root / ".git").exists())
            self.assertTrue((root / ".devagentstudio/plans/technical-plan.json").is_file())

    def test_readiness_rejects_runtime_request_without_formal_runtime_selection(self) -> None:
        """调用方提交 Runtime capability 不能绕过当前技术规划的显式选择。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _formal_artifacts(root)
            plan = _plan()
            del plan["topology"]
            (root / ".devagentstudio/plans/technical-plan.json").write_text(json.dumps(plan), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "当前正式规划"):
                validate_workspace_bootstrap_readiness(root, requested_config={"capabilities": _capabilities()})

    def _limits(self) -> ArchiveLimits:
        """为测试夹具设置有限 ZIP 配额，不放宽生产限额。"""

        return ArchiveLimits(max_package_bytes=1024 * 1024, max_files=40, max_extracted_bytes=1024 * 1024)
