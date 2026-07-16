from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from app.agents.model_factory import create_chat_model
from app.config import Settings


ModelTextReporter = Callable[[str], Awaitable[None]]


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class ApplicationPageContext(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    scenario: str = Field(default="", max_length=3000)
    terminal: Literal["PC", "Mobile"] = "PC"


class PagePlanningQuestion(ApiModel):
    id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=300)
    rationale: str = Field(default="", max_length=300)
    placeholder: str = Field(default="", max_length=200)


class PagePlanningQuestionsRequest(ApiModel):
    application: ApplicationPageContext


class PagePlanningQuestionsResponse(ApiModel):
    questions: list[PagePlanningQuestion] = Field(min_length=3, max_length=5)


class PagePlanningAnswer(ApiModel):
    question_id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1, max_length=2000)


class ApplicationPageDefinition(ApiModel):
    id: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=500)
    key_features: list[str] = Field(default_factory=list, max_length=8)
    related_page_ids: list[str] = Field(default_factory=list, max_length=8)
    api_ids: list[str] = Field(default_factory=list, max_length=12)
    interactions: list["ApplicationPageInteraction"] = Field(
        default_factory=list, max_length=8
    )


class ApplicationPageInteraction(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    trigger: str = Field(min_length=1, max_length=240)
    user_action: str = Field(min_length=1, max_length=500)
    system_response: str = Field(min_length=1, max_length=500)
    target_page_id: str | None = Field(default=None, max_length=60)
    api_ids: list[str] = Field(default_factory=list, max_length=8)


class ApplicationApiDefinition(ApiModel):
    id: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=120)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=500)
    request_design: str = Field(default="无需业务请求参数", max_length=1000)
    response_design: str = Field(min_length=1, max_length=1000)
    used_by_page_ids: list[str] = Field(default_factory=list, max_length=12)


class ApplicationPagePlan(ApiModel):
    schema_version: Literal[1] = 1
    application: ApplicationPageContext
    clarifications: list[PagePlanningAnswer] = Field(default_factory=list, max_length=5)
    pages: list[ApplicationPageDefinition] = Field(min_length=1, max_length=12)
    apis: list[ApplicationApiDefinition] = Field(default_factory=list, max_length=24)


class ApplicationMenuItem(ApiModel):
    key: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=100)
    type: Literal["menu", "page"]
    purpose: str = Field(min_length=1, max_length=500)
    key_features: list[str] = Field(default_factory=list, max_length=8)
    related_page_ids: list[str] = Field(default_factory=list, max_length=8)
    api_ids: list[str] = Field(default_factory=list, max_length=12)
    interactions: list[ApplicationPageInteraction] = Field(default_factory=list, max_length=8)
    page_key: str | None = None
    children: list["ApplicationMenuItem"] | None = None


class ApplicationMenus(ApiModel):
    home_menu_key: str = "default"
    items: list[ApplicationMenuItem] = Field(min_length=1)


class GeneratePagePlanRequest(ApiModel):
    application: ApplicationPageContext
    answers: list[PagePlanningAnswer] = Field(min_length=1, max_length=5)
    current_plan: ApplicationPagePlan | None = None
    feedback: str | None = Field(default=None, max_length=3000)


class GeneratePagePlanResponse(ApiModel):
    plan: ApplicationPagePlan


class ConfirmPagePlanRequest(ApiModel):
    workspace_root: str = Field(min_length=1)
    plan: ApplicationPagePlan


class ConfirmPagePlanResponse(ApiModel):
    path: str
    sha256: str
    confirmed_at: str
    menus: ApplicationMenus
    apis: list[ApplicationApiDefinition]


class PagePlanningModelError(RuntimeError):
    pass


_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_SAFE_ID_PATTERN = re.compile(r"[^a-z0-9_-]+")
_SAFE_PATH_PATTERN = re.compile(r"^/[a-z0-9_:/-]*$")

