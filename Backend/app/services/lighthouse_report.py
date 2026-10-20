"""提取 Lighthouse 报告中用于用户说明的有限结构化数据。"""

from __future__ import annotations

import math
from typing import Any


CATEGORIES = {
    "performance": "performance",
    "accessibility": "accessibility",
    "best-practices": "best_practices",
    "seo": "seo",
}
METRICS = {
    "first-contentful-paint": "fcp",
    "largest-contentful-paint": "lcp",
    "total-blocking-time": "tbt",
    "cumulative-layout-shift": "cls",
    "speed-index": "si",
}


def finite_number(value: Any, *, maximum: float | None = None) -> float | None:
    """只接受非负有限数值，避免缺失值、布尔值或异常值被当作零分。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except OverflowError:
        return None
    if not math.isfinite(numeric) or numeric < 0:
        return None
    if maximum is not None and numeric > maximum:
        return None
    return numeric


def _opportunities(categories: dict[str, Any], audits: dict[str, Any]) -> list[dict[str, Any]]:
    """按低分类得分和检查得分选出最多三项实际失败检查，不推测问题。"""

    candidates: dict[str, tuple[tuple[float, float, str], dict[str, Any]]] = {}
    for category_id, output_key in CATEGORIES.items():
        category = categories.get(category_id)
        if not isinstance(category, dict):
            continue
        category_score = finite_number(category.get("score"), maximum=1)
        refs = category.get("auditRefs")
        if category_score is None or not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict) or not finite_number(ref.get("weight")):
                continue
            audit_id = ref.get("id")
            audit = audits.get(audit_id) if isinstance(audit_id, str) else None
            if not isinstance(audit, dict) or audit_id in METRICS:
                continue
            score = finite_number(audit.get("score"), maximum=1)
            if score is None or score >= 0.9 or audit.get("scoreDisplayMode") in {
                "manual", "notApplicable", "informative", "error",
            }:
                continue
            title = str(audit.get("title") or "").strip()[:200]
            if not title:
                continue
            item = {
                "id": audit_id,
                "category": output_key,
                "title": title,
                "description": str(audit.get("description") or "").strip()[:600],
            }
            rank = (category_score, score, audit_id)
            previous = candidates.get(audit_id)
            if previous is None or rank < previous[0]:
                candidates[audit_id] = (rank, item)
    return [item for _, item in sorted(candidates.values(), key=lambda entry: entry[0])[:3]]


def parse_lighthouse_report(lhr: dict[str, Any]) -> dict[str, Any] | None:
    """保留分类得分、原始指标评级、采样环境及真实问题，供 Markdown 确定性说明。"""

    categories = lhr.get("categories")
    categories = categories if isinstance(categories, dict) else {}
    audits = lhr.get("audits")
    audits = audits if isinstance(audits, dict) else {}
    scores: dict[str, int] = {}
    for category_id, output_key in CATEGORIES.items():
        category = categories.get(category_id)
        score = finite_number(category.get("score"), maximum=1) if isinstance(category, dict) else None
        if score is not None:
            scores[output_key] = int(round(score * 100))
    metrics: dict[str, float] = {}
    metric_scores: dict[str, float] = {}
    for audit_id, output_key in METRICS.items():
        audit = audits.get(audit_id)
        if not isinstance(audit, dict):
            continue
        value = finite_number(audit.get("numericValue"))
        if value is None:
            continue
        metrics[output_key] = round(value, 3)
        score = finite_number(audit.get("score"), maximum=1)
        if score is not None:
            metric_scores[output_key] = score
    if not scores and not metrics:
        return None
    settings = lhr.get("configSettings")
    settings = settings if isinstance(settings, dict) else {}
    return {
        "scores": scores,
        "metrics": metrics,
        "details": {
            "metric_scores": metric_scores,
            "opportunities": _opportunities(categories, audits),
            "context": {
                "url": str(lhr.get("finalUrl") or "")[:1_000],
                "fetch_time": str(lhr.get("fetchTime") or "")[:80],
                "device": str(settings.get("formFactor") or "")[:30],
                "lighthouse_version": str(lhr.get("lighthouseVersion") or "")[:30],
            },
        },
    }
