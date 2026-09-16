from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.workspace_bootstrap.models import TemplateConfigError
from app.services.workspace_bootstrap.requested_config import (
    bootstrap_managed_roots,
    compile_template_requested_config,
)


class RequestedConfigTests(unittest.TestCase):
    """验证 application.json 到 Engine RequestedConfig 的唯一映射边界。"""

    def test_compiles_enabled_capabilities_from_application_config(self) -> None:
        """确认请求只读取 application.json 中已校验的能力开关。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, login=True, authorization=True)
            self.assertEqual(
                compile_template_requested_config(root)["capabilities"],
                {"login": {"enabled": True, "config": {}}, "authorization": {"enabled": True, "config": {}}},
            )

    def test_rejects_technical_plan_from_an_older_configuration_revision(self) -> None:
        """当前 application.json 已变化时，旧 TechnicalPlan 不得驱动模板请求。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, login=True, authorization=True)
            application_file = root / ".xcodeagent" / "application.json"
            application = json.loads(application_file.read_text(encoding="utf-8"))
            application["configRevision"] = 2
            application["authorization"]["enabled"] = False
            application["authorization"]["initialAdministratorSubjects"] = []
            application_file.write_text(json.dumps(application), encoding="utf-8")
            with self.assertRaisesRegex(TemplateConfigError, "过期 application.json"):
                compile_template_requested_config(root)

    def test_includes_agent_runtime_only_for_confirmed_agent_contracts(self) -> None:
        """确认第三根只由 TechnicalPlan 的正式 Agent Contract 决定。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, login=False, authorization=False)
            plan_file = root / ".xcodeagent/plans/technical-plan.json"
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
            self.assertEqual(
                bootstrap_managed_roots(root), ("frontend", "backend")
            )
            plan["agent_contracts"] = [{"agentId": "policy_assistant"}]
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            self.assertEqual(
                bootstrap_managed_roots(root),
                ("frontend", "backend", "agent-runtime"),
            )

    def _write(self, root: Path, *, login: bool, authorization: bool) -> None:
        """写入最小且正式的 Application 与 confirmed TechnicalPlan fixture。"""

        xcodeagent = root / ".xcodeagent"
        (xcodeagent / "plans").mkdir(parents=True, exist_ok=True)
        (xcodeagent / "application.json").write_text(
            json.dumps({"schemaVersion": 6, "configRevision": 1, "datasource": {"type": "database"}, "auth": {"enable": login}, "authorization": {"enabled": authorization, "initialAdministratorSubjects": ["ops@example.com"] if authorization else []}}),
            encoding="utf-8",
        )
        (xcodeagent / "plans/technical-plan.json").write_text(
            json.dumps({"artifact_type": "technical-plan", "confirmation_status": "confirmed", "sourceConfigRevision": 1, "authorization_manifest": {}, "agent_contracts": []}),
            encoding="utf-8",
        )
