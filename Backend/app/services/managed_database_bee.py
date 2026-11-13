"""校验 Managed Database 普通 JSON 接口的 Bee 成功包装。"""

from typing import Any


def unwrap_bee_envelope(payload: Any) -> Any:
    """严格核对 Bee 成功包装，业务数据仍由端点各自校验。"""

    if (
        not isinstance(payload, dict)
        or set(payload) != {"returnCode", "errorMsg", "body"}
        or payload["returnCode"] != "SUC0000"
        or payload["errorMsg"] not in ("", None)
    ):
        raise ValueError("SaaS 响应不符合 Bee 成功合同。")
    return payload["body"]
