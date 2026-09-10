"""执行当前唯一支持的 Template Capability Reconcile V2 编排。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from app.config import Settings
from app.services.template_reconcile.executor_v2 import ModificationStrategyExecutorV2, WorkingCopyStoreV2, apply_working_copy_v2, restore_working_copy_v2
from app.services.template_reconcile.protocol_v2 import StrategyUpdatePackageV2, TemplateStateV2, assert_reconcile_state_invariant_v2
from app.services.template_reconcile.runtime_v2 import ReconcileAttemptV2, load_current_attempt, persist_prepared_attempt, reconcile_run_gate, reconcile_v2_root, recovery_action, update_attempt
from app.services.template_reconcile.state_v2 import load_template_state_v2, write_template_state_v2
from app.services.template_reconcile.strategy_update_package import ValidatedStrategyUpdatePackage, validate_strategy_update_package
from app.services.template_reconcile.validation_v2 import execute_reconcile_validation_v2, execute_validation_plan_v2, validation_plan_passed_v2
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplateStateError
from app.services.workspace_bootstrap.template_engine_client import TemplateEngineClient


class TemplateReconcileService:
    """以 V2-only Strategy Package 收敛 Capability，仅在 Validation 成功后提交 State。"""

    def __init__(self, settings: Settings) -> None:
        """保存 Template Engine 连接与 ZIP 安全限额配置。"""

        self._settings = settings

    async def reconcile(self, workspace: str | Path, *, change_id: str, requested_config: dict[str, Any], technical_plan_sha256: str, mode: Literal["APPLY", "RECONCILE"] = "APPLY") -> str:
        """执行单调用 V2 更新，或基于 durable Attempt 完成唯一 Roll-forward 恢复。"""

        if not self._settings.template_reconcile_enabled:
            raise TemplateStateError("TEMPLATE_RECONCILE_DISABLED：模板能力增量更新功能尚未开启。")
        root = Path(workspace).expanduser().resolve()
        with reconcile_run_gate(root):
            current = load_template_state_v2(root)
            requested = _normalized_requested_config(requested_config)
            if mode == "RECONCILE" and requested != _requested_from_state(current):
                raise TemplateStateError("RECONCILE_REQUESTED_CONFIG_MISMATCH：requestedConfig 与当前 TemplateState.requested 不一致。")
            attempt = load_current_attempt(root)
            if attempt is not None and attempt.status != "SUCCEEDED":
                return self._recover(root, attempt, current, requested)
            return await self._start(root, current, requested_config, technical_plan_sha256, mode, change_id)

    async def _start(self, root: Path, current: TemplateStateV2, requested_config: dict[str, Any], plan_sha: str, mode: Literal["APPLY", "RECONCILE"], _change_id: str) -> str:
        """下载、校验并先持久化 immutable Package，204 不创建虚假 Attempt。"""

        client = TemplateEngineClient(base_url=self._settings.template_engine_base_url, token=self._settings.template_engine_token, connect_timeout=self._settings.template_engine_connect_timeout_seconds, read_timeout=self._settings.template_engine_read_timeout_seconds, max_package_bytes=self._settings.template_package_max_bytes)
        download = await client.update(current.model_dump(mode="json"), requested_config, mode=mode)
        if download is None:
            return "NO_CHANGE"
        try:
            validated = validate_strategy_update_package(download.temporary_path, self._limits())
            _validate_binding(current, validated.package, mode)
            attempt = ReconcileAttemptV2(attempt_id=uuid4().hex, retry_of=None, operation_type="UPDATE", mode=mode, protocol_version="2", technical_plan_sha256=plan_sha, package_id=validated.package.packageId, package_digest="sha256:" + download.sha256, current_state_digest=validated.package.currentStateDigest, next_state_digest=validated.package.nextStateDigest, phase="PREPARED", status="RUNNING", started_at=_now(), updated_at=_now())
            persist_prepared_attempt(root, attempt, download.temporary_path)
            return self._execute(root, attempt, validated, current)
        finally:
            download.temporary_path.unlink(missing_ok=True)

    def _recover(self, root: Path, attempt: ReconcileAttemptV2, current: TemplateStateV2, requested: dict[str, Any]) -> str:
        """按 digest 唯一选择重放或最终验收，状态冲突时失败关闭。"""

        if attempt.status == "SUCCEEDED":
            return "ALREADY_RECONCILED"
        if attempt.mode == "RECONCILE" and requested != _requested_from_state(current):
            raise TemplateStateError("RECONCILE_REQUESTED_CONFIG_MISMATCH：恢复请求与 State.requested 不一致。")
        action = recovery_action(attempt, _state_digest(current))
        package_path = reconcile_v2_root(root) / "attempts" / attempt.attempt_id / "update-package.zip"
        validated = validate_strategy_update_package(package_path, self._limits())
        if action == "CONFLICT":
            update_attempt(root, attempt, phase="RECOVERY_REQUIRED", status="FAILED", error_code="RECOVERY_STATE_CONFLICT", error_message="当前 TemplateState 与未完成 Attempt 不匹配。")
            raise TemplateStateError("RECOVERY_STATE_CONFLICT：无法安全恢复模板更新。")
        if action == "FINALIZE":
            if not validation_plan_passed_v2(self._validate(root, validated.package, current)):
                update_attempt(root, attempt, phase="FAILED", status="FAILED", error_code="VALIDATION_FAILED", error_message="Roll-forward Validation 未通过。")
                raise TemplateStateError("VALIDATION_FAILED：Template Preparation 验收未通过。")
            return self._commit(root, attempt, validated.package.nextTemplateState)
        return self._execute(root, attempt, validated, current)

    def _execute(self, root: Path, attempt: ReconcileAttemptV2, validated: ValidatedStrategyUpdatePackage, current: TemplateStateV2) -> str:
        """在一个 Working Copy 完成 Strategy、Apply、Validation 与最终 State Commit。"""

        store = WorkingCopyStoreV2(root)
        attempt = update_attempt(root, attempt, phase="APPLYING")
        try:
            package = validated.package
            ModificationStrategyExecutorV2().execute(package.strategies, store, _payloads(validated))
            apply_working_copy_v2(root, store)
            attempt = update_attempt(root, attempt, phase="VALIDATING")
            if not validation_plan_passed_v2(self._validate(root, package, current)):
                raise TemplateStateError("VALIDATION_FAILED：Template Preparation 验收未通过。")
            return self._commit(root, attempt, package.nextTemplateState)
        except Exception as exc:
            restore_working_copy_v2(root, store)
            update_attempt(root, attempt, phase="FAILED", status="FAILED", error_code=_error_code(exc), error_message=str(exc)[:2048])
            raise

    def _validate(self, root: Path, package: StrategyUpdatePackageV2, current: TemplateStateV2):
        """按 Package mode 运行唯一的 Validation Plan 权威验收。"""

        return execute_reconcile_validation_v2(root, package, current) if package.mode == "RECONCILE" else execute_validation_plan_v2(root, package.validationPlan)

    def _commit(self, root: Path, attempt: ReconcileAttemptV2, state: TemplateStateV2) -> str:
        """将 Attempt 置于提交临界区后原子写 State，再记录成功终态。"""

        attempt = update_attempt(root, attempt, phase="COMMITTING_STATE")
        write_template_state_v2(root, state)
        update_attempt(root, attempt, phase="SUCCEEDED", status="SUCCEEDED")
        return "CHANGED"

    def _limits(self) -> ArchiveLimits:
        """把 Settings 映射为 V2 ZIP 校验限额。"""

        return ArchiveLimits(self._settings.template_package_max_bytes, self._settings.template_package_max_files, self._settings.template_package_max_extracted_bytes)


def _normalized_requested_config(value: dict[str, Any]) -> dict[str, Any]:
    """归一 Engine requestedConfig，供 RECONCILE 严格比较当前 requested。"""

    capabilities = value.get("capabilities")
    if not isinstance(capabilities, dict):
        raise TemplateStateError("requestedConfig.capabilities 必须是对象。")
    normalized: dict[str, Any] = {}
    for key, raw in capabilities.items():
        if not isinstance(key, str) or not key or not isinstance(raw, dict) or raw.get("enabled") is not True:
            raise TemplateStateError("requestedConfig 包含无效 Capability。")
        config = raw.get("config") or {}
        if not isinstance(config, dict):
            raise TemplateStateError("Capability config 必须是对象。")
        normalized[key] = {"enabled": True, "config": config}
    return normalized


def _requested_from_state(state: TemplateStateV2) -> dict[str, Any]:
    """提取 TemplateState.requested 的 JSON 语义表示，避免比较 Pydantic 实例身份。"""

    return {
        capability_id: capability.model_dump(mode="json")
        for capability_id, capability in state.requested.items()
    }


def _validate_binding(current: TemplateStateV2, package: StrategyUpdatePackageV2, mode: str) -> None:
    """要求 Service Package 的模式和 currentStateDigest 均绑定本地 State。"""

    if package.mode != mode or package.currentStateDigest != _state_digest(current):
        raise TemplateStateError("TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED：Strategy Package 未绑定当前 State。")
    if mode == "RECONCILE":
        assert_reconcile_state_invariant_v2(current, package.nextTemplateState)


def _payloads(validated: ValidatedStrategyUpdatePackage) -> dict[str, str]:
    """读取已验证 ZIP 的文本 payload，拒绝二进制 Strategy 内容。"""

    try:
        with zipfile.ZipFile(validated.archive_path) as archive:
            return {ref: archive.read(ref).decode("utf-8") for ref in validated.package.payloadManifest}
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise TemplateStateError("Strategy Package payload 无法读取为 UTF-8 文本。") from exc


def _state_digest(state: TemplateStateV2) -> str:
    """计算冻结 JSON State digest，作为 Package binding 与恢复的唯一比较值。"""

    data = json.dumps(state.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _error_code(exc: Exception) -> str:
    """提取可展示协议码，未知异常统一收敛为执行失败码。"""

    return str(exc).split("：", 1)[0] if "：" in str(exc) else "TEMPLATE_RECONCILE_EXECUTION_FAILED"


def _now() -> str:
    """生成 Attempt 审计时间。"""

    return datetime.now(timezone.utc).isoformat()
