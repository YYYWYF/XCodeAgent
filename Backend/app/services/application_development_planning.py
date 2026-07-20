from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.services.application_planning_persistence import project_plan_application_payload


ModelTextReporter = Callable[[str], Awaitable[None]]


def _to_camel(value: str) -> str:
    """把后端蛇形字段转换为前端使用的驼峰字段。"""

    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    """为开发计划协议提供统一的字段别名规则。"""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)


class DevelopmentPlanningAnswer(ApiModel):
    """保存用户对开发计划澄清问题的回答。"""

    question_id: str = Field(min_length=1, max_length=60)
    question: str = Field(min_length=1, max_length=300)
    answer: str = Field(min_length=1, max_length=2000)


class DevelopmentPlanningQuestion(ApiModel):
    """描述会实质影响任务拆分的一个澄清问题。"""

    id: str = Field(min_length=1, max_length=60)
    question: str = Field(min_length=1, max_length=300)
    rationale: str = Field(default="", max_length=300)
    placeholder: str = Field(default="", max_length=200)


class ApplicationDevelopmentTask(ApiModel):
    """描述一个可执行、可验收且带依赖关系的开发任务。"""

    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1000)
    kind: Literal["feature", "integration", "shared"] = "feature"
    status: Literal["todo", "in_progress", "completed"] = "todo"
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    blocks: list[str] = Field(default_factory=list, max_length=20)
    covers_features: list[str] = Field(default_factory=list, max_length=12)
    acceptance_criteria: list[str] = Field(min_length=2, max_length=6)


class SharedDevelopmentModule(ApiModel):
    """描述被多个菜单复用的公共模块及其独立任务。"""

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    responsibility: str = Field(min_length=1, max_length=600)
    used_by_menu_keys: list[str] = Field(min_length=2, max_length=20)
    tasks: list[ApplicationDevelopmentTask] = Field(min_length=1, max_length=12)


class MenuDevelopmentPlan(ApiModel):
    """把一组页面级开发任务绑定到 application.json 的菜单项。"""

    menu_key: str = Field(min_length=1, max_length=120)
    menu_label: str = Field(min_length=1, max_length=120)
    tasks: list[ApplicationDevelopmentTask] = Field(min_length=1, max_length=20)


class ApplicationDevelopmentPlan(ApiModel):
    """保存经用户审核后可写入应用配置的完整开发顺序。"""

    schema_version: Literal[1] = 1
    summary: str = Field(min_length=1, max_length=1200)
    execution_order: list[str] = Field(min_length=1, max_length=200)
    shared_modules: list[SharedDevelopmentModule] = Field(default_factory=list, max_length=20)
    menu_plans: list[MenuDevelopmentPlan] = Field(min_length=1, max_length=80)


class GenerateDevelopmentPlanRequest(ApiModel):
    """校验一次模型规划调用所需的 application.json 与可选回答。"""

    workspace_root: str = Field(min_length=1)
    selected_page_key: str = Field(min_length=1, max_length=120)
    answers: list[DevelopmentPlanningAnswer] = Field(default_factory=list, max_length=5)


class GenerateDevelopmentPlanResponse(ApiModel):
    """返回澄清问题或完整计划，两者必须且只能存在一个。"""

    questions: list[DevelopmentPlanningQuestion] | None = None
    plan: ApplicationDevelopmentPlan | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "GenerateDevelopmentPlanResponse":
        """拒绝同时缺少或同时返回问题与计划的模糊模型结果。"""

        if (self.questions is None) == (self.plan is None):
            raise ValueError("模型必须返回澄清问题或完整开发计划。")
        return self


class ConfirmDevelopmentPlanRequest(ApiModel):
    """校验用户确认计划时的工作区与计划内容。"""

    workspace_root: str = Field(min_length=1)
    selected_page_key: str = Field(min_length=1, max_length=120)
    plan: ApplicationDevelopmentPlan


class ConfirmDevelopmentPlanResponse(ApiModel):
    """返回开发计划持久化后的文件摘要和最新菜单。"""

    path: str
    sha256: str
    confirmed_at: str
    menus: dict[str, Any]


_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_SYSTEM_PROMPT = (
    "你是一名资深应用交付规划师。你的职责是把已经确认的 application.json 转换为简洁、可执行、"
    "可逐项更新状态的开发任务清单，不生成代码，不臆造产品范围。"
    "必须严格遵守用户提供的工程基线和输出结构；模型输出只能是一个 JSON 对象，不要使用 Markdown。"
)


