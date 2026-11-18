"""集成修复的单次结构化推理和结果校验。"""

from typing import TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.observability.build_instrumentation import record_prompt
from app.services.run_workers import run_worker

Result = TypeVar("Result", bound=BaseModel)


def invoke_repair_result(
    model: BaseChatModel, schema: type[Result], *, instructions: str, evidence: str, first_prompt: bool = False,
) -> Result | None:
    """只绑定结果 schema，一次调用后由平台验证参数，不执行返回的工具调用。"""
    with run_worker():
        system_prompt = f"{instructions}\nSubmit the final result by calling {schema.__name__} exactly once."
        if first_prompt:
            record_prompt(system_prompt + evidence, task_context=evidence)
        # 直连模型服务允许隐式工具选择，但可能拒绝强制指定具名工具。
        message = model.bind_tools([schema]).invoke([
            SystemMessage(content=system_prompt), HumanMessage(content=evidence),
        ])
    # 停止发生在模型等待期间时，阻止后续计划落盘或派发。
    with run_worker():
        if not isinstance(message, AIMessage):
            return None
        calls = message.tool_calls
        if len(calls) != 1 or calls[0]["name"] != schema.__name__ or message.invalid_tool_calls:
            return None
        try:
            return schema.model_validate(calls[0]["args"])
        except ValidationError:
            return None
