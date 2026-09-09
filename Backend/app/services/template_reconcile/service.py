"""当前 Engine 三类文件操作的 Template Capability Reconcile 编排。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.template_reconcile.applier import (
    apply_file_operations,
    assert_clean_worktree,
    git_head,
    rollback_workspace,
)
from app.services.template_reconcile.health import assert_managed_workspace_healthy
from app.services.template_reconcile.models import RequestedConfig, TemplateState
from app.services.template_reconcile.preflight import ReconcileOwnershipRegistry
from app.services.template_reconcile.recovery import recover_reconcile_attempt
from app.services.template_reconcile.runtime_state import (
    ReconcileAttempt,
    atomic_write_json,
    load_reconcile_attempt,
    save_reconcile_attempt,
)
from app.services.template_reconcile.update_package import validate_update_package
from app.services.template_state import load_template_state, template_state_path, validate_template_state
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplateStateError
from app.services.workspace_bootstrap.template_engine_client import TemplateEngineClient


class TemplateReconcileService:
    """执行一次不可并发的 Capability 增量更新，失败时回到 Git baseline。"""

    def __init__(self, settings: Settings) -> None:
        """保存 Engine 连接与 Package 限额配置。"""

        self._settings = settings

    async def reconcile(
        self,
        workspace: str | Path,
        *,
        change_id: str,
        requested_config: dict[str, Any],
        technical_plan_sha256: str,
    ) -> str:
        """调用一次 `/v1/update`，并只在完整成功后提交新的 TemplateState。"""

        if not self._settings.template_reconcile_enabled:
            raise TemplateStateError("TEMPLATE_RECONCILE_DISABLED：模板能力增量更新功能尚未开启。")
        root = Path(workspace).expanduser().resolve()
        existing = load_reconcile_attempt(root)
        if (
            existing is not None
            and existing.phase == "RECONCILED"
            and existing.change_id == change_id
            and existing.technical_plan_sha256 == technical_plan_sha256
        ):
            assert_managed_workspace_healthy(root, load_template_state(root))
            return "ALREADY_RECONCILED"
        recovered = recover_reconcile_attempt(root, change_id=change_id)
        if recovered is not None and recovered.phase not in {"FAILED_CLEAN"}:
            raise TemplateStateError("Template Reconcile 恢复状态不允许重新执行。")
        assert_clean_worktree(root)
        current_state = TemplateState.model_validate(load_template_state(root))
        request = RequestedConfig.model_validate(requested_config)
        client = TemplateEngineClient(
            base_url=self._settings.template_engine_base_url,
            token=self._settings.template_engine_token,
            connect_timeout=self._settings.template_engine_connect_timeout_seconds,
            read_timeout=self._settings.template_engine_read_timeout_seconds,
            max_package_bytes=self._settings.template_package_max_bytes,
        )
        download = await client.update(current_state.model_dump(), request.model_dump())
        if download is None:
            assert_managed_workspace_healthy(root, current_state.model_dump())
            return "NO_CHANGE"
        attempt = ReconcileAttempt(
            change_id=change_id,
            technical_plan_sha256=technical_plan_sha256,
            pre_reconcile_head=git_head(root),
            phase="APPLYING",
        )
        save_reconcile_attempt(root, attempt)
        try:
            package = validate_update_package(download.temporary_path, self._archive_limits())
            _validate_update_policy(current_state, package.next_template_state, package.change_set)
            ReconcileOwnershipRegistry(current_state, package.next_template_state).classify_change_set(package.change_set)
            attempt = apply_file_operations(root, package.change_set, attempt)
            attempt = replace(attempt, phase="VALIDATING")
            save_reconcile_attempt(root, attempt)
            target_state = validate_template_state(package.next_template_state.model_dump())
            assert_managed_workspace_healthy(root, target_state)
            attempt = replace(attempt, phase="COMMITTING_METADATA")
            save_reconcile_attempt(root, attempt)
            atomic_write_json(template_state_path(root), target_state)
            save_reconcile_attempt(root, replace(attempt, phase="RECONCILED", error=None))
            return "CHANGED"
        except Exception as exc:
            if attempt.phase == "COMMITTING_METADATA":
                raise TemplateStateError(
                    "RECONCILE_RECOVERY_REQUIRED：TemplateState 元数据提交可能不完整，请人工恢复。"
                ) from exc
            current_attempt = replace(attempt, phase="FAILED_CLEAN", error=str(exc)[:2048])
            try:
                rollback_workspace(root, attempt)
                save_reconcile_attempt(root, current_attempt)
            except Exception as rollback_exc:
                raise TemplateStateError("TEMPLATE_RECONCILE_ROLLBACK_FAILED：请按运行态记录人工恢复。") from rollback_exc
            raise
        finally:
            download.temporary_path.unlink(missing_ok=True)

    def _archive_limits(self) -> ArchiveLimits:
        """把 Settings 映射为与首次 Bootstrap 相同的 ZIP 安全限额。"""

        return ArchiveLimits(
            max_package_bytes=self._settings.template_package_max_bytes,
            max_files=self._settings.template_package_max_files,
            max_extracted_bytes=self._settings.template_package_max_extracted_bytes,
        )


def _validate_update_policy(
    current: TemplateState,
    target: TemplateState,
    change_set: Any,
) -> None:
    """实施当前 Engine 可验证的 Revision 不变和能力不移除策略。"""

    if target.templateRevision != current.templateRevision:
        raise TemplateStateError("TEMPLATE_REVISION_UPGRADE_NOT_SUPPORTED：当前仅支持同 revision 的能力增量。")
    removed = set(current.effective) - set(target.effective)
    if removed:
        raise TemplateStateError("CAPABILITY_REMOVE_NOT_SUPPORTED：当前不支持移除已生效模板能力。")
