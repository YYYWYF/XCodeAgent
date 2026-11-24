"""从本地 JSONL 离线汇总 Build 样本并验证实验配对条件。"""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import mean, median

from pydantic import ValidationError

from app.observability.build_model_timing_report import TIMING_FIELDS, model_timing_metrics, timing_pair
from app.observability.telemetry_models import ExecutionTraceRecord

_CONDITIONS = (
    "scenario_id", "project_id", "requirement_revision", "workspace_snapshot",
    "task_plan_sha256", "model", "template_version", "tool_environment", "validation_suite",
    "cache_condition", "repetitions", "quality_gate",
)
_SECTIONS = {
    "A. Prompt 体积": {
        key: f"{label}（{unit}）"
        for key_base, label in (
            ("project_plan", "ProjectPlan 注入"), ("workspace_context", "Workspace 投影注入"),
            ("task_context", "Task Context 注入"), ("initial_prompt", "初始 Prompt"),
        )
        for key, unit in ((f"{key_base}_chars", "字符"), (f"{key_base}_bytes", "UTF-8 字节"))
    },
    "B. 整体 Token": {
        "input_tokens": "Input Token", "output_tokens": "Output Token", "total_tokens": "Total Token",
        "model_calls": "模型轮数", "usage_coverage": "完整 Usage 覆盖率", "tool_calls": "工具调用次数",
        "tool_bytes": "可测工具返回字节", "tool_coverage": "工具返回字节覆盖率",
        "retry_tokens": "重试 Total Token（含修复任务重试）", "repair_tokens": "修复 Total Token",
    },
    "C. 交付效率与质量": {
        "wall_ms": "Build 墙钟毫秒（单调时钟）", "active_ms": "调度片段耗时合计",
        "build_completed": "Build 完成率", "first_rate": "Task 首轮完成率",
        "final_rate": "Task 最终完成率", "acceptance_rate": "Task 验收通过率（含未知分母）",
        "acceptance_coverage": "Task 验收覆盖率", "repair_count": "修复规划次数",
        "repair_attempts": "修复执行次数", "repair_ms": "修复规划与执行耗时合计",
    },
    "D. 模型响应耗时": TIMING_FIELDS,
}


def _read_input(root):
    """逐行校验，损坏或无法归属的分析记录只影响报告可信度。"""
    records, problems, ids = defaultdict(list), [], set()
    paths = sorted(root.glob("*.jsonl"))
    if not paths:
        problems.append("缺少 LOCAL JSONL。")
    for path in paths:
        try:
            with path.open("rb") as stream:
                for line_number, line in enumerate(stream, 1):
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise ValueError
                        context = row.get("context") or {}
                        if row.get("type") != "execution_trace" or context.get("business_stage") != "build":
                            continue
                        record = ExecutionTraceRecord.model_validate(row).model_dump()
                        extra = record["context"]["extra"] or {}
                        data = record["data"] or {}
                        if not extra.get("build_run_id") or not data.get("trace_id") or not data.get("span_id"):
                            raise ValueError
                        if data["span_id"] in ids:
                            raise ValueError
                        ids.add(data["span_id"])
                        records[(extra.get("scenario_id"), extra["build_run_id"])].append(record)
                    except (ValueError, TypeError, AttributeError, ValidationError):
                        problems.append(f"{path.name}:{line_number} 有无效、重复或未绑定的 Build 记录。")
        except OSError:
            problems.append(f"无法读取 {path.name}。")
    return records, problems


def _load_manifest(root):
    """读取独立实验清单，检查必要条件、重复与预设质量阈值。"""
    problems, by_id = [], {}
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, list) or not manifest:
            raise ValueError
        for entry in manifest:
            if not isinstance(entry, dict):
                raise ValueError
            keys = (*_CONDITIONS, "build_run_id", "experiment_tag", "code_revision")
            if any(entry.get(key) in (None, "", []) for key in keys):
                raise ValueError
            model = entry["model"]
            if (not isinstance(model, dict) or not all(model.get(key) for key in ("name", "provider", "transport"))
                    or not isinstance(model.get("parameters"), dict) or not model["parameters"]):
                raise ValueError
            gates = entry["quality_gate"]
            if not isinstance(gates, dict):
                raise ValueError
            for key in ("minimum_final_rate", "minimum_acceptance_rate", "wall_time_tolerance", "repair_count_tolerance"):
                threshold = gates.get(key)
                if (type(threshold) not in (int, float) or not 0 <= threshold < float("inf")
                        or (key.startswith("minimum") and threshold > 1)):
                    raise ValueError
            if type(entry["repetitions"]) is not int or entry["repetitions"] < 1:
                raise ValueError
            key = (entry["scenario_id"], entry["build_run_id"])
            if key in by_id:
                raise ValueError
            by_id[key] = entry
    except (OSError, ValueError, TypeError):
        problems.append("manifest.json 缺失或格式无效，无法核对实验条件。")
        by_id.clear()
    return by_id, problems


