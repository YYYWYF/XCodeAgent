"""执行历史 V3 更新与当前 V4 Capability Ensure 编排。"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Literal
from app.config import Settings
from app.services.artifact_invalidation import ArtifactInvalidationError, canonical_sha256
from app.services.template_reconcile.digest_v3 import template_state_digest_v3
from app.services.template_reconcile.executor_v3 import UpdatePackageExecutorV3, WorkingCopyStoreV3, apply_working_copy_v3
from app.services.template_reconcile.protocol_v3 import TemplateStateV3
from app.services.template_reconcile.state_v3 import load_template_state_v3, write_template_state_v3
from app.services.template_reconcile.update_package_v3 import validate_update_package_v3
from app.services.template_reconcile.update_package_v4 import validate_update_package_v4
from app.services.template_reconcile.config_surfaces import validate_ensure_package, validate_working_surfaces
from app.services.template_reconcile.transaction_v4 import apply_working_copy_v4, recover_v4_transaction
from app.services.template_reconcile.desired import compile_template_capabilities
from app.services.application_config import read_application_config
from app.branding import WORKSPACE_ARTIFACT_DIR
import json
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplateStateError
from app.services.workspace_bootstrap.template_engine_client import TemplateEngineClient

class TemplateReconcileService:
    """以受校验的更新包收敛 Capability，全部 Operation 成功后才提交 State。"""
    def __init__(self, settings: Settings) -> None:
        """保存 Template Engine 连接与 ZIP 限额。"""
        self._settings = settings

    async def reconcile_capabilities(self, workspace: str | Path) -> str:
        """从 canonical application.json 安装缺失能力，不读取 TechnicalPlan 或计算摘要。"""

        root = Path(workspace).expanduser().resolve()
        recover_v4_transaction(root)
        # 先以两个 canonical 文件做纯本地判定：已满足时不得创建 Client、访问网络或改写 State。
        desired = compile_template_capabilities(read_application_config(root))
        current = load_template_state_v3(root)
        applied = {
            name: capability.model_dump(mode="json")
            for name, capability in current.effective.items()
        }
        if set(applied) - set(desired):
            raise TemplateStateError("CAPABILITY_REMOVAL_UNSUPPORTED：已安装能力不能关闭。")
        # 名称相同但配置不同仍需要 Engine 明确收敛，不能误报为已满足。
        if applied == desired:
            return "NO_CHANGE"
        if not self._settings.template_reconcile_enabled:
            raise TemplateStateError("TEMPLATE_RECONCILE_DISABLED：模板能力增量更新功能尚未开启。")
        recover_v4_transaction(root)
        # 恢复事务可能已完成上次安装；两个 canonical 输入都重新读取，不能沿用旧快照。
        desired = compile_template_capabilities(read_application_config(root))
        current = load_template_state_v3(root)
        applied = {
            name: capability.model_dump(mode="json")
            for name, capability in current.effective.items()
        }
        if set(applied) - set(desired):
            raise TemplateStateError("CAPABILITY_REMOVAL_UNSUPPORTED：已安装能力不能关闭。")
        if applied == desired:
            return "NO_CHANGE"
        installed = set(applied)
        requested = {"capabilities": desired}
        client = TemplateEngineClient(
            base_url=self._settings.template_engine_base_url,
            connect_timeout=self._settings.template_engine_connect_timeout_seconds,
            read_timeout=self._settings.template_engine_read_timeout_seconds,
            max_package_bytes=self._settings.template_package_max_bytes,
        )
        download = await client.update_v4(current.model_dump(mode="json", exclude={"managedConfiguration"}), requested)
        if download is None:
            raise TemplateStateError("TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED：缺失能力时 V4 返回无变更。")
        try:
            validated = validate_update_package_v4(download.temporary_path, self._limits())
            package = validated.package
            if package.mode != "APPLY" or package.sourceRevision != current.templateRevision or package.expectedStateSequence != current.stateSequence:
                raise TemplateStateError("TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED：V4 包未绑定当前 State。")
            next_state = package.nextTemplateState
            next_applied = {
                name: capability.model_dump(mode="json")
                for name, capability in next_state.effective.items()
            }
            if next_state.stateSequence != current.stateSequence + 1 or next_applied != desired or installed - set(next_applied):
                raise TemplateStateError("TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED：V4 能力或 State 序列无效。")
            newly_installed = set(next_applied) - installed
            validate_ensure_package(package, validated.payloads, newly_installed)
            store = WorkingCopyStoreV3(root)
            UpdatePackageExecutorV3().execute(package, store, validated.payloads)
            validate_working_surfaces(store, newly_installed)
            state_entry = store.entry((WORKSPACE_ARTIFACT_DIR / "template-state.json").as_posix())
            state_entry.working = (json.dumps(next_state.model_dump(mode="json", exclude={"managedConfiguration"}), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            apply_working_copy_v4(root, store)
            return "CHANGED"
        finally:
            download.temporary_path.unlink(missing_ok=True)

    async def reconcile(self, workspace: str | Path, *, change_id: str, requested_config: dict[str, Any], technical_plan_sha256: str, mode: Literal["APPLY", "RECONCILE"] = "APPLY") -> str:
        """下载、预检并执行一次 V3 更新；V2 State 会被明确拒绝。"""
        del change_id
        if not self._settings.template_reconcile_enabled:
            raise TemplateStateError("TEMPLATE_RECONCILE_DISABLED：模板能力增量更新功能尚未开启。")
        root = Path(workspace).expanduser().resolve()
        _assert_technical_plan(root, technical_plan_sha256)
        current = load_template_state_v3(root)
        requested = _normalize_requested(requested_config)
        if mode == "RECONCILE" and requested != _requested(current):
            raise TemplateStateError("RECONCILE_REQUESTED_CONFIG_MISMATCH：requestedConfig 与当前 V3 State.requested 不一致。")
        client = TemplateEngineClient(base_url=self._settings.template_engine_base_url, connect_timeout=self._settings.template_engine_connect_timeout_seconds, read_timeout=self._settings.template_engine_read_timeout_seconds, max_package_bytes=self._settings.template_package_max_bytes)
        current_payload = current.model_dump(mode="json", exclude_none=True)
        if "stateSequence" not in current.model_fields_set:
            current_payload.pop("stateSequence", None)
        download = await client.update(current_payload, requested_config, mode=mode)
        if download is None:
            return "NO_CHANGE"
        try:
            validated = validate_update_package_v3(download.temporary_path, self._limits())
            package = validated.package
            if package.mode != mode or package.sourceRevision != current.templateRevision or package.currentStateDigest != template_state_digest_v3(current):
                raise TemplateStateError("TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED：V3 Update Package 未绑定当前 State。")
            if mode == "RECONCILE" and package.nextTemplateState != current:
                raise TemplateStateError("RECONCILE_STATE_CHANGE_REQUIRED：RECONCILE 不得改变 TemplateState。")
            if mode == "APPLY" and set(current.effective) - set(package.nextTemplateState.effective):
                raise TemplateStateError("CAPABILITY_REMOVAL_UNSUPPORTED：V3 第一版不支持卸载已生效 Capability。")
            store = WorkingCopyStoreV3(root)
            UpdatePackageExecutorV3().execute(package, store, validated.payloads)
            apply_working_copy_v3(root, store)
            if mode == "APPLY":
                write_template_state_v3(root, package.nextTemplateState)
            return "CHANGED"
        finally:
            download.temporary_path.unlink(missing_ok=True)

    async def retry_template_preparation(self, workspace: str | Path, *, change_id: str, requested_config: dict[str, Any], technical_plan_sha256: str, mode: Literal["APPLY", "RECONCILE"]) -> str:
        """V3 将用户重试映射为一次新的安全执行。"""
        return await self.reconcile(workspace, change_id=change_id, requested_config=requested_config, technical_plan_sha256=technical_plan_sha256, mode=mode)

    def _limits(self) -> ArchiveLimits:
        """将 Settings 映射为 V3 ZIP 预检限额。"""
        return ArchiveLimits(self._settings.template_package_max_bytes, self._settings.template_package_max_files, self._settings.template_package_max_extracted_bytes)

def _normalize_requested(value: dict[str, Any]) -> dict[str, Any]:
    """移除 disabled Capability，保留 enabled/config 的 V3 不透明语义。"""
    capabilities = value.get("capabilities")
    if not isinstance(capabilities, dict):
        raise TemplateStateError("requestedConfig.capabilities 必须是对象。")
    result: dict[str, Any] = {}
    for name, request in capabilities.items():
        if not isinstance(name, str) or not name or not isinstance(request, dict) or set(request) != {"enabled", "config"} or not isinstance(request["enabled"], bool) or not isinstance(request["config"], dict):
            raise TemplateStateError("requestedConfig 包含无效 Capability。")
        if request["enabled"]:
            result[name] = {"enabled": True, "config": request["config"]}
    return result

def _requested(state: TemplateStateV3) -> dict[str, Any]:
    """生成 State requested 的 JSON 语义快照。"""
    return {name: item.model_dump(mode="json") for name, item in state.requested.items()}

def _assert_technical_plan(root: Path, supplied: str) -> None:
    """保持更新必须绑定当前已确认 TechnicalPlan 的既有安全门。"""
    if not isinstance(supplied, str) or len(supplied) != 64 or any(char not in "0123456789abcdef" for char in supplied):
        raise TemplateStateError("TECHNICAL_PLAN_SHA_INVALID：technicalPlanSha256 必须是小写 SHA-256。")
    try:
        actual = canonical_sha256(root / ".devagentstudio/plans/technical-plan.json")
    except ArtifactInvalidationError as exc:
        raise TemplateStateError("TECHNICAL_PLAN_MISSING：无法读取当前 TechnicalPlan。") from exc
    if actual != supplied:
        raise TemplateStateError("TECHNICAL_PLAN_MISMATCH：当前 TechnicalPlan 已发生变化。")
