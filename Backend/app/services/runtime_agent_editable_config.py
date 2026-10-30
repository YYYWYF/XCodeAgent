"""Agent 可编辑配置的当前字段合同及确定性 Prompt 编译。"""

from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class ConfigValue(BaseModel):
    """拒绝未实现参数，避免界面保存成功但 Runtime 不消费。"""

    model_config = ConfigDict(extra="forbid")


class PersonaSettings(ConfigValue):
    """角色与表达风格作为成组的可选人设。"""

    role: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    tone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class PromptSettings(ConfigValue):
    """支持业务提示词、人设和业务约束，不允许修改平台凭据或权限。"""

    systemPrompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100000)]
    persona: PersonaSettings | None = None
    constraints: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]] = Field(default_factory=list, max_length=100)


class GenerationSettings(ConfigValue):
    """Temperature 只覆盖当前 Agent 的模型副本。"""

    temperature: float = Field(ge=0, le=2, allow_inf_nan=False, strict=True)


class ProjectModelSettings(ConfigValue):
    """模型身份和凭据继承项目，仅开放 Temperature。"""

    source: Literal["project"]
    generation: GenerationSettings | None = None


class ModelPatch(ConfigValue):
    """显式清空 generation 可恢复跟随项目默认 Temperature。"""

    generation: GenerationSettings | None


class SettingsPatch(ConfigValue):
    """只允许修改实际变化的 Prompt 或模型生成参数。"""

    prompt: PromptSettings | None = None
    model: ModelPatch | None = None

    @model_validator(mode="after")
    def require_change(self):
        """禁止没有任何可编辑配置的空提交。"""

        if self.prompt is None and self.model is None:
            raise ValueError("请至少修改提示词配置或 Temperature。")
        return self


def compile_agent_prompt(configuration: dict) -> str:
    """由正式人设、正文和约束编译唯一生效字符串，生成与验收共用此规则。"""

    prompt = PromptSettings.model_validate(configuration)
    sections = []
    if prompt.persona is not None:
        sections.append(f"角色：{prompt.persona.role}\n表达风格：{prompt.persona.tone}")
    sections.append(prompt.systemPrompt)
    if prompt.constraints:
        sections.append("业务约束：\n" + "\n".join(f"- {item}" for item in prompt.constraints))
    return "\n\n".join(sections)


def editable_settings(contract: dict) -> dict:
    """投影当前可编辑值；未配置 Temperature 时明确跟随项目默认。"""

    settings = contract["agentSettings"]
    model = ProjectModelSettings.model_validate(settings["model"])
    return {"prompt": deepcopy(settings["prompt"]),
            "temperature": model.generation.temperature if model.generation else None}


def apply_settings_patch(plan: dict, agent_id: str, patch: SettingsPatch) -> dict:
    """仅合并选定 Agent 的允许字段，其他模块、Agent 和业务结构保持原值。"""

    candidate = deepcopy(plan)
    target = next(item for item in candidate["agent_contracts"] if item["agentId"] == agent_id)
    settings = target["agentSettings"]
    if patch.prompt is not None:
        settings["prompt"] = patch.prompt.model_dump(exclude_none=True, exclude_unset=True)
    if patch.model is not None:
        if patch.model.generation is None:
            settings["model"].pop("generation", None)
        else:
            settings["model"]["generation"] = patch.model.generation.model_dump()
    return candidate


def settings_diffs(before: dict, after: dict) -> list[dict]:
    """按五个可编辑字段生成差异，不要求用户阅读整份技术规划。"""

    old, new = before["prompt"], after["prompt"]
    fields = [("role", "角色", (old.get("persona") or {}).get("role"), (new.get("persona") or {}).get("role")),
              ("tone", "表达风格", (old.get("persona") or {}).get("tone"), (new.get("persona") or {}).get("tone")),
              ("systemPrompt", "System Prompt", old["systemPrompt"], new["systemPrompt"]),
              ("constraints", "业务约束", old.get("constraints", []), new.get("constraints", [])),
              ("temperature", "Temperature", before["temperature"], after["temperature"])]
    return [{"field": key, "label": label, "before": left, "after": right}
            for key, label, left, right in fields if left != right]