def _metric(row, key):
    """读取可选数值，保留未知。"""
    return (row["data"]["metrics"] or {}).get(key)


def _sum(values):
    """只有所有值都已知才得出总量。"""
    values = list(values)
    return sum(values) if all(value is not None for value in values) else None


def _ratio(numerator, denominator):
    """保留空分母和缺失数据。"""
    return numerator / denominator if numerator is not None and denominator else None


def _token(row, key):
    """只接受真实 Provider 用量。"""
    model = row["data"]["model"] or {}
    return model.get(key) if model.get("usage_source") == "provider" else None


def _validate_trace(rows, roots):
    """按根计数核对丢片、父链和每次调用的归属。"""
    by_id = {row["data"]["span_id"]: row["data"] for row in rows}
    root_counts = Counter(row["data"]["trace_id"] for row in roots)
    counts = Counter()
    for row in rows:
        data = row["data"]
        if data["run_name"] == "build" and data["parent_span_id"] is None:
            continue
        parent = by_id.get(data["parent_span_id"])
        if not parent or parent["trace_id"] != data["trace_id"] or root_counts[data["trace_id"]] != 1:
            return False
        counts[data["trace_id"], data["span_kind"]] += 1
    return bool(roots) and all(
        _metric(root, f"{kind}_spans") == counts[root["data"]["trace_id"], kind]
        for root in roots for kind in ("model", "tool", "node")
    )


def _sample(key, rows, manifest, problems):
    """按真实 BuildRun 汇总，尝试、Model 和 Tool 独立计量。"""
    roots, kinds, names = [], defaultdict(list), defaultdict(list)
    for row in rows:
        data = row["data"]
        kinds[data["span_kind"]].append(row)
        names[data["run_name"]].append(row)
        if data["span_kind"] == "workflow" and data["run_name"] == "build":
            roots.append(row)
    roots.sort(key=lambda row: row["occurred_at"] or "")
    terminal = roots[-1] if roots else None
    if not _validate_trace(rows, roots):
        problems.append(f"{key[1]} 的 Span 不完整。")
    if not manifest or any(
        (row["identity"] or {}).get("project_id") != manifest["project_id"]
        or row["context"]["extra"]["task_plan_sha256"] != manifest["task_plan_sha256"]
        or row["context"]["extra"]["experiment_tag"] != manifest["experiment_tag"]
        for row in rows
    ):
        problems.append(f"{key[1]} 缺少匹配清单或记录与清单不一致。")
    attempts = names["task_attempt"]
    models, tools, prompts = kinds["model"], kinds["tool"], names["initial_prompt"]
    if manifest and any(
        observed and observed != manifest["model"][key]
        for row in models for key, observed in (
            (key, (row["data"]["model"] or {}).get(key)) for key in ("name", "provider")
        )
    ):
        problems.append(f"{key[1]} 的实际模型或 Provider 与清单不一致。")
    if prompts and not models:
        problems.append(f"{key[1]} 有生成 Prompt，但缺少模型终态。")
    if len(roots) > 1:
        problems.append(f"{key[1]} 有多次调度调用，完整墙钟时间不可用。")
    planned = _metric(terminal, "planned_tasks") if terminal else None
    first_completed = _metric(roots[0], "first_completed_tasks") if roots else None
    first_planned = _metric(roots[0], "planned_tasks") if roots else None
    passed = _metric(terminal, "acceptance_passed") if terminal else None
    failed = _metric(terminal, "acceptance_failed") if terminal else None
    measured_tools = [_metric(row, "tool_result_bytes") for row in tools
                      if _metric(row, "tool_result_bytes") is not None]
    repairs = names["repair_planning"]
    repair_attempts = [row for row in attempts if row["context"]["extra"]["repair"]]
    metrics = {
        key: sum(known) if (known := [_metric(row, key) for row in prompts
                                     if _metric(row, key) is not None]) else None
        for key in _SECTIONS["A. Prompt 体积"]
    }
    metrics.update({key: _sum(_token(row, key) for row in models)
                    for key in ("input_tokens", "output_tokens", "total_tokens")})
    metrics.update(
        model_calls=len(models),
        usage_coverage=_ratio(sum(all(_token(row, key) is not None for key in
                                     ("input_tokens", "output_tokens", "total_tokens"))
                                  for row in models), len(models)),
        tool_calls=len(tools), tool_bytes=sum(measured_tools) if measured_tools or not tools else None,
        tool_coverage=_ratio(len(measured_tools), len(tools)),
        retry_tokens=_sum(_token(row, "total_tokens") for row in models if row["context"]["extra"]["retry"]),
        repair_tokens=_sum(_token(row, "total_tokens") for row in models if row["context"]["extra"]["repair"]),
        wall_ms=terminal["data"]["duration_ms"] if len(roots) == 1 else None,
        active_ms=_sum(row["data"]["duration_ms"] for row in roots) if roots else None,
        build_completed=int(terminal["data"]["status"] == "completed") if terminal else None,
        first_rate=_ratio(first_completed, first_planned),
        final_rate=_ratio(_metric(terminal, "completed_tasks") if terminal else None, planned),
        acceptance_rate=_ratio(passed, planned) if passed or failed else None,
        acceptance_coverage=_ratio(_sum((passed, failed)), planned),
        repair_count=len(repairs), repair_attempts=len(repair_attempts),
        repair_ms=_sum(row["data"]["duration_ms"] for row in repairs + repair_attempts),
    )
    metrics.update(model_timing_metrics(models))
    return metrics


