from __future__ import annotations

import json
import re
from typing import Any

from app.agents.design_conversation.models import (
    DesignConversationDecision,
    ProductChangeLevel,
    ProductConversationIntent,
    SuggestedPhase,
)

_TEST_SIGNALS = (
    "运行测试",
    "跑一下测试",
    "写测试",
    "集成测试",
    "测试修复",
    "单元测试",
    "测试一下",
    "验证一下",
)
_DEVELOPMENT_SIGNALS = (
    ".tsx",
    ".ts",
    ".py",
    "源代码",
    "修改代码",
    "后端代码",
    "前端代码",
    "修复代码",
    "修复 bug",
    "修 bug",
    "组件源码",
    "重构",
    "安装依赖",
    "运行命令",
    "执行命令",
    "shell",
)
_PLANNING_SIGNALS = (
    "api",
    "endpoint",
    "接口",
    "request contract",
    "response contract",
    "request/response",
    "请求参数",
    "响应字段",
    "schema",
    "数据库",
    "表结构",
    "sqlite",
    "postgresql",
    "索引",
    "redis",
    "缓存策略",
    "消息队列",
    "技术方案",
    "技术规划",
    "技术架构",
    "实体持久化",
    "数据源技术",
    "数据源实现",
    "technicalplan",
    "technical plan",
)


def fallback_design_conversation_decision(
    request: str,
    *,
    requirement_spec: dict[str, Any] | None,
    product_plan: dict[str, Any] | None,
) -> DesignConversationDecision:
    """模型不可用时执行保守兜底，越界信号优先且不会获得额外权限。"""

    text = request.strip().lower()
    intent, change_level, suggested_phase = _fallback_semantics(text)
    response = ""
    clarification_question = ""
    if intent == "read_only":
        response = _fallback_read_only_response(text, requirement_spec, product_plan)
    elif intent == "chat":
        response = "我可以帮你梳理或调整当前产品的需求、页面行为和 UI 设计。"
    elif intent == "clarification":
        clarification_question = "请再说明你希望调整的产品事实、页面行为或 UI 表现。"
    return DesignConversationDecision(
        intent=intent,
        change_level=change_level,
        reason="分类模型不可用，已使用产品阶段保守规则判断。",
        affected_page_ids=_mentioned_page_ids(text, product_plan),
        response=response,
        suggested_phase=suggested_phase,
        clarification_question=clarification_question,
    )


def invalid_model_decision(value: Any) -> DesignConversationDecision:
    """把无法通过当前语义契约的模型输出降级为零写入越界结果。"""

    serialized = json.dumps(value, ensure_ascii=False, default=str).lower()
    if any(signal in serialized for signal in ("test", "测试", "verify")):
        suggested_phase: SuggestedPhase = "test"
    elif any(
        signal in serialized
        for signal in ("code", "frontend", "backend", "tsx", "python", "development")
    ):
        suggested_phase = "development"
    elif any(
        signal in serialized
        for signal in ("technical", "planning", "api", "schema", "database", "数据库")
    ):
        suggested_phase = "planning"
    else:
        suggested_phase = "none"
    return DesignConversationDecision(
        intent="out_of_scope",
        change_level="none",
        reason="Coordinator 返回了当前产品语义契约不允许的结果，已拒绝执行。",
        suggested_phase=suggested_phase,
    )


def out_of_scope_phase_for_request(request: str) -> SuggestedPhase | None:
    """只识别产品阶段绝对禁止执行的技术、开发和测试能力请求。"""

    text = request.strip().lower()
    if any(signal in text for signal in _TEST_SIGNALS):
        return "test"
    if any(signal in text for signal in _DEVELOPMENT_SIGNALS):
        return "development"
    if any(signal in text for signal in _PLANNING_SIGNALS):
        return "planning"
    return None