def _message_text(content: Any) -> str:
    """从不同模型供应商的消息内容中提取纯文本。"""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text") or item.get("content") or "")
            for item in content
            if isinstance(item, dict)
        )
    return str(content or "")


async def _stream_model_text(
    messages: list[SystemMessage | HumanMessage],
    on_text_delta: ModelTextReporter | None,
) -> str:
    """流式转发模型增量，同时累积最终可解析的 JSON。"""

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
    """从模型文本中提取唯一 JSON 对象并拒绝无效结构。"""

    match = _JSON_OBJECT_PATTERN.search(text)
    if not match:
        raise ValueError("模型没有返回 JSON 开发计划。")
    payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("模型开发计划必须是 JSON 对象。")
    return payload


def _menu_identity(items: Any) -> list[dict[str, Any]]:
    """递归提取菜单键、名称和类型，限制发送给模型的上下文。"""

    result: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key:
            result.append(
                {
                    "key": key,
                    "label": str(item.get("label") or key)[:120],
                    "type": str(item.get("type") or "page")[:20],
                    "keyFeatures": [str(feature)[:200] for feature in item.get("keyFeatures", []) if str(feature).strip()][:8],
                }
            )
        result.extend(_menu_identity(item.get("children")))
    return result


def _application_payload_for_development(application: dict[str, Any]) -> dict[str, Any]:
    """优先读取现有派生结构，缺失时从已确认 ProjectPlan 延迟生成。"""

    menus = application.get("menus")
    if _menu_identity(menus.get("items") if isinstance(menus, dict) else None):
        return {
            "menus": menus,
            "apis": application.get("apis", []),
            "schemas": application.get("schemas", {}),
            "dataSources": application.get("dataSources", []),
        }
    planning = application.get("planning")
    project_plan = planning.get("projectPlan") if isinstance(planning, dict) else None
    if not isinstance(project_plan, dict) or project_plan.get("confirmation_status") != "confirmed":
        raise ValueError("当前应用中没有已确认的项目计划。")
    return project_plan_application_payload(project_plan)


