from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TsxReplacement(BaseModel):
    """表示模型建议的单处精确 TSX 替换。"""

    model_config = ConfigDict(extra="forbid")

    old: str
    new: str


class TsxRepairPatch(BaseModel):
    """限制修复模型仅返回局部替换，避免重新生成整页时截断。"""

    model_config = ConfigDict(extra="forbid")

    edits: list[TsxReplacement]


def apply_tsx_repair_patch(source: str, patch: TsxRepairPatch) -> str:
    """只在旧片段唯一且互不重叠时应用局部修复，否则拒绝修改。"""

    if not 1 <= len(patch.edits) <= 20:
        raise ValueError("TSX 修复必须包含 1–20 处局部替换")
    ranges: list[tuple[int, int, str]] = []
    for edit in patch.edits:
        if not edit.old or not edit.new or len(edit.old) > 2_500 or len(edit.new) > 3_500:
            raise ValueError("TSX 修复片段为空或过长")
        if source.count(edit.old) != 1:
            raise ValueError("TSX 修复原片段在候选稿中并非唯一")
        start = source.index(edit.old)
        end = start + len(edit.old)
        if any(start < previous_end and end > previous_start for previous_start, previous_end, _ in ranges):
            raise ValueError("TSX 修复片段互相重叠")
        ranges.append((start, end, edit.new))
    if sum(end - start for start, end, _ in ranges) > max(2_000, len(source) // 3):
        raise ValueError("TSX 修复修改范围过大，应改用完整代码修复")
    result = source
    for start, end, replacement in sorted(ranges, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result
