"""二次修改专用的目标导向代码证据扫描。

扫描器只返回文件、行号和局部代码片段，不判断 Bug 类型，也不修改工作区。
它在契约分析确认 preserves 之后才会被调用。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from app.domain.change_impact import CodeFinding, CodeScanEvidence


_SOURCE_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rs", ".swift",
    ".kt", ".kts", ".vue", ".css", ".less", ".scss", ".html",
}
_IGNORED_PARTS = {
    ".git", ".xcodeagent", ".venv", "node_modules", "dist", "build", "target",
    "coverage", ".next", ".turbo", "__pycache__",
}
_CJK_RE = re.compile(r"[\u3400-\u9fff]{2,}")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{1,80}")


def scan_targeted_code(
    *,
    workspace: str | Path,
    request: str,
    candidate_paths: Iterable[str] | None = None,
    target: dict[str, Any] | None = None,
    max_results: int = 20,
) -> CodeScanEvidence:
    """依据用户目标读取少量源码片段，生成可复核的 code.scan 证据。"""

    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        return CodeScanEvidence(performed=True, reason="workspace 不存在，无法取得代码证据。", findings=[])
    hints = _keywords(request, target=target)
    explicit_candidates = list(candidate_paths or [])
    # 没有目标、候选路径和足够关键词时不做仓库级猜测，直接让上层进入澄清。
    if not explicit_candidates and not _has_sufficient_scan_hints(hints) and not _has_target(target):
        return CodeScanEvidence(
            performed=True,
            reason="缺少明确目标或足够关键词，未执行宽范围源码检索。",
            findings=[],
        )
    paths = _candidate_files(
        root,
        candidate_paths=explicit_candidates,
        target=target,
        hints=hints,
    )
    findings: list[CodeFinding] = []
    for path in paths:
        if len(findings) >= max(1, min(int(max_results), 100)):
            break
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if len(text) > 512_000:
            text = text[:512_000]
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if not _line_matches(line, hints):
                continue
            start = max(0, index - 2)
            end = min(len(lines), index + 3)
            relative = path.relative_to(root).as_posix()
            findings.append(
                CodeFinding(
                    path=relative,
                    summary=f"在目标源码中发现与请求相关的实现位置（第 {index + 1} 行）。",
                    symbol=_nearby_symbol(lines, index),
                    relevantCode="\n".join(lines[start:end])[:8_000],
                    lineStart=start + 1,
                    lineEnd=end,
                )
            )
            break
    reason = (
        "已按用户目标完成限定源码检索，返回局部实现证据。"
        if findings
        else "已按用户目标完成限定源码检索，但没有找到可复核的实现位置。"
    )
    return CodeScanEvidence(performed=True, reason=reason, findings=findings)


def sanitize_code_scan_evidence(
    raw: CodeScanEvidence | dict[str, Any] | Any,
    *,
    workspace: str | Path,
    max_results: int = 20,
    require_exists: bool = True,
) -> CodeScanEvidence:
    """校验代码扫描器返回的相对路径，过滤越界、依赖和生成文件证据。"""

    root = Path(workspace).expanduser().resolve()
    if isinstance(raw, CodeScanEvidence):
        payload = raw.model_dump(mode="json", by_alias=True)
    elif isinstance(raw, dict):
        payload = raw
    else:
        payload = {}
    raw_findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    safe_findings: list[CodeFinding] = []
    try:
        limit = max(1, min(int(max_results), 100))
    except (TypeError, ValueError):
        limit = 20
    for item in raw_findings[:limit]:
        if not isinstance(item, dict):
            continue
        safe_path = _safe_relative_source_path(root, item.get("path"))
        if not safe_path:
            continue
        # code.scan 的 finding 必须能回到当前工作区的真实源码文件；否则它
        # 只是模型/扫描器声称的路径，不能作为写入前的实现证据。
        if require_exists and not (root / safe_path).is_file():
            continue
        try:
            safe_findings.append(
                CodeFinding(
                    path=safe_path,
                    summary=str(item.get("summary") or "发现与请求相关的源码位置。")[:2_000],
                    symbol=(str(item.get("symbol"))[:512] if item.get("symbol") else None),
                    relevantCode=(
                        str(item.get("relevantCode", item.get("relevant_code")))[:8_000]
                        if item.get("relevantCode", item.get("relevant_code")) is not None
                        else None
                    ),
                    lineStart=_positive_int(item.get("lineStart", item.get("line_start"))),
                    lineEnd=_positive_int(item.get("lineEnd", item.get("line_end"))),
                )
            )
        except (TypeError, ValueError):
            continue
    performed = bool(payload.get("performed"))
    # 保守处理不一致的扫描器返回：未执行就算带 findings，也不能把它
    # 暴露给 Router 或写 Agent。
    if not performed:
        safe_findings = []
    return CodeScanEvidence(
        performed=performed,
        reason=str(payload.get("reason") or "未提供代码扫描原因")[:2_000],
        findings=safe_findings,
    )


def _candidate_files(
    root: Path,
    *,
    candidate_paths: Iterable[str] | None,
    target: dict[str, Any] | None,
    hints: list[str] | None = None,
) -> list[Path]:
    """优先使用分类候选路径，否则只枚举常见源码根目录。"""

    result: list[Path] = []
    seen: set[Path] = set()
    supplied_candidates = list(candidate_paths or [])
    for raw in supplied_candidates:
        normalized = str(raw or "").replace("\\", "/").lstrip("/")
        if not normalized or any(
            part.casefold() in _IGNORED_PARTS for part in normalized.split("/")
        ):
            continue
        path = (root / normalized).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path.is_file() and path.suffix.lower() in _SOURCE_SUFFIXES and path not in seen:
            seen.add(path)
            result.append(path)
    target_type = str((target or {}).get("type") or "")
    roots = (
        ["Frontend/src", "frontend/src"]
        if target_type == "page"
        else ["Backend/app", "backend/app", "backend/src", "Backend/src"]
        if target_type == "endpoint"
        else ["Frontend/src", "frontend/src", "Backend/app", "backend/app", "backend/src", "Backend/src"]
    )
    if not result and not supplied_candidates:
        explicit_target = target_type in {"page", "endpoint"}
        for relative_root in roots:
            directory = root / relative_root
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if len(result) >= (120 if explicit_target else 80):
                    break
                if not path.is_file() or path.suffix.lower() not in _SOURCE_SUFFIXES:
                    continue
                if any(part.casefold() in _IGNORED_PARTS for part in path.relative_to(root).parts):
                    continue
                if not explicit_target and hints and not _path_matches_hints(path, root, hints):
                    continue
                result.append(path)
            if result and explicit_target:
                break
    return result[:200]


def _keywords(request: str, *, target: dict[str, Any] | None = None) -> list[str]:
    """提取中文业务词、英文标识和常见实现别名。"""

    terms: list[str] = []
    for run in _CJK_RE.findall(request):
        terms.append(run)
        terms.extend(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    terms.extend(_WORD_RE.findall(request))
    aliases = {
        "登录": ("login", "signin", "auth", "onClick"),
        "按钮": ("button", "onClick", "click"),
        "详情": ("detail", "Detail", "show"),
        "首页": ("home", "Home", "index"),
        "接口": ("api", "endpoint", "route"),
    }
    for source, values in aliases.items():
        if source in request:
            terms.extend(values)
    if isinstance(target, dict):
        for key in ("pageId", "page_id", "endpointId", "endpoint_id", "apiContractId", "api_contract_id"):
            value = str(target.get(key) or "").strip()
            if value:
                terms.extend(_WORD_RE.findall(value))
                terms.append(value)
    return list(dict.fromkeys(term.casefold() for term in terms if term))[:80]


def _line_matches(line: str, hints: list[str]) -> bool:
    """判断单行是否包含目标关键词。"""

    folded = line.casefold()
    return any(term in folded for term in hints if len(term) >= 2)


def _nearby_symbol(lines: list[str], index: int) -> str | None:
    """从当前位置向上寻找常见函数或组件声明作为辅助定位。"""

    pattern = re.compile(r"(?:function|def|class)\s+([A-Za-z_$][\w$]*)|const\s+([A-Za-z_$][\w$]*)\s*=\s*\(")
    for line in reversed(lines[max(0, index - 20) : index + 1]):
        match = pattern.search(line)
        if match:
            return next((value for value in match.groups() if value), None)
    return None


def _has_target(target: dict[str, Any] | None) -> bool:
    """判断请求是否携带已校验的页面或接口目标。"""

    return isinstance(target, dict) and str(target.get("type") or "") in {"page", "endpoint"}


def _has_sufficient_scan_hints(hints: list[str]) -> bool:
    """判断关键词是否足够支持一次有界的目标导向检索。"""

    meaningful = [item for item in hints if len(item) >= 3 or any("\u4e00" <= char <= "\u9fff" for char in item)]
    return len(meaningful) >= 2


def _path_matches_hints(path: Path, root: Path, hints: list[str]) -> bool:
    """在无显式 target 时只保留文件名或目录名命中关键词的源码文件。"""

    relative = path.relative_to(root).as_posix().casefold()
    return any(len(term) >= 2 and term.casefold() in relative for term in hints)


def _safe_relative_source_path(root: Path, raw: Any) -> str:
    """把扫描证据路径约束为工作区内的源码相对路径。"""

    value = str(raw or "").strip().replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        return ""
    parts = [part for part in value.split("/") if part]
    if ".." in parts or any(part.casefold() in _IGNORED_PARTS for part in parts):
        return ""
    candidate = (root / "/".join(parts)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return ""
    if candidate.suffix.lower() not in _SOURCE_SUFFIXES:
        return ""
    return candidate.relative_to(root).as_posix()[:1_000]


def _positive_int(value: Any) -> int | None:
    """把可选行号归一化为正整数。"""

    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None
