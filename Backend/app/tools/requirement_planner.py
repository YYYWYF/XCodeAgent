from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from app.config import Settings
from app.development_contract import normalize_contract
from app.llm_client import create_anthropic_client


PLANNING_DATA_START = "<planning-data>"
PLANNING_DATA_END = "</planning-data>"

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_TARGET_TYPES = {"frontend", "backend", "fullstack"}


class RequirementPlannerRuntime:
    """Guides users from rough requirements to a concrete development plan."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = create_anthropic_client(settings)

    async def run(
        self,
        message: str,
        *,
        planner_state: Optional[Dict[str, Any]] = None,
        application: Optional[Dict[str, Any]] = None,
        action: str = "answer",
    ) -> Dict[str, Any]:
        state = _normalize_state(planner_state)
        if not state.get("requirement"):
            state["requirement"] = message.strip()

        if action == "finalize" or _should_generate_plan(state):
            payload = await self._generate_plan(message, state=state, application=application)
            payload["state"] = _next_state(state, payload)
            return payload

        payload = await self._generate_questions(message, state=state, application=application)
        payload["state"] = _next_state(state, payload)
        return payload

    async def _generate_questions(
        self,
        message: str,
        *,
        state: Dict[str, Any],
        application: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        prompt = _question_prompt(message, state, application)
        data = await self._call_json(prompt, max_tokens=2200)
        questions = _normalize_questions(data.get("questions"))
        if not questions:
            questions = _fallback_questions(state)

        return {
            "tool": "requirement_planner",
            "status": "questions",
            "phase": "discovery",
            "iteration": int(state.get("iteration", 0)) + 1,
            "message": str(
                data.get("message")
                or "我需要再确认几个关键点，选完这些问题后就能继续收敛开发计划。"
            ),
            "questions": questions[:5],
            "answers": list(state.get("answers", [])),
            "plan": None,
        }

    async def _generate_plan(
        self,
        message: str,
        *,
        state: Dict[str, Any],
        application: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        prompt = _plan_prompt(message, state, application)
        data = await self._call_json(prompt, max_tokens=3600)
        plan = _normalize_plan(data.get("plan") if "plan" in data else data, state)

        return {
            "tool": "requirement_planner",
            "status": "plan",
            "phase": "planning",
            "iteration": int(state.get("iteration", 0)) + 1,
            "message": str(data.get("message") or "信息已经足够，我先生成一版可执行的开发计划。"),
            "questions": [],
            "answers": list(state.get("answers", [])),
            "plan": plan,
        }

    async def _call_json(self, prompt: str, *, max_tokens: int) -> Dict[str, Any]:
        response = await self.client.messages.create(
            model=self.settings.anthropic_api_model,
            max_tokens=max_tokens,
            temperature=0.2,
            system=(
                "You are a senior product and engineering planning assistant. "
                "Return valid JSON only. Do not include markdown fences or commentary."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = _extract_text(response)
        try:
            return _loads_json_object(text)
        except (json.JSONDecodeError, ValueError):
            return {}


def attach_planning_data(message: str, payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{message.rstrip()}\n\n{PLANNING_DATA_START}{encoded}{PLANNING_DATA_END}"


def summarize_planning_payload(payload: Dict[str, Any]) -> str:
    status = payload.get("status")
    if status == "plan" and isinstance(payload.get("plan"), dict):
        plan = payload["plan"]
        title = str(plan.get("title") or "开发计划")
        target_label = _target_type_label(plan.get("targetType"))
        summary = str(plan.get("summary") or payload.get("message") or "")
        next_actions = _as_list(plan.get("nextActions"))[:3]
        lines = [f"已生成《{title}》{target_label}。", summary]
        if next_actions:
            lines.append("下一步建议：")
            lines.extend(f"{index + 1}. {item}" for index, item in enumerate(next_actions))
        return "\n".join(line for line in lines if line)

    questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
    question_titles = [str(question.get("title", "")) for question in questions[:5] if isinstance(question, dict)]
    lines = [str(payload.get("message") or "我先问几个问题，把需求收敛成开发计划。")]
    lines.extend(f"{index + 1}. {title}" for index, title in enumerate(question_titles) if title)
    return "\n".join(lines)


def planner_capabilities() -> Dict[str, Any]:
    return {
        "name": "requirement_planner",
        "description": "Analyze a user's requirement, ask guided questions, and produce a development plan.",
        "input": {
            "action": ["start", "answer", "finalize"],
            "plannerState": "State returned from the previous planner run.",
            "application": "Optional XCodeAgent application metadata.",
        },
        "output": {
            "status": ["questions", "plan"],
            "questions": ["single", "multiple", "text", "confirm"],
            "plan": "Structured development plan when status is plan.",
        },
    }


def _normalize_state(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {"requirement": "", "answers": [], "iteration": 0}
    return {
        "requirement": str(value.get("requirement", "")),
        "answers": _normalize_answers(value.get("answers")),
        "iteration": int(value.get("iteration", 0) or 0),
        "lastQuestions": _normalize_questions(value.get("lastQuestions")),
    }


def _normalize_answers(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    answers: List[Dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        question_id = str(item.get("questionId") or item.get("question_id") or f"answer-{index + 1}")
        answers.append(
            {
                "questionId": question_id,
                "question": str(item.get("question") or ""),
                "value": item.get("value"),
                "label": item.get("label"),
            }
        )
    return answers


def _normalize_questions(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []

    questions: List[Dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        question_type = str(item.get("type") or "single")
        if question_type not in {"single", "multiple", "text", "confirm"}:
            question_type = "single"
        options = _normalize_options(item.get("options"))
        if question_type in {"single", "multiple", "confirm"} and not options:
            options = [
                {"id": "yes", "label": "是", "description": ""},
                {"id": "no", "label": "否", "description": ""},
            ]
        questions.append(
            {
                "id": str(item.get("id") or f"q{index + 1}"),
                "type": question_type,
                "title": str(item.get("title") or item.get("question") or "请补充一个需求信息"),
                "description": str(item.get("description") or ""),
                "required": bool(item.get("required", True)),
                "options": options,
            }
        )
    return questions


def _normalize_options(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    options: List[Dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        options.append(
            {
                "id": str(item.get("id") or f"option-{index + 1}"),
                "label": str(item.get("label") or item.get("title") or f"选项 {index + 1}"),
                "description": str(item.get("description") or ""),
            }
        )
    return options


def _normalize_plan(value: Any, state: Dict[str, Any]) -> Dict[str, Any]:
    plan = dict(value) if isinstance(value, dict) else _fallback_plan(state)
    plan["targetType"] = _normalize_target_type(
        plan.get("targetType") or plan.get("target_type"),
        state,
    )
    plan.setdefault("title", "应用开发计划")
    plan.setdefault("summary", "基于当前需求和选择结果生成的开发计划。")
    plan.setdefault("sdd", _fallback_sdd(state))
    plan.setdefault("features", [])
    plan.setdefault("sharedWork", [])
    plan.setdefault("taskGraph", {"tasks": []})
    plan.setdefault("verificationPlan", {"commands": [], "checks": []})
    plan.setdefault("risks", [])
    plan.setdefault("openQuestions", [])
    plan.setdefault("nextActions", [])
    return normalize_contract(plan, requirement=str(state.get("requirement") or ""))


def _next_state(state: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    next_state = dict(state)
    next_state["iteration"] = int(payload.get("iteration", state.get("iteration", 0)))
    next_state["lastQuestions"] = payload.get("questions") or []
    next_state["status"] = payload.get("status")
    if payload.get("plan"):
        next_state["plan"] = payload["plan"]
    return next_state


def _should_generate_plan(state: Dict[str, Any]) -> bool:
    answers = state.get("answers")
    iteration = int(state.get("iteration", 0) or 0)
    return isinstance(answers, list) and len(answers) >= 6 or iteration >= 3


def _normalize_target_type(value: Any, state: Dict[str, Any]) -> str:
    raw = str(value or "").strip().lower()
    if raw in _TARGET_TYPES:
        return raw
    if any(token in raw for token in ("fullstack", "full-stack", "全栈", "前后端", "端到端")):
        return "fullstack"
    if any(token in raw for token in ("backend", "back-end", "后端", "接口", "服务", "数据库")):
        return "backend"
    if any(token in raw for token in ("frontend", "front-end", "前端", "页面", "界面", "组件")):
        return "frontend"
    return _infer_target_type(state)


def _infer_target_type(state: Dict[str, Any]) -> str:
    parts = [str(state.get("requirement") or "")]
    for answer in _normalize_answers(state.get("answers")):
        parts.append(str(answer.get("question") or ""))
        value = answer.get("value")
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        else:
            parts.append(str(value or ""))
        parts.append(str(answer.get("label") or ""))

    text = " ".join(parts).lower()
    if any(token in text for token in ("fullstack", "full-stack", "全栈", "前后端", "端到端", "闭环")):
        return "fullstack"
    frontend = any(
        token in text
        for token in ("frontend", "front-end", "前端", "页面", "界面", "组件", "样式", "布局", "react", "antd")
    )
    backend = any(
        token in text
        for token in ("backend", "back-end", "后端", "接口", "api", "服务", "数据库", "权限", "认证", "存储")
    )
    if frontend and backend:
        return "fullstack"
    if backend:
        return "backend"
    if frontend:
        return "frontend"
    return "fullstack"


def _target_type_label(value: Any) -> str:
    labels = {"frontend": "（前端需求）", "backend": "（后端需求）", "fullstack": "（全栈需求）"}
    return labels.get(str(value or ""), "")


def _question_prompt(
    message: str,
    state: Dict[str, Any],
    application: Optional[Dict[str, Any]],
) -> str:
    return f"""
