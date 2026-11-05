"""校验 Agent 正式输入输出 Schema，不允许解析工程外部引用。"""

import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


def validate_execution_schema(value: dict[str, Any], *, object_input: bool = False) -> dict[str, Any]:
    """确认当前 JSON Schema 合法且自包含，输入根对象由业务参数组成。"""
    if len(json.dumps(value, ensure_ascii=False).encode()) > 65536:
        raise ValueError("Agent 输入输出 Schema 不能超过 64 KiB。")
    if object_input and value.get("type") != "object":
        raise ValueError("Agent inputSchema 必须声明 type=object。")
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key in ("$ref", "$dynamicRef"):
                if key in item and (not isinstance(item[key], str) or not item[key].startswith("#")):
                    raise ValueError("Agent Schema 只允许本地片段引用。")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as exc:
        raise ValueError("Agent JSON Schema 无效：" + exc.message) from exc
    return value
