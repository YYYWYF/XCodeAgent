"""按内部输出预算决定 Contract 物理分批，不改变业务 Contract 的身份。"""

from hashlib import sha256
import json
import math


def fragment_digest(value) -> str:
    """用稳定内容身份缓存任务，批次重排不依赖数组序号。"""

    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def contract_part_prefix(contract_id: str) -> str:
    """对子片段的 Contract 归属编码，避免 ID 含冒号时前缀误匹配。"""

    return f"contract-part:{fragment_digest(contract_id)}:"


def approximate_tokens(value) -> int:
    """以紧凑 JSON 的 UTF-8 字节数保守估量，结果不是供应商真实 token 计数。"""

    return math.ceil(len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) / 2)


def output_targets(max_output_tokens: int) -> tuple[int, int]:
    """整份目标最多六千或预算四成，批次目标最多三千五或预算四分之一。"""

    if max_output_tokens <= 0:
        raise ValueError("技术规划输出预算必须为正数。")
    return min(6000, int(max_output_tokens * .4)), min(3500, int(max_output_tokens * .25))


def estimated_contract_tokens(catalog: dict, spec: dict) -> int:
    """结合接口元数据、实体字段及请求响应重复成本估量首版输出，不让模型自报大小。"""

    fields = [field for entity in catalog["entities"] if entity["id"] in spec["entity_ids"] for field in entity["fields"]]
    return 400 + sum(300 + approximate_tokens(endpoint) for endpoint in spec["endpoints"]) + sum(
        max(80, approximate_tokens(field) * 2) for field in fields)


def needs_batches(catalog: dict, spec: dict, max_output_tokens: int) -> bool:
    """数量或估算体量任一超限就分批，十二个轻量接口仍可整份生成。"""

    whole, _ = output_targets(max_output_tokens)
    return len(spec["endpoints"]) > 12 or estimated_contract_tokens(catalog, spec) > whole


def pack_groups(items: list, *, limit: int, target: int, weight) -> list[list]:
    """按稳定输入顺序装箱，同时限制条目数和保守输出体量。"""

    groups, current, total = [], [], 0
    for item in items:
        cost = weight(item)
        if current and (len(current) >= limit or total + cost > target):
            groups.append(current)
            current, total = [], 0
        current.append(item)
        total += cost
    if current:
        groups.append(current)
    return groups