你是一个类似 Plan Mode 的需求澄清助手，目标是把用户的粗略需求逐步转成开发计划。

请根据已有信息生成最多 3 个最关键的问题，问题应该让用户可以快速选择，必要时才用短文本。

约束：
- 面向完整应用开发，而不是单纯前端页面开发。
- 已有固定模块可以让用户选择是否集成，例如登录认证、权限、埋点、主题、布局、数据源、API 集成、部署/验证。
- 不要询问页面和接口的详细字段配置，这部分后续会由更可视化的界面完成。
- 如果无法确定需求主要落在前端、后端还是全栈，第一优先询问落地范围。
- 优先确认：业务目标、用户角色、核心流程、固定模块选择、数据来源、权限边界、API/服务边界、交付优先级、验证方式。
- 只询问会阻塞开发设计或验证方式的问题，不要为了完整性而追问。
- 问题必须可直接渲染成 UI。
- 返回 JSON，不要 markdown。

JSON 结构：
{{
  "message": "对用户说明为什么要问这些问题",
  "questions": [
    {{
      "id": "stable-kebab-id",
      "type": "single | multiple | text | confirm",
      "title": "问题标题",
      "description": "一句补充说明",
      "required": true,
      "options": [
        {{"id": "option-id", "label": "选项文案", "description": "选项影响"}}
      ]
    }}
  ]
}}

