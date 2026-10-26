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
    def config_route(self) -> str:
        """返回配置意图的明确路由，避免用 requires_card 猜测是否命中配置。"""

        if self.requires_card:
            return "awaiting_config_confirmation"
        if _has_config_request(self.original_request) and not self.business_request_required:
            return "config_only_satisfied"
        return "business_continuation" if self.business_request_required else "not_applicable"

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
    # 标量地址只在用户给出完整值时生成建议；缺值保留卡片供用户选择，绝不猜测 URL。
    for path, aliases in {
        "auth.yht.centerUri": ("认证中心地址", "认证中心"),
        "auth.yht.authorizeUri": ("登录授权地址", "授权地址"),
        "auth.yht.endUrl": ("前端登出地址", "登出地址"),
        "auth.yht.logoutShareJs": ("登出同步脚本", "同步脚本"),
        "authorization.api.baseUrl": ("权限用户服务地址", "权限服务地址"),
        "authorization.api.viewId": ("用户视图 ID", "用户视图ID", "viewId"),
    }.items():
        scalar = _scalar_config_intent(request, path, aliases)
        if scalar is not None:
            explicit.append(scalar)
            if scalar["desiredValue"] is not None:
                suggested[path] = scalar["desiredValue"]
    name_match = re.search(r"(?:把|将)?(?:应用|系统)?(?:名称|名字)(?:改为|修改为|设为|设置为|改成)\s*([^，,。；;\s]+)", request)
    rename_requested = name_match is not None or re.search(r"(?:应用|系统)?改名", request) is not None
    if rename_requested:
        name = name_match.group(1) if name_match else None
        if name != application.get("appName"):
            explicit.append({"path": "appName", "desiredValue": name, "source": "explicit"})
            if name:
                suggested["appName"] = name
    theme_intent = _theme_color_intent(request)
    if theme_intent is not None:
        desired = theme_intent["desiredValue"]
        if desired is None or desired.casefold() != str(application["theme"]["primaryColor"]).casefold():
            explicit.append(theme_intent)
            if desired is not None:
                suggested["theme.primaryColor"] = desired
    icon_intent = _app_icon_intent(request)
    if icon_intent is not None:
        desired = icon_intent["desiredValue"]
        if desired is None or desired != application.get("appIcon"):
            explicit.append(icon_intent)
            if desired is not None:
                suggested["appIcon"] = desired
    business_access = has_explicit_business_access_control_change(request)
    business_required = business_access or _has_remaining_business_request(request)
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


def _theme_color_intent(request: str) -> dict[str, Any] | None:
    """从应用级主题表达提取受管颜色；页面或组件作用域不属于应用配置。"""

    if not re.search(r"(?:应用|系统|全局)?(?:主题色|主题颜色|主色)", request):
        return None
    if re.search(r"(?:页面|首页|卡片|按钮|组件|模块).{0,12}(?:主题色|主题颜色|主色|颜色)", request):
        return None
    hex_match = re.search(r"#([0-9a-fA-F]{6})\b", request)
    if hex_match:
        value: str | None = f"#{hex_match.group(1).lower()}"
    elif re.search(r"蓝色|蓝", request):
        value = "#1677ff"
    else:
        value = None
    return {"path": "theme.primaryColor", "desiredValue": value, "source": "explicit", "valueStatus": "resolved" if value else "needs_user_selection"}


def _app_icon_intent(request: str) -> dict[str, Any] | None:
    """只映射现有图标选项，未知图标要求用户在配置卡选择。"""

    match = re.search(r"(?:应用|系统)?图标(?:改为|修改为|设为|设置为|改成)\s*([^，,。；;\s]+)", request, re.IGNORECASE)
    if match is None:
        return None
    aliases = {"dashboard": "DashboardOutlined", "仪表盘": "DashboardOutlined", "appstore": "AppstoreOutlined", "应用商店": "AppstoreOutlined", "desktop": "DesktopOutlined", "桌面": "DesktopOutlined"}
    value = aliases.get(match.group(1).casefold())
    return {"path": "appIcon", "desiredValue": value, "source": "explicit", "valueStatus": "resolved" if value else "needs_user_selection"}


def _has_config_request(request: str) -> bool:
    """判断文本是否包含本模块负责的应用配置语义，即使目标当前已满足。"""

    return bool(_theme_color_intent(request) or _app_icon_intent(request) or _scalar_config_intent(request, "", ("认证中心地址", "登录授权地址", "权限用户服务地址", "用户视图 ID", "用户视图ID", "前端登出地址", "登出同步脚本")) or re.search(r"(?:应用|系统)?(?:名称|名字)(?:改为|修改为|设为|设置为|改成)|(?:开启|启用|关闭|禁用)(?:登录认证|登录|权限管理|权限控制)|(?:把|将)?(?:登录认证|登录|权限管理|权限控制)(?:开启|启用|关闭|禁用)", request))


def _scalar_config_intent(request: str, path: str, aliases: tuple[str, ...]) -> dict[str, Any] | None:
    """解析明确的标量配置修改；URL 缺失或非法时要求用户在安全卡片填写。"""

    names = "|".join(re.escape(alias) for alias in aliases)
    match = re.search(rf"(?:把|将)?\s*(?:{names})\s*(?:改为|修改为|设为|设置为|改成)\s*([^，,。；;\s]+)?", request, re.IGNORECASE)
    if match is None:
        return None
    value = match.group(1)
    if path.endswith(("Uri", "Url")) and value is not None and not re.fullmatch(r"https?://[^\s]+", value, re.IGNORECASE):
        value = None
    return {"path": path, "desiredValue": value, "source": "explicit", "valueStatus": "resolved" if value else "needs_user_selection"}


def _has_remaining_business_request(request: str) -> bool:
    """移除配置子句后检查独立业务对象，通用“修改”本身不再制造业务需求。"""

    return bool(re.search(r"新增|增加|创建|开发|实现|修复|查询|页面|接口|按钮|功能", business_request_for_routing(request)))


def business_request_for_routing(request: str) -> str:
    """仅抽出混合请求中的非配置部分供分类，不改变 checkpoint 保存的原始请求。"""

    text = re.sub(r"(?:把|将)?(?:应用|系统)?(?:主题色|主题颜色|主色|图标|名称|名字|认证中心地址|登录授权地址|权限用户服务地址|用户视图\s*ID|前端登出地址|登出同步脚本)(?:改为|修改为|设为|设置为|改成).*?(?=(?:，|,|。|；|;|并|同时|然后)|$)", "", request, flags=re.IGNORECASE)
    return re.sub(r"(?:开启|启用|关闭|禁用)(?:登录认证|登录|权限管理|权限控制)|(?:把|将)?(?:登录认证|登录|权限管理|权限控制)(?:开启|启用|关闭|禁用)", "", text).strip(" ，,；;")
