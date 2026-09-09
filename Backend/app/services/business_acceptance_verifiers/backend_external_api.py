"""后端 upstream 传输、调用和转换检查器入口。"""

from __future__ import annotations

from typing import Any

from app.services.business_acceptance_verifiers.common import verification_result
from app.services.business_acceptance_verifiers.java_inspection import (
    verify_external_client_source,
    verify_external_mapping_source,
)

__all__ = ["verify_upstream_source"]


def verify_upstream_source(
    files: dict[str, str], expected: dict[str, Any]
) -> dict[str, Any]:
    """联合验证 upstream 的传输 Client 与必要字段转换。"""

    client_result = verify_external_client_source(files, expected)
    converter_result = verify_external_mapping_source(files, expected)
    statuses = {str(client_result.get("status")), str(converter_result.get("status"))}
    status = (
        "failed"
        if "failed" in statuses
        else "blocked"
        if "blocked" in statuses
        else "passed"
    )
    evidence = "；".join(
        str(result.get("evidence") or "").strip()
        for result in (client_result, converter_result)
        if str(result.get("evidence") or "").strip()
    )
    return verification_result(
        status,
        evidence or "已完成 upstream Client 与转换验收。",
        facts={
            "client": client_result.get("facts") or {},
            "converter": converter_result.get("facts") or {},
        },
    )