应用元数据：
{json.dumps(application or {}, ensure_ascii=False, indent=2)}

当前需求：
{state.get("requirement") or message}

用户本轮输入：
{message}

已有答案：
{json.dumps(state.get("answers", []), ensure_ascii=False, indent=2)}
""".strip()


def _plan_prompt(
    message: str,
    state: Dict[str, Any],
    application: Optional[Dict[str, Any]],
) -> str:
    return f"""
你是一个资深应用开发规划 agent。请基于用户需求、应用元数据和已收集答案，生成一份统一的 SDD 和可执行开发计划。

要求：
- 面向 XCodeAgent 里的完整应用生成，不是只做前端页面。
- 如果计划包含 React + TypeScript + Ant Design 前端代码，前端实现必须遵循 React 最佳实践、Ant Design v4.24.16、内置 `REACT_BEST_PRACTICES_GUIDE.md`、`AGENTS.md` 和 `react-antd-v4-codegen` 引用规则。
- 不要把页面和 API 分成两套计划。请按业务功能切片输出，每个 feature 同时包含 UI、API、数据模型、验收标准和验证方式。
- 页面和接口字段详设可以标记为后续可视化配置，不要展开到字段级穷举。
- 必须识别需求类型，并输出 `targetType`，只能是 `frontend`、`backend` 或 `fullstack`。
- 计划要具体到可分发任务，但不要写代码。
- 任务图要考虑效率：能并行的独立功能切片应标记 canRunInParallel=true；共享能力、同文件修改和最终验证必须串行。
- 最终必须包含 verificationPlan，用于验证生成代码是否准确。
- 返回 JSON，不要 markdown。

