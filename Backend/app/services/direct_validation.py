"""二次修改的前端编译、打包与项目重启验收。"""

from __future__ import annotations

import re
import shutil
from typing import Any

from app.services.integration_test_runner import (
    _find_frontend_package,
    _missing_tool_result,
    _run_command_result,
)
from app.services.project_launcher import run_project_restart_validation
from app.services.small_task_context import repair_candidate_files
from app.workspace.spec_documents import workflow_artifact_root, workspace_root


DIRECT_FRONTEND_BUILD_CHECKS: frozenset[str] = frozenset({"frontend_typecheck", "frontend_build"})
_VITE_SOURCE_PATH = re.compile(
    r'''\bfrom\s+["']([^"'\n]+\.[jt]sx?)["']|^file:\s*(.+?\.[jt]sx?)(?::\d|$)''',
    re.MULTILINE,
)


def run_direct_fix_validation(
    state: dict[str, Any],
    *,
    affected_layers: set[str],
    lightweight: bool = False,
) -> dict[str, Any]:
    """前端按 tsc、Vite、重启顺序验收，编译失败保留真实诊断并立即返修。

    lightweight=True 时只跑 tsc 类型检查，跳过 vite build 和项目重启：
    用于自动修复轮次，避免每轮 repair 都重复完整构建把时间拖长。
    首次验证仍跑全套，保证交付前的完整门禁。
    """

    results: list[dict[str, Any]] = []
    if "frontend" in affected_layers:
        root = workspace_root(state).resolve()
        frontend = _find_frontend_package(root)
        manager = shutil.which(frontend.package_manager) if frontend else None
        if frontend is None or manager is None:
            result = _missing_tool_result(
                check_id="frontend_typecheck",
                name="前端类型检查（tsc -b）",
                layer="frontend",
                language="typescript",
                required=True,
                evidence=(
                    "未找到前端 package.json。" if frontend is None
                    else f"未找到包管理器命令：{frontend.package_manager}。"
                ),
                on_progress=None,
            )
            return {"test_results": [result], "test_events": [result["id"]]}

        log_root = workflow_artifact_root(state).resolve() / "runtime" / "direct-validation"
        # 首次验证跑 tsc + vite build；repair 轮次只跑 tsc，跳过 vite build。
        checks = (
            (("frontend_typecheck", "前端类型检查（tsc -b）", ["tsc", "-b"]),)
            if lightweight
            else (
                ("frontend_typecheck", "前端类型检查（tsc -b）", ["tsc", "-b"]),
                ("frontend_build", "前端构建检查（vite build）", ["vite", "build"]),
            )
        )
        # 分开执行以保留准确的命令、退出码和日志，同时保证 && 的短路语义。
        for check_id, name, command in checks:
            result = _run_command_result(
                check_id=check_id,
                name=name,
                layer="frontend",
                language="typescript",
                argv=[manager, "exec", *command],
                cwd=frontend.cwd,
                root=root,
                log_root=log_root,
                required=True,
                on_progress=None,
            )
            results.append(result)
            if not result["passed"]:
                return {"test_results": results, "test_events": [item["id"] for item in results]}

    # 首次验证才重启项目；repair 轮次跳过重启，类型检查通过即视为可继续。
    if not lightweight:
        restart = run_project_restart_validation(state)
        results.extend(restart["test_results"])
    return {"test_results": results, "test_events": [item["id"] for item in results]}


def direct_frontend_diagnostic_paths(state: dict[str, Any]) -> list[str]:
    """复用单测诊断解析，仅授权当前前端工程中真实存在的报错源码。"""

    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip()
    if not workspace:
        return []
    root = workspace_root(state).resolve()
    frontend = _find_frontend_package(root)
    if frontend is None:
        return []
    requests = state.get("revision_requests")
    candidates: list[str] = []
    for request in requests if isinstance(requests, list) else []:
        check = request.get("failed_check") if isinstance(request, dict) else None
        if not isinstance(check, dict) or check.get("id") not in DIRECT_FRONTEND_BUILD_CHECKS:
            continue
        execution = check.get("execution")
        if check.get("passed") is not False or not isinstance(execution, dict):
            continue
        cwd = (root / str(execution.get("cwd") or "")).resolve()
        if cwd != frontend.cwd.resolve() or not cwd.is_relative_to(root):
            continue
        diagnostic = "\n".join(
            str(value or "") for value in (
                execution.get("stdout_tail"), execution.get("stderr_tail"), check.get("evidence"),
            )
        )
        # Vite 的模块解析错误通常只有 importer/file 路径，没有行列号。
        # 转成统一诊断格式后仍由共享解析器执行存在性、工作区和源码范围检查。
        vite_paths = [
            next(value for value in match.groups() if value)
            for match in _VITE_SOURCE_PATH.finditer(diagnostic[:24_000])
        ]
        task = {
            "allowed_paths": [(cwd / "src").relative_to(root).as_posix()],
            "failure_evidence": {
                "evidence": "\n".join(f"{path}:1:1" for path in vite_paths),
                "failed_attempt": {"execution": execution},
            },
        }
        candidates.extend(repair_candidate_files(task, workspace))
    return list(dict.fromkeys(candidates))[:100]