_QUESTION_SYSTEM_PROMPT = (
    "你是一名资深产品经理。请根据应用名称和应用场景，找出决定页面结构所必需、"
    "但当前信息中缺失的细节。只返回 JSON，不要使用 Markdown。"
)

_PAGE_PLAN_SYSTEM_PROMPT = (
    "你是一名资深产品架构师。请根据应用信息和用户对细节问题的回答，设计精简且完整的页面结构。"
    "当用户提供页面结构调整意见时，最新调整意见拥有最高优先级，必须据此删除、合并、保留或修改页面。"
    "页面必须有清晰职责，避免把弹窗、抽屉或微小组件误列成独立页面。"
    "同时设计支撑页面交互的业务 API，但不要输出任何实现代码。只返回 JSON，不要使用 Markdown。"
)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content or "")


async def _stream_model_text(
    messages: list[SystemMessage | HumanMessage],
    on_text_delta: ModelTextReporter | None,
) -> str:
    """流式读取模型文本，逐块上报给协议层并保留可解析的完整结果。"""

    model = create_chat_model(Settings.from_env())
    chunks: list[str] = []
    async for chunk in model.astream(messages):
        text = _message_text(getattr(chunk, "content", ""))
        if not text:
            continue
        chunks.append(text)
        if on_text_delta:
            await on_text_delta(text)
    return "".join(chunks)


def _json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_PATTERN.search(text)
        if not match:
            return {}
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}


def _clean_text(value: Any, *, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _safe_id(value: Any, fallback: str) -> str:
    normalized = _SAFE_ID_PATTERN.sub("-", _clean_text(value).lower()).strip("-_")
    return (normalized or fallback)[:60]


def _normalize_questions(payload: dict[str, Any]) -> list[PagePlanningQuestion]:
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        raise PagePlanningModelError("模型没有返回 questions 数组。")

    questions: list[PagePlanningQuestion] = []
    for index, item in enumerate(raw_questions[:5], start=1):
        if not isinstance(item, dict):
            continue
        question = _clean_text(item.get("question"))
        if not question:
            continue
        questions.append(
            PagePlanningQuestion(
                id=_safe_id(item.get("id"), f"question-{index}"),
                question=question[:300],
                rationale=_clean_text(item.get("rationale"))[:300],
                placeholder=_clean_text(
                    item.get("placeholder"), fallback="请输入你的考虑"
                )[:200],
            )
        )
    if len(questions) < 3:
        raise PagePlanningModelError("模型返回的有效细节问题少于 3 个。")
    return questions


def _normalize_path(value: Any, index: int) -> str:
    path = _clean_text(value).lower().replace(" ", "-")
    if not path.startswith("/"):
        path = f"/{path}"
    if not _SAFE_PATH_PATTERN.fullmatch(path):
        return "/" if index == 1 else f"/page-{index}"
    return path.rstrip("/") or "/"


def _normalize_pages(payload: dict[str, Any]) -> list[ApplicationPageDefinition]:
    """规范页面、页面关系和页面内交互设计。"""

    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list):
        raise PagePlanningModelError("模型没有返回 pages 数组。")

    pages: list[ApplicationPageDefinition] = []
    used_ids: set[str] = set()
    used_paths: set[str] = set()
    for index, item in enumerate(raw_pages[:12], start=1):
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"))
        purpose = _clean_text(item.get("purpose"))
        if not name or not purpose:
            continue

        page_id = _safe_id(item.get("id"), f"page-{index}")
        if page_id in used_ids:
            page_id = f"{page_id}-{index}"
        path = _normalize_path(item.get("path"), index)
        if path in used_paths:
            path = f"/page-{index}"

        raw_features = item.get("keyFeatures") or item.get("key_features") or []
        features = (
            [_clean_text(feature)[:160] for feature in raw_features if _clean_text(feature)][:8]
            if isinstance(raw_features, list)
            else []
        )
        interactions: list[ApplicationPageInteraction] = []
        raw_interactions = item.get("interactions") or []
        if isinstance(raw_interactions, list):
            for interaction_index, interaction in enumerate(raw_interactions[:8], start=1):
                if not isinstance(interaction, dict):
                    continue
                trigger = _clean_text(interaction.get("trigger"))
                user_action = _clean_text(
                    interaction.get("userAction") or interaction.get("user_action")
                )
                system_response = _clean_text(
                    interaction.get("systemResponse")
                    or interaction.get("system_response")
                )
                if not trigger or not user_action or not system_response:
                    continue
                raw_interaction_api_ids = (
                    interaction.get("apiIds") or interaction.get("api_ids") or []
                )
                interactions.append(
                    ApplicationPageInteraction(
                        name=_clean_text(
                            interaction.get("name"),
                            fallback=f"交互 {interaction_index}",
                        )[:120],
                        trigger=trigger[:240],
                        user_action=user_action[:500],
                        system_response=system_response[:500],
                        target_page_id=_clean_text(
                            interaction.get("targetPageId")
                            or interaction.get("target_page_id")
                        )[:60]
                        or None,
                        api_ids=(
                            [
                                _clean_text(value)[:60]
                                for value in raw_interaction_api_ids
                                if _clean_text(value)
                            ][:8]
                            if isinstance(raw_interaction_api_ids, list)
                            else []
                        ),
                    )
                )
        raw_related_page_ids = (
            item.get("relatedPageIds") or item.get("related_page_ids") or []
        )
        raw_api_ids = item.get("apiIds") or item.get("api_ids") or []
        pages.append(
            ApplicationPageDefinition(
                id=page_id,
                name=name[:100],
                path=path,
                purpose=purpose[:500],
                key_features=features,
                related_page_ids=(
                    [
                        _clean_text(value)[:60]
                        for value in raw_related_page_ids
                        if _clean_text(value)
                    ][:8]
                    if isinstance(raw_related_page_ids, list)
                    else []
                ),
                api_ids=(
                    [_clean_text(value)[:60] for value in raw_api_ids if _clean_text(value)][:12]
                    if isinstance(raw_api_ids, list)
                    else []
                ),
                interactions=interactions,
            )
        )
        used_ids.add(page_id)
        used_paths.add(path)

    if not pages:
        raise PagePlanningModelError("模型没有返回有效页面。")
    return pages


