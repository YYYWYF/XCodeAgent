"""记忆能力选择及代码生成说明，连接信息仅由运行环境提供。"""

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ShortTermMemory(BaseModel):
    """缺省值沿用当前模板的会话记忆，不要求补齐旧配置。"""

    model_config = ConfigDict(extra="allow")
    enabled: bool = True
    storage: Literal["template", "object_storage", "tdsql"] = "template"


class LongTermMemory(BaseModel):
    """只选择是否生成长期记忆云服务接入。"""

    model_config = ConfigDict(extra="allow")
    enabled: bool = False


class MemorySettings(BaseModel):
    """保留模板已有的隔离、checkpoint 等说明，不在设计阶段校验环境连接。"""

    model_config = ConfigDict(extra="allow")
    shortTerm: ShortTermMemory = Field(default_factory=ShortTermMemory)
    longTerm: LongTermMemory = Field(default_factory=LongTermMemory)


def memory_settings(configuration: dict) -> dict:
    """为界面读取用户选择，不写回或迁移原配置。"""

    return MemorySettings.model_validate(configuration).model_dump()


def runtime_memory_settings(configuration: dict) -> dict:
    """按当前临时策略派生执行配置：统一使用 SQLite 短期记忆，关闭长期云记忆。"""

    result = memory_settings(configuration)
    # 界面选择保留在正式配置中；存储适配交付前，执行统一沿用模板默认值。
    result["shortTerm"].update(enabled=True, storage="template")
    result["longTerm"]["enabled"] = False
    return result


def merge_memory_settings(configuration: dict, patch: MemorySettings) -> dict:
    """仅合并提交的选择，保留原模板的其他记忆字段和未修改段。"""

    result = deepcopy(configuration)
    for section, values in patch.model_dump(exclude_unset=True).items():
        previous = result.get(section)
        result[section] = {**(previous if isinstance(previous, dict) else {}), **values} if isinstance(values, dict) else values
    return result


MEMORY_GENERATION_RULES = (
    "Current temporary memory execution policy: always use the template SQLite checkpointer for "
    "short-term conversation memory and keep long-term cloud memory disabled, regardless of UI choices. "
    "configuration retains the user's saved choices; memorySelection contains the effective execution "
    "settings and takes precedence for implementation. Do not implement or initialize TDSQL, MySQL, "
    "object storage or cloud memory adapters, request their connection parameters, or fail because "
    "these adapters are unavailable. Preserve the template checkpoint lifecycle, native interaction "
    "continuation and trusted Principal/thread isolation for every Agent. Reuse the actual template "
    "get_checkpointer entry point. Prefer direct reuse over additional configuration wrappers. If "
    "configuration uses immutable mappings, validate Mapping rather than dict at every nested level. "
)
