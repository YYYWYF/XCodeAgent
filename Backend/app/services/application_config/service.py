"""应用配置变更的统一业务服务。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.services.application_config.change import (
    ApplicationConfigChange,
    NATURAL_LANGUAGE_MUTABLE_APPLICATION_CONFIG,
)
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.application_config.schema import ApplicationConfigError, validate_application_configuration


class ApplicationConfigService:
    """统一编排 application.json 的读取、预览、校验与正式提交。"""

    def __init__(self, workspace_root: str | Path) -> None:
        """为一个工作区创建配置服务。"""

        self._repository = ApplicationConfigRepository(workspace_root)

    def read(self) -> dict[str, Any]:
        """返回当前已验证的唯一权威配置。"""

        return self._repository.load()

    def preview(
        self,
        *,
        changes: Sequence[ApplicationConfigChange],
        initial_administrator_subjects: Sequence[str] | None = None,
        allow_existing_targets: bool = False,
    ) -> dict[str, Any]:
        """不写文件地应用待确认变更，供正式确认前检查结果。"""

        current = self.read()
        normalized_changes = self._expand_dependencies(current, list(changes))
        self._validate_changes(current, normalized_changes, allow_existing_targets=allow_existing_targets)
        if not normalized_changes and initial_administrator_subjects is None:
            return current
        updated = deepcopy(current)
        for change in normalized_changes:
            section, field = change.path.split(".")
            updated[section][field] = change.to_value
        if initial_administrator_subjects is not None:
            updated["authorization"]["initialAdministratorSubjects"] = self._normalize_initial_administrator_subjects(
                initial_administrator_subjects
            )
        return validate_application_configuration(updated)

    def changes_for_targets(
        self,
        targets: Mapping[str, bool],
        *,
        reason: str,
        evidence: str,
    ) -> list[ApplicationConfigChange]:
        """依据当前快照把人工配置目标转换为统一的不可变 Delta。"""

        return self.changes_for_target_details(
            targets,
            reasons={path: reason for path in targets},
            evidence={path: evidence for path in targets},
        )

    def changes_for_target_details(
        self,
        targets: Mapping[str, bool],
        *,
        reasons: Mapping[str, str],
        evidence: Mapping[str, str],
    ) -> list[ApplicationConfigChange]:
        """依据每个目标的审计说明生成 Delta，并过滤已经达到的目标。"""

        current = self.read()
        changes: list[ApplicationConfigChange] = []
        for path, target in targets.items():
            if path not in NATURAL_LANGUAGE_MUTABLE_APPLICATION_CONFIG or type(target) is not bool:
                raise ApplicationConfigError("配置变更包含不允许的字段。")
            section, field = path.split(".")
            current_value = current[section][field]
            if current_value is target:
                continue
            reason = reasons.get(path)
            path_evidence = evidence.get(path)
            if not isinstance(reason, str) or not reason.strip() or not isinstance(path_evidence, str) or not path_evidence.strip():
                raise ApplicationConfigError("配置变更必须包含非空 reason 和 evidence。")
            changes.append(
                ApplicationConfigChange(
                    path=path,
                    operation="set",
                    from_value=current_value,
                    to_value=target,
                    reason=reason,
                    evidence=path_evidence,
                )
            )
        return changes

    def apply(
        self,
        *,
        changes: Sequence[ApplicationConfigChange],
        initial_administrator_subjects: Sequence[str] | None = None,
        allow_existing_targets: bool = False,
    ) -> dict[str, Any]:
        """提交全部已验证 Delta，并生成下一次配置事实版本。"""

        updated = self.preview(
            changes=changes,
            initial_administrator_subjects=initial_administrator_subjects,
            allow_existing_targets=allow_existing_targets,
        )
        updated["configRevision"] = int(updated["configRevision"]) + 1
        self._repository.save(updated)
        return updated

    def _validate_changes(
        self,
        current: dict[str, Any],
        changes: list[ApplicationConfigChange],
        *,
        allow_existing_targets: bool,
    ) -> None:
        """校验可变白名单、重复路径与来源快照，阻止陈旧提案覆盖当前状态。"""

        paths: set[str] = set()
        for change in changes:
            if not isinstance(change, ApplicationConfigChange) or change.path not in NATURAL_LANGUAGE_MUTABLE_APPLICATION_CONFIG:
                raise ApplicationConfigError("配置变更包含不允许的字段。")
            if change.path in paths:
                raise ApplicationConfigError(f"配置变更重复设置 {change.path}。")
            paths.add(change.path)
            section, field = change.path.split(".")
            current_value = current[section][field]
            if allow_existing_targets and current_value is change.to_value:
                continue
            if current_value is not change.from_value:
                raise ApplicationConfigError(f"application.json 的 {change.path} 已变化，请重新生成正式修订。")

    def _expand_dependencies(
        self,
        current: dict[str, Any],
        changes: list[ApplicationConfigChange],
    ) -> list[ApplicationConfigChange]:
        """统一补齐权限依赖登录，并拒绝同一提交中的显式冲突。"""

        targets = {change.path: change for change in changes}
        authorization = targets.get("authorization.enabled")
        login = targets.get("auth.enable")
        if authorization is None or authorization.to_value is not True:
            return changes
        if login is not None and login.to_value is False:
            raise ApplicationConfigError("启用权限管理不能同时关闭登录认证。")
        if login is not None or current["auth"]["enable"] is True:
            return changes
        # 自动补齐仍是普通 ApplicationConfigChange，保证后续预览与提交完全一致。
        dependency = ApplicationConfigChange(
            path="auth.enable",
            operation="set",
            from_value=False,
            to_value=True,
            reason="启用权限管理需要同时启用登录认证",
            evidence=authorization.evidence,
        )
        return [dependency, *changes]

    def _normalize_initial_administrator_subjects(self, values: Sequence[str]) -> list[str]:
        """去重并拒绝空白或 current-user 等非真实管理员主体。"""

        subjects: list[str] = []
        for raw_value in values:
            subject = str(raw_value).strip()
            if not subject or subject in subjects:
                continue
            if subject == "current-user":
                raise ApplicationConfigError("初始管理员必须使用真实 subjectId，不能使用 current-user。")
            subjects.append(subject)
        if not subjects:
            raise ApplicationConfigError("启用权限控制时至少需要一个真实初始管理员 subjectId。")
        return subjects


def read_application_config(workspace_root: str | Path) -> dict[str, Any]:
    """兼容函数式调用方，统一委托 ApplicationConfigService 读取。"""

    return ApplicationConfigService(workspace_root).read()


def preview_application_config_changes(
    workspace_root: str | Path,
    *,
    changes: Sequence[ApplicationConfigChange],
    initial_administrator_subjects: Sequence[str] | None = None,
    allow_existing_targets: bool = False,
) -> dict[str, Any]:
    """兼容函数式调用方，统一委托 ApplicationConfigService 预览。"""

    return ApplicationConfigService(workspace_root).preview(
        changes=changes,
        initial_administrator_subjects=initial_administrator_subjects,
        allow_existing_targets=allow_existing_targets,
    )


def apply_application_config_changes(
    workspace_root: str | Path,
    *,
    changes: Sequence[ApplicationConfigChange],
    initial_administrator_subjects: Sequence[str] | None = None,
    allow_existing_targets: bool = False,
) -> dict[str, Any]:
    """兼容函数式调用方，统一委托 ApplicationConfigService 提交。"""

    return ApplicationConfigService(workspace_root).apply(
        changes=changes,
        initial_administrator_subjects=initial_administrator_subjects,
        allow_existing_targets=allow_existing_targets,
    )
