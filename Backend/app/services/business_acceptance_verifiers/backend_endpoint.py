"""后端 Endpoint Controller 契约检查器入口。"""

from __future__ import annotations

from app.services.business_acceptance_verifiers.java_inspection import verify_endpoint_source

__all__ = ["verify_endpoint_source"]