JSON 结构：
{{
  "message": "一句话说明计划已生成",
  "plan": {{
    "title": "计划标题",
    "targetType": "frontend | backend | fullstack",
    "summary": "一段摘要",
    "sdd": {{
      "spec": {{
        "goal": "业务目标",
        "users": ["用户角色"],
        "scopeIn": ["本期包含"],
        "scopeOut": ["本期不包含"],
        "acceptanceCriteria": ["整体验收标准"]
      }},
      "design": {{
        "features": ["功能切片名称"],
        "sharedCapabilities": ["登录、布局、权限、API client 等共享能力"],
        "apiConventions": ["接口约定"],
        "dataModels": [{{"name": "模型名", "description": "模型用途"}}],
        "permissions": ["权限边界"],
        "errorHandling": ["异常、空态、加载态处理"]
      }}
    }},
    "features": [
      {{
        "id": "stable-feature-id",
        "name": "功能切片名称",
        "userGoal": "用户完成什么目标",
        "ui": {{
          "pages": ["页面或组件"],
          "states": ["loading", "empty", "error", "success"],
          "interactions": ["关键交互"]
        }},
        "apis": [
          {{"method": "GET/POST/PUT/DELETE", "path": "/api/example", "purpose": "用途"}}
        ],
        "dataModels": ["模型名"],
        "dependencies": ["依赖的共享能力或其他 feature id"],
        "acceptanceCriteria": ["该功能验收标准"],
        "verification": ["该功能验证方式"]
      }}
    ],
    "sharedWork": [
      {{"id": "shared-api-client", "title": "共享任务", "reason": "为什么需要"}}
    ],
    "taskGraph": {{
      "tasks": [
        {{
          "id": "task-id",
          "title": "任务标题",
          "type": "inspect | shared | feature | test | verify",
          "featureId": "stable-feature-id 或 null",
          "dependsOn": ["task-id"],
          "targetFiles": ["可能修改的文件或目录"],
          "canRunInParallel": true,
          "acceptanceCriteria": ["任务验收标准"],
          "verificationCommands": ["npm run build"]
        }}
      ]
    }},
    "verificationPlan": {{
      "commands": ["构建、测试或静态检查命令"],
      "checks": ["人工或自动验收检查点"]
    }},
    "risks": ["风险或注意事项"],
    "openQuestions": ["仍待确认的问题"],
    "nextActions": ["下一步动作"]
  }}
}}

应用元数据：
{json.dumps(application or {}, ensure_ascii=False, indent=2)}

当前需求：
{state.get("requirement") or message}

用户本轮输入：
{message}

