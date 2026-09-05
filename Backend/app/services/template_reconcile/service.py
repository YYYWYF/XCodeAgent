"""执行当前唯一支持的 Template Capability Reconcile V2 编排。"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from app.config import Settings
from app.services.artifact_invalidation import ArtifactInvalidationError, canonical_sha256
from app.services.template_reconcile.digest_v2 import package_digest_v2, template_state_digest_v2
from app.services.template_reconcile.executor_v2 import ModificationStrategyExecutorV2, WorkingCopyStoreV2, apply_working_copy_v2, restore_working_copy_v2
from app.services.template_reconcile.protocol_v2 import StrategyUpdatePackageV2, TemplateStateV2, assert_reconcile_state_invariant_v2
from app.services.template_reconcile.runtime_v2 import ReconcileAttemptV2, load_current_attempt, persist_prepared_attempt, reconcile_run_gate, reconcile_v2_root, recovery_action, update_attempt
from app.services.template_reconcile.state_v2 import load_template_state_v2, write_template_state_v2
from app.services.template_reconcile.strategy_update_package import ValidatedStrategyUpdatePackage, validate_strategy_update_package
from app.services.template_reconcile.validation_v2 import ValidationResultV2, execute_reconcile_validation_v2, execute_validation_plan_v2, validation_plan_passed_v2
from app.services.project_launcher import inspect_project_preview, launch_project_preview, stop_standard_project_preview
from app.utils.atomic_json import atomic_write_json
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
            if attempt is not None and attempt.status == "RUNNING":
                return self._recover(root, attempt, current, requested, technical_plan_sha256, mode)
            if attempt is not None and attempt.status == "FAILED":
                raise TemplateStateError("TEMPLATE_RECONCILE_RETRY_REQUIRED：上一 Attempt 已失败，必须创建新的 Retry Attempt。")
            return await self._start(root, current, requested_config, technical_plan_sha256, mode, change_id, retry_of=None)

    async def retry_template_preparation(self, workspace: str | Path, *, change_id: str, requested_config: dict[str, Any], technical_plan_sha256: str, mode: Literal["APPLY", "RECONCILE"]) -> str:
        """针对已明确失败的 Attempt 下载新 Package 并创建带 retryOf 的新 Attempt。"""

        if not self._settings.template_reconcile_enabled:
            raise TemplateStateError("TEMPLATE_RECONCILE_DISABLED：模板能力增量更新功能尚未开启。")
        root = Path(workspace).expanduser().resolve()
        with reconcile_run_gate(root):
            current = load_template_state_v2(root)
            requested = _normalized_requested_config(requested_config)
            if mode == "RECONCILE" and requested != _requested_from_state(current):
                raise TemplateStateError("RECONCILE_REQUESTED_CONFIG_MISMATCH：requestedConfig 与当前 TemplateState.requested 不一致。")
            previous = load_current_attempt(root)
            if previous is None or previous.status != "FAILED" or previous.phase != "FAILED":
                raise TemplateStateError("TEMPLATE_RECONCILE_RETRY_NOT_ALLOWED：只有明确失败的 Attempt 可以创建 Retry。")
            return await self._start(root, current, requested_config, technical_plan_sha256, mode, change_id, retry_of=previous.attempt_id)

    async def _start(self, root: Path, current: TemplateStateV2, requested_config: dict[str, Any], plan_sha: str, mode: Literal["APPLY", "RECONCILE"], _change_id: str, *, retry_of: str | None) -> str:
        """下载、校验并先持久化 immutable Package，204 不创建虚假 Attempt。"""

        _assert_technical_plan_binding(root, plan_sha)
        client = TemplateEngineClient(base_url=self._settings.template_engine_base_url, token=self._settings.template_engine_token, connect_timeout=self._settings.template_engine_connect_timeout_seconds, read_timeout=self._settings.template_engine_read_timeout_seconds, max_package_bytes=self._settings.template_package_max_bytes)
        download = await client.update(current.model_dump(mode="json"), requested_config, mode=mode)
        if download is None:
            return "NO_CHANGE"
        try:
            validated = validate_strategy_update_package(download.temporary_path, self._limits())
            _validate_binding(current, validated.package, mode)
            attempt = ReconcileAttemptV2(attempt_id=uuid4().hex, retry_of=retry_of, operation_type="UPDATE", mode=mode, protocol_version="2", technical_plan_sha256=plan_sha, package_id=validated.package.packageId, source_revision=validated.package.sourceRevision, package_digest="sha256:" + download.sha256, current_state_digest=validated.package.currentStateDigest, next_state_digest=validated.package.nextStateDigest, phase="PREPARED", status="RUNNING", started_at=_now(), updated_at=_now())
            persist_prepared_attempt(root, attempt, download.temporary_path)
            return self._execute(root, attempt, validated, current)
        finally:
            download.temporary_path.unlink(missing_ok=True)

    def _recover(self, root: Path, attempt: ReconcileAttemptV2, current: TemplateStateV2, requested: dict[str, Any], technical_plan_sha256: str, mode: Literal["APPLY", "RECONCILE"]) -> str:
        """按冻结 ZIP、Package、Attempt、State 与技术规划的顺序安全决定恢复分支。"""

        if attempt.status == "SUCCEEDED":
            return "ALREADY_RECONCILED"
        if attempt.mode == "RECONCILE" and requested != _requested_from_state(current):
            raise TemplateStateError("RECONCILE_REQUESTED_CONFIG_MISMATCH：恢复请求与 State.requested 不一致。")
        if attempt.mode != mode:
            raise TemplateStateError("RECOVERY_OPERATION_MISMATCH：恢复请求模式与未完成 Attempt 不一致。")
        package_path = reconcile_v2_root(root) / "attempts" / attempt.attempt_id / "update-package.zip"
        if package_digest_v2(package_path) != attempt.package_digest:
            update_attempt(root, attempt, phase="RECOVERY_REQUIRED", status="FAILED", error_code="PACKAGE_DIGEST_MISMATCH", error_message="immutable Package 摘要不匹配。")
            raise TemplateStateError("PACKAGE_DIGEST_MISMATCH：无法安全恢复模板更新。")
        validated = validate_strategy_update_package(package_path, self._limits())
        _assert_attempt_package_binding(attempt, validated.package)
        current_digest = _state_digest(current)
        _assert_technical_plan_binding(root, technical_plan_sha256, attempt)
        action = recovery_action(attempt, current_digest)
        if action == "CONFLICT":
            update_attempt(root, attempt, phase="RECOVERY_REQUIRED", status="FAILED", error_code="RECOVERY_STATE_CONFLICT", error_message="当前 TemplateState 与未完成 Attempt 不匹配。")
            raise TemplateStateError("RECOVERY_STATE_CONFLICT：无法安全恢复模板更新。")
        if action == "FINALIZE":
            results, attempt = self._validate(root, validated.package, current, attempt)
            if not validation_plan_passed_v2(results):
                message = _validation_failure_message(results)
                update_attempt(root, attempt, phase="FAILED", status="FAILED", error_code=_validation_error_code(results), error_message=message)
                raise TemplateStateError(message)
            # nextState 已是唯一 State 事实；FINALIZE 只能收口 Attempt，绝不能再次写 State。
            update_attempt(root, attempt, phase="SUCCEEDED", status="SUCCEEDED")
            return "FINALIZED"
        return self._execute(root, attempt, validated, current)

    def _execute(self, root: Path, attempt: ReconcileAttemptV2, validated: ValidatedStrategyUpdatePackage, current: TemplateStateV2) -> str:
        """在一个 Working Copy 完成 Strategy、Apply、Validation 与最终 State Commit。"""

        store = WorkingCopyStoreV2(root)
        pre_launch_state = inspect_project_preview(root)
        results: list[ValidationResultV2] = []
        attempt = update_attempt(root, attempt, phase="APPLYING")
        try:
            package = validated.package
            ModificationStrategyExecutorV2().execute(package.strategies, store, _payloads(validated))
            apply_working_copy_v2(root, store)
            attempt = update_attempt(root, attempt, phase="VALIDATING")
            results, attempt = self._validate(root, package, current, attempt)
            if not validation_plan_passed_v2(results):
                raise TemplateStateError(_validation_failure_message(results))
            return self._commit(root, attempt, package.nextTemplateState)
        except StateCommittedV2Error:
            # State 已成为权威事实，后续只能由 digest 驱动 Roll-forward，禁止恢复 Workspace。
            raise
        except Exception as exc:
            recovery_error = self._restore_after_failed_acceptance(
                root,
                store,
                pre_launch_state,
                restart_attempted=any(item.validation_id == "PROJECT_RESTART" for item in results),
            )
            error_code = _error_code(exc)
            message = str(exc)
            if recovery_error is not None:
                error_code = recovery_error[0]
                message = f"{message}；{recovery_error[1]}"
            update_attempt(root, attempt, phase="FAILED", status="FAILED", error_code=error_code, error_message=message[:2048])
            raise TemplateStateError(message) from exc

    def _validate(self, root: Path, package: StrategyUpdatePackageV2, current: TemplateStateV2, attempt: ReconcileAttemptV2) -> tuple[list[ValidationResultV2], ReconcileAttemptV2]:
        """先验证静态后置条件，再强制重启真实工程完成运行态验收。"""

        if package.mode == "RECONCILE":
            results = execute_reconcile_validation_v2(root, package, current, attempt_id=attempt.attempt_id)
        else:
            results = execute_validation_plan_v2(root, package.validationPlan, attempt_id=attempt.attempt_id)
        if validation_plan_passed_v2(results):
            attempt = update_attempt(root, attempt, phase="VALIDATING", event_message="正在重新启动项目进行模板更新验证。")

            def report(stage: str, status: str, message: str) -> None:
                """把 Project Launcher 的细粒度进度追加到当前 Attempt。"""

                nonlocal attempt
                attempt = update_attempt(root, attempt, phase="VALIDATING", event_message=f"{stage}/{status}：{message}")

            launch = launch_project_preview(root, force_restart=True, on_progress=report)
            results.append(_project_restart_result(launch))
        atomic_write_json(reconcile_v2_root(root) / "attempts" / attempt.attempt_id / "validation-results.json",
                          {"checks": [item.__dict__ for item in results]})
        return results, attempt

    def _restore_after_failed_acceptance(self, root: Path, store: WorkingCopyStoreV2, pre_launch_state: dict[str, Any], *, restart_attempted: bool) -> tuple[str, str] | None:
        """按运行态清理、文件回滚、旧运行态恢复的顺序补偿失败的模板更新。"""

        recovery_error: tuple[str, str] | None = None
        if restart_attempted:
            cleanup = stop_standard_project_preview(root)
            if cleanup.get("status") == "failed":
                recovery_error = "ROLLBACK_RESTORE_FAILED", "无法停止失败的新版本 standard preview。"
        try:
            restore_working_copy_v2(root, store)
        except Exception as exc:
            return "ROLLBACK_RESTORE_FAILED", f"无法恢复模板文件：{exc}"
        if pre_launch_state.get("running"):
            previous = launch_project_preview(root, force_restart=True)
            if previous.get("status") != "running":
                return "PREVIOUS_RUNTIME_RESTORE_FAILED", "模板文件已恢复，但原项目运行态恢复失败。"
        return recovery_error

    def _commit(self, root: Path, attempt: ReconcileAttemptV2, state: TemplateStateV2) -> str:
        """将 Attempt 置于提交临界区后原子写 State，再记录成功终态。"""

        attempt = update_attempt(root, attempt, phase="COMMITTING_STATE")
        try:
            write_template_state_v2(root, state)
        except Exception as exc:
            # rename 后抛错时必须以磁盘事实判定；只要 nextState 已生效就绝不能回滚 Workspace。
            if _state_digest_after_write_error(root) == _state_digest(state):
                raise StateCommittedV2Error("RECONCILE_STATE_COMMITTED_PENDING：State 已提交，必须 Roll-forward finalize。") from exc
            raise
        try:
            update_attempt(root, attempt, phase="SUCCEEDED", status="SUCCEEDED")
        except Exception as exc:
            raise StateCommittedV2Error("RECONCILE_FINALIZE_PENDING：State 已提交，必须 Roll-forward finalize。") from exc
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

    if package.mode != mode or package.sourceRevision != current.templateRevision or package.currentStateDigest != _state_digest(current):
        raise TemplateStateError("TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED：Strategy Package 未绑定当前 State。")
    if mode == "RECONCILE":
        assert_reconcile_state_invariant_v2(current, package.nextTemplateState)


def _assert_attempt_package_binding(attempt: ReconcileAttemptV2, package: StrategyUpdatePackageV2) -> None:
    """要求已解析 Package 的全部可比冻结字段与 durable Attempt 完全一致。"""

    expected = {
        "protocolVersion": attempt.protocol_version,
        "packageId": attempt.package_id,
        "mode": attempt.mode,
        "sourceRevision": attempt.source_revision,
        "currentStateDigest": attempt.current_state_digest,
        "nextStateDigest": attempt.next_state_digest,
    }
    actual = {
        "protocolVersion": package.protocolVersion,
        "packageId": package.packageId,
        "mode": package.mode,
        "sourceRevision": package.sourceRevision,
        "currentStateDigest": package.currentStateDigest,
        "nextStateDigest": package.nextStateDigest,
    }
    if actual != expected:
        raise TemplateStateError("RECOVERY_PACKAGE_BINDING_MISMATCH：immutable Package 与 Attempt 绑定不一致。")


def _assert_technical_plan_binding(root: Path, supplied_sha256: str, attempt: ReconcileAttemptV2 | None = None) -> None:
    """要求调用参数、持久 Attempt（如有）与当前 TechnicalPlan 内容使用同一 SHA-256。"""

    if not isinstance(supplied_sha256, str) or len(supplied_sha256) != 64 or any(char not in "0123456789abcdef" for char in supplied_sha256):
        raise TemplateStateError("TECHNICAL_PLAN_SHA_INVALID：technicalPlanSha256 必须是小写 SHA-256。")
    if attempt is not None and attempt.technical_plan_sha256 != supplied_sha256:
        raise TemplateStateError("RECOVERY_TECHNICAL_PLAN_MISMATCH：恢复请求未绑定原 Attempt 的 TechnicalPlan。")
    try:
        actual = canonical_sha256(root / ".xcodeagent/plans/technical-plan.json")
    except ArtifactInvalidationError as exc:
        raise TemplateStateError("RECOVERY_TECHNICAL_PLAN_MISSING：无法读取当前 TechnicalPlan。") from exc
    if actual != supplied_sha256:
        raise TemplateStateError("RECOVERY_TECHNICAL_PLAN_MISMATCH：当前 TechnicalPlan 已发生变化。")


def _state_digest_after_write_error(root: Path) -> str | None:
    """在 State 写入异常后重新读取唯一 State，确认 rename 是否实际已经完成。"""

    try:
        return _state_digest(load_template_state_v2(root))
    except Exception:
        return None


def _payloads(validated: ValidatedStrategyUpdatePackage) -> dict[str, str]:
    """读取已验证 ZIP 的文本 payload，拒绝二进制 Strategy 内容。"""

    try:
        with zipfile.ZipFile(validated.archive_path) as archive:
            return {ref: archive.read(ref).decode("utf-8") for ref in validated.package.payloadManifest}
    except (OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise TemplateStateError("Strategy Package payload 无法读取为 UTF-8 文本。") from exc


def _state_digest(state: TemplateStateV2) -> str:
    """计算冻结 JSON State digest，作为 Package binding 与恢复的唯一比较值。"""

    return template_state_digest_v2(state)


def _error_code(exc: Exception) -> str:
    """提取可展示协议码，未知异常统一收敛为执行失败码。"""

    return str(exc).split("：", 1)[0] if "：" in str(exc) else "TEMPLATE_RECONCILE_EXECUTION_FAILED"


def _validation_error_code(results: list[Any]) -> str:
    """保留首个阻断验收的精确失败码，供工作流和用户界面区分环境与代码错误。"""

    failed = next((item for item in results if item.blocking and not item.passed), None)
    if failed is None:
        return "VALIDATION_FAILED"
    if failed.error_code == "VALIDATION_ASSERTION_FAILED":
        return "POSTCONDITION_FAILED"
    return str(failed.error_code or "VALIDATION_FAILED")


def _validation_failure_message(results: list[Any]) -> str:
    """把首个阻断验收的真实原因投影为 Template Preparation 错误正文。"""

    failed = next((item for item in results if item.blocking and not item.passed), None)
    if failed is None:
        return "VALIDATION_FAILED：Template Preparation 验收未通过。"
    code = _validation_error_code(results)
    return f"{code}：{failed.message}"


def _project_restart_result(launch: dict[str, Any]) -> ValidationResultV2:
    """将统一 Project Launcher 结果投影为 Template Reconcile 的单项验收。"""

    passed = launch.get("status") == "running"
    return ValidationResultV2(
        validation_id="PROJECT_RESTART",
        passed=passed,
        blocking=True,
        execution_mode="REAL_WORKSPACE",
        duration_ms=0,
        exit_code=None,
        error_code=None if passed else "PROJECT_LAUNCH_FAILED",
        message=str(launch.get("message") or ("项目重启并就绪。" if passed else "项目重启失败。")),
        details=launch,
    )


def _now() -> str:
    """生成 Attempt 审计时间。"""

    return datetime.now(timezone.utc).isoformat()


class StateCommittedV2Error(TemplateStateError):
    """表示 State 已落盘但 Attempt finalize 未完成，调用方必须进入 Roll-forward。"""
