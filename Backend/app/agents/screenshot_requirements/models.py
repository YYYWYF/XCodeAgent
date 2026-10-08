from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_SCREENSHOTS_PER_REQUEST = 10
MAX_SCREENSHOT_BYTES = 15 * 1024 * 1024
SCREENSHOT_INPUT_ROOT = Path(".xcodeagent/inputs/screenshots")


class StrictModel(BaseModel):
    """为截图输入和模型输出启用禁止额外字段的严格契约。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _normalize_model_text_list(
    value: Any,
    *,
    primary_keys: tuple[str, ...],
    detail_keys: tuple[str, ...] = (),
) -> Any:
    """将模型常见的带标签对象转换为文字，保留描述并让未知结构继续严格校验。"""

    if not isinstance(value, list):
        return value
    normalized: list[Any] = []
    allowed_keys = set(primary_keys) | set(detail_keys)
    for item in value:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        if set(item) - allowed_keys:
            normalized.append(item)
            continue
        primary = next(
            (
                item[key].strip()
                for key in primary_keys
                if isinstance(item.get(key), str) and item[key].strip()
            ),
            "",
        )
        if not primary:
            normalized.append(item)
            continue
        details: list[str] = []
        invalid_detail = False
        for key in detail_keys:
            detail = item.get(key)
            if detail is None or detail == "":
                continue
            if not isinstance(detail, str):
                invalid_detail = True
                break
            detail = detail.strip()
            if detail and detail != primary and detail not in details:
                details.append(detail)
        normalized.append(item if invalid_detail else "：".join([primary, *details]))
    return normalized


class ScreenshotReference(StrictModel):
    """描述 Electron 已复制到应用工作区的一张截图。"""

    relative_path: str = Field(alias="relativePath", min_length=1)
    name: str = Field(min_length=1)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"] = Field(
        alias="mimeType"
    )
    size: int = Field(gt=0, le=MAX_SCREENSHOT_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RequirementInput(StrictModel):
    """校验创建规划选择的需求生成方式和截图清单。"""

    mode: Literal["text", "screenshot"] = "text"
    screenshots: list[ScreenshotReference] = Field(
        default_factory=list,
        max_length=MAX_SCREENSHOTS_PER_REQUEST,
    )

    @model_validator(mode="after")
    def validate_mode_assets(self) -> "RequirementInput":
        """确保截图模式至少携带一张截图，文字模式不接受残留截图。"""

        if self.mode == "screenshot" and not self.screenshots:
            raise ValueError("截图生成模式至少需要一张截图")
        if self.mode == "text" and self.screenshots:
            raise ValueError("文字生成模式不能携带截图")
        return self


class ScreenshotAppInfo(StrictModel):
    """定义视觉模型可生成的应用基础信息。"""

    name: str
    summary: str
    target: str = "PC"
    menu_enabled: bool = True
    route_root_path: str = "/page"


class ScreenshotUserRole(StrictModel):
    """定义截图中可观察或可合理归纳的业务参与者。"""

    id: str
    name: str
    description: str
    isSystemRole: bool = False
    isInitialAdminRole: bool = False


class ScreenshotFeatureModule(StrictModel):
    """定义截图反推的功能模块。"""

    id: str
    name: str
    description: str
    priority: Literal["must", "should", "could", "wont"] = "must"


class ScreenshotPage(StrictModel):
    """定义截图反推的页面目录项。"""

    pageId: str
    name: str
    path: str
    module_id: str = Field(
        validation_alias=AliasChoices("module_id", "moduleId"),
    )
    description: str
    visible_information: list[str] = Field(default_factory=list, max_length=80)
    visible_controls: list[str] = Field(default_factory=list, max_length=80)

    @field_validator("visible_information", "visible_controls", mode="before")
    @classmethod
    def normalize_visible_evidence(cls, value: Any) -> Any:
        """保留截图可见项目的标签和说明，并统一写入现有字符串证据契约。"""

        return _normalize_model_text_list(
            value,
            primary_keys=("label", "name", "title", "text"),
            detail_keys=("description", "value", "position", "location", "type"),
        )


class ScreenshotEntityField(StrictModel):
    """定义截图中直接可见的业务信息项，不提前推断技术字段类型。"""

    label: str
    description: str


class ScreenshotEntity(StrictModel):
    """定义截图反推的需求阶段业务实体。"""

    id: str
    name: str
    description: str
    module_id: str = Field(
        default="",
        validation_alias=AliasChoices("module_id", "moduleId"),
    )
    fields: list[ScreenshotEntityField] = Field(default_factory=list)


class ScreenshotBusinessFlow(StrictModel):
    """定义截图反推的业务流程。"""

    id: str
    name: str
    description: str
    steps: list[str]

    @field_validator("steps", mode="before")
    @classmethod
    def normalize_steps(cls, value: Any) -> Any:
        """接收模型输出的 step 对象，同时保留每一步的补充说明。"""

        return _normalize_model_text_list(
            value,
            primary_keys=("step", "text", "action"),
            detail_keys=("description",),
        )


class ScreenshotAuthorizationRequirements(StrictModel):
    """承载创建表单控制的权限开关，不从截图猜测权限规则。"""

    enabled: bool = False
    restrictedPages: list[str] = Field(default_factory=list)
    restrictedOperations: list[str] = Field(default_factory=list)


class ScreenshotRequirementOutput(StrictModel):
    """约束视觉模型返回的 XCodeAgent RequirementSpec 候选。"""

    version: Literal["0.1.0"] = "0.1.0"
    status: Literal["draft"] = "draft"
    generated_at: str
    app_info: ScreenshotAppInfo
    user_roles: list[ScreenshotUserRole]
    feature_modules: list[ScreenshotFeatureModule]
    pages: list[ScreenshotPage]
    entities: list[ScreenshotEntity]
    business_flows: list[ScreenshotBusinessFlow]
    authorization_requirements: ScreenshotAuthorizationRequirements
    acceptance_criteria: list[str]
    clarification_questions: list[str] = Field(default_factory=list)
    unresolved_requirement_dimensions: list[str] = Field(default_factory=list)
    agent_note: str = ""

    @field_validator(
        "acceptance_criteria",
        "clarification_questions",
        "unresolved_requirement_dimensions",
        mode="before",
    )
    @classmethod
    def normalize_summary_text(cls, value: Any) -> Any:
        """统一模型偶发的准则或疑问对象格式，避免列表项类型差异中断流程。"""

        return _normalize_model_text_list(
            value,
            primary_keys=("criterion", "question", "dimension", "text"),
            detail_keys=("description",),
        )


def normalize_requirement_input(value: Any) -> dict[str, Any]:
    """把 AG-UI 中的不可信需求输入归一化为可写入 Graph State 的字典。"""

    if value is None:
        return RequirementInput().model_dump(by_alias=True)
    return RequirementInput.model_validate(value).model_dump(by_alias=True)
