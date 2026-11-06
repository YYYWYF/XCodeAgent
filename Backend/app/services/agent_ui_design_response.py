"""严格解析设计模型文件响应，并提供可回灌的格式诊断。"""

from __future__ import annotations

import json


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """拒绝重复 JSON key，避免模型响应中隐藏路径覆盖或合同覆盖。"""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("设计响应不能包含重复 JSON 字段。")
        result[key] = value
    return result


def parse_design_files(content: str, allowed: tuple[str, ...]) -> dict[str, str]:
    """只接受完整业务文件集合；格式错误给出位置，禁止猜测或改写源码。"""
    if len(content.encode("utf-8")) > 512 * 1024:
        raise ValueError("设计响应超过允许的文件总量。")
    text = content.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"设计响应 JSON 格式错误：行 {exc.lineno}，列 {exc.colno}，字符 {exc.pos}；{exc.msg}。"
            "请对源码字符串内的双引号、反斜杠及换行正确转义，并返回完整 files JSON。"
        ) from exc
    if not isinstance(value, dict) or set(value) != {"files"}:
        raise ValueError("设计响应必须仅包含 files 对象。")
    files = value["files"]
    if not isinstance(files, dict) or set(files) != set(allowed):
        raise ValueError("设计响应必须完整覆盖本轮允许的业务文件，禁止额外路径。")
    if any(not isinstance(code, str) or not code.strip() for code in files.values()):
        raise ValueError("每个设计文件必须包含非空源码。")
    return files