def _normalize_api_path(value: Any, index: int) -> str:
    """把模型输出的 API 路径规范为可读且稳定的设计路径。"""

    path = _clean_text(value).strip()
    if not path.startswith("/"):
        path = f"/{path}"
    if not re.fullmatch(r"/[A-Za-z0-9_{}:/.-]*", path):
        return f"/api/resource-{index}"
    return path[:200]


def _normalize_apis(payload: dict[str, Any]) -> list[ApplicationApiDefinition]:
    """校验并规范模型返回的 API 功能设计列表。"""

    raw_apis = payload.get("apis")
    if not isinstance(raw_apis, list):
        raise PagePlanningModelError("模型没有返回 apis 数组。")

    apis: list[ApplicationApiDefinition] = []
    used_ids: set[str] = set()
    allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    for index, item in enumerate(raw_apis[:24], start=1):
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"))
        purpose = _clean_text(item.get("purpose"))
        response_design = _clean_text(
            item.get("responseDesign") or item.get("response_design")
        )
        method = _clean_text(item.get("method")).upper()
        if not name or not purpose or not response_design or method not in allowed_methods:
            continue
        api_id = _safe_id(item.get("id"), f"api-{index}")
        if api_id in used_ids:
            api_id = f"{api_id}-{index}"
        raw_page_ids = (
            item.get("usedByPageIds") or item.get("used_by_page_ids") or []
        )
        apis.append(
            ApplicationApiDefinition(
                id=api_id,
                name=name[:120],
                method=method,
                path=_normalize_api_path(item.get("path"), index),
                purpose=purpose[:500],
                request_design=_clean_text(
                    item.get("requestDesign") or item.get("request_design"),
                    fallback="无需业务请求参数",
                )[:1000],
                response_design=response_design[:1000],
                used_by_page_ids=(
                    [_clean_text(value)[:60] for value in raw_page_ids if _clean_text(value)][:12]
                    if isinstance(raw_page_ids, list)
                    else []
                ),
            )
        )
        used_ids.add(api_id)
    return apis


