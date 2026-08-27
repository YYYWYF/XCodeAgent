from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.workspace.spec_documents import workspace_root


TEST_REPORT_RELATIVE_PATH = ".xcodeagent/reports/test-report.md"
TEST_REPORT_JSON_RELATIVE_PATH = ".xcodeagent/reports/test-report.json"


def test_report_json_path(state: dict[str, Any]) -> Path:
    """返回内部 JSON 测试报告路径。"""

    existing_path = state.get("test_report_json_path")
    if existing_path:
        path = Path(existing_path)
        return path if path.is_absolute() else workspace_root(state) / path
    return workspace_root(state) / TEST_REPORT_JSON_RELATIVE_PATH


def test_report_markdown_path(state: dict[str, Any]) -> Path:
    """返回用户可读 Markdown 测试报告路径。"""

    existing_path = state.get("test_report_path")
    if existing_path:
        path = Path(existing_path)
        return path if path.is_absolute() else workspace_root(state) / path
    return workspace_root(state) / TEST_REPORT_RELATIVE_PATH


def write_test_report_json(state: dict[str, Any], test_report: dict[str, Any]) -> str:
    """原子覆盖内部 JSON 测试报告。"""

    path = test_report_json_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(test_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return str(path)


def _safe_report_text(value: Any, *, workspace: Path, limit: int = 1_000) -> str:
    """清理报告文案中的换行、Markdown 表格字符和宿主机绝对路径。"""

    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    workspace_text = str(workspace.resolve())
    workspace_variants = {workspace_text}
    if workspace_text.startswith("/private/"):
        workspace_variants.add(workspace_text.removeprefix("/private"))
    for workspace_variant in workspace_variants:
        if workspace_variant:
            text = text.replace(workspace_variant, ".")
    text = re.sub(
        r"(?<![\w:/])/(?:Users|home|private|var|tmp|opt|usr)/[^\s|`]+",
        "[宿主路径]",
        text,
    )
    text = re.sub(r"\b[A-Za-z]:[\\/][^\s|`]+", "[宿主路径]", text)
    return text[:limit].replace("|", "\\|")


def _check_status_label(check: dict[str, Any] | None) -> str:
    """把检查结果转换为测试报告使用的中文状态。"""

    if check is None:
        return "未执行/不适用"
    if check.get("skipped") is True and check.get("passed") is True:
        return "已跳过"
    return "通过" if check.get("passed") is True else "失败"


def _check_by_id(test_report: dict[str, Any], check_id: str) -> dict[str, Any] | None:
    """按稳定检查 ID 读取一项结构化测试结果。"""

    return next(
        (
            item
            for item in test_report.get("checks", [])
            if isinstance(item, dict) and item.get("id") == check_id
        ),
        None,
    )


def _score_rows(check: dict[str, Any]) -> list[tuple[str, str]]:
    """提取 Lighthouse 四项分类得分。"""

    scores = check.get("performance_scores")
    scores = scores if isinstance(scores, dict) else {}
    return [
        (label, str(scores[key]))
        for key, label in (
            ("performance", "Performance"),
            ("accessibility", "Accessibility"),
            ("best_practices", "Best Practices"),
            ("seo", "SEO"),
        )
        if isinstance(scores.get(key), (int, float))
        and not isinstance(scores.get(key), bool)
    ]


def _metric_rows(check: dict[str, Any]) -> list[tuple[str, str]]:
    """提取并格式化 Lighthouse 五项核心指标。"""

    metrics = check.get("performance_metrics")
    metrics = metrics if isinstance(metrics, dict) else {}
    rows: list[tuple[str, str]] = []
    for key, label in (
        ("fcp", "FCP"),
        ("lcp", "LCP"),
        ("tbt", "TBT"),
        ("cls", "CLS"),
        ("si", "SI"),
    ):
        value = metrics.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        rows.append(
            (label, f"{float(value):.3f}" if key == "cls" else f"{float(value) / 1000:.2f}s")
        )
    return rows


def render_test_report_markdown(
    state: dict[str, Any], test_report: dict[str, Any]
) -> str:
    """把最新集成质量门结果渲染为精简、安全的 Markdown 测试报告。"""

    workspace = workspace_root(state)
    lines = [
        "# 测试报告",
        "",
        "## 1. 前后端构建检查",
        "",
        "| 检查 | 状态 | 结果摘要 |",
        "| --- | --- | --- |",
    ]
    for check_id, name in (
        ("frontend_build", "前端构建检查"),
        ("backend_build", "后端构建检查"),
    ):
        check = _check_by_id(test_report, check_id)
        evidence = (
            _safe_report_text(check.get("evidence"), workspace=workspace)
            if check is not None
            else "当前项目或本轮检查未产生该构建结果。"
        )
        lines.append(f"| {name} | {_check_status_label(check)} | {evidence or '无'} |")

    if str(state.get("frontend_performance_decision") or "") == "run":
        check = _check_by_id(test_report, "frontend_performance")
        lines.extend(["", "## 2. 前端性能测试", ""])
        if check is None:
            lines.append("前端性能测试已选择执行，但未获得 Lighthouse 检查结果。")
        else:
            evidence = _safe_report_text(check.get("evidence"), workspace=workspace)
            lines.extend(
                [
                    f"- 状态：{_check_status_label(check)}",
                    f"- 结果摘要：{evidence or '未提供'}",
                    "",
                ]
            )
            score_rows = _score_rows(check)
            metric_rows = _metric_rows(check)
            if score_rows:
                lines.extend(
                    [
                        "### Lighthouse 分类得分",
                        "",
                        "| 分类 | 得分 |",
                        "| --- | ---: |",
                        *(f"| {label} | {value} |" for label, value in score_rows),
                        "",
                    ]
                )
            if metric_rows:
                lines.extend(
                    [
                        "### 核心性能指标",
                        "",
                        "| 指标 | 数值 |",
                        "| --- | ---: |",
                        *(f"| {label} | {value} |" for label, value in metric_rows),
                        "",
                    ]
                )
            if not score_rows and not metric_rows:
                lines.append("未获得可展示的 Lighthouse 分类得分或核心指标。")
    return "\n".join(lines).rstrip() + "\n"


def write_test_report_markdown(
    state: dict[str, Any], test_report: dict[str, Any]
) -> str:
    """原子覆盖用户可读 Markdown 测试报告。"""

    path = test_report_markdown_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(
        render_test_report_markdown(state, test_report), encoding="utf-8"
    )
    temporary.replace(path)
    return str(path)


def load_test_report_json(path: str | Path) -> dict[str, Any]:
    """读取内部 JSON 测试报告。"""

    return json.loads(Path(path).read_text(encoding="utf-8"))
