"""应用配置 Desired、Capability 和工程参数的统一提交编排。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.config import Settings
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.application_config.schema import ApplicationConfigError, validate_application_configuration
from app.services.application_parameter_updater import ApplicationParameterUpdater
from app.services.application_parameter_updater import ParameterUpdateError
from app.services.template_reconcile.desired import compile_template_application_config
from app.services.template_reconcile.service import TemplateReconcileService
from app.services.template_reconcile.state_v3 import load_template_state_v3
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
_MISSING = object()
_FAILURE_STAGE_LABELS = {
    "template_state_read": "读取工程模板状态",
    "template_capability_reconcile": "模板能力代码安装",
    "application_parameter_update": "工程参数写入",
}
_FAILURE_CODE_HINTS = {
    "TEMPLATE_ENGINE_UNAVAILABLE": "模板引擎不可用，请检查模板服务连接后重试。",
    "TEMPLATE_STATE_INVALID": "模板能力状态或更新包不符合当前工程合同，请检查模板服务返回结果。",
    "TEMPLATE_PACKAGE_CONTRACT_INVALID": "模板更新包校验失败，请检查模板服务返回结果。",
    "CONFIG_APPLY_FAILED": "工程参数写入未完成，请检查受管配置文件后重试。",
}


class ApplicationConfigOperationError(ValueError):
    """统一配置操作的安全错误码与说明。"""

    def __init__(self, code: str, message: str, *, failure_stage: str | None = None, failure_code: str | None = None) -> None:
        """构造不携带提交值的可投影错误。"""

        super().__init__(f"{code}：{message}")
        self.code = code
        self.failure_stage = failure_stage
        self.failure_code = failure_code


class ApplicationConfigOrchestrator:
    """只接受字段提案，在服务端合并当前 application.json。"""

    def __init__(self, settings: Settings) -> None:
        """绑定模板服务配置并复用纯参数更新器。"""

        self._reconcile = TemplateReconcileService(settings)
        self._parameters = ApplicationParameterUpdater()

    def preview(self, workspace: str | Path, *, expected_revision: int, changes: dict[str, Any]) -> dict[str, Any]:
        """只读预检白名单值、配置依赖和已安装能力。"""

        root = Path(workspace).expanduser().resolve()
        current = ApplicationConfigRepository(root).load()
        if current["configRevision"] != expected_revision:
            raise ApplicationConfigOperationError("CONFIG_REVISION_CONFLICT", "配置已变化，请刷新后重试。")
        proposed = self._merge(current, changes)
        state_path = root / WORKSPACE_ARTIFACT_DIR / "template-state.json"
        installed: set[str] = set()
        if state_path.is_file():
            installed = set(load_template_state_v3(root).effective)
        desired = set()
        if proposed["auth"]["enable"]:
            desired.add("login")
        if proposed["authorization"]["enabled"]:
            desired.add("authorization")
        if installed - desired:
            raise ApplicationConfigOperationError("CAPABILITY_REMOVAL_UNSUPPORTED", "已安装的登录或权限能力不能关闭。")
        parameter_files: list[str] = []
        if state_path.is_file():
            try:
                # 新能力尚未通过 V4 生成受管节点时，预览不能抢先把缺结构误判为冲突。
                parameter_files = list(self._parameters.preview(root, proposed, login_applied="login" in installed, authorization_applied="authorization" in installed).changed_files)
            except ParameterUpdateError as exc:
                raise ApplicationConfigOperationError("CONFIG_TARGET_CONFLICT", "工程参数文件与受管字段合同冲突。") from exc
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
                current = ApplicationConfigRepository(root).load()
                if current["configRevision"] == record["targetConfigRevision"]:
                    desired = current
                elif current["configRevision"] == expected_revision:
                    desired = self._merge(current, changes)
                    desired["configRevision"] = record["targetConfigRevision"]
                    ApplicationConfigRepository(root).save(desired)
                else:
                    raise ApplicationConfigOperationError("CONFIG_REVISION_CONFLICT", "配置已由其他操作修改。")
            else:
                projection = self.preview(root, expected_revision=expected_revision, changes=changes)
                current = ApplicationConfigRepository(root).load()
                desired = self._merge(current, changes)
                if not projection["changedKeys"] and not projection["toInstall"] and not projection["parameterFiles"]:
                    return {"operationId": operation_id, "status": "APPLIED", "changedKeys": [], "changedFiles": [], "installedCapabilities": [], "configRevision": expected_revision}
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
                    # 仅持久化提案的稳定摘要，重试时既能验证字段值又不会在运行态文件写入密钥。
                    "changesDigest": _changes_digest(changes),
                }
                self._write_operation(root, record)
                if target_revision != expected_revision:
                    try:
                        ApplicationConfigRepository(root).save(desired)
                    except Exception as exc:
                        record["status"] = "FAILED"
                        record["errorCode"] = "CONFIG_SAVE_FAILED"
                        self._write_operation(root, record)
                        raise ApplicationConfigOperationError("CONFIG_SAVE_FAILED", "应用配置未保存，可使用原 operationId 重试。") from exc
            failure_stage = "template_state_read"
            try:
                state_path = root / WORKSPACE_ARTIFACT_DIR / "template-state.json"
                if state_path.is_file():
                    before = set(load_template_state_v3(root).effective)
                    desired_capabilities = _desired_capabilities(desired)
                    # 仅修改名称、主题或参数时不能触发模板更新包；能力差异才进入 V4 收敛。
                    if before != desired_capabilities:
                        failure_stage = "template_capability_reconcile"
                        await self._reconcile.reconcile_capabilities(root)
                    failure_stage = "template_state_read"
                    after = set(load_template_state_v3(root).effective)
                    record["installedCapabilities"] = sorted(after - before)
                    self._write_operation(root, record)
                    failure_stage = "application_parameter_update"
                    plan = self._parameters.preview(root, desired, login_applied="login" in after, authorization_applied="authorization" in after)
                    self._parameters.apply(root, plan)
                    record["changedFiles"] = list(plan.changed_files)
                # 失败操作恢复成功后清除旧错误码，避免 APPLIED 状态仍显示历史失败。
                record.pop("errorCode", None)
                record.pop("failureStage", None)
                record.pop("failureSummary", None)
                record["status"] = "APPLIED"
                record["configRevision"] = desired["configRevision"]
                self._write_operation(root, record)
                return record
            except Exception as exc:
                record["status"] = "FAILED"
                # 持久状态只记录安全错误码，不写上游异常或任何字段值。
                failure_code = _safe_failure_code(exc)
                record["errorCode"] = failure_code
                record["failureStage"] = failure_stage
                record["failureSummary"] = _failure_summary(failure_stage, failure_code)
                self._write_operation(root, record)
                raise ApplicationConfigOperationError(
                    "CONFIG_APPLY_FAILED",
                    f"{record['failureSummary']}（内部错误码：{failure_code}），可使用原 operationId 重试。",
                    failure_stage=failure_stage,
                    failure_code=failure_code,
                ) from exc

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
        result = deepcopy(current)
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


def _value_at(config: dict[str, Any], path: str) -> Any:
    """读取白名单字段；历史缺失叶子以哨兵参与差异比较。"""

    value: Any = config
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _desired_capabilities(config: dict[str, Any]) -> set[str]:
    """从唯一 application.json 当前配置推导模板能力集合。"""

    result: set[str] = set()
    if config["auth"]["enable"]:
        result.add("login")
    if config["authorization"]["enabled"]:
        result.add("authorization")
    return result


def _safe_failure_code(error: Exception) -> str:
    """只提取稳定错误码，禁止把上游响应正文或配置值写进运行态记录。"""

    code = getattr(error, "code", None)
    return code if isinstance(code, str) and code else "CONFIG_APPLY_FAILED"


def _changes_digest(changes: dict[str, Any]) -> str:
    """为一次配置提案生成不暴露字段值的稳定身份摘要。"""

    encoded = json.dumps(changes, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _failure_summary(stage: str, code: str) -> str:
    """将内部阶段和错误码转换为可行动且不含敏感信息的用户提示。"""

    stage_label = _FAILURE_STAGE_LABELS.get(stage, "工程配置同步")
    hint = _FAILURE_CODE_HINTS.get(code, "请根据内部错误码检查该环节后重试。")
    return f"{stage_label}失败：{hint}"