def _normalize_plan_relations(
    pages: list[ApplicationPageDefinition],
    apis: list[ApplicationApiDefinition],
) -> tuple[list[ApplicationPageDefinition], list[ApplicationApiDefinition]]:
    """移除指向不存在页面或 API 的模型引用，保证方案内部关系一致。"""

    page_ids = {page.id for page in pages}
    api_ids = {api.id for api in apis}
    normalized_pages: list[ApplicationPageDefinition] = []
    for page in pages:
        interactions = [
            interaction.model_copy(
                update={
                    "target_page_id": (
                        interaction.target_page_id
                        if interaction.target_page_id in page_ids
                        else None
                    ),
                    "api_ids": [
                        api_id for api_id in interaction.api_ids if api_id in api_ids
                    ],
                }
            )
            for interaction in page.interactions
        ]
        normalized_pages.append(
            page.model_copy(
                update={
                    "related_page_ids": [
                        page_id
                        for page_id in page.related_page_ids
                        if page_id in page_ids and page_id != page.id
                    ],
                    "api_ids": [api_id for api_id in page.api_ids if api_id in api_ids],
                    "interactions": interactions,
                }
            )
        )
    normalized_apis = [
        api.model_copy(
            update={
                "used_by_page_ids": [
                    page_id for page_id in api.used_by_page_ids if page_id in page_ids
                ]
            }
        )
        for api in apis
    ]
    return normalized_pages, normalized_apis


def _page_key(page: ApplicationPageDefinition) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", page.id) if part]
    return "".join(part[:1].upper() + part[1:] for part in parts) or "DefaultPage"


def _menu_path(page: ApplicationPageDefinition) -> str:
    return page.path.lstrip("/") or "default"


def _menu_item_for_page(
    page: ApplicationPageDefinition,
    *,
    key: str | None = None,
) -> ApplicationMenuItem:
    """把已校验页面设计转换为 application.json 的页面菜单项。"""

    return ApplicationMenuItem(
        key=key or _safe_id(_menu_path(page), page.id),
        path=_menu_path(page),
        label=page.name,
        type="page",
        purpose=page.purpose,
        key_features=page.key_features,
        related_page_ids=page.related_page_ids,
        api_ids=page.api_ids,
        interactions=page.interactions,
        page_key=_page_key(page),
    )


def _group_key_features(pages: list[ApplicationPageDefinition]) -> list[str]:
    features: list[str] = []
    for page in pages:
        for feature in page.key_features:
            if feature not in features:
                features.append(feature)
            if len(features) == 8:
                return features
    return features


