"""后端 Repository 契约检查器入口。"""

from __future__ import annotations

from app.services.business_acceptance_verifiers.java_inspection import verify_repository_source

__all__ = ["verify_repository_source"]
