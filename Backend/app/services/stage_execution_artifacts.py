"""从磁盘读取已提交的 Build 终态与当前源码绑定的单测报告。"""

import hashlib
import json
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.workspace.task_documents import build_task_plan_sha256, build_run_task_plan_json_path


def _read_result_object(path: Path) -> dict[str, Any] | None:
    """旧执行结果是可重算缓存，损坏、缺失或不可读均不冒充有效通过事实。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _result_candidates(directory: Path) -> list[Path]:
    """容忍并发清理导致的文件消失，按当前仍可读取的结果更新时间排序。"""
    candidates = []
    try:
        for path in directory.glob("execution-*.json"):
            try:
                candidates.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue
    except OSError:
        return []
    return [path for _, path in sorted(candidates, reverse=True)]


def _file_digest(root: Path, relative: str) -> str | None:
    """仅摘要工作区内实际源码和测试文件，拒绝链接、穿越及缺失文件。"""
    from app.services.development_review_files import current_review_file
    if not current_review_file(root, relative):
        return None
    try:
        return hashlib.sha256((root / relative).read_bytes()).hexdigest()
    except OSError:
        return None


def bind_unit_test_report(state: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """把测试通过事实绑定到当前计划、范围和磁盘源码/测试，不借用旧 State 放行门禁。"""
    root = Path(str(state.get("workspace") or state.get("workspace_path") or ".")).resolve()
    context = state.get("unit_test_generation_context") or {}
    generation = state.get("unit_test_generation") or {}
    paths = set(context.get("source_files") or state.get("development_review_files") or [])
    paths.update(generation.get("test_files") or [])
    paths.update(path for path in (
        "frontend/tests/setupTests.ts", "frontend/tests/__mocks__/styleMock.cjs",
        "frontend/tests/__mocks__/fileMock.cjs", "frontend/jest.config.cjs",
        "frontend/tsconfig.test.json", "frontend/package.json", "frontend/pnpm-lock.yaml",
    ) if (root / path).is_file())
    return {**report, "artifact_context": {
        "build_execution_scope": state.get("build_execution_scope"),
        "plan_sha256": build_task_plan_sha256(state.get("build_task_plan") or {}),
        "files": {path: _file_digest(root, path) for path in sorted(paths)},
    }}


def load_stage_execution_artifacts(state: dict[str, Any], plan: dict[str, Any], node: str) -> dict[str, Any]:
    """同线程及范围只读取最新 Build 提交，摘要漂移或非终态不借用更旧成功结果。"""
    if node not in {"unit_test", "unit_test_repair", "test_phase_confirmation"}:
        return {}
    root = Path(str(state.get("workspace") or state.get("workspace_path") or ".")).resolve()
    artifact = root / WORKSPACE_ARTIFACT_DIR
    scope = state.get("build_execution_scope")
    digest = build_task_plan_sha256(plan)
    # 明确覆盖 checkpoint 中的旧通过状态；缓存缺失不能让旧 State 绕过重新执行。
    fields: dict[str, Any] = {"build_summary": {}, "build_results": [], "tasks": [],
        "build_run_id": "", "build_run_plan_path": "", "build_run_plan_sha256": "",
        "unit_test_gate_passed": False, "unit_test_quality_gate_passed": False,
        "unit_test_report": {}, "unit_test_report_path": ""}
    if node == "unit_test_repair":
        # 修复实际消费本轮失败报告，不能在读取 Build 事实时清掉返修证据。
        fields.update(unit_test_report=state.get("unit_test_report") or {},
                      unit_test_report_path=state.get("unit_test_report_path") or "")
    if not plan:
        return fields
    candidates = _result_candidates(artifact / "plans/build-runs")
    for path in candidates:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        record = _read_result_object(path)
        if record is None:
            # 最新缓存无法证明属于其他会话时，不回退借用更旧的成功记录。
            break
        if record.get("thread_id") != state.get("active_thread_id") or record.get("build_execution_scope") != scope:
            continue
        if record.get("execution_stage") != "finalized" or record.get("build_run_plan_sha256") != digest:
            break
        try:
            expected = build_run_task_plan_json_path(state, record["build_run_id"]).resolve()
            same_path = Path(record.get("build_run_plan_path") or "").resolve() == expected
        except (KeyError, TypeError, ValueError, OSError):
            break
        if not expected.is_relative_to(root) or not same_path:
            break
        bound = _read_result_object(expected)
        try:
            matches = bound is not None and build_task_plan_sha256(bound) == digest
        except (TypeError, ValueError, AttributeError):
            matches = False
        if not matches:
            break
        progress = record.get("progress") or {}
        if not isinstance(progress, dict):
            break
        if any(key in progress and not isinstance(progress[key], kind) for key, kind in (
                ("build_summary", dict), ("tasks", list), ("build_results", list))):
            break
        fields.update({key: progress[key] for key in (
            "build_run_id", "build_run_plan_path", "build_run_plan_sha256", "tasks", "build_results", "build_summary",
        ) if key in progress})
        break
    if node in {"unit_test_repair", "test_phase_confirmation"}:
        report_path = artifact / "reports/unit-test-report.json"
        report = _read_result_object(report_path)
        if report is not None:
            context = report.get("artifact_context") or {}
            if not isinstance(context, dict):
                return fields
            files = context.get("files") or {}
            if not isinstance(files, dict):
                return fields
            if (context.get("plan_sha256") == digest
                    and context.get("build_execution_scope") == scope
                    and all(value and _file_digest(root, path) == value for path, value in files.items())):
                if node == "unit_test_repair" or report.get("passed") is True:
                    fields.update(unit_test_report=report, unit_test_report_path=str(report_path))
                if node == "test_phase_confirmation" and report.get("passed") is True:
                    fields.update(unit_test_gate_passed=True, unit_test_quality_gate_passed=True)
    return fields
