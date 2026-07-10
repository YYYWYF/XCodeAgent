from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from app.agents.model_factory import create_chat_model
from app.config import Settings


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


class ApplicationPagePlan(ApiModel):
    schema_version: Literal[1] = 1
    application: ApplicationPageContext
    clarifications: list[PagePlanningAnswer] = Field(default_factory=list, max_length=5)
    pages: list[ApplicationPageDefinition] = Field(min_length=1, max_length=12)


class ApplicationMenuItem(ApiModel):
    key: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=100)
    type: Literal["menu", "page"]
    purpose: str = Field(min_length=1, max_length=500)
    key_features: list[str] = Field(default_factory=list, max_length=8)
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
    "页面必须有清晰职责，避免把弹窗、抽屉或微小组件误列成独立页面。只返回 JSON，不要使用 Markdown。"
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
        pages.append(
            ApplicationPageDefinition(
                id=page_id,
                name=name[:100],
                path=path,
                purpose=purpose[:500],
                key_features=features,
            )
        )
        used_ids.add(page_id)
        used_paths.add(path)

    if not pages:
        raise PagePlanningModelError("模型没有返回有效页面。")
    return pages


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
    return ApplicationMenuItem(
        key=key or _safe_id(_menu_path(page), page.id),
        path=_menu_path(page),
        label=page.name,
        type="page",
        purpose=page.purpose,
        key_features=page.key_features,
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
) -> PagePlanningQuestionsResponse:
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
    model = create_chat_model(Settings.from_env())
    result = await model.ainvoke(
        [SystemMessage(content=_QUESTION_SYSTEM_PROMPT), HumanMessage(content=prompt)]
    )
    payload = _json_object(_message_text(getattr(result, "content", "")))
    return PagePlanningQuestionsResponse(questions=_normalize_questions(payload))


async def generate_application_page_plan(
    request: GeneratePagePlanRequest,
) -> GeneratePagePlanResponse:
    application = request.application
    answers = [answer.model_dump(by_alias=True) for answer in request.answers]
    revision_context = ""
    if request.current_plan and request.feedback:
        revision_context = f"""

当前页面结构：
{json.dumps(request.current_plan.model_dump(by_alias=True), ensure_ascii=False)}

用户修改意见：
{request.feedback}

请在当前页面结构上落实用户意见。保留没有被意见影响的合理页面和字段，不要无故整体重做。
"""
    prompt = f"""请设计 1 到 12 个页面，并说明每个页面的大致作用。

要求：
- 覆盖用户的核心端到端任务，但不要为假设中的未来需求增加页面。
- path 使用小写英文 kebab-case 路径；首页可以使用 /。
- keyFeatures 只列出该页面最关键的 2 到 5 项能力。
- 不要输出页面之外的实现方案。

返回格式：
{{
  "pages": [
    {{
      "id": "英文短标识",
      "name": "页面名称",
      "path": "/route-path",
      "purpose": "页面的大致作用",
      "keyFeatures": ["关键能力"]
    }}
  ]
}}

应用信息：
{json.dumps(application.model_dump(by_alias=True), ensure_ascii=False)}

细节问答：
{json.dumps(answers, ensure_ascii=False)}
{revision_context}
"""
    model = create_chat_model(Settings.from_env())
    result = await model.ainvoke(
        [SystemMessage(content=_PAGE_PLAN_SYSTEM_PROMPT), HumanMessage(content=prompt)]
    )
    payload = _json_object(_message_text(getattr(result, "content", "")))
    plan = ApplicationPagePlan(
        application=application,
        clarifications=request.answers,
        pages=_normalize_pages(payload),
    )
    return GeneratePagePlanResponse(plan=plan)


def confirm_application_page_plan(
    request: ConfirmPagePlanRequest,
) -> ConfirmPagePlanResponse:
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
    )