def _normalize_plan(
    plan: ApplicationDevelopmentPlan,
    menu_items: list[dict[str, Any]],
    *,
    expected_menu_keys: set[str] | None = None,
    require_todo: bool = False,
) -> ApplicationDevelopmentPlan:
    """校验菜单覆盖与任务引用，并从依赖关系反向推导阻塞关系。"""

    if plan.shared_modules:
        raise ValueError("当前工程已具备公共基础能力，开发计划不得新增 sharedModules。")
    menu_keys = {str(item["key"]) for item in menu_items}
    if len(menu_keys) != len(menu_items):
        raise ValueError("application.json 的菜单 key 必须全局唯一。")
    required_keys = expected_menu_keys or menu_keys
    if not required_keys <= menu_keys:
        raise ValueError(f"开发计划包含未知页面：{sorted(required_keys - menu_keys)}。")
    planned_keys = {item.menu_key for item in plan.menu_plans}
    if planned_keys != required_keys or len(plan.menu_plans) != len(required_keys):
        missing = sorted(required_keys - planned_keys)
        unknown = sorted(planned_keys - required_keys)
        raise ValueError(f"开发计划菜单覆盖不完整，缺少 {missing}，未知 {unknown}。")
    feature_map = {str(item["key"]): set(item.get("keyFeatures", [])) for item in menu_items}
    for menu_plan in plan.menu_plans:
        covered = {feature for task in menu_plan.tasks for feature in task.covers_features}
        missing_features = feature_map[menu_plan.menu_key] - covered
        if missing_features:
            raise ValueError(f"菜单 {menu_plan.menu_key} 的功能未被任务覆盖：{sorted(missing_features)}。")
    all_tasks = [task for item in plan.menu_plans for task in item.tasks]
    if any(task.kind == "shared" for task in all_tasks):
        raise ValueError("菜单开发计划不得包含 shared 类型任务。")
    if require_todo and any(task.status != "todo" for task in all_tasks):
        raise ValueError("新生成任务的初始状态必须全部为 todo。")
    task_ids = [task.id for task in all_tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("开发计划任务 id 必须全局唯一。")
    task_id_set = set(task_ids)
    for task in all_tasks:
        unknown_dependencies = set(task.depends_on) - task_id_set
        if unknown_dependencies:
            raise ValueError(f"任务 {task.id} 引用了未知依赖：{sorted(unknown_dependencies)}。")
        task.blocks = sorted(other.id for other in all_tasks if task.id in other.depends_on)
    remaining = {task.id: set(task.depends_on) for task in all_tasks}
    resolved: set[str] = set()
    while remaining:
        ready = {task_id for task_id, dependencies in remaining.items() if dependencies <= resolved}
        if not ready:
            raise ValueError(f"开发计划存在循环依赖：{sorted(remaining)}。")
        resolved.update(ready)
        remaining = {task_id: dependencies for task_id, dependencies in remaining.items() if task_id not in ready}
    if set(plan.execution_order) != task_id_set or len(plan.execution_order) != len(task_ids):
        raise ValueError("executionOrder 必须且只能包含全部任务 id。")
    order_index = {task_id: index for index, task_id in enumerate(plan.execution_order)}
    if any(order_index[dependency] > order_index[task.id] for task in all_tasks for dependency in task.depends_on):
        raise ValueError("executionOrder 必须把依赖任务排在使用方之前。")
    return plan


async def generate_application_development_plan(
    request: GenerateDevelopmentPlanRequest,
    on_text_delta: ModelTextReporter | None = None,
) -> GenerateDevelopmentPlanResponse:
    """基于 application.json 一次生成澄清问题或完整页面级开发计划。"""

    workspace_root = Path(request.workspace_root).expanduser().resolve()
    target = workspace_root / ".xcodeagent" / "application.json"
    if not workspace_root.is_dir() or not target.is_file():
        raise ValueError(f"应用配置不存在：{target}")
    application = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(application, dict):
        raise ValueError("application.json 必须是 JSON 对象。")
    derived_application = _application_payload_for_development(application)
    menus = derived_application["menus"]
    all_menu_items = _menu_identity(menus.get("items"))
    selected_items = [item for item in all_menu_items if item["key"] == request.selected_page_key and item["type"] == "page"]
    if not selected_items:
        raise ValueError(f"所选页面不存在或不是可开发页面：{request.selected_page_key}")
    menu_items = selected_items
    compact_application = {
        "appName": application.get("appName"),
        "senario": application.get("senario"),
        "terminal": application.get("terminal"),
        "layout": application.get("layout"),
        "datasource": application.get("datasource"),
        "auth": application.get("auth"),
        "menus": menus,
        "apis": derived_application["apis"],
    }
    prompt = f"""请把下面已经确认的 application.json 转换为应用功能开发任务清单。

【已知工程基线】
- 工程已经具备路由管理、API 调用封装、导航、页面布局等基础能力。
- 不要创建或重建路由框架、通用请求层、导航框架、布局框架等公共基础模块。
- 本次只规划各菜单自身的业务功能、页面交互，以及页面与既有 API 能力的接入。
- sharedModules 必须返回空数组。不得用“公共模块”“基础设施”“通用组件”等任务填充计划。

【任务拆分目标】
- menuPlans 必须覆盖下列每一个菜单项，包括目录型 menu 和页面型 page，且每项至少有一个 tasks 数组：{json.dumps(menu_items, ensure_ascii=False)}
- tasks 是有序任务列表，数组顺序就是界面展示的 1、2、3……开发顺序。每个任务只描述一个可独立完成、独立验收并独立更新状态的交付单元。
- 标题使用明确的动宾短语；description 用简短文字说明实现边界，不写背景长文，不拆成文件级或代码行级微任务。
- 每个任务必须有 2 到 6 条具体、可观察、可验证的 acceptanceCriteria。每条只表达一个验收结果，不使用“功能正常”“符合预期”等空泛描述。
- status 固定为 todo。kind 只能是 feature 或 integration；仅在接入某个页面的既有 API 或串联该页面完整流程时使用 integration。

【覆盖与依赖规则】
- 每个菜单的 keyFeatures、interactions 和关联 apis 都必须被任务覆盖。每项 keyFeatures 原文必须至少出现在一个该菜单任务的 coversFeatures 中。
- dependsOn 只填写真实的直接前置任务 id，不要为了显得完整而制造依赖。blocks 返回空数组，后端会根据 dependsOn 自动推导。
- executionOrder 必须且只能包含全部任务 id，并保证依赖任务排在使用方之前。
- 若缺失信息会实质改变业务任务边界、开发顺序或 API 接入方式，返回 needsClarification=true 和 1 到 5 个 questions；不要询问可从配置安全推断的细节。
- 信息足够时返回 needsClarification=false 和 plan。summary 只概括业务功能交付策略，不重复工程基线。

澄清返回格式：
{{"needsClarification":true,"questions":[{{"id":"q1","question":"问题","rationale":"为什么影响计划","placeholder":"回答提示"}}]}}

计划返回格式：
{{"needsClarification":false,"plan":{{"schemaVersion":1,"summary":"业务功能交付策略","executionOrder":["menu-key-task-1","menu-key-task-2"],"sharedModules":[],"menuPlans":[{{"menuKey":"menu-key","menuLabel":"菜单名","tasks":[{{"id":"menu-key-task-1","title":"实现具体业务功能","description":"说明该任务的实现范围和边界。","kind":"feature","status":"todo","dependsOn":[],"blocks":[],"coversFeatures":["application.json 中的功能原文"],"acceptanceCriteria":["可验证的验收结果一","可验证的验收结果二"]}}]}}]}}}}

application.json：
{json.dumps(compact_application, ensure_ascii=False)}

用户补充回答：
{json.dumps([item.model_dump(by_alias=True) for item in request.answers], ensure_ascii=False)}
"""
    raw = _json_object(
        await _stream_model_text(
            [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)],
            on_text_delta,
        )
    )
    if raw.get("needsClarification"):
        questions = [DevelopmentPlanningQuestion.model_validate(item) for item in raw.get("questions", [])]
        if not questions or len(questions) > 5:
            raise ValueError("模型澄清问题数量必须为 1 到 5 个。")
        return GenerateDevelopmentPlanResponse(questions=questions)
    plan = ApplicationDevelopmentPlan.model_validate(raw.get("plan"))
    return GenerateDevelopmentPlanResponse(plan=_normalize_plan(
        plan,
        all_menu_items,
        expected_menu_keys={request.selected_page_key},
        require_todo=True,
    ))


