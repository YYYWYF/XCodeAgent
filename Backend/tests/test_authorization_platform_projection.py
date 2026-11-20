from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.authorization_edd import verify_authorization_edd
from app.services.authorization_platform_projection import PlatformProjectionError, apply_platform_projections
from app.services.runtime_endpoint_contracts import materialize_runtime_endpoints
from app.services.runtime_execution_policy import BEGIN, END, POLICY_PATH, execution_policy_operation, verify_execution_policy
from tests.test_runtime_endpoint_contracts import business, plan as runtime_plan


class AuthorizationPlatformProjectionTests(unittest.TestCase):
    """验证显式前端路由和后端常量由平台统一投影。"""

    def test_records_frontend_and_backend_diffs(self) -> None:
        """首次生成两处平台文件，重复执行不再产生差异。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_template(workspace)
            first = apply_platform_projections(workspace, self._plan())
            second = apply_platform_projections(workspace, self._plan())

        self.assertEqual(first["summary"]["files"], 2)
        self.assertEqual(second["summary"]["files"], 0)

    def test_edd_reports_resource_drift_without_rewrite(self) -> None:
        """EDD 仅报告权限资源漂移，不能通过重写掩盖失败。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self._write_template(workspace)
            plan = self._plan()
            apply_platform_projections(workspace, plan)
            resource_file = workspace / "frontend/src/constants/resources.ts"
            resource_file.write_text("// drift", encoding="utf-8")
            errors = verify_authorization_edd(workspace, plan)

        self.assertTrue(any("权限共享投影 EDD 失败" in error for error in errors), errors)

    def test_standalone_business_endpoint_finalizes_before_agent_implementation(self) -> None:
        """独立查询构建可先完成，既不发布模板 Agent 的策略，也不改变正式启动校验。"""
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            formal = self._write_runtime_template(workspace)
            scope = {"type": "endpoint", "apiContractId": "order_api", "targetId": "order.list"}
            dag = {"build_execution_scope": scope, "runtime_execution_policy": execution_policy_operation(formal, scope)}
            policy = workspace / POLICY_PATH
            original = policy.read_bytes()

            evidence = apply_platform_projections(workspace, dag)

            self.assertEqual(evidence["runtime"], {"status": "not_required"})
            self.assertEqual(evidence["files"], [])
            self.assertEqual(policy.read_bytes(), original)
            with self.assertRaisesRegex(ValueError, "正式合同以外"):
                verify_execution_policy(workspace, formal)
            with self.assertRaisesRegex(PlatformProjectionError, "已确认 Pending 来源不一致"):
                apply_platform_projections(workspace, {**dag, "runtime_execution_policy": None})

    def test_agent_endpoint_still_rejects_template_chat(self) -> None:
        """Endpoint scope 涉及正式 Agent 时，仍需通过原注册和策略发布校验。"""
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            formal = self._write_runtime_template(workspace)
            scope = {"type": "endpoint", "apiContractId": "agent.order_assistant", "targetId": "agent.order_assistant.run"}
            dag = {"build_execution_scope": scope, "runtime_execution_policy": execution_policy_operation(formal, scope)}

            with self.assertRaisesRegex(PlatformProjectionError, "正式合同以外"):
                apply_platform_projections(workspace, dag)

    def _write_runtime_template(self, workspace: Path) -> dict:
        """创建已有正式规划、但仍处于模板初始注册状态的 Runtime 应用。"""
        source = runtime_plan()
        source["api_contracts"].append(business())
        formal = {**materialize_runtime_endpoints(source), "confirmation_status": "confirmed"}
        self._write(workspace / ".devagentstudio/plans/technical-plan.json", json.dumps(formal))
        self._write(workspace / "agent-runtime/src/app/agent/factory.py", '_AGENT_BUILDERS = {"chat": _create_chat_agent}\n')
        self._write(workspace / POLICY_PATH, BEGIN + "POLICIES_JSON = '{}'\n" + END + "\n")
        return formal

    def _plan(self) -> dict:
        """构造最小确认 Build DAG 权限投影。"""

        return {
            "authorization_frontend_projection": {
                "resources": [
                    {"group": "SYSTEM", "name": "AUTHORIZATION_MANAGEMENT", "resourceKey": "system_authorization_management"},
                    {"group": "PAGE", "name": "ORDERS", "resourceKey": "orders"},
                    {"group": "OPERATION", "name": "ORDERS_APPROVE", "resourceKey": "orders_approve"},
                ],
                "routeDecorations": [{"pageId": "orders", "resourceKey": "orders"}],
            },
            "authorization_constants_projection": [{"name": "ORDERS_APPROVE_RESOURCE", "resourceKey": "orders_approve"}],
        }

    def _write_template(self, workspace: Path) -> None:
        """创建带固定业务路由托管区的最小 auth 模板。"""

        self._write(workspace / ".devagentstudio/template-state.json", json.dumps({
            "schemaVersion": 3,
            "templateRevision": "2026.11.13.2",
            "requested": {"authorization": {"enabled": True, "config": {}}},
            "effective": {"authorization": {"enabled": True, "config": {}}},
            "installedArtifacts": {},
            "managedContributions": {},
        }))
        self._write(workspace / "frontend/src/constants/resources.ts", "export const RESOURCES = {} as const;\n")
        self._write(workspace / "backend/src/main/java/com/cmbchina/backend/auth/domain/constant/AuthConstants.java", "// DEVAGENTSTUDIO_AUTH_CONSTANTS_START\n// DEVAGENTSTUDIO_AUTH_CONSTANTS_END\n")

    def _write(self, path: Path, content: str) -> None:
        """创建测试用 UTF-8 文件。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
