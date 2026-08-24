"""后端外部 API Client 和映射检查器入口。"""

from __future__ import annotations

from app.services.business_acceptance_verifiers.java_inspection import (
    verify_external_client_source,
    verify_external_mapping_source,
)

__all__ = ["verify_external_api_client_source", "verify_external_api_mapping_source"]

verify_external_api_client_source = verify_external_client_source
verify_external_api_mapping_source = verify_external_mapping_source
