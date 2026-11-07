"""保留模型明确截断信号，并严格解析内部批次，防止重复键静默覆盖。"""

import json


class PlanningOutputTruncated(ValueError):
    """模型明确因输出长度结束，区别于普通 JSON 或业务校验错误。"""


def check_output_finish(message) -> None:
    """只根据 SDK 明确的长度终止原因触发缩批，不猜测网络错误或无效 JSON。"""

    metadata = getattr(message, "response_metadata", {}) or {}
    if metadata.get("finish_reason") == "length" or metadata.get("stop_reason") == "max_tokens":
        raise PlanningOutputTruncated("模型达到输出长度上限，当前片段未提交。")


def parse_batch_json(output: str) -> dict:
    """批次只接纳完整 JSON 根对象，重复 Schema 名或字段名必须显式拒绝。"""

    text = output.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) >= 3 and lines[-1].strip() == "```" else text

    def unique_object(pairs):
        """构造对象前拒绝重复键，禁止最后一次声明覆盖前一次定义。"""

        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"分批片段包含重复键：{key}。")
            result[key] = value
        return result

    value = json.loads(text, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("分批生成必须返回完整 JSON 根对象。")
    return value
