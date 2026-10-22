"""无工具字段映射建议模型调用，不拥有文件或数据库执行能力。"""

from typing import Any

from app.agents.endpoint_design_assist_prompts import build_assist_messages
from app.agents.messages import _coerce_content_text
from app.agents.model_factory import create_chat_model
from app.config import Settings
from app.domain.endpoint_design_assist_steps import ASSIST_STEP_MODELS, AssistStep
from app.services.endpoint_design_assist_steps import step_model_result
from app.domain.endpoint_design_external_assist import ExternalRequestStepResult
from app.services.endpoint_design_external_assist_validation import expand_external_step
from app.utils.model_output import extract_json_object


async def invoke_mapping_assistant(
    context: dict[str, Any], *, step: AssistStep, thread_id: str | None = None,
) -> dict[str, Any]:
    """每次只调用一个区域的模型，使用简洁输出再确定性补齐正式配置。"""
    messages = build_assist_messages(context, step)
    model = create_chat_model(Settings.from_env())
    response = await model.ainvoke(
        messages,
        config={"configurable": {"thread_id": thread_id}} if thread_id else None,
    )
    parsed = extract_json_object(_coerce_content_text(response.content))
    result = (ExternalRequestStepResult if step == "request" else ASSIST_STEP_MODELS[step]).model_validate(parsed)
    if step in {"rule", "operation"}:
        return result.model_dump(mode="json", by_alias=True)
    if context.get("selection", {}).get("sourceType") == "external_api":
        return expand_external_step(result, context).model_dump(mode="json", by_alias=True)
    # 模型只提供字段引用；来源、类型和快照均从本轮真实上下文补齐。
    return step_model_result(result, context).model_dump(mode="json", by_alias=True)
