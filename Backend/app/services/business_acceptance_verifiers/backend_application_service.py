"""后端 ApplicationService 契约检查器入口。"""

from __future__ import annotations

from app.services.business_acceptance_verifiers.java_inspection import verify_application_service_source

__all__ = ["verify_application_service_source"]