def _page_plan_menus(plan: ApplicationPagePlan) -> ApplicationMenus:
    root_pages = [page for page in plan.pages if page.path == "/"]
    grouped_pages: dict[str, list[ApplicationPageDefinition]] = {}
    direct_pages: list[ApplicationPageDefinition] = list(root_pages)

    for page in plan.pages:
        if page.path == "/":
            continue
        segments = [segment for segment in page.path.strip("/").split("/") if segment]
        group_key = segments[0] if segments else "default"
        if len(segments) > 1:
            grouped_pages.setdefault(group_key, []).append(page)
        else:
            direct_pages.append(page)

    items: list[ApplicationMenuItem] = []
    direct_by_path = {_menu_path(page): page for page in direct_pages}
    handled_paths: set[str] = set()
    for page in direct_pages:
        page_path = _menu_path(page)
        if page_path in handled_paths:
            continue
        nested_pages = grouped_pages.get(page_path)
        if nested_pages:
            children = [_menu_item_for_page(page, key=f"{page_path}-index")]
            children.extend(_menu_item_for_page(child) for child in nested_pages)
            items.append(
                ApplicationMenuItem(
                    key=_safe_id(page_path, "menu"),
                    path=page_path,
                    label=page.name,
                    type="menu",
                    purpose=page.purpose,
                    key_features=page.key_features,
                    children=children,
                )
            )
            handled_paths.add(page_path)
        else:
            items.append(_menu_item_for_page(page))
            handled_paths.add(page_path)

    for group_key, pages in grouped_pages.items():
        if group_key in direct_by_path:
            continue
        items.append(
            ApplicationMenuItem(
                key=_safe_id(group_key, "menu"),
                path=group_key,
                label=pages[0].name,
                type="menu",
                purpose=f"组织{pages[0].name}及相关页面",
                key_features=_group_key_features(pages),
                children=[_menu_item_for_page(page) for page in pages],
            )
        )

    if not items:
        raise ValueError("页面计划没有可写入菜单的页面。")
    items[0] = items[0].model_copy(update={"key": "default"})
    return ApplicationMenus(home_menu_key="default", items=items)


async def generate_page_planning_questions(
    request: PagePlanningQuestionsRequest,
    on_text_delta: ModelTextReporter | None = None,
) -> PagePlanningQuestionsResponse:
    """流式生成决定页面结构所需的业务澄清问题。"""

    application = request.application
    prompt = f"""请提出 3 到 5 个会显著影响页面划分的细节问题。

要求：
- 聚焦用户角色、核心任务、关键业务流程、数据范围和权限边界。
- 不要询问颜色、字体等不会改变页面结构的细节。
- 每个问题都要便于普通用户直接回答。

返回格式：
{{
  "questions": [
    {{
      "id": "英文短标识",
      "question": "问题",
      "rationale": "为什么这个答案会影响页面结构",
      "placeholder": "简短回答提示"
    }}
  ]
}}

应用名称：{application.name}
应用场景：{application.scenario or "用户未填写"}
终端类型：{application.terminal}
"""
    result_text = await _stream_model_text(
        [SystemMessage(content=_QUESTION_SYSTEM_PROMPT), HumanMessage(content=prompt)],
        on_text_delta,
    )
    payload = _json_object(result_text)
    return PagePlanningQuestionsResponse(questions=_normalize_questions(payload))


