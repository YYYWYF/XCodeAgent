"""提取异常链身份和底层系统错误，供运行日志去重。"""

from __future__ import annotations

from typing import Any


def exception_details(exception: BaseException) -> tuple[BaseException, dict[str, Any]]:
    """沿有效异常链查找根因及系统错误码，防止循环引用导致日志阻塞。"""

    chain: list[BaseException] = []
    current: BaseException | None = exception
    while current is not None and all(current is not item for item in chain):
        chain.append(current)
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    root = chain[-1]
    fields: dict[str, Any] = {
        "rootExceptionType": type(root).__name__,
        "rootExceptionMessage": str(root),
    }
    for item in reversed(chain):
        for attribute in ("errno", "winerror"):
            value = getattr(item, attribute, None)
            if value is not None:
                fields.setdefault(attribute, value)
    return root, fields