def _apply_menu_tasks(items: Any, task_map: dict[str, list[dict[str, Any]]]) -> None:
    """递归写入本次确认页面的任务，并保留其他页面和设计字段。"""

    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if key in task_map:
            item["developmentTasks"] = task_map[key]
        _apply_menu_tasks(item.get("children"), task_map)


def _existing_task_ids(items: Any, excluded_menu_key: str) -> list[str]:
    """按菜单顺序收集其他页面已经持久化的任务 id。"""

    result: list[str] = []
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("key") or "") != excluded_menu_key:
            result.extend(
                str(task.get("id"))
                for task in item.get("developmentTasks", [])
                if isinstance(task, dict) and task.get("id")
            )
        result.extend(_existing_task_ids(item.get("children"), excluded_menu_key))
    return result


def confirm_application_development_plan(
    request: ConfirmDevelopmentPlanRequest,
) -> ConfirmDevelopmentPlanResponse:
    """在用户明确确认后把任务和公共模块原子写回 application.json。"""

    workspace_root = Path(request.workspace_root).expanduser().resolve()
    target = workspace_root / ".xcodeagent" / "application.json"
    if not workspace_root.is_dir() or not target.is_file():
        raise ValueError(f"应用配置不存在：{target}")
    existing = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise ValueError("application.json 必须是 JSON 对象。")
    derived_application = _application_payload_for_development(existing)
    existing.update(derived_application)
    menu_items = _menu_identity(existing["menus"].get("items"))
    plan = _normalize_plan(
        request.plan,
        menu_items,
        expected_menu_keys={request.selected_page_key},
    )
    task_map = {
        item.menu_key: [task.model_dump(by_alias=True) for task in item.tasks]
        for item in plan.menu_plans
    }
    _apply_menu_tasks(existing["menus"].get("items"), task_map)
    existing["menus"]["sharedModules"] = [
        module.model_dump(by_alias=True) for module in plan.shared_modules
    ]
    existing["menus"]["developmentPlan"] = {
        "schemaVersion": plan.schema_version,
        "summary": plan.summary,
        "executionOrder": [
            *_existing_task_ids(existing["menus"].get("items"), request.selected_page_key),
            *plan.execution_order,
        ],
    }
    content = f"{json.dumps(existing, ensure_ascii=False, indent=2)}\n"
    temporary = target.with_name(".application.json.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)
    return ConfirmDevelopmentPlanResponse(
        path=str(target),
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        confirmed_at=datetime.now(timezone.utc).isoformat(),
        menus=existing["menus"],
    )
