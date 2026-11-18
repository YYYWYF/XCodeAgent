"""只定位单个完整任务根对象，不改写 JSON 或从损坏外层结构中恢复嵌套任务。"""

from dataclasses import dataclass
import json
import re


@dataclass(frozen=True, slots=True)
class UnitCandidateEnvelope:
    """保留候选正文的原始切片及其外围字符数量。"""

    text: str
    prefix_chars: int = 0
    suffix_chars: int = 0


def _container_end(text: str, start: int) -> int | None:
    """按字符串转义及容器层级定位闭合边界，外层截断或括号错配时立即拒绝。"""

    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"}": "{", "]": "["}
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            stack.append(character)
        elif character in "}]":
            if not stack or stack.pop() != pairs[character]:
                return None
            if not stack:
                return index + 1
    return None


def _contains_nested_tasks(value: object) -> bool:
    """发现其他根容器中的任务对象时拒绝挑选，避免把示例或包裹对象当最终结果。"""

    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if "tasks" in item:
                return True
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def select_unit_candidate_envelope(text: str) -> UnitCandidateEnvelope | None:
    """定位唯一完整 tasks 根对象；严格字段和数值校验仍由原 Parser 对原文执行。"""

    # 完整响应本身已经是 JSON 时原样交回，包括错误顶层类型及额外字段，不能隐藏它们。
    try:
        json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        pass
    else:
        return UnitCandidateEnvelope(text=text)

    candidate: UnitCandidateEnvelope | None = None
    index = 0
    while index < len(text):
        character = text[index]
        if character not in "{[":
            if character in "}]":
                return None
            index += 1
            continue
        end = _container_end(text, index)
        if end is None:
            return None
        block = text[index:end]
        try:
            value = json.loads(block)
        except (json.JSONDecodeError, RecursionError):
            # 可能是 JSON 的损坏容器不能跳过；也覆盖转义键、单引号或未加引号的 tasks。
            looks_like_json = block[1:].lstrip().startswith(('"', "{", "["))
            if looks_like_json or re.search(r"""(?:["']|\b)tasks(?:["'])?\s*:""", block):
                return None
        else:
            if isinstance(value, dict) and "tasks" in value:
                if candidate is not None:
                    return None
                candidate = UnitCandidateEnvelope(
                    text=block, prefix_chars=index, suffix_chars=len(text) - end,
                )
            elif _contains_nested_tasks(value):
                return None
        index = end
    return candidate
