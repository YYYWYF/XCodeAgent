"""以受控 Engine ZIP fixture 覆盖 V2 login → authorization 的端到端收敛。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.services.template_reconcile.runtime_v2 import ReconcileAttemptV2, persist_prepared_attempt, update_attempt
from app.services.template_reconcile.service import (
    TemplateReconcileService,
    _state_digest,
    reconcile_mode_for_requested_config,
)
from app.services.template_reconcile.state_v2 import load_template_state_v2, write_template_state_v2
from app.services.workspace_bootstrap.models import TemplatePackageDownload


def _state(capabilities: list[str]) -> dict[str, object]:
    """构造 login 或 login+authorization 的最小 V2 Capability State。"""

    enabled = {name: {"enabled": True, "config": {}} for name in capabilities}
    return {"schemaVersion": 2, "templateRevision": "r1", "releaseDigest": "sha256:" + "a" * 64, "requested": enabled, "effective": enabled, "appliedAdditions": {}}


class TemplateReconcileV2E2ETests(unittest.TestCase):
    """确认 V2 生产编排不再走旧 ChangeSet、Git rollback 或 Health 路径。"""

    def test_product_mode_uses_apply_for_requested_change_and_reconcile_for_drift_convergence(self) -> None:
        """产品入口必须用 requested 差异决定 APPLY，并把相同请求路由到 RECONCILE。"""

        current = load_template_state_v2_from(_state(["login"]))
        self.assertEqual(
            "RECONCILE",
            reconcile_mode_for_requested_config(
                current, {"capabilities": {"login": {"enabled": True, "config": {}}}}
            ),
        )
        self.assertEqual(
            "APPLY",
            reconcile_mode_for_requested_config(
                current,
                {"capabilities": {"login": {"enabled": True, "config": {}}, "authorization": {"enabled": True, "config": {}}}},
            ),
        )

    def test_login_to_authorization_apply_then_reconcile_is_idempotent(self) -> None:
        """共享 routes 文件经过 APPLY 与 RECONCILE 后只有一个受管理结构且 State 仅在验收后推进。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            routes = root / "src/routes.tsx"
            routes.write_text("const routes = [\n  // routes\n];\n", encoding="utf-8")
            plan_path = root / ".xcodeagent/plans/technical-plan.json"
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text('{"artifact_type":"technical-plan"}\n', encoding="utf-8")
            plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()
            current = _state(["login"])
            write_template_state_v2(root, load_template_state_v2_from(current))
            apply_zip = _package_zip(root, current, _state(["login", "authorization"]), "APPLY")
            reconcile_zip = _package_zip(root, _state(["login", "authorization"]), _state(["login", "authorization"]), "RECONCILE")
            downloads = [_download(apply_zip), _download(reconcile_zip)]

            class FakeClient:
                """按调用顺序返回受控的 Engine Strategy Package。"""

                def __init__(self, **_kwargs: object) -> None:
                    """接受与真实 Client 相同的构造参数。"""

                async def update(self, *_args: object, **_kwargs: object) -> TemplatePackageDownload:
                    """返回下一份不可变 Package 下载描述。"""

                    return downloads.pop(0)

            service = TemplateReconcileService(_settings())
            with patch("app.services.template_reconcile.service.TemplateEngineClient", FakeClient):
                result = asyncio.run(service.reconcile(root, change_id="c1", requested_config={"capabilities": {"login": {"enabled": True, "config": {}}, "authorization": {"enabled": True, "config": {}}}}, technical_plan_sha256=plan_sha256))
                self.assertEqual("CHANGED", result)
                result = asyncio.run(service.reconcile(root, change_id="c2", requested_config={"capabilities": {"login": {"enabled": True, "config": {}}, "authorization": {"enabled": True, "config": {}}}}, technical_plan_sha256=plan_sha256, mode="RECONCILE"))
                self.assertEqual("CHANGED", result)
            self.assertEqual(1, routes.read_text(encoding="utf-8").count("xcodeagent:authorization-route"))
            self.assertEqual({"login", "authorization"}, set(load_template_state_v2(root).effective))

    def test_finalize_only_marks_attempt_succeeded_without_rewriting_state(self) -> None:
        """State 已等于 next digest 时只允许完成 Attempt，禁止再次调用 State writer。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/routes.tsx").write_text(
                "const routes = [\n  // routes\n  // xcodeagent:authorization-route\n  { path: '/authorization' },\n];\n",
                encoding="utf-8",
            )
            plan_path = root / ".xcodeagent/plans/technical-plan.json"
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text('{"artifact_type":"technical-plan"}\n', encoding="utf-8")
            plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()
            current = _state(["login"])
            next_state = _state(["login", "authorization"])
            write_template_state_v2(root, load_template_state_v2_from(next_state))
            package_path = _package_zip(root, current, next_state, "APPLY")
            content = package_path.read_bytes()
            attempt = ReconcileAttemptV2(
                attempt_id="finalize-only", retry_of=None, operation_type="UPDATE", mode="APPLY",
                protocol_version="2", technical_plan_sha256=plan_sha256, package_id="pkg-APPLY",
                source_revision="r1", package_digest="sha256:" + hashlib.sha256(content).hexdigest(),
                current_state_digest=_state_digest(load_template_state_v2_from(current)),
                next_state_digest=_state_digest(load_template_state_v2_from(next_state)),
                phase="PREPARED", status="RUNNING", started_at="2026-09-10T00:00:00+00:00",
                updated_at="2026-09-10T00:00:00+00:00",
            )
            persist_prepared_attempt(root, attempt, package_path)
            attempt = update_attempt(root, attempt, phase="COMMITTING_STATE")
            service = TemplateReconcileService(_settings())
            with patch("app.services.template_reconcile.service.write_template_state_v2", side_effect=AssertionError("FINALIZE 不得写 State")):
                result = service._recover(root, attempt, load_template_state_v2(root), {}, plan_sha256, "APPLY")
            self.assertEqual("FINALIZED", result)

    def test_explicit_retry_creates_new_attempt_with_retry_of(self) -> None:
        """明确失败后只能经 Retry 创建新 Attempt，并将旧 Attempt 身份写入 retryOf。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = load_template_state_v2_from(_state(["login"]))
            write_template_state_v2(root, current)
            plan_path = root / ".xcodeagent/plans/technical-plan.json"
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text('{"artifact_type":"technical-plan"}\n', encoding="utf-8")
            plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()
            package = root / "failed.zip"
            package.write_bytes(b"failed package")
            failed = ReconcileAttemptV2(
                attempt_id="failed-attempt", retry_of=None, operation_type="UPDATE", mode="APPLY",
                protocol_version="2", technical_plan_sha256=plan_sha256, package_id="failed-package",
                source_revision="r1", package_digest="sha256:" + "a" * 64,
                current_state_digest=_state_digest(current), next_state_digest="sha256:" + "b" * 64,
                phase="PREPARED", status="RUNNING", started_at="2026-09-10T00:00:00+00:00",
                updated_at="2026-09-10T00:00:00+00:00",
            )
            persist_prepared_attempt(root, failed, package)
            failed = update_attempt(root, failed, phase="FAILED", status="FAILED", error_code="VALIDATION_FAILED", error_message="验证失败")
            service = TemplateReconcileService(_settings())
            start = AsyncMock(return_value="RETRIED")
            with patch.object(service, "_start", start):
                result = asyncio.run(service.retry_template_preparation(
                    root,
                    change_id="retry-change",
                    requested_config={"capabilities": {"login": {"enabled": True, "config": {}}}},
                    technical_plan_sha256=plan_sha256,
                    mode="APPLY",
                ))
            self.assertEqual("RETRIED", result)
            self.assertEqual("failed-attempt", start.await_args.kwargs["retry_of"])
            self.assertEqual(failed.attempt_id, start.await_args.kwargs["retry_of"])


