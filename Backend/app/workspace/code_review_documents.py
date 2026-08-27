"""代码审查 Markdown 报告的安全渲染与持久化。"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from app.workspace.spec_documents import workflow_artifact_root, workspace_root


CODE_REVIEW_REPORT_RELATIVE_PATH = ".xcodeagent/reports/code-review.md"


def _safe_text(value: Any, *, workspace: Path) -> str:
    """清理 Markdown 控制字符和宿主工作区绝对路径。"""

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
    return text.replace("|", "\\|")


def _safe_relative_path(value: Any) -> str:
    """只允许报告展示工作区相对路径，拒绝绝对路径和目录穿越。"""

    normalized = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or (len(normalized) >= 2 and normalized[1] == ":")
        or ".." in path.parts
    ):
        return "未提供"
    return path.as_posix()


def _status_label(value: Any) -> str:
    """把内部审查状态转换为报告中的中文状态。"""

    status = str(value or "completed")
    return {
        "completed": "已完成",
        "skipped": "已跳过",
        "failed": "失败",
    }.get(status, status)


def _severity_label(value: Any) -> str:
    """把内部严重级别转换为报告中的中文标签。"""

    severity = str(value or "unknown").lower()
    return {
        "critical": "严重",
        "high": "高风险",
        "medium": "中风险",
        "low": "低风险",
    }.get(severity, severity)


def render_code_review_markdown(
    state: dict[str, Any], review_result: dict[str, Any]
) -> str:
    """把归一化审查结果渲染为不含扫描文件清单和内部动作的 Markdown。"""

    workspace = workspace_root(state)
    targets = [
        item for item in review_result.get("targets", []) if isinstance(item, dict)
    ][:2]
    issues = [
        item for item in review_result.get("issues", []) if isinstance(item, dict)
    ][:100]
    issue_count = int(review_result.get("issue_count", len(issues)) or 0)
    total_files = sum(
        max(0, int(target.get("scanned_file_count", 0) or 0)) for target in targets
    )
    conclusion = (
        f"发现 {issue_count} 个需要处理的问题。"
        if issue_count
        else "审查通过，未发现需要处理的问题。"
    )
    lines = [
        "# 代码审查报告",
        "",
        "## 审查结论",
        "",
        f"- 状态：{_status_label(review_result.get('status'))}",
        f"- 结论：{conclusion}",
        f"- 问题总数：{issue_count}",
        "",
        "## 扫描汇总",
        "",
        "| 范围 | 扫描根目录 | 状态 | 文件总数 |",
        "| --- | --- | --- | ---: |",
    ]
    for target in targets:
        side = "前端" if target.get("side") == "frontend" else "后端"
        lines.append(
            "| "
            + " | ".join(
                [
                    side,
                    f"`{_safe_relative_path(target.get('root'))}`",
                    _status_label(target.get("status")),
                    str(max(0, int(target.get("scanned_file_count", 0) or 0))),
                ]
            )
            + " |"
        )
    lines.extend(["", f"**前后端扫描文件总数：{total_files}**", ""])

    warnings = [
        _safe_text(target.get("warning"), workspace=workspace)
        for target in targets
        if str(target.get("warning") or "").strip()
    ]
    lines.extend(["## 扫描提示", ""])
    lines.extend([f"- {warning}" for warning in warnings] or ["- 无"])

    lines.extend(["", "## 问题详情", ""])
    if not issues:
        lines.append("未发现需要处理的问题，代码审查通过。")
    else:
        for index, issue in enumerate(issues, start=1):
            line = issue.get("line")
            location = _safe_relative_path(issue.get("file"))
            if isinstance(line, int) and not isinstance(line, bool) and line > 0:
                location = f"{location}:{line}"
            lines.extend(
                [
                    f"### {index}. {_safe_text(issue.get('title') or '未命名问题', workspace=workspace)}",
                    "",
                    f"- 严重级别：{_severity_label(issue.get('severity'))}",
                    f"- 规则 ID：`{_safe_text(issue.get('rule_id') or issue.get('ruleId') or 'unknown', workspace=workspace)}`",
                    f"- 范围：{'前端' if issue.get('side') == 'frontend' else '后端'}",
                    f"- 文件位置：`{location}`",
                    f"- 说明：{_safe_text(issue.get('summary') or '未提供', workspace=workspace)}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def write_code_review_markdown(
    state: dict[str, Any], review_result: dict[str, Any]
) -> str:
    """原子覆盖最新代码审查报告，并返回内部绝对路径。"""

    path = workflow_artifact_root(state) / "reports" / "code-review.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(
        render_code_review_markdown(state, review_result), encoding="utf-8"
    )
    temporary.replace(path)
    return str(path)
