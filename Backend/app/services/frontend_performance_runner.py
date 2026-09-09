from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.frontend_project_launcher import (
    launch_frontend_project,
    stop_frontend_project,
)
from app.services.workspace_process_registry import workspace_process_registry
from app.utils.subprocess_output import subprocess_output_text
from app.workspace.spec_documents import workflow_artifact_root, workspace_root


PERFORMANCE_CHECK_ID = "frontend_performance"
PERFORMANCE_CHECK_NAME = "前端性能测试"
PERFORMANCE_COMMAND_TIMEOUT_SECONDS = 600
PERFORMANCE_OUTPUT_SUMMARY_LIMIT = 4_000
_FRONTEND_PACKAGE_CANDIDATES = (
    "frontend/package.json",
    "Frontend/package.json",
    "app/frontend/package.json",
    "package.json",
)


def frontend_performance_available(state: dict[str, Any]) -> bool:
    """判断用户工程是否具备可启动并执行性能测试的前端。"""

    package_json_path = _find_frontend_package_json(state)
    if package_json_path is None:
        return False
    try:
        package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(package_json, dict):
        return False
    scripts = package_json.get("scripts")
    if not isinstance(scripts, dict):
        return False
    return "dev" in scripts or "start" in scripts


def frontend_build_passed(state: dict[str, Any]) -> bool:
    """读取本轮集成测试结果中的前端构建检查是否通过。"""

    for result in state.get("test_results", []):
        if not isinstance(result, dict):
            continue
        if result.get("id") == "frontend_build":
            return bool(result.get("passed"))
    return False


def _lighthouserc_config(preview_url: str) -> dict[str, Any]:
    """生成 LHCI 0.7.2 配置；跳过整页截图规避新版 Chrome 的协议超时。"""

    return {
        "ci": {
            "collect": {
                "url": [preview_url],
                "numberOfRuns": 1,
                "settings": {
                    # 保持 Lighthouse 7.3 可用的移动端模拟采集链路，
                    # 但把网络/CPU 限速调到接近本地无限制，避免
                    # dev server 未打包依赖被 1.6Mbps 模型放大。
                    "throttlingMethod": "simulate",
                    "throttling": {
                        "rttMs": 0,
                        "throughputKbps": 100_000,
                        "cpuSlowdownMultiplier": 1,
                    },
                    # 整页截图仅用于 HTML 报告的视觉展示，不参与评分和指标；
                    # Lighthouse 7.3 在较新 HeadlessChrome 上调用
                    # Page.captureScreenshot 会挂起并超时，直接跳过。
                    "skipAudits": ["full-page-screenshot"],
                },
            },
            "upload": {
                "target": "filesystem",
                "outputDir": ".",
                "reportFilenamePattern": "frontend-performance.%%EXTENSION%%",
            },
        }
    }


