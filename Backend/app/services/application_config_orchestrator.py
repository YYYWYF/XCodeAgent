"""应用配置 Desired、Capability 和工程参数的统一提交编排。"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.config import Settings
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.application_config.schema import ApplicationConfigError, normalize_application_configuration, validate_application_configuration
from app.services.application_parameter_updater import ApplicationParameterUpdater
from app.services.application_parameter_updater import ParameterUpdateError
from app.services.template_reconcile.desired import compile_template_application_config
from app.services.template_reconcile.transaction_v4 import recover_v4_transaction
from app.services.template_reconcile.executor_v3 import WorkingCopyStoreV3
from app.services.template_reconcile.config_surfaces import validate_working_surfaces, ConfigSurfaceError
from app.services.template_reconcile.service import TemplateReconcileService
from app.services.template_reconcile.state_v3 import load_template_state_v3
from app.services.workspace_bootstrap.template_layout import compile_workspace_template_capabilities
from app.utils.atomic_json import atomic_write_json
from app.workspace.run_lease import workspace_run_leases

_PATHS = frozenset({
    "appName", "appIcon", "theme.primaryColor", "auth.enable",
    "auth.yht.clientId", "auth.yht.privateKey", "auth.yht.publicKey", "auth.yht.centerPublicKey",
    "auth.yht.centerUri", "auth.yht.authorizeUri", "auth.yht.redirectUri", "auth.yht.endUrl", "auth.yht.logoutShareJs",
    "authorization.enabled", "authorization.initialAdministratorSubjects", "authorization.api.baseUrl", "authorization.api.viewId",
})
_OPTIONAL_SCALAR_PATHS = frozenset({
    "auth.yht.centerUri", "auth.yht.authorizeUri", "auth.yht.redirectUri", "auth.yht.endUrl",
    "auth.yht.logoutShareJs", "authorization.api.baseUrl", "authorization.api.viewId",
})
_LOCKS: dict[str, asyncio.Lock] = {}
from app.services.config_operation_state import (
    ApplicationConfigOperationError, changes_digest as _changes_digest,
    value_at as _value_at, safe_failure_code as _safe_failure_code,
    safe_failure_target, failure_summary as _failure_summary,
)


class ApplicationConfigOrchestrator:
    """只接受字段提案，在服务端合并当前 application.json。"""

    def __init__(self, settings: Settings) -> None:
        """绑定模板服务配置并复用纯参数更新器。"""

        self._reconcile = TemplateReconcileService(settings)
        self._parameters = ApplicationParameterUpdater()

    def preview(self, workspace: str | Path, *, expected_revision: int, changes: dict[str, Any]) -> dict[str, Any]:
        """只读预检白名单值、配置依赖和已安装能力。"""

        root = Path(workspace).expanduser().resolve()
        current = self._load_current(root)
        if current["configRevision"] != expected_revision:
            raise ApplicationConfigOperationError("CONFIG_REVISION_CONFLICT", "配置已变化，请刷新后重试。")
        proposed = self._merge(current, changes)
        state_path = root / WORKSPACE_ARTIFACT_DIR / "template-state.json"
        installed: set[str] = set()
        if state_path.is_file():
            installed = set(load_template_state_v3(root).effective)
        desired = set(compile_workspace_template_capabilities(root, proposed))
        if installed - desired:
            raise ApplicationConfigOperationError("CAPABILITY_REMOVAL_UNSUPPORTED", "已安装的登录或权限能力不能关闭。")
        parameter_files: list[str] = []
        if state_path.is_file():
            try:
                # 新能力尚未通过 V4 生成受管节点时，预览不能抢先把缺结构误判为冲突。
                parameter_files = list(self._parameters.preview(root, proposed, login_applied="login" in installed, authorization_applied="authorization" in installed).changed_files)
            except ParameterUpdateError as exc:
                raise ApplicationConfigOperationError(_safe_failure_code(exc), "工程参数预检失败。", failure_stage="application_parameter_update", failure_code=_safe_failure_code(exc), failure_target=safe_failure_target(exc)) from exc
        changed = tuple(path for path in changes if _value_at(current, path) != _value_at(proposed, path))
        return {
            "expectedConfigRevision": expected_revision,
            "changedKeys": list(changed),
            "toInstall": sorted(desired - installed) if state_path.is_file() else [],
            "requiresConfirmation": bool(changed),
            "generated": state_path.is_file(),
            "parameterFiles": parameter_files,
        }

    async def apply(self, workspace: str | Path, *, operation_id: str, expected_revision: int, changes: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
        """幂等提交 Desired，随后收敛能力与参数，并持久化可恢复状态。"""

        root = Path(workspace).expanduser().resolve()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", operation_id):
            raise ApplicationConfigOperationError("CONFIG_OPERATION_INVALID", "operationId 无效。")
        lock = _LOCKS.setdefault(str(root), asyncio.Lock())
        async with lock:
            if workspace_run_leases.active_owner(workspace_root=str(root), project_id=None) is not None:
                raise ApplicationConfigOperationError("CONFIG_WORKSPACE_BUSY", "工作区有正在执行的任务。")
            record = self._read_operation(root, operation_id)
            if record is not None:
                if (
                    record["expectedConfigRevision"] != expected_revision
                    or record.get("changesDigest") != _changes_digest(changes)
                    or record.get("requestId") != request_id
                ):
                    raise ApplicationConfigOperationError("CONFIG_OPERATION_CONFLICT", "operationId 已绑定另一份配置提案。")
                if record["status"] == "APPLIED":
                    return record
                if record["status"] not in {"APPLYING", "FAILED"}:
                    raise ApplicationConfigOperationError("CONFIG_OPERATION_CONFLICT", "配置操作状态无效。")
                current = self._load_current(root)
                if current["configRevision"] == record["targetConfigRevision"]:
                    if any(_value_at(current, path) != value for path, value in changes.items()):
                        raise ApplicationConfigOperationError("CONFIG_REVISION_CONFLICT", "磁盘 Desired 与原提案不一致，请检查配置。")
                    desired = current
                elif current["configRevision"] == expected_revision:
                    desired = self._merge(current, changes)
                    desired["configRevision"] = record["targetConfigRevision"]
                    self._save_desired(root, desired, record)
                else:
                    raise ApplicationConfigOperationError("CONFIG_REVISION_CONFLICT", "配置已由其他操作修改。")
            else:
                self._assert_no_pending_desired(root)
                projection = self.preview(root, expected_revision=expected_revision, changes=changes)
                current = self._load_current(root)
                desired = self._merge(current, changes)
                target_revision = expected_revision + (1 if projection["changedKeys"] else 0)
                desired["configRevision"] = target_revision
                record = {
                    "operationId": operation_id,
                    "status": "APPLYING",
                    "expectedConfigRevision": expected_revision,
                    "targetConfigRevision": target_revision,
                    "changedKeys": list(changes),
                    "changedFiles": [],
                    "installedCapabilities": [],
                    "requestId": request_id,
                    "generated": projection["generated"],
                    # 仅持久化提案的稳定摘要，重试时既能验证字段值又不会在运行态文件写入密钥。
                    "changesDigest": _changes_digest(changes),
                }
                self._write_operation(root, record)
                if target_revision != expected_revision:
                    self._save_desired(root, desired, record)
            record["status"] = "APPLYING"
            record["progressStage"] = record.get("progressStage") or "DESIRED_SAVED"
            self._write_operation(root, record)
            failure_stage = "template_state_read"
            try:
                state_path = root / WORKSPACE_ARTIFACT_DIR / "template-state.json"
                if record["generated"] and not state_path.is_file():
                    raise ConfigSurfaceError("CONFIG_STRUCTURE_MISSING", ".devagentstudio/template-state.json")
                if state_path.is_file():
                    recover_v4_transaction(root)
                    applied = {name: value.model_dump(mode="json") for name, value in load_template_state_v3(root).effective.items()}
                    before = set(applied)
                    desired_capabilities = _desired_capabilities(desired)
                    # 仅修改名称、主题或参数时不能触发模板更新包；能力差异才进入 V4 收敛。
                    if applied != compile_workspace_template_capabilities(root, desired):
                        failure_stage = "template_capability_reconcile"
                        await self._reconcile.reconcile_capabilities(root)
                    failure_stage = "template_state_read"
                    after = set(load_template_state_v3(root).effective)
                    record["installedCapabilities"] = sorted(set(record["installedCapabilities"]) | (after - before))
                    record["progressStage"] = "CAPABILITY_APPLIED"
                    self._write_operation(root, record)
                    failure_stage = "application_parameter_update"
                    validate_working_surfaces(WorkingCopyStoreV3(root), desired_capabilities)
                    plan = self._parameters.preview(root, desired, login_applied="login" in after, authorization_applied="authorization" in after)
                    self._parameters.apply(root, plan)
                    record["changedFiles"] = list(plan.changed_files)
                record["progressStage"] = "PARAMETERS_APPLIED"
                self._write_operation(root, record)
                # 失败操作恢复成功后清除旧错误码，避免 APPLIED 状态仍显示历史失败。
                record.pop("errorCode", None)
                record.pop("failureStage", None)
                record.pop("failureSummary", None)
                record.pop("failureCode", None)
                record.pop("failureTarget", None)
                record["status"] = "APPLIED"
                record["configRevision"] = desired["configRevision"]
                self._write_operation(root, record)
                return record
            except Exception as exc:
                record["status"] = "FAILED"
                # 持久状态只记录安全错误码，不写上游异常或任何字段值。
                failure_code = _safe_failure_code(exc)
                record["errorCode"] = failure_code
                record["failureCode"] = failure_code
                record["failureTarget"] = safe_failure_target(exc)
                record["failureStage"] = failure_stage
                record["failureSummary"] = _failure_summary(failure_stage, failure_code)
                self._write_operation(root, record)
                raise ApplicationConfigOperationError(
                    failure_code,
                    f"{record['failureSummary']}（内部错误码：{failure_code}），可使用原 operationId 重试。",
                    failure_stage=failure_stage,
                    failure_code=failure_code,
                    failure_target=record["failureTarget"],
                ) from exc

    async def resume(self, workspace: str | Path, *, operation_id: str, request_id: str | None = None) -> dict[str, Any]:
        """显式恢复已保存的 Desired；前端只提交身份，不重传旧凭据。"""
        root = Path(workspace).expanduser().resolve()
        record = self.get_operation(root, operation_id)
        if record.get("requestId") != request_id:
            raise ApplicationConfigOperationError("CONFIG_OPERATION_CONFLICT", "请回到原配置请求恢复操作。")
        if record["status"] == "APPLIED":
            return record
        current = self._load_current(root)
        if current["configRevision"] != record["targetConfigRevision"]:
            raise ApplicationConfigOperationError("CONFIG_REVISION_CONFLICT", "原 Desired 尚未保存或已被修改，不能按磁盘恢复。")
        changes = {path: _value_at(current, path) for path in record["changedKeys"]}
        if any(not isinstance(value, (str, bool, list)) for value in changes.values()) or _changes_digest(changes) != record["changesDigest"]:
            raise ApplicationConfigOperationError("CONFIG_OPERATION_CONFLICT", "磁盘 Desired 与原提案摘要不一致。")
        return await self.apply(root, operation_id=operation_id, expected_revision=record["expectedConfigRevision"], changes=changes, request_id=request_id)

    def _assert_no_pending_desired(self, root: Path) -> None:
        """不同会话也不能跨过仍待同步的 Desired 创建新提案。"""
        directory = root / WORKSPACE_ARTIFACT_DIR / "runtime/application-config"
        revision = self._load_current(root)["configRevision"]
        for path in directory.glob("*.json"):
            record = self._read_operation(root, path.stem)
            if record and record["status"] in {"APPLYING", "FAILED"} and (record.get("progressStage") in {"DESIRED_SAVED", "CAPABILITY_APPLIED", "PARAMETERS_APPLIED"} or record["targetConfigRevision"] != record["expectedConfigRevision"] and record["targetConfigRevision"] == revision):
                raise ApplicationConfigOperationError("CONFIG_OPERATION_PENDING", "原配置操作尚未完成，请先使用原 operationId 恢复并刷新配置。")

    def _save_desired(self, root: Path, desired: dict[str, Any], record: dict[str, Any]) -> None:
        """初次提交和恢复都记录 Desired 写盘失败，不把失败归入模板安装。"""
        try:
            ApplicationConfigRepository(root).save(desired)
        except Exception as exc:
            record.update(status="FAILED", errorCode="CONFIG_SAVE_FAILED", failureCode="CONFIG_SAVE_FAILED", failureStage="desired_save", failureTarget=".devagentstudio/application.json")
            self._write_operation(root, record)
            raise ApplicationConfigOperationError("CONFIG_SAVE_FAILED", "应用配置未保存，可使用原 operationId 重试。", failure_stage="desired_save", failure_code="CONFIG_SAVE_FAILED", failure_target=".devagentstudio/application.json") from exc

    def _load_current(self, root: Path) -> dict[str, Any]:
        """将 canonical 读取错误转换为稳定安全的阶段诊断。"""
        try:
            return ApplicationConfigRepository(root).load()
        except ApplicationConfigError as exc:
            raise ApplicationConfigOperationError("CONFIG_SNAPSHOT_INVALID", str(exc), failure_stage="config_snapshot_read", failure_code="CONFIG_SNAPSHOT_INVALID", failure_target=".devagentstudio/application.json") from exc

    def get_operation(self, workspace: str | Path, operation_id: str) -> dict[str, Any]:
        """读取仅含字段名和阶段的持久操作状态。"""

        root = Path(workspace).expanduser().resolve()
        record = self._read_operation(root, operation_id)
        if record is None:
            raise ApplicationConfigOperationError("CONFIG_OPERATION_MISSING", "找不到配置操作。")
        return record

    def _merge(self, current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
        """按字段白名单合并服务端当前配置，并确定性展开权限登录依赖。"""

        if not isinstance(changes, dict) or not changes or not set(changes) <= _PATHS:
            raise ApplicationConfigOperationError("CONFIG_FIELDS_INVALID", "配置提案包含未知字段或为空。")
        result = normalize_application_configuration(current)
        for path, value in changes.items():
            if path in {"auth.enable", "authorization.enabled"}:
                if type(value) is not bool:
                    raise ApplicationConfigOperationError("CONFIG_VALUE_INVALID", f"{path} 必须是布尔值。")
            elif path == "authorization.initialAdministratorSubjects":
                if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                    raise ApplicationConfigOperationError("CONFIG_VALUE_INVALID", "初始管理员必须是非空 Subject 列表。")
            elif not isinstance(value, str) or (path not in _OPTIONAL_SCALAR_PATHS and not value.strip()):
                raise ApplicationConfigOperationError("CONFIG_VALUE_INVALID", f"{path} 必须是字符串。")
            cursor = result
            parts = path.split(".")
            for part in parts[:-1]:
                # 关闭登录的当前配置可以没有 auth.yht；首次启用时按白名单路径补齐容器。
                nested = cursor.get(part)
                if nested is None:
                    nested = {}
                    cursor[part] = nested
                if not isinstance(nested, dict):
                    raise ApplicationConfigOperationError("CONFIG_VALUE_INVALID", f"{path} 的父级配置结构无效。")
                cursor = nested
            cursor[parts[-1]] = value
        if result["authorization"]["enabled"] and not result["auth"]["enable"]:
            if changes.get("auth.enable") is False:
                raise ApplicationConfigOperationError("CONFIG_DEPENDENCY_CONFLICT", "权限能力需要登录认证。")
            result["auth"]["enable"] = True
        try:
            validate_application_configuration(result)
            compile_template_application_config(result)
        except (ApplicationConfigError, ValueError) as exc:
            raise ApplicationConfigOperationError("CONFIG_VALUE_INVALID", "应用配置未满足当前能力或参数约束。") from exc
        return result

    def _operation_path(self, root: Path, operation_id: str) -> Path:
        """把经过格式校验的操作身份限定到工作区运行目录。"""

        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", operation_id):
            raise ApplicationConfigOperationError("CONFIG_OPERATION_INVALID", "operationId 无效。")
        return root / WORKSPACE_ARTIFACT_DIR / "runtime/application-config" / f"{operation_id}.json"

    def _read_operation(self, root: Path, operation_id: str) -> dict[str, Any] | None:
        """读取非敏感操作记录并拒绝损坏状态。"""

        path = self._operation_path(root, operation_id)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ApplicationConfigOperationError("CONFIG_OPERATION_INVALID", "配置操作记录损坏。") from exc
        if not isinstance(record, dict) or record.get("operationId") != operation_id:
            raise ApplicationConfigOperationError("CONFIG_OPERATION_INVALID", "配置操作记录身份不符。")
        return record

    def _write_operation(self, root: Path, record: dict[str, Any]) -> None:
        """原子写入不含敏感字段值的操作进度。"""

        path = self._operation_path(root, record["operationId"])
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, record)


def _desired_capabilities(config: dict[str, Any]) -> set[str]:
    """从唯一 application.json 当前配置推导模板能力集合。"""

    result: set[str] = set()
    if config["auth"]["enable"]:
        result.add("login")
    if config["authorization"]["enabled"]:
        result.add("authorization")
    return result
