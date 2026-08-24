"""前端 API 契约检查器入口。"""

from __future__ import annotations

from typing import Any

from app.services.business_acceptance_verifiers.typescript_inspection import (
    verify_api_contract_source,
)

__all__ = ["verify_api_contract_source"]
