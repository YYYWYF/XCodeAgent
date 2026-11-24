"""Build 报告的模型耗时统计；缺失证据不填零，独立于质量门禁。"""

from math import ceil
from statistics import mean, median

TIMING_FIELDS = {
    "model_duration_ms": "模型累计耗时 ms",
    "ttft_samples": "TTFT 有效配对样本数",
    "ttft_total_ms": "TTFT 累计 ms",
    "output_duration_total_ms": "首 Token 后累计 ms",
    "ttft_mean_ms": "TTFT 平均 ms",
    "ttft_p50_ms": "TTFT P50 ms",
    "ttft_p95_ms": "TTFT P95 ms（nearest-rank）",
    "ttft_coverage": "TTFT 配对覆盖率",
    "ttft_ratio": "配对样本 TTFT / 总耗时",
}


def timing_pair(row):
    """只接受非负整数且满足加和的模型配对，排除 bool 和非法历史值。"""
    data = row["data"]
    model = data.get("model") or {}
    values = (data.get("duration_ms"), model.get("ttft_ms"), model.get("output_duration_ms"))
    if data.get("span_kind") != "model" or not all(type(value) is int and value >= 0 for value in values):
        return None
    return values if values[0] == values[1] + values[2] else None


def model_timing_metrics(models):
    """在同一批有效配对上计算 TTFT、后续耗时和占比。"""
    pairs = [pair for row in models if (pair := timing_pair(row)) is not None]
    ttfts = sorted(pair[1] for pair in pairs)
    durations = [row["data"].get("duration_ms") for row in models]
    paired_duration = sum(pair[0] for pair in pairs)
    return {
        "model_duration_ms": sum(durations) if durations and all(
            type(value) is int and value >= 0 for value in durations) else None,
        "ttft_samples": len(pairs),
        "ttft_total_ms": sum(ttfts) if pairs else None,
        "output_duration_total_ms": sum(pair[2] for pair in pairs) if pairs else None,
        "ttft_mean_ms": mean(ttfts) if pairs else None,
        "ttft_p50_ms": median(ttfts) if pairs else None,
        "ttft_p95_ms": ttfts[ceil(len(ttfts) * .95) - 1] if pairs else None,
        "ttft_coverage": len(pairs) / len(models) if models else None,
        "ttft_ratio": sum(ttfts) / paired_duration if paired_duration else None,
    }
