"""把 Lighthouse 的真实检测结果解释为面向用户的 Markdown。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from app.services.lighthouse_report import finite_number


CATEGORY_LABELS = {
    "performance": ("页面性能", "页面加载、内容呈现及加载期间的阻塞情况。"),
    "accessibility": ("无障碍访问", "是否便于键盘、读屏等辅助方式使用。"),
    "best_practices": ("最佳实践", "是否符合 Lighthouse 检查的常见 Web 开发实践。"),
    "seo": ("搜索引擎优化", "是否具备基础的搜索引擎理解与收录条件。"),
}
METRIC_LABELS = {
    "fcp": ("首次内容显示（FCP）", "用户多久能看到第一段文字或图像，越短越好。"),
    "lcp": ("最大内容呈现（LCP）", "可视区域中最大的图片或文本块多久呈现，越短越好。"),
    "tbt": ("总阻塞时间（TBT）", "加载期间长任务超过 50 毫秒部分的累计阻塞时间，越少越好；不是单次点击响应时间。"),
    "cls": ("布局偏移（CLS）", "内容在显示过程中意外移动的程度，越小越稳定；没有时间单位。"),
    "si": ("视觉加载速度（SI）", "可视区域内容逐步显示的整体速度，越短越好；不是全部资源加载完成时间。"),
}
CATEGORY_IMPACTS = {
    "performance": "可能增加用户等待时间或加载期间的阻塞。",
    "accessibility": "可能影响依赖键盘、读屏等辅助方式的用户。",
    "best_practices": "可能影响页面的可靠性及常见 Web 实践符合情况。",
    "seo": "可能影响搜索引擎理解或收录页面。",
}
AUDIT_ADVICE = {
    "crawlable-anchors": "为需要被抓取的链接设置有效的 href 目标，避免仅依赖点击事件完成页面跳转。",
    "meta-description": "为页面补充简洁、准确的 meta description，概括该页面的主要内容。",
    "robots-txt": "检查 robots.txt 实际返回的内容和规则语法，确保抓取工具能正确解析；修复后重新检测。",
    "color-contrast": "调整文字与背景的颜色组合，提高对比度，并核对浅色和深色主题。",
    "button-name": "为缺少名称的按钮补充可识别的文字或无障碍名称，让读屏用户能理解操作。",
}


def _text(value: Any, workspace: Path, limit: int = 600) -> str:
    """清理外部文案的链接、HTML 和宿主路径，并限制为单行，保留行内代码可读性。"""

    text = str(value or "").replace(str(workspace), "[工作区]")
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"</?([A-Za-z][\w:-]*)[^>]*>", r"\1", text)
    text = re.sub(r"(?<![\w:/])/(?:Users|home|private|var|tmp|opt|usr)/[^\s|`]+", "[宿主路径]", text)
    text = re.sub(r"\b[A-Za-z]:[\\/][^\s|`]+", "[宿主路径]", text)
    text = re.sub(r"\s+", " ", text).strip()[:limit]
    return text.replace("|", "／")


def _mapping(value: Any) -> dict[str, Any]:
    """把可选结果字段收敛为字典，缺失字段不伪造默认数值。"""

    return value if isinstance(value, dict) else {}


def _rating(score: float | None) -> str:
    """按 Lighthouse 的原始 0–1 分值评级，不使用四舍五入后的展示分数。"""

    if score is None:
        return "未提供评级"
    return "良好" if score >= 0.9 else "需改进" if score >= 0.5 else "较差"


def _metric_value(key: str, value: float | None) -> str:
    """按指标语义统一单位，布局偏移无单位，总阻塞时间保留毫秒。"""

    if value is None:
        return "未获得数据"
    if key == "cls":
        return f"{value:.3f}"
    if key == "tbt":
        return f"{value:g} 毫秒"
    return f"{value / 1000:.2f} 秒"


def _conclusion(scores: dict[str, Any], passed: bool) -> str:
    """分别表达检测是否成功和质量表现，不把命令成功解释为全部达标。"""

    if not passed:
        return "本次检测未完整完成，已有数据仅供排查，暂不能给出完整质量结论。"
    performance = finite_number(scores.get("performance"), maximum=100)
    result = (
        f"页面性能表现{_rating(performance / 100)}（{performance:g}/100）。"
        if performance is not None
        else "检测已完成，但未获得页面性能评分，暂无法判断整体性能。"
    )
    attention = [
        label for key, (label, _) in CATEGORY_LABELS.items()
        if key != "performance"
        and (score := finite_number(scores.get(key), maximum=100)) is not None
        and score < 90
    ]
    return result + (f"建议优先关注：{'、'.join(attention)}。" if attention else "")


def _report_link(value: Any, workspace: Path) -> str | None:
    """只为当前工作区性能测试目录内的 HTML 文件生成可打开的相对链接。"""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        path = Path(value)
        path = (path if path.is_absolute() else workspace / path).resolve()
        base = workspace / ".devagentstudio/runtime/tests/frontend_performance"
        if not path.is_relative_to(base.resolve()) or path.suffix.lower() != ".html":
            return None
        relative = path.relative_to(workspace)
    except (OSError, ValueError):
        return None
    return "./" + quote(relative.as_posix(), safe="/")


def _recommendations(details: dict[str, Any], workspace: Path) -> list[str]:
    """用真实失败检查说明问题、影响和建议，缺少证据时只提示查看原报告。"""

    lines = ["### 优先改进项", ""]
    opportunities = details.get("opportunities")
    opportunities = opportunities if isinstance(opportunities, list) else []
    for item in opportunities[:3]:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"), workspace, 200)
        if not title:
            continue
        category = str(item.get("category") or "")
        label = CATEGORY_LABELS.get(category, ("检查项", ""))[0]
        impact = CATEGORY_IMPACTS.get(category, "请结合完整报告确认受影响的页面体验。")
        advice = AUDIT_ADVICE.get(str(item.get("id") or "")) or _text(item.get("description"), workspace)
        advice = advice or "请在完整 Lighthouse 报告中查看该检查项的修复说明。"
        lines.extend([
            f"**{label}：{title}**", "",
            f"- 用户影响：{impact}", f"- 处理建议：{advice}", "",
        ])
    if len(lines) == 2:
        lines.extend(["未获得可展示的具体改进项；可在完整 Lighthouse 报告中核对检查详情。", ""])
    return lines


def _context(details: dict[str, Any], workspace: Path) -> list[str]:
    """展示真实检测环境与时间，URL 隐去查询参数和用户凭据。"""

    context = _mapping(details.get("context"))
    lines = ["### 检测说明", "", "本次为本地开发环境单次采样，网络与 CPU 限速接近本地条件，结果反映本次检测条件下的表现。", ""]
    page_url = "未获得数据"
    try:
        url = urlsplit(str(context.get("url") or ""))
        if url.scheme in {"http", "https"} and url.hostname:
            host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
            host += f":{url.port}" if url.port else ""
            page_url = _text(urlunsplit((url.scheme, host, url.path, '', '')), workspace)
    except ValueError:
        page_url = "未获得数据"
    lines.append(f"- 检测页面：{page_url}")
    device = {"mobile": "移动端模拟", "desktop": "桌面端"}.get(str(context.get("device") or ""), "未获得数据")
    lines.append(f"- 设备模式：{device}")
    try:
        timestamp = datetime.fromisoformat(str(context.get("fetch_time") or "").replace("Z", "+00:00"))
        lines.append(f"- 检测时间：{timestamp.isoformat(timespec='seconds')}")
    except ValueError:
        lines.append("- 检测时间：未获得数据")
    version = _text(context.get("lighthouse_version"), workspace, 30)
    if version:
        lines.append(f"- Lighthouse：{version}")
    return [*lines, ""]


def render_performance_report(check: dict[str, Any] | None, workspace: Path) -> list[str]:
    """按结论、分类、指标、改进项和环境顺序生成可读的性能测试章节。"""

    lines = ["", "## 2. 前端性能测试", ""]
    if check is None:
        return [*lines, "检测状态：未获得结果。前端性能测试已选择执行，但未获得 Lighthouse 检查结果。"]
    if check.get("skipped") is True and check.get("passed") is True:
        return [*lines, "检测状态：已跳过，本轮未执行 Lighthouse 检测。"]
    scores = _mapping(check.get("performance_scores"))
    metrics = _mapping(check.get("performance_metrics"))
    details = _mapping(check.get("performance_details"))
    metric_scores = _mapping(details.get("metric_scores"))
    passed = check.get("passed") is True
    lines.extend([
        f"> **整体结论：{_conclusion(scores, passed)}**", "",
        f"检测状态：{'已完成' if passed else '执行失败或未完整完成'}。", "",
    ])
    if not passed:
        lines.extend([f"失败原因：{_text(check.get('evidence'), workspace, 800) or '未提供错误详情。'}", ""])
    observations = []
    for key, label in (("fcp", "开始显示内容"), ("lcp", "呈现最大内容")):
        value = finite_number(metrics.get(key))
        if value is not None:
            observations.append(f"约 {_metric_value(key, value)}{label}")
    if finite_number(metrics.get("cls")) == 0:
        observations.append("本次未观察到布局偏移")
    if observations:
        lines.extend(["本次观测：" + "，".join(observations) + "。", ""])
    lines.extend(["### 分类评分", "", "| 检测维度 | 得分与评价 | 含义 |", "| --- | --- | --- |"])
    for key, (label, meaning) in CATEGORY_LABELS.items():
        score = finite_number(scores.get(key), maximum=100)
        result = f"{score:g}/100 · {_rating(score / 100)}" if score is not None else "未获得数据"
        lines.append(f"| {label} | {result} | {meaning} |")
    lines.extend(["", "评分说明：90–100 分为良好，50–89 分为需改进，0–49 分为较差。", "",
                  "### 页面性能指标", "", "| 指标 | 本次结果与评价 | 含义 |", "| --- | --- | --- |"])
    for key, (label, meaning) in METRIC_LABELS.items():
        value = finite_number(metrics.get(key))
        rating = _rating(finite_number(metric_scores.get(key), maximum=1)) if value is not None else ""
        result = _metric_value(key, value) + (f" · {rating}" if rating else "")
        lines.append(f"| {label} | {result} | {meaning} |")
    lines.extend(["", "指标评价采用本次 Lighthouse 原始评级；缺少数值或评级时明确标注，不自行套用其他设备的阈值。", ""])
    lines.extend(_recommendations(details, workspace))
    link = _report_link(check.get("report_path"), workspace)
    lines.extend([f"[查看完整 Lighthouse 报告]({link})" if link else "完整 Lighthouse 报告暂不可用。", ""])
    lines.extend(_context(details, workspace))
    return lines
