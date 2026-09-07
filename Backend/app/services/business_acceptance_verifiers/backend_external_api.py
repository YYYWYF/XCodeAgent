"""后端外部 API Client 和映射检查器入口。"""

from __future__ import annotations

from typing import Any

from app.services.business_acceptance_verifiers.java_inspection import (
    verify_external_client_source,
    verify_external_mapping_source,
)

__all__ = ["verify_external_api_client_source", "verify_external_api_mapping_source"]


def verify_external_api_client_source(
    files: dict[str, str], expected: dict[str, Any]
) -> dict[str, Any]:
    """按 Endpoint API 设计快照验证外部 Client 的调用事实。"""

    return verify_external_client_source(files, expected)


def verify_external_api_mapping_source(
    files: dict[str, str], expected: dict[str, Any]
) -> dict[str, Any]:
    """按 Endpoint 动态映射验证外部字段到内部语义的关系。"""

    return verify_external_mapping_source(files, expected)