def _conditions(entries, size):
    """同一 Scenario 内固定条件并核对冻结的重复次数。"""
    if len(entries) != size or not size or any(entry is None for entry in entries):
        return None
    reference = {key: entries[0][key] for key in _CONDITIONS}
    if size != reference["repetitions"] or any(
        any(entry[key] != value for key, value in reference.items()) for entry in entries
    ):
        return None
    if any(len({entry[key] for entry in entries}) != 1 for key in ("experiment_tag", "code_revision")):
        return None
    return reference


def _values(samples, key):
    """返回完整样本的平均数和中位数。"""
    values = [sample[key] for sample in samples]
    if not values or any(value is None for value in values):
        return None, None
    return mean(values), median(values)


def _number(value):
    """在 Markdown 中显示实测值或 N/A。"""
    return "N/A" if value is None else f"{value:.3f}".rstrip("0").rstrip(".")


def _conclusion(before, after, gates, comparable):
    """基于冻结阈值同时检查 Token、质量、耗时和修复成本。"""
    keys = ("total_tokens", "usage_coverage", "wall_ms", "build_completed", "final_rate",
            "acceptance_rate", "acceptance_coverage", "repair_count", "repair_attempts", "repair_ms")
    if not comparable or any(before[key] is None or after[key] is None for key in keys):
        return "不可比较/数据不足，无法给出优化结论。"
    if before["usage_coverage"] != 1 or after["usage_coverage"] != 1:
        return "不可比较/数据不足，模型 Usage 覆盖不完整。"
    threshold = {"final_rate": gates["minimum_final_rate"], "acceptance_rate": gates["minimum_acceptance_rate"]}
    if any(after[key] < max(before[key], minimum) for key, minimum in threshold.items()) or any(
        after[key] < before[key] for key in ("build_completed", "acceptance_coverage")
    ):
        return "质量门禁未通过。"
    if after["total_tokens"] >= before["total_tokens"]:
        return "整体 Token 未下降，请查看模型轮数、工具反馈和修复成本。"
    if (after["wall_ms"] > before["wall_ms"] * (1 + gates["wall_time_tolerance"])
            or any(after[key] > before[key] + gates["repair_count_tolerance"]
                   for key in ("repair_count", "repair_attempts"))
            or after["repair_ms"] > before["repair_ms"] * (1 + gates["wall_time_tolerance"])):
        return "整体 Token 下降，交付时间或修复成本超过预设容差。"
    return "该场景整体 Token 下降，质量、交付时间及修复成本符合预设门禁。"


def _render_tree(lines, label, groups):
    """保留 BuildRun 到 Task/Attempt 和真实 Model/Tool 片段的可追溯标识。"""
    for (_, build_id), rows in groups.items():
        lines.append(f"\n#### {label} BuildRun `{build_id}`")
        lines.append("\n| 片段 | Task | Attempt | 状态 | 毫秒 | TTFT ms | Output Duration ms | Total Token | Span / Parent |")
        lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |")
        for row in rows:
            data, extra = row["data"], row["context"]["extra"]
            pair = timing_pair(row)
            columns = [data["run_name"], extra["task_id"] or ", ".join(extra["task_ids"] or []),
                       extra["attempt_id"], data["status"], _number(data["duration_ms"]),
                       _number(pair[1] if pair else None), _number(pair[2] if pair else None),
                       _number(_token(row, "total_tokens")),
                       f'{data["span_id"]} / {data["parent_span_id"] or "—"}']
            lines.append("| " + " | ".join(str(col or "—").replace("|", "\\|").replace("\n", " ")
                                           for col in columns) + " |")