def run_frontend_performance_check(
    state: dict[str, Any],
    *,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """启动用户 frontend 工程并对真实 preview URL 执行 Lighthouse 性能测试。"""

    from app.services.integration_test_runner import report_check_progress

    root = workspace_root(state).resolve()
    if not frontend_performance_available(state) or not frontend_build_passed(state):
        result = _skipped_result(
            "未找到可启动的 frontend/package.json、缺少 dev/start 脚本，"
            "或前端构建检查未通过，跳过前端性能测试。"
        )
        report_check_progress(on_progress, status="skipped", check=result)
        return {"test_results": [result], "test_events": [PERFORMANCE_CHECK_ID]}

    npx_command = shutil.which("npx")
    if not npx_command:
        result = _failed_result(
            evidence="未找到 npx 命令，无法执行 @lhci/cli 前端性能测试。",
            argv=[],
            cwd=".",
            returncode=None,
            timed_out=False,
            error="npx not found",
            stdout="",
            stderr="",
            root=root,
        )
        report_check_progress(on_progress, status="failed", check=result)
        return {"test_results": [result], "test_events": [PERFORMANCE_CHECK_ID]}

    runtime_dir = (
        workflow_artifact_root(state).resolve()
        / "runtime"
        / "tests"
        / PERFORMANCE_CHECK_ID
    )
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _reset_performance_artifacts(runtime_dir)
    report_check_progress(
        on_progress,
        status="running",
        check={
            "id": PERFORMANCE_CHECK_ID,
            "name": PERFORMANCE_CHECK_NAME,
            "required": False,
            "blocking": False,
            "advisory": True,
            "skipped": False,
            "evidence": "正在启动前端工程并执行 Lighthouse 性能测试。",
        },
    )

    launch = launch_frontend_project(root, skip_install=True)
    preview_url = str(launch.get("preview_url") or "").strip()
    server_reused = bool(launch.get("server", {}).get("reused"))
    server_started = launch.get("status") == "running" and preview_url
    started_at = datetime.now(UTC).isoformat()
    stdout = ""
    stderr = ""
    returncode: int | None = None
    timed_out = False
    error: str | None = None
    try:
        if not server_started:
            error = str(launch.get("message") or "前端工程启动失败。")
            result = _failed_result(
                evidence=error,
                argv=[],
                cwd=".",
                returncode=None,
                timed_out=False,
                error=error,
                stdout="",
                stderr="",
                root=root,
                started_at=started_at,
            )
            report_check_progress(on_progress, status="failed", check=result)
            return {"test_results": [result], "test_events": [PERFORMANCE_CHECK_ID]}

        config_path = runtime_dir / "lighthouserc.json"
        config_path.write_text(
            json.dumps(
                _lighthouserc_config(preview_url),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        argv = [
            npx_command,
            "--yes",
            "--package",
            "@lhci/cli@0.7.2",
            "lhci",
            "autorun",
            "--config",
            str(config_path),
        ]
        env = {
            **os.environ,
            "NO_UPDATE_NOTIFIER": "1",
            "CI": "1",
            "LHCI_BUILD_CONTEXT__CURRENT_BRANCH": "local",
        }
        try:
            completed = workspace_process_registry.run(
                argv,
                workspace=root,
                cwd=str(runtime_dir),
                text=True,
                capture_output=True,
                timeout=PERFORMANCE_COMMAND_TIMEOUT_SECONDS,
                check=False,
                env=env,
            )
            stdout = subprocess_output_text(completed.stdout)
            stderr = subprocess_output_text(completed.stderr)
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = subprocess_output_text(exc.stdout)
            stderr = subprocess_output_text(exc.stderr)
            timed_out = True
        except OSError as exc:
            error = str(exc)

        command_log_root = runtime_dir
        stdout_log = command_log_root / "frontend.stdout.log"
        stderr_log = command_log_root / "frontend.stderr.log"
        stdout_log.write_text(stdout, encoding="utf-8")
        stderr_log.write_text(stderr, encoding="utf-8")

        parsed = _parse_lighthouse_report(runtime_dir)
        report_path = _find_html_report(runtime_dir)
        passed = (
            returncode == 0
            and not timed_out
            and error is None
            and parsed is not None
        )
        if parsed is not None:
            evidence = _evidence_summary(parsed, report_path)
        elif passed:
            passed = False
            evidence = "LHCI 命令执行成功，但未找到可解析的 Lighthouse 报告。"
        else:
            evidence = "前端性能测试执行失败。"
            if timed_out:
                evidence += f" 超过 {PERFORMANCE_COMMAND_TIMEOUT_SECONDS}s 超时。"
            if returncode not in (0, None):
                evidence += f" 退出码：{returncode}。"
            if error:
                evidence += f" 错误：{error}"
            if stderr.strip():
                evidence += f" stderr 末尾:\n{stderr[-PERFORMANCE_OUTPUT_SUMMARY_LIMIT:]}"
            if stdout.strip():
                evidence += f" stdout 末尾:\n{stdout[-PERFORMANCE_OUTPUT_SUMMARY_LIMIT:]}"

        result = {
            "id": PERFORMANCE_CHECK_ID,
            "name": PERFORMANCE_CHECK_NAME,
            "layer": "frontend",
            "language": "typescript",
            "passed": passed,
            "skipped": False,
            "required": False,
            "blocking": False,
            "advisory": True,
            "command": " ".join(argv),
            "evidence": evidence[: PERFORMANCE_OUTPUT_SUMMARY_LIMIT * 2],
            "failure_category": None if passed else "performance_test_failure",
            "execution": {
                "tool": "npx",
                "argv": argv,
                "cwd": _relative(runtime_dir, root),
                "returncode": returncode,
                "timed_out": timed_out,
                "error": error,
                "started_at": started_at,
                "finished_at": datetime.now(UTC).isoformat(),
                "stdout_log": str(stdout_log),
                "stderr_log": str(stderr_log),
                "stdout_log_virtual": f"/{_relative(stdout_log, root)}",
                "stderr_log_virtual": f"/{_relative(stderr_log, root)}",
                "stdout_tail": stdout[-PERFORMANCE_OUTPUT_SUMMARY_LIMIT:],
                "stderr_tail": stderr[-PERFORMANCE_OUTPUT_SUMMARY_LIMIT:],
            },
        }
        if parsed is not None:
            result["performance_scores"] = parsed["scores"]
            result["performance_metrics"] = parsed["metrics"]
        if report_path:
            result["report_path"] = str(report_path)
        report_check_progress(
            on_progress,
            status="passed" if passed else "failed",
            check=result,
        )
        return {"test_results": [result], "test_events": [PERFORMANCE_CHECK_ID]}
    finally:
        if server_started and not server_reused:
            stop_frontend_project(root)


def _skipped_result(evidence: str) -> dict[str, Any]:
    """构造用户或前置条件导致的咨询性跳过结果。"""

    return {
        "id": PERFORMANCE_CHECK_ID,
        "name": PERFORMANCE_CHECK_NAME,
        "layer": "frontend",
        "language": "typescript",
        "passed": True,
        "skipped": True,
        "required": False,
        "blocking": False,
        "advisory": True,
        "command": None,
        "evidence": evidence,
        "failure_category": None,
        "execution": {
            "tool": "none",
            "argv": [],
            "cwd": ".",
            "returncode": None,
            "timed_out": False,
            "stdout_log": None,
            "stderr_log": None,
        },
    }


def _reset_performance_artifacts(runtime_dir: Path) -> None:
    """清理上一次 LHCI 产物，避免失败运行时把旧报告当作本轮结果。"""

    for name in (
        "frontend-performance.json",
        "frontend-performance.html",
        "manifest.json",
    ):
        try:
            (runtime_dir / name).unlink(missing_ok=True)
        except OSError:
            pass
    lighthouse_dir = runtime_dir / ".lighthouseci"
    if lighthouse_dir.is_dir():
        shutil.rmtree(lighthouse_dir, ignore_errors=True)


def _failed_result(
    *,
    evidence: str,
    argv: list[str],
    cwd: str,
    returncode: int | None,
    timed_out: bool,
    error: str | None,
    stdout: str,
    stderr: str,
    root: Path,
    started_at: str | None = None,
) -> dict[str, Any]:
    """构造性能测试未能执行或执行失败的咨询性结果。"""

    return {
        "id": PERFORMANCE_CHECK_ID,
        "name": PERFORMANCE_CHECK_NAME,
        "layer": "frontend",
        "language": "typescript",
        "passed": False,
        "skipped": True,
        "required": False,
        "blocking": False,
        "advisory": True,
        "command": " ".join(argv) if argv else None,
        "evidence": evidence[: PERFORMANCE_OUTPUT_SUMMARY_LIMIT * 2],
        "failure_category": "performance_test_failure",
        "execution": {
            "tool": "npx" if argv else "none",
            "argv": argv,
            "cwd": cwd,
            "returncode": returncode,
            "timed_out": timed_out,
            "error": error,
            "started_at": started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "stdout_log": None,
            "stderr_log": None,
            "stdout_log_virtual": None,
            "stderr_log_virtual": None,
            "stdout_tail": stdout[-PERFORMANCE_OUTPUT_SUMMARY_LIMIT:],
            "stderr_tail": stderr[-PERFORMANCE_OUTPUT_SUMMARY_LIMIT:],
        },
    }


def _find_frontend_package_json(state: dict[str, Any]) -> Path | None:
    """按现有启动器顺序定位用户工程 frontend 的 package.json。"""

    root = workspace_root(state).resolve()
    for relative in _FRONTEND_PACKAGE_CANDIDATES:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return None


def _parse_lighthouse_report(runtime_dir: Path) -> dict[str, Any] | None:
    """从 LHCI filesystem 输出中解析分类得分和核心性能指标。"""

    json_candidates = list(runtime_dir.glob("frontend-performance.json"))
    json_candidates.extend(
        path
        for path in runtime_dir.glob("*.json")
        if path.name not in {"manifest.json", "lighthouserc.json"}
        and path not in json_candidates
    )
    if not json_candidates:
        return None
    json_candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    try:
        lhr = json.loads(json_candidates[0].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(lhr, dict):
        return None
    categories = lhr.get("categories") if isinstance(lhr.get("categories"), dict) else {}
    audits = lhr.get("audits") if isinstance(lhr.get("audits"), dict) else {}
    scores: dict[str, int] = {}
    for key, label in (
        ("performance", "performance"),
        ("accessibility", "accessibility"),
        ("best-practices", "best_practices"),
        ("seo", "seo"),
    ):
        category = categories.get(key)
        if isinstance(category, dict):
            raw_score = category.get("score")
            if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool):
                scores[label] = max(0, min(100, int(round(raw_score * 100))))
    metrics: dict[str, float] = {}
    for key, output_key in (
        ("first-contentful-paint", "fcp"),
        ("largest-contentful-paint", "lcp"),
        ("total-blocking-time", "tbt"),
        ("cumulative-layout-shift", "cls"),
        ("speed-index", "si"),
    ):
        audit = audits.get(key)
        if isinstance(audit, dict):
            value = audit.get("numericValue")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                metrics[output_key] = round(float(value), 3)
    if not scores and not metrics:
        return None
    return {"scores": scores, "metrics": metrics}


def _find_html_report(runtime_dir: Path) -> Path | None:
    """定位 LHCI 生成的 HTML 报告文件。"""

    html_candidates = list(runtime_dir.glob("frontend-performance.html"))
    html_candidates.extend(path for path in runtime_dir.glob("*.html") if path not in html_candidates)
    if not html_candidates:
        return None
    html_candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return html_candidates[0]


def _evidence_summary(
    parsed: dict[str, Any],
    report_path: Path | None,
) -> str:
    """把得分和核心指标压缩为检查 evidence 摘要。"""

    scores = parsed.get("scores") or {}
    metrics = parsed.get("metrics") or {}
    parts = []
    if scores:
        labels = {
            "performance": "性能",
            "accessibility": "可访问性",
            "best_practices": "最佳实践",
            "seo": "SEO",
        }
        parts.append(
            "、".join(
                f"{labels.get(key, key)} {value}"
                for key, value in scores.items()
            )
        )
    if metrics:
        metric_labels = {
            "fcp": "FCP",
            "lcp": "LCP",
            "tbt": "TBT",
            "cls": "CLS",
            "si": "SI",
        }
        metric_parts = []
        for key, value in metrics.items():
            label = metric_labels.get(key, key)
            if key == "cls":
                metric_parts.append(f"{label} {value}")
            else:
                metric_parts.append(f"{label} {value / 1000:.2f}s")
        parts.append("、".join(metric_parts))
    if report_path is not None:
        parts.append(f"报告: {report_path}")
    return "；".join(parts) if parts else "Lighthouse 报告已生成。"


def _relative(path: Path, root: Path) -> str:
    """返回稳定的 POSIX 风格相对路径，供事件和执行记录使用。"""

    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