def _fallback_semantics(
    text: str,
) -> tuple[ProductConversationIntent, ProductChangeLevel, SuggestedPhase]:
    """按越界优先级为模型故障请求选择最保守的产品语义。"""

    requirement_signals = (
        "需求",
        "范围",
        "角色",
        "模块",
        "增加页面",
        "新增页面",
        "增加一个",
        "新增一个",
        "详情页",
        "去掉",
        "删除页面",
        "业务流程",
        "业务权限",
        "管理员",
        "普通用户",
        "审核员",
        "不能看到",
        "不能访问",
        "不需要这个页面",
    )
    product_signals = (
        "筛选功能",
        "批量",
        "支持批量",
        "批量归档",
        "审批完成",
        "审批通过",
        "成功提示",
        "可以从",
        "操作",
        "跳转",
        "导出",
        "验收标准",
        "加载状态",
        "空状态",
        "业务结果",
    )
    ui_signals = (
        "ui",
        "界面",
        "布局",
        "样式",
        "颜色",
        "字号",
        "间距",
        "圆角",
        "两列",
        "双栏",
        "导航",
        "弹窗",
        "控件",
        "响应式",
        "暗色",
        "深色",
        "浅色",
    )
    question_signals = ("哪些", "什么", "多少", "为什么", "现在有", "当前有", "目前有")
    if out_of_scope_phase := out_of_scope_phase_for_request(text):
        return "out_of_scope", "none", out_of_scope_phase
    if any(signal in text for signal in question_signals) or text.endswith(("?", "？")):
        return "read_only", "none", "none"
    if any(signal in text for signal in requirement_signals):
        return "requirement_change", "requirement", "none"
    if any(signal in text for signal in product_signals):
        return "requirement_change", "product_behavior", "none"
    if any(signal in text for signal in ui_signals):
        return "ui_change", "ui", "none"
    if any(signal in text for signal in ("你好", "谢谢", "辛苦了", "嗨", "hello")):
        return "chat", "none", "none"
    return "clarification", "none", "none"


def _fallback_read_only_response(
    request: str,
    requirement_spec: dict[str, Any] | None,
    product_plan: dict[str, Any] | None,
) -> str:
    """模型故障时仅从紧凑产品产物回答常见只读问题。"""

    spec = requirement_spec or {}
    plan = product_plan or {}
    if "角色" in request:
        names = _item_names(spec.get("user_roles"))
        return (
            f"当前产品角色包括：{'、'.join(names)}。"
            if names
            else "当前产品设计中还没有可确认的角色信息。"
        )
    if "页面" in request:
        names = _item_names(plan.get("pages")) or _item_names(spec.get("pages"))
        return (
            f"当前产品页面包括：{'、'.join(names)}。"
            if names
            else "当前产品设计中还没有可确认的页面信息。"
        )
    if "操作" in request:
        page_name, action_names = _page_actions_for_request(request, plan)
        if action_names:
            return f"{page_name or '该页面'}当前支持：{'、'.join(action_names)}。"
        return "当前产品设计中没有足够信息确定该页面的操作。"
    return "当前产品设计中没有足够信息确定这一点。"


def _item_names(value: Any) -> list[str]:
    """从正式产物条目中提取非空名称并保持原有顺序。"""

    return [
        name
        for item in value or []
        if isinstance(item, dict) and (name := str(item.get("name") or "").strip())
    ][:100]


def _page_actions_for_request(
    request: str,
    product_plan: dict[str, Any],
) -> tuple[str, list[str]]:
    """从显式提及的 ProductPlan 页面中读取当前产品操作。"""

    for page in product_plan.get("pages", []):
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("pageId") or "").strip().lower()
        page_name = str(page.get("name") or "").strip()
        if (page_id and page_id in request) or (page_name and page_name.lower() in request):
            return page_name, _item_names(page.get("actions"))
    return "", []


def _mentioned_page_ids(
    request: str,
    product_plan: dict[str, Any] | None,
) -> list[str]:
    """在兜底路径中按 pageId 或页面名识别显式提及的页面。"""

    matched: list[str] = []
    for page in (product_plan or {}).get("pages", []):
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("pageId") or "").strip()
        name = str(page.get("name") or "").strip().lower()
        if not page_id:
            continue
        if re.search(rf"(?<![\w-]){re.escape(page_id.lower())}(?![\w-])", request) or (
            name and name in request
        ):
            matched.append(page_id)
    return matched