def load_template_state_v2_from(value: dict[str, object]):
    """将测试字典严格解析为 V2 State，保持 fixture 与生产协议一致。"""

    from app.services.template_reconcile.protocol_v2 import TemplateStateV2

    return TemplateStateV2.model_validate(value)


def _package_zip(root: Path, current: dict[str, object], next_state: dict[str, object], mode: str) -> Path:
    """生成包含共享 route Strategy 和全 Capability 后置条件的冻结 ZIP fixture。"""

    current_digest = _state_digest(load_template_state_v2_from(current))
    next_digest = _state_digest(load_template_state_v2_from(next_state))
    if mode == "RECONCILE":
        next_digest = current_digest
    payload = "  // xcodeagent:authorization-route\n  { path: '/authorization' },\n"
    descriptor = {"size": len(payload.encode()), "sha256": "sha256:" + hashlib.sha256(payload.encode()).hexdigest()}
    effective = list((next_state["effective"] if isinstance(next_state["effective"], dict) else {}).keys())
    package = {"protocolVersion": "2", "packageId": f"pkg-{mode}", "mode": mode, "sourceRevision": "r1", "currentStateDigest": current_digest, "nextStateDigest": next_digest, "strategies": [{"strategyId": "authorization-route", "index": 0, "schemaVersion": 1, "type": "ENSURE_ROUTE", "target": "src/routes.tsx", "precondition": {}, "parameters": {"astSelector": {"nodeType": "array", "position": "beforeEnd"}, "managedMarker": "xcodeagent:authorization-route", "content": payload}, "payloadRef": None}], "validationPlan": [{"validationId": f"{capability}-post", "index": index, "type": "CAPABILITY_POSTCONDITION", "capabilityId": capability, "workingDirectory": ".", "checks": [{"type": "STRUCTURE_CHECK", "path": "src/routes.tsx", "containsAll": ["xcodeagent:authorization-route"]}], "blocking": True, "timeoutSeconds": 10, "executionMode": "REAL_WORKSPACE"} for index, capability in enumerate(effective)], "payloadManifest": {}, "nextTemplateState": next_state, "diagnostics": []}
    path = root / f"{mode}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("strategy-update-package.json", json.dumps(package))
    return path


def _download(path: Path) -> TemplatePackageDownload:
    """按真实下载对象形状提供 ZIP 摘要。"""

    content = path.read_bytes()
    return TemplatePackageDownload(path, hashlib.sha256(content).hexdigest(), len(content), "application/zip")


def _settings() -> Settings:
    """构造不访问网络的最小 Template Engine 配置。"""

    return Settings(model_base_url="http://model", model_api_key="key", model_name="model", template_engine_base_url="http://engine", template_engine_token="token")