def render_report(baseline, after=None):
    """输出三类指标、可比较性结论和逐片段追溯。"""
    before, before_problems = _read_input(baseline)
    before_manifest, before_manifest_problems = _load_manifest(baseline)
    later, later_problems = _read_input(after) if after else ({}, [])
    later_manifest, later_manifest_problems = _load_manifest(after) if after else ({}, [])
    input_problems = before_problems + later_problems + before_manifest_problems + later_manifest_problems
    lines = ["# Build 性能 Baseline" if after is None else "# Build 性能同条件对比",
             "\n每行按 BuildRun 统计平均数（中位数）。比率范围为 0–1。变化率为 (Baseline − After) / Baseline。",
             "\nTTFT 为客户端观测，累计模型耗时含并发调用，不能等同 Build 墙钟。后续耗时截至终态（包括失败/取消），不代表纯解码耗时。",
             "\nTTFT 仅统计三个非负整数且加和一致的配对；P95 使用 nearest-rank。不同覆盖率的耗时不能直接归因比较，TTFT 不参与质量门禁。"]
    if not before:
        lines.append("\n未获得真实 Baseline，缺少可归属的 Build 样本。")
    for problem in input_problems:
        lines.append(f"\n- {problem}")
    scenarios = sorted({key[0] for key in {*before, *later, *before_manifest, *later_manifest}}, key=str)
    for scenario in scenarios or ["N/A"]:
        groups = [{key: rows for key, rows in side.items() if key[0] == scenario} for side in (before, later)]
        errors = []
        samples = [[_sample(key, rows, manifest.get(key), errors) for key, rows in group.items()]
                   for group, manifest in zip(groups, (before_manifest, later_manifest))]
        conditions = [_conditions([entry for key, entry in manifest.items() if key[0] == scenario], len(group))
                      for group, manifest in zip(groups, (before_manifest, later_manifest))]
        comparable = bool(after and conditions[0] and conditions[0] == conditions[1] and not input_problems and not errors)
        lines.append(f"\n## Scenario {scenario}（Baseline n={len(samples[0])}，After n={len(samples[1])}）")
        if after and not comparable:
            lines.append("\n不可比较/数据不足。请核对采集完整性、清单条件与预设重复次数。")
        for error in errors:
            lines.append(f"\n- {error}")
        for name, side in zip(("Baseline", "After"), samples):
            lines.append(f"\n{name} TTFT 有效配对样本 / 总调用数："
                         f"{sum(item['ttft_samples'] for item in side)} / {sum(item['model_calls'] for item in side)}。")
        aggregates = [{}, {}]
        for title, fields in _SECTIONS.items():
            lines += [f"\n### {title}\n", "| 指标 | Baseline | After | 差值（After − Baseline） | 变化率 |",
                      "| --- | ---: | ---: | ---: | ---: |"]
            for key, label in fields.items():
                base, new = [_values(side, key) for side in samples]
                aggregates[0][key], aggregates[1][key] = base[0], new[0]
                timing_comparable = key not in TIMING_FIELDS or (
                    _values(samples[0], "ttft_coverage")[0] == _values(samples[1], "ttft_coverage")[0]
                )
                delta = new[0] - base[0] if comparable and timing_comparable and base[0] is not None and new[0] is not None else None
                change = "N/A" if delta is None or base[0] <= 0 else f"{(base[0] - new[0]) / base[0]:.1%}"
                display = [f"{_number(avg)}（{_number(mid)}）" if avg is not None else "N/A"
                           for avg, mid in (base, new)]
                lines.append(f"| {label} | {display[0]} | {display[1]} | {_number(delta)} | {change} |")
        if after:
            lines.append("\n" + _conclusion(*aggregates, (conditions[0] or {}).get("quality_gate"), comparable))
        for name, group in zip(("Baseline", "After"), groups):
            _render_tree(lines, name, group)
    return "\n".join(lines) + "\n"


def main():
    """运行 summary / compare 并写 Markdown。"""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    summary = commands.add_parser("summary")
    summary.add_argument("--input", type=Path, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    for command in (summary, compare):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = render_report(args.input) if args.command == "summary" else render_report(args.baseline, args.after)
    args.output.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