async def generate_application_page_plan(
    request: GeneratePagePlanRequest,
    on_text_delta: ModelTextReporter | None = None,
) -> GeneratePagePlanResponse:
    """调用模型生成仅供审核的页面、交互和 API 功能设计方案。"""

    application = request.application
    answers = [answer.model_dump(by_alias=True) for answer in request.answers]
    revision_context = ""
    if request.current_plan and request.feedback:
        revision_context = f"""

当前页面结构：
{json.dumps(request.current_plan.model_dump(by_alias=True), ensure_ascii=False)}

用户修改意见：
{request.feedback}

这是一次产品设计方案修订任务。请以“用户修改意见”为最高优先级，返回落实意见后的完整 pages 和 apis 数组。
修订规则：
- 如果用户要求删除、不需要、不要、只保留、只需要或仅保留某些页面，返回结果中必须移除其他页面。
- 如果用户要求合并页面，返回结果中必须减少对应页面，并把必要能力合并到保留页面的 purpose 或 keyFeatures。
- 如果用户要求新增、重命名、调整路径或修改职责，返回结果中必须体现这些变更。
- 页面变化后同步更新页面关联、交互步骤及 API 的引用关系，不得保留悬空引用。
- 如果用户要求调整 API 功能、路径、请求或响应设计，必须同步更新 apis 以及相关页面的 apiIds。
- 只保留与用户意见不冲突、且仍服务核心流程的页面；不要因为当前页面结构中已有某页面就默认保留。
- 不要新增用户没有要求、应用信息也不能支持的页面。
"""
    prompt = f"""请设计 1 到 12 个页面及支撑这些页面交互的业务 API，输出可供用户确认的产品设计方案。

要求：
- 覆盖用户的核心端到端任务，但不要为假设中的未来需求增加页面。
- path 使用小写英文 kebab-case 路径；首页可以使用 /。
- keyFeatures 列出页面最关键的 2 到 5 项功能。
- relatedPageIds 使用页面 id，说明用户可从该页面关联或跳转到哪些页面。
- interactions 说明触发条件、用户动作、系统反馈、目标页面和调用的 apiIds。
- API 只做功能契约设计，使用 /api 开头的路径，说明用途、请求和响应数据语义；不要生成代码、框架或数据库实现。
- 只设计当前页面实际需要的 API；纯前端交互不需要虚构 API。

返回格式：
{{
  "pages": [
    {{
      "id": "英文短标识",
      "name": "页面名称",
      "path": "/route-path",
      "purpose": "页面的功能与职责详情",
      "keyFeatures": ["关键能力"],
      "relatedPageIds": ["related-page-id"],
      "apiIds": ["api-id"],
      "interactions": [
        {{
          "name": "交互名称",
          "trigger": "何时发生",
          "userAction": "用户如何操作",
          "systemResponse": "系统如何反馈",
          "targetPageId": "可选目标页面 id",
          "apiIds": ["api-id"]
        }}
      ]
    }}
  ],
  "apis": [
    {{
      "id": "api-id",
      "name": "API 名称",
      "method": "GET|POST|PUT|PATCH|DELETE",
      "path": "/api/resource",
      "purpose": "API 的业务功能",
      "requestDesign": "请求参数、筛选、分页或业务约束的语义说明",
      "responseDesign": "响应数据、状态和错误语义说明",
      "usedByPageIds": ["page-id"]
    }}
  ]
}}

应用信息：
{json.dumps(application.model_dump(by_alias=True), ensure_ascii=False)}

细节问答：
{json.dumps(answers, ensure_ascii=False)}
{revision_context}
"""
    result_text = await _stream_model_text(
        [SystemMessage(content=_PAGE_PLAN_SYSTEM_PROMPT), HumanMessage(content=prompt)],
        on_text_delta,
    )
    payload = _json_object(result_text)
    pages, apis = _normalize_plan_relations(
        _normalize_pages(payload),
        _normalize_apis(payload),
    )
    plan = ApplicationPagePlan(
        application=application,
        clarifications=request.answers,
        pages=pages,
        apis=apis,
    )
    return GeneratePagePlanResponse(plan=plan)


def confirm_application_page_plan(
    request: ConfirmPagePlanRequest,
) -> ConfirmPagePlanResponse:
    """在用户明确确认后原子写入 application.json 的 menus 与 apis。"""

    workspace_root = Path(request.workspace_root).expanduser().resolve()
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise ValueError(f"工作目录不存在或不是文件夹：{workspace_root}")

    target = workspace_root / "application.json"
    if not target.exists() or not target.is_file():
        raise ValueError(f"应用配置不存在：{target}")
    try:
        existing = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"应用配置不是有效 JSON：{target}") from exc
    if not isinstance(existing, dict):
        raise ValueError(f"应用配置必须是 JSON 对象：{target}")

    confirmed_at = datetime.now(timezone.utc).isoformat()
    menus = _page_plan_menus(request.plan)
    for transient_key in ("pagePlan", "clarification", "clarifications"):
        existing.pop(transient_key, None)
    payload = {
        **existing,
        "menus": menus.model_dump(by_alias=True, exclude_none=True),
        "apis": [
            api.model_dump(by_alias=True, exclude_none=True) for api in request.plan.apis
        ],
    }
    content = f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    temporary = workspace_root / ".application.json.tmp"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)
    return ConfirmPagePlanResponse(
        path=str(target),
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        confirmed_at=confirmed_at,
        menus=menus,
        apis=request.plan.apis,
    )
