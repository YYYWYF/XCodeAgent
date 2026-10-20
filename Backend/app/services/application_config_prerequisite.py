"""保留业务原文的配置意图与能力前置条件分析。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.access_control_intent import has_explicit_business_access_control_change, resolve_capability_intents
from app.services.application_config import read_application_config
from app.services.template_reconcile.state_v3 import load_template_state_v3


@dataclass(frozen=True)
class ConfigPrerequisiteAnalysis:
    """仅保存非敏感配置目标与主业务是否必须继续。"""

    original_request: str
    expected_config_revision: int
    config_intents: tuple[dict[str, Any], ...]
    config_prerequisites: tuple[dict[str, Any], ...]
    suggested_changes: dict[str, Any]
    business_request_required: bool
    conflict: str | None = None

    @property
    def requires_card(self) -> bool:
        """只有尚未满足的显式或隐含目标需要配置卡。"""

        return bool(self.config_intents or self.config_prerequisites)


def analyze_application_config_prerequisite(request: str, workspace: str | Path) -> ConfigPrerequisiteAnalysis:
    """按当前 Desired 和已安装能力分析原文，不写文件或构造私钥值。"""

    root = Path(workspace).expanduser().resolve()
    application = read_application_config(root)
    state_path = root / WORKSPACE_ARTIFACT_DIR / "template-state.json"
    installed = set(load_template_state_v3(root).effective) if state_path.is_file() else set()
    raw_intents = resolve_capability_intents(request)
    explicit: list[dict[str, Any]] = []
    suggested: dict[str, Any] = {}
    conflict: str | None = None
    for intent in raw_intents:
        if intent.path not in {"auth.enable", "authorization.enabled"}:
            continue
        capability = "login" if intent.path == "auth.enable" else "authorization"
        current = application["auth"]["enable"] if capability == "login" else application["authorization"]["enabled"]
        if not intent.enabled and capability in installed:
            conflict = "CAPABILITY_REMOVAL_UNSUPPORTED：已安装的能力不能关闭。"
        if current != intent.enabled or (intent.enabled and capability not in installed and state_path.is_file()):
            explicit.append({"path": intent.path, "desiredValue": intent.enabled, "source": "explicit"})
            suggested[intent.path] = intent.enabled
    name_match = re.search(r"(?:把|将)?(?:应用|系统)?(?:名称|名字)(?:改为|修改为|设为|设置为|改成)\s*([^，,。；;\s]+)", request)
    rename_requested = name_match is not None or re.search(r"(?:应用|系统)?改名", request) is not None
    if rename_requested:
        name = name_match.group(1) if name_match else None
        if name != application.get("appName"):
            explicit.append({"path": "appName", "desiredValue": name, "source": "explicit"})
            if name:
                suggested["appName"] = name
    business_access = has_explicit_business_access_control_change(request)
    business_required = business_access or bool(re.search(r"新增|增加|创建|开发|实现|修改|调整|修复|查询|页面|接口|按钮", request) and not _pure_config_request(request))
    prerequisites: list[dict[str, Any]] = []
    if business_access and "authorization" not in installed and not application["authorization"]["enabled"]:
        if any(intent.path == "authorization.enabled" and not intent.enabled for intent in raw_intents) or re.search(r"(?:不要|不想|禁止|不能)\s*(?:开启|启用)?\s*(?:权限|授权)", request):
            conflict = "CONFIG_DEPENDENCY_CONFLICT：该业务访问限制需要权限能力，但请求同时禁止启用权限。"
        else:
            prerequisites.append({
                "path": "authorization.enabled", "desiredValue": True, "source": "inferred",
                "reason": "业务访问限制需要权限能力", "requiresConfirmation": True,
            })
    if application["authorization"]["enabled"] and "authorization" not in installed and state_path.is_file():
        if not any(item["path"] == "authorization.enabled" for item in explicit + prerequisites):
            prerequisites.append({
                "path": "authorization.enabled", "desiredValue": True, "source": "inferred",
                "reason": "配置已启用但工程尚未安装权限能力", "requiresConfirmation": True,
            })
    return ConfigPrerequisiteAnalysis(
        original_request=request,
        expected_config_revision=application["configRevision"],
        config_intents=tuple(explicit),
        config_prerequisites=tuple(prerequisites),
        suggested_changes=suggested,
        business_request_required=business_required,
        conflict=conflict,
    )


def _pure_config_request(request: str) -> bool:
    """只为有限的纯配置短句省略业务分类，混合任务保持主路由。"""

    text = request.strip()
    return bool(re.fullmatch(
        r"(?:把|将)?(?:应用|系统)?(?:名称|名字)(?:改为|修改为|设为|设置为|改成)\s*[^，,。；;\s]+|"
        r"(?:开启|启用|关闭|禁用)(?:登录认证|登录|权限管理|权限控制)|"
        r"(?:把|将)?(?:登录认证|登录|权限管理|权限控制)(?:开启|启用|关闭|禁用)",
        text,
    ))
