"""自然语言中的应用配置目标与业务访问控制变更识别。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.application_config_change import ApplicationConfigPath


@dataclass(frozen=True)
class CapabilityIntent:
    """仅表达当前用户的配置目标，不携带或推断应用当前状态。"""

    path: ApplicationConfigPath
    enabled: bool
    evidence: str


# 接口埋点必须先作为完整词组识别，避免误匹配成普通埋点。
_CAPABILITY_TERMS: tuple[tuple[ApplicationConfigPath, str], ...] = (
    ("auth.enable", r"登录认证|登录|登陆|认证|\blogin\b|\bauthentication\b"),
    ("authorization.enabled", r"权限管理|权限控制|\brbac\b|\bauthorization\b"),
    ("track.enable", r"(?<!接口)(?<!API)(?<!api)埋点|\btrack\b"),
    ("apiTrack.enable", r"接口埋点|api\s*埋点|\bapitrack\b"),
)
_ENABLE = r"增加|添加|启用|开启|需要|支持|\benable\b|\badd\b"
_DISABLE = r"不再需要|不需要|无需|关闭|禁用|停用|取消|移除|删除|去掉|\bdisable\b|\bremove\b"
_GAP = r"\s*(?:(?:应用|系统|用户)的?\s*)?(?:(?:一个|一套|内置的?|统一的?)\s*)?"
_DETAIL = re.compile(
    r"^\s*(?:功能|模块|能力)?\s*"
    r"(?:页|按钮|样式|颜色|文案|图标|接口|流程|错误|日志|提示|配置项|后|才能|失败|成功|系统"
    r"|\bpage\b|\bbutton\b|\bstyle\b|\berror\b)",
    re.IGNORECASE,
)


def _explicit_switches(clause: str, terms: str) -> list[tuple[int, bool]]:
    """仅匹配直接作用于能力的开关指令，排除否定命令、疑问和局部界面修改。"""

    if re.search(r"如果|假如|是否|能否|为什么|如何|怎么|例如|比如", clause):
        return []
    matches: list[tuple[int, bool]] = []
    for enabled, verbs in ((False, _DISABLE), (True, _ENABLE)):
        pattern = rf"(?:{verbs}){_GAP}(?:{terms})"
        for match in re.finditer(pattern, clause, flags=re.IGNORECASE):
            prefix = clause[:match.start()].rstrip()
            # “不要开启”“不需要登录”中的子串“开启”“需要”不能成为启用指令。
            if re.search(r"(?:不|别|不要|不必|无需|不再|不用|不需要|不想|禁止|do not|don't)\s*$", prefix):
                continue
            if _DETAIL.match(clause[match.end():]):
                continue
            matches.append((match.start(), enabled))
    # 支持“登录不再需要了”“将权限管理关闭”等能力在动词前面的表达。
    suffix_pattern = rf"(?:{terms})\s*(?:功能|模块|能力)?\s*(?:设为|设置为|改为)?\s*(?P<verb>开启|启用|关闭|禁用|停用|不再需要|不需要)"
    for match in re.finditer(suffix_pattern, clause, flags=re.IGNORECASE):
        if re.search(r"(?:不|别|不要|不必)\s*(?:把|将)?\s*$", clause[:match.start()]):
            continue
        matches.append((match.start(), match.group("verb") in {"开启", "启用"}))
    return sorted(matches)


def resolve_capability_intents(
    request: str,
) -> list[CapabilityIntent]:
    """识别四类应用配置的显式启停目标；当前值由配置 Resolver 读取。"""

    text = str(request or "").strip()
    if not text:
        return []
    intents: list[CapabilityIntent] = []
    for clause in re.split(r"[，,。；;！？!?\n]", text):
        for path, terms in _CAPABILITY_TERMS:
            # 屏蔽完整 API 埋点词组，防止“API 埋点关闭”被普通埋点的后置动词规则重复识别。
            candidate = (
                re.sub(_CAPABILITY_TERMS[3][1], "__api_tracking__", clause, flags=re.IGNORECASE)
                if path == "track.enable" else clause
            )
            for _, enabled in _explicit_switches(candidate, terms):
                intents.append(CapabilityIntent(path=path, enabled=enabled, evidence=request))
    return intents


def has_explicit_capability_change(request: str) -> bool:
    """供现有正式修订路由判断是否存在配置启停目标，不查询当前值。"""

    return bool(resolve_capability_intents(request))


def has_explicit_business_access_control_change(request: str) -> bool:
    """识别明确改变角色、页面或操作可访问性的自然语言需求。"""

    text = str(request or "").strip().casefold()
    if not text:
        return False
    permission_terms = ("权限", "授权", "访问控制", "无权限")
    access_patterns = (
        r"才能看到",
        r"才能查看",
        r"仅.*可见",
        r"只有.*可见",
        r"禁止访问",
        r"无法访问",
        r"取消.*权限",
        r"所有人.*(?:可见|访问)",
        r"403",
    )
    return any(term in text for term in permission_terms) and any(
        re.search(pattern, text) is not None for pattern in access_patterns
    )
