"""后端领域映射检查器入口。"""

from __future__ import annotations

from app.services.business_acceptance_verifiers.java_inspection import verify_domain_mapping_source

__all__ = ["verify_domain_mapping_source"]
