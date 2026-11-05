"""在 checkpoint 存储边界引用化大块资料，不让前端会话承担恢复事实。"""

from typing import Any
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from app.workspace.content_artifacts import pack_materials, expand_materials


class ArtifactCheckpointSerializer:
    """沿用 LangGraph 类型序列化，仅把 JSON 执行材料存为工作区内容引用。"""

    def __init__(self, workspace: str):
        """把资料解析范围固定到拥有该 checkpoint 的工作区。"""
        self.workspace = workspace
        self.base = JsonPlusSerializer()

    def dumps_typed(self, value: Any) -> tuple[str, bytes]:
        """序列化前提取重复源材料，原类型元数据仍由 LangGraph 维护。"""
        return self.base.dumps_typed(pack_materials(self.workspace, value))

    def loads_typed(self, value: tuple[str, bytes]) -> Any:
        """恢复时核验并复用 hash 内容，缺失资料不会降级为不完整执行状态。"""
        return expand_materials(self.workspace, self.base.loads_typed(value))