已收集答案：
{json.dumps(state.get("answers", []), ensure_ascii=False, indent=2)}
""".strip()


def _fallback_questions(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    iteration = int(state.get("iteration", 0) or 0)
    if iteration <= 0:
        return [
            {
                "id": "target-surface",
                "type": "single",
                "title": "这次需求主要落在哪一层？",
                "description": "我会用它判断后续是前端需求、后端需求还是全栈需求。",
                "required": True,
                "options": [
                    {"id": "frontend", "label": "前端", "description": "主要改页面、组件、样式、路由或交互。"},
                    {"id": "backend", "label": "后端", "description": "主要改接口、服务、数据模型、权限或存储。"},
                    {"id": "fullstack", "label": "全栈", "description": "需要前端、后端或数据/API 一起闭环。"},
                ],
            },
            {
                "id": "business-goal",
                "type": "single",
                "title": "这个应用最核心的业务目标是什么？",
                "description": "我会据此决定功能切片、数据/API 边界和交付优先级。",
                "required": True,
                "options": [
                    {"id": "manage-data", "label": "管理业务数据", "description": "偏后台 CRUD、筛选、批量操作。"},
                    {"id": "workflow", "label": "推进业务流程", "description": "偏审批、状态流转、任务处理。"},
                    {"id": "dashboard", "label": "查看分析看板", "description": "偏指标、图表、概览。"},
                    {"id": "customer-facing", "label": "面向客户使用", "description": "偏体验、转化、移动适配。"},
                ],
            },
            {
                "id": "target-users",
                "type": "multiple",
                "title": "主要用户角色有哪些？",
                "description": "角色会影响权限、菜单和默认入口。",
                "required": True,
                "options": [
                    {"id": "admin", "label": "管理员", "description": "需要配置、权限和全局管理。"},
                    {"id": "operator", "label": "运营/业务人员", "description": "需要高效处理日常任务。"},
                    {"id": "viewer", "label": "只读查看者", "description": "主要消费信息和报表。"},
                    {"id": "external-user", "label": "外部用户", "description": "需要更严格的认证和体验约束。"},
                ],
            },
            {
                "id": "integrated-modules",
                "type": "multiple",
                "title": "本期要集成哪些固定模块？",
                "description": "这些模块可以直接走已有代码模板。",
                "required": True,
                "options": [
                    {"id": "auth", "label": "登录认证", "description": "接入登录、会话和权限入口。"},
                    {"id": "api-integration", "label": "API 集成", "description": "约定请求封装、错误处理和数据适配。"},
                    {"id": "tracking", "label": "埋点统计", "description": "记录关键行为和页面访问。"},
                    {"id": "theme", "label": "主题设置", "description": "使用已封装主题方案。"},
                    {"id": "layout", "label": "布局设置", "description": "使用已封装布局方案。"},
                ],
            },
            {
                "id": "delivery-priority",
                "type": "single",
                "title": "你更希望先交付哪一类成果？",
                "description": "这会决定计划里的里程碑顺序。",
                "required": True,
                "options": [
                    {"id": "design-plan", "label": "设计方案", "description": "先把功能、数据、API 和验证方案定清楚。"},
                    {"id": "usable-mvp", "label": "可用 MVP", "description": "前后端闭环优先。"},
                    {"id": "production-ready", "label": "生产可上线", "description": "更重视异常、权限、质量和验收。"},
                ],
            },
        ]

    return [
        {
            "id": "data-source",
            "type": "single",
            "title": "数据主要从哪里来？",
            "description": "我会据此安排数据流、接口、状态和联调策略。",
            "required": True,
            "options": [
                {"id": "existing-api", "label": "已有服务/API", "description": "以对接、适配和联调为主。"},
                {"id": "new-api", "label": "需要新建 API", "description": "计划里会包含接口和数据模型设计。"},
                {"id": "mock-first", "label": "先用 Mock 数据", "description": "适合先验证功能流程和交互闭环。"},
            ],
        },
        {
            "id": "permission-depth",
            "type": "single",
            "title": "权限控制需要做到什么粒度？",
            "description": "先确认边界，详设后续再展开。",
            "required": True,
            "options": [
                {"id": "none", "label": "暂不需要", "description": "应用流程更轻。"},
                {"id": "module", "label": "模块/入口级", "description": "控制功能入口、路由或接口访问。"},
                {"id": "action", "label": "操作/API 级", "description": "需要更完整的权限模型和后端校验。"},
            ],
        },
        {
            "id": "special-notes",
            "type": "text",
            "title": "有没有必须提前纳入计划的特殊约束？",
            "description": "例如兼容性、上线时间、数据安全、第三方系统等。",
            "required": False,
            "options": [],
        },
    ]


def _fallback_plan(state: Dict[str, Any]) -> Dict[str, Any]:
    sdd = _fallback_sdd(state)
    return {
        "title": "应用开发计划",
        "targetType": _infer_target_type(state),
        "summary": "基于当前需求和已选择信息，先交付一版按功能切片组织的统一开发计划。",
        "sdd": sdd,
        "features": [
            {
                "id": "core-workflow",
                "name": "核心业务流程",
                "userGoal": "用户可以完成需求中描述的主要业务动作。",
                "ui": {
                    "pages": ["核心页面"],
                    "states": ["loading", "empty", "error", "success"],
                    "interactions": ["查看", "筛选", "提交或保存"],
                },
                "apis": [],
                "dataModels": [],
                "dependencies": ["shared-app-shell"],
                "acceptanceCriteria": ["核心流程可运行", "异常和空态有明确反馈"],
                "verification": ["运行构建或类型检查", "按验收标准做 smoke check"],
            }
        ],
        "sharedWork": [
            {"id": "shared-app-shell", "title": "确认应用骨架、路由、布局和 API 调用约定", "reason": "所有功能切片都依赖工程既有结构。"}
        ],
        "taskGraph": {
            "tasks": [
                {
                    "id": "inspect-workspace",
                    "title": "读取工程结构并识别路由、接口、构建命令",
                    "type": "inspect",
                    "featureId": None,
                    "dependsOn": [],
                    "targetFiles": [],
                    "canRunInParallel": False,
                    "acceptanceCriteria": ["识别项目技术栈和关键入口"],
                    "verificationCommands": [],
                },
                {
                    "id": "implement-core-workflow",
                    "title": "实现核心业务流程",
                    "type": "feature",
                    "featureId": "core-workflow",
                    "dependsOn": ["inspect-workspace"],
                    "targetFiles": [],
                    "canRunInParallel": True,
                    "acceptanceCriteria": ["核心流程符合功能验收标准"],
                    "verificationCommands": [],
                },
                {
                    "id": "verify-generated-code",
                    "title": "验证生成代码",
                    "type": "verify",
                    "featureId": None,
                    "dependsOn": ["implement-core-workflow"],
                    "targetFiles": [],
                    "canRunInParallel": False,
                    "acceptanceCriteria": ["构建、测试或 smoke check 通过"],
                    "verificationCommands": [],
                },
            ]
        },
        "verificationPlan": {
            "commands": [],
            "checks": ["检查 git diff", "运行可用的 build/lint/test", "按功能验收标准做 smoke check"],
        },
        "risks": ["需求细节仍需在功能、数据、API、交互和验证详设阶段补充。"],
        "openQuestions": [],
        "nextActions": ["进入工程侦察", "按任务图分批实现", "执行验证计划"],
    }


def _fallback_sdd(state: Dict[str, Any]) -> Dict[str, Any]:
    requirement = str(state.get("requirement") or "用户当前需求")
    return {
        "spec": {
            "goal": requirement,
            "users": [],
            "scopeIn": ["核心功能切片", "必要 UI/API/数据设计", "最终验证环节"],
            "scopeOut": ["未确认的复杂字段级配置"],
            "acceptanceCriteria": ["生成代码应能通过可用的构建、测试或 smoke 验证。"],
        },
        "design": {
            "features": ["核心业务流程"],
            "sharedCapabilities": ["工程结构识别", "路由/布局接入", "API 调用约定"],
            "apiConventions": [],
            "dataModels": [],
            "permissions": [],
            "errorHandling": ["loading", "empty", "error", "success"],
        },
    }


def _extract_text(response: Any) -> str:
    chunks: List[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    return "\n".join(chunks).strip()


def _loads_json_object(value: str) -> Dict[str, Any]:
    cleaned = value.strip()
    fenced = _JSON_FENCE_RE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    data = json.loads(cleaned)
    return data if isinstance(data, dict) else {}


def _as_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]
