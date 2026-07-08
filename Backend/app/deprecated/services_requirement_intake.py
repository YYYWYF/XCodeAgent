# from __future__ import annotations
# 
# import re
# from typing import Any, Literal, TypedDict
# 
# 
# Complexity = Literal["simple", "complex"]
# TargetType = Literal["frontend", "backend", "fullstack", "unknown"]
# Route = Literal["main-agent", "development-orchestrator"]
# 
# 
# class RequirementIntakeDecision(TypedDict):
#     complexity: Complexity
#     targetType: TargetType
#     route: Route
#     confidence: float
#     reasons: list[str]
#     signals: dict[str, Any]
# 
# 
# _FRONTEND_KEYWORDS = (
#     "frontend",
#     "front-end",
#     "react",
#     "vue",
#     "antd",
#     "ant design",
#     "typescript",
#     "css",
#     "less",
#     "ui",
#     "ux",
#     "page",
#     "component",
#     "页面",
#     "界面",
#     "前端",
#     "组件",
#     "样式",
#     "布局",
#     "按钮",
#     "表单",
#     "弹窗",
#     "路由",
#     "看板",
# )
# 
# _BACKEND_KEYWORDS = (
#     "backend",
#     "back-end",
#     "api",
#     "fastapi",
#     "server",
#     "service",
#     "database",
#     "db",
#     "sql",
#     "postgres",
#     "redis",
#     "auth",
#     "endpoint",
#     "接口",
#     "后端",
#     "服务",
#     "数据库",
#     "数据表",
#     "鉴权",
#     "认证",
#     "权限",
#     "登录态",
#     "存储",
# )
# 
# _FULLSTACK_KEYWORDS = (
#     "fullstack",
#     "full-stack",
#     "end-to-end",
#     "e2e",
#     "前后端",
#     "全栈",
#     "端到端",
#     "闭环",
#     "从页面到接口",
# )
# 
# _COMPLEX_KEYWORDS = (
#     "开发一个",
#     "做一个",
#     "搭建",
#     "从零",
#     "完整",
#     "系统",
#     "平台",
#     "应用",
#     "管理后台",
#     "工作流",
#     "审批",
#     "多角色",
#     "权限",
#     "登录",
#     "注册",
#     "支付",
#     "订单",
#     "报表",
#     "仪表盘",
#     "数据模型",
#     "数据库",
# )
# 
# _FEATURE_KEYWORDS = (
#     "登录",
#     "注册",
#     "权限",
#     "角色",
#     "用户",
#     "列表",
#     "表格",
#     "搜索",
#     "筛选",
#     "导入",
#     "导出",
#     "报表",
#     "看板",
#     "接口",
#     "数据库",
#     "审批",
#     "通知",
#     "支付",
#     "订单",
# )
# 
# _SIMPLE_SCOPE_RE = re.compile(
#     r"^\s*(修复|改|修改|调整|新增|添加|删除|重命名|解释|查看|运行|测试|检查|帮我看|fix|change|add|remove|rename|explain|run|test)",
#     re.IGNORECASE,
# )
# 
# 
# def analyze_requirement_intake(message: str) -> RequirementIntakeDecision:
#     text = " ".join(message.strip().split())
#     lowered = text.lower()
#     frontend_hits = _keyword_hits(lowered, _FRONTEND_KEYWORDS)
#     backend_hits = _keyword_hits(lowered, _BACKEND_KEYWORDS)
#     fullstack_hits = _keyword_hits(lowered, _FULLSTACK_KEYWORDS)
#     complex_hits = _keyword_hits(lowered, _COMPLEX_KEYWORDS)
#     feature_hits = _keyword_hits(lowered, _FEATURE_KEYWORDS)
#     separator_count = sum(text.count(separator) for separator in ("、", "，", ",", "\n", ";", "；"))
# 
#     target_type = _target_type(
#         frontend_hits=frontend_hits,
#         backend_hits=backend_hits,
#         fullstack_hits=fullstack_hits,
#         complex_hits=complex_hits,
#     )
#     score = 0
#     reasons: list[str] = []
# 
#     if len(text) >= 140:
#         score += 2
#         reasons.append("requirement is long enough to need upfront scoping")
#     if fullstack_hits:
#         score += 3
#         reasons.append("explicit full-stack or end-to-end wording")
#     if frontend_hits and backend_hits:
#         score += 2
#         reasons.append("mentions both frontend and backend concerns")
#     if complex_hits:
#         score += min(3, len(complex_hits))
#         reasons.append("contains application-level feature signals")
#     if any(keyword in complex_hits for keyword in ("系统", "平台", "应用", "管理后台")):
#         score += 1
#         reasons.append("mentions an application-level delivery surface")
#     if len(feature_hits) >= 3:
#         score += 2
#         reasons.append("mentions multiple feature areas")
#     if separator_count >= 3:
#         score += 1
#         reasons.append("lists several requested capabilities")
#     if _looks_scoped_simple(text, complex_hits=complex_hits, feature_hits=feature_hits):
#         score -= 2
#         reasons.append("looks like a scoped implementation or maintenance request")
# 
#     complexity: Complexity = "complex" if score >= 3 else "simple"
#     route: Route = "development-orchestrator" if complexity == "complex" else "main-agent"
#     confidence = _confidence(score, complexity)
# 
#     if not reasons:
#         reasons.append("short request without broad planning signals")
# 
#     return {
#         "complexity": complexity,
#         "targetType": target_type,
#         "route": route,
#         "confidence": confidence,
#         "reasons": reasons[:4],
#         "signals": {
#             "frontend": frontend_hits[:6],
#             "backend": backend_hits[:6],
#             "fullstack": fullstack_hits[:6],
#             "complex": complex_hits[:6],
#             "features": feature_hits[:8],
#             "separatorCount": separator_count,
#             "length": len(text),
#             "score": score,
#         },
#     }
# 
# 
# def intake_capabilities() -> dict[str, Any]:
#     return {
#         "name": "requirement_intake",
#         "description": "Classifies new user requirements as simple or complex and routes complex work to clarification/planning.",
#         "output": {
#             "complexity": ["simple", "complex"],
#             "targetType": ["frontend", "backend", "fullstack", "unknown"],
#             "route": ["main-agent", "development-orchestrator"],
#         },
#     }
# 
# 
# def summarize_intake(decision: RequirementIntakeDecision) -> str:
#     if decision["complexity"] == "simple":
#         return "我判断这是一个简单需求，直接由主 Agent 处理。"
# 
#     target_label = {
#         "frontend": "前端",
#         "backend": "后端",
#         "fullstack": "全栈",
#         "unknown": "暂不确定",
#     }[decision["targetType"]]
#     if decision["targetType"] == "unknown":
#         return "我判断这是一个复杂需求，会先通过对话确认它更偏前端、后端还是全栈。"
#     return f"我判断这是一个复杂需求，初步范围偏{target_label}，会先通过对话把意图收敛清楚。"
# 
# 
# def _keyword_hits(text: str, keywords: tuple[str, ...]) -> list[str]:
#     return [keyword for keyword in keywords if keyword.lower() in text]
# 
# 
# def _target_type(
#     *,
#     frontend_hits: list[str],
#     backend_hits: list[str],
#     fullstack_hits: list[str],
#     complex_hits: list[str],
# ) -> TargetType:
#     if fullstack_hits or (frontend_hits and backend_hits):
#         return "fullstack"
#     if backend_hits and len(backend_hits) >= len(frontend_hits):
#         return "backend"
#     if frontend_hits:
#         return "frontend"
#     if any(keyword in complex_hits for keyword in ("系统", "平台", "应用", "管理后台")):
#         return "fullstack"
#     return "unknown"
# 
# 
# def _looks_scoped_simple(
#     text: str,
#     *,
#     complex_hits: list[str],
#     feature_hits: list[str],
# ) -> bool:
#     return (
#         len(text) < 90
#         and bool(_SIMPLE_SCOPE_RE.search(text))
#         and len(complex_hits) <= 1
#         and len(feature_hits) <= 2
#     )
# 
# 
# def _confidence(score: int, complexity: Complexity) -> float:
#     if complexity == "complex":
#         return min(0.95, 0.6 + max(score - 3, 0) * 0.08)
#     return min(0.9, 0.55 + max(3 - score, 0) * 0.07)
