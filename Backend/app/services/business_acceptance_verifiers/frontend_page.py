"""前端页面 endpoint 消费检查器入口。"""

from __future__ import annotations

from app.services.business_acceptance_verifiers.typescript_inspection import (
    verify_page_endpoint_usage_source,
)

__all__ = ["verify_page_endpoint_usage_source"]
