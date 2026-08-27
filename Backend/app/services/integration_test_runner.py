from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.services.data_source_policy import read_application_datasource_type
from app.utils.subprocess_output import subprocess_output_text
from app.workspace.spec_documents import workflow_artifact_root, workspace_root


COMMAND_TIMEOUT_SECONDS = 180
COMMAND_OUTPUT_SUMMARY_LIMIT = 4_000
CheckProgressCallback = Callable[[dict[str, Any]], None]
IntegrationCheckPhase = Literal["all", "build", "unit"]


@dataclass(frozen=True)
class PackageProject:
    cwd: Path
    package_json: dict[str, Any]
    package_json_path: Path
    package_manager: str


def run_integration_checks(
    state: dict[str, Any],
    *,
    on_progress: CheckProgressCallback | None = None,
    phase: IntegrationCheckPhase = "all",
    artifact_namespace: str = "tests",
    frontend_install_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按阶段执行集成检查，支持构建和单元测试之间的显式生成门。"""

    if phase not in {"all", "build", "unit"}:
        raise ValueError(f"不支持的集成检查阶段：{phase}")

    root = workspace_root(state).resolve()
    safe_namespace = _safe_artifact_namespace(artifact_namespace)
    log_root = workflow_artifact_root(state).resolve() / "runtime" / safe_namespace
    log_root.mkdir(parents=True, exist_ok=True)
    frontend = _find_frontend_package(root)
    datasource_type = _configured_datasource_type(root)
    affected_unit_test_layers = _unit_test_affected_layers(state)

    results: list[dict[str, Any]] = []
    events: list[str] = []
    frontend_unit_tests_affected = (
        affected_unit_test_layers is None
        or "frontend" in affected_unit_test_layers
    )
    backend_unit_tests_affected = (
        affected_unit_test_layers is None
        or "backend" in affected_unit_test_layers
    )

    if phase in {"all", "build"}:
        frontend_results = _frontend_checks(
            root,
            log_root,
            frontend,
            state=state,
            unit_tests_affected=frontend_unit_tests_affected,
            include_unit_tests=phase == "all",
            on_progress=on_progress,
            preinstalled_result=frontend_install_result,
        )
    else:
        frontend_results = _frontend_unit_checks(
            root,
            log_root,
            frontend,
            state=state,
            unit_tests_affected=frontend_unit_tests_affected,
            on_progress=on_progress,
        )
    for result in frontend_results:
        results.append(result)
        events.append(result["id"])
    # Static 是纯前端运行时，即使模板保留 pom.xml 也不得触发后端质量门。
    if datasource_type != "static":
        if frontend is not None and _has_blocking_failure(frontend_results):
            backend_results = _backend_checks_skipped_after_frontend_failure(
                phase=phase,
                on_progress=on_progress,
            )
        elif phase in {"all", "build"}:
            backend_results = _backend_checks(
                root,
                log_root,
                state=state,
                unit_tests_affected=backend_unit_tests_affected,
                include_unit_tests=phase == "all",
                on_progress=on_progress,
            )
        else:
            backend_results = _backend_unit_checks(
                root,
                log_root,
                state=state,
                unit_tests_affected=backend_unit_tests_affected,
                on_progress=on_progress,
            )
        for result in backend_results:
            results.append(result)
            events.append(result["id"])
    return {"test_results": results, "test_events": events}


def _safe_artifact_namespace(value: str) -> str:
    """把检查日志命名空间限制为单层相对目录名。"""

    normalized = str(value or "tests").strip().replace("\\", "_").replace("/", "_")
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", normalized).strip("._")
    return normalized[:80] or "tests"


def _unit_test_affected_layers(state: dict[str, Any]) -> set[str] | None:
    """规范化单元测试受影响层；字段缺失时返回 None 以兼容旧调用方。"""

    if "unit_test_affected_layers" not in state:
        return None
    raw_layers = state.get("unit_test_affected_layers")
    if isinstance(raw_layers, str):
        raw_layers = [raw_layers]
    if not isinstance(raw_layers, (list, tuple, set)):
        return set()
    return {
        str(layer).strip().lower()
        for layer in raw_layers
        if str(layer).strip()
    }


def _configured_datasource_type(root: Path) -> str | None:
    """仅在合法应用配置存在时读取权威类型，普通快速修改工作区继续自动发现工程。"""

    application_path = root / ".xcodeagent" / "application.json"
    if not application_path.is_file():
        return None
    return read_application_datasource_type(root)


def report_check_progress(
    on_progress: CheckProgressCallback | None,
    *,
    check: dict[str, Any],
    status: str,
) -> None:
    """安全地发送单项检查的实时状态，进度展示失败不能影响实际测试。"""

    if on_progress is None:
        return
    try:
        on_progress({"check": check, "status": status})
    except Exception:
        return


def _frontend_checks(
    root: Path,
    log_root: Path,
    frontend: PackageProject | None,
    *,
    state: dict[str, Any] | None = None,
    unit_tests_affected: bool = True,
    include_unit_tests: bool = True,
    on_progress: CheckProgressCallback | None = None,
    preinstalled_result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """依次执行前端安装、构建和可选的单元测试。"""

    if frontend is None:
        results = [
            _missing_tool_result(
                check_id="frontend_install",
                name="前端依赖安装检查",
                layer="frontend",
                language="typescript",
                evidence="未找到前端 package.json，无法执行前端依赖安装和构建检查。",
                required=True,
                on_progress=on_progress,
            ),
        ]
        if include_unit_tests:
            results.append(
                _missing_tool_result(
                    check_id="frontend_unit_tests",
                    name="前端单元测试",
                    layer="frontend",
                    language="typescript",
                    evidence="未找到前端 package.json，无法确认对应单元测试文件，跳过前端单元测试。",
                    required=False,
                    on_progress=on_progress,
                )
            )
        return results

    package_manager_command = shutil.which(frontend.package_manager)
    install_result = dict(preinstalled_result) if isinstance(preinstalled_result, dict) else (
        _run_command_result(
            check_id="frontend_install",
            name="前端依赖安装检查",
            layer="frontend",
            language="typescript",
            argv=[package_manager_command, "install"],
            cwd=frontend.cwd,
            root=root,
            log_root=log_root,
            required=True,
            on_progress=on_progress,
        )
        if package_manager_command
        else _missing_tool_result(
            check_id="frontend_install",
            name="前端依赖安装检查",
            layer="frontend",
            language="typescript",
            evidence=f"未找到包管理器命令：{frontend.package_manager}。",
            required=True,
            on_progress=on_progress,
        )
    )
    if isinstance(preinstalled_result, dict):
        report_check_progress(
            on_progress,
            status="passed" if install_result.get("passed") is True else "failed",
            check=install_result,
        )
    build_result = _run_script_result(
        check_id="frontend_build",
        name="前端构建检查",
        layer="frontend",
        language="typescript",
        package=frontend,
        script_name="build",
        root=root,
        log_root=log_root,
        required=True,
        on_progress=on_progress,
    )
    if not include_unit_tests:
        return [install_result, build_result]
    blocked_reason = None
    if not install_result["passed"]:
        blocked_reason = "前端依赖安装检查失败，跳过本轮前端单元测试。"
    elif not build_result["passed"]:
        blocked_reason = "前端构建检查失败，跳过本轮前端单元测试。"
    unit_result = _frontend_unit_test_result(
        root=root,
        log_root=log_root,
        frontend=frontend,
        state=state or {},
        unit_tests_affected=unit_tests_affected,
        blocked_reason=blocked_reason,
        on_progress=on_progress,
    )
    return [install_result, build_result, unit_result]


def _frontend_unit_checks(
    root: Path,
    log_root: Path,
    frontend: PackageProject | None,
    *,
    state: dict[str, Any],
    unit_tests_affected: bool,
    on_progress: CheckProgressCallback | None,
) -> list[dict[str, Any]]:
    """在前端构建阶段完成后执行单元测试，不重复安装或构建。"""

    if frontend is None:
        return [
            _missing_tool_result(
                check_id="frontend_unit_tests",
                name="前端单元测试",
                layer="frontend",
                language="typescript",
                evidence="未找到前端 package.json，无法确认对应单元测试文件，跳过前端单元测试。",
                required=False,
                on_progress=on_progress,
            )
        ]
    blocked_reason = _blocked_by_previous_check(
        state,
        check_ids=("frontend_install", "frontend_build"),
        evidence="前端构建检查未通过，跳过本轮前端单元测试。",
    )
    return [
        _frontend_unit_test_result(
            root=root,
            log_root=log_root,
            frontend=frontend,
            state=state,
            unit_tests_affected=unit_tests_affected,
            blocked_reason=blocked_reason,
            on_progress=on_progress,
        )
    ]


def _frontend_unit_test_result(
    *,
    root: Path,
    log_root: Path,
    frontend: PackageProject,
    state: dict[str, Any] | None = None,
    unit_tests_affected: bool = True,
    blocked_reason: str | None = None,
    on_progress: CheckProgressCallback | None = None,
) -> dict[str, Any]:
    """仅在约定目录存在前端单元测试文件时执行项目测试脚本。"""

    state = state or {}
    has_tests = (
        _has_corresponding_unit_tests(state, root, "frontend")
        if "unit_test_affected_layers" in state
        else _has_frontend_unit_tests(frontend.cwd)
    )
    if not has_tests:
        return _missing_tool_result(
            check_id="frontend_unit_tests",
            name="前端单元测试",
            layer="frontend",
            language="typescript",
            evidence="本次前端功能没有对应单元测试文件，按策略通过。",
            required=False,
            on_progress=on_progress,
        )
    if not unit_tests_affected:
        return _missing_tool_result(
            check_id="frontend_unit_tests",
            name="前端单元测试",
            layer="frontend",
            language="typescript",
            evidence="本次变更未影响前端业务代码，跳过前端单元测试。",
            required=False,
            on_progress=on_progress,
        )
    if blocked_reason:
        return _missing_tool_result(
            check_id="frontend_unit_tests",
            name="前端单元测试",
            layer="frontend",
            language="typescript",
            evidence=blocked_reason,
            required=False,
            on_progress=on_progress,
        )
    script_name = _first_script(_scripts(frontend), ("test:unit", "test"))
    return _run_script_result(
        check_id="frontend_unit_tests",
        name="前端单元测试",
        layer="frontend",
        language="typescript",
        package=frontend,
        script_name=script_name,
        root=root,
        log_root=log_root,
        required=True,
        missing_evidence="发现前端单元测试文件，但 package.json 未声明 test:unit 或 test script。",
        on_progress=on_progress,
    )


def _backend_checks(
    root: Path,
    log_root: Path,
    *,
    state: dict[str, Any] | None = None,
    unit_tests_affected: bool = True,
    include_unit_tests: bool = True,
    on_progress: CheckProgressCallback | None = None,
) -> list[dict[str, Any]]:
    """执行后端生产构建和可选的 Maven 单测。"""

    state = state or {}
    maven_root = _find_maven_project_root(root)
    if maven_root is not None:
        maven_argv = _maven_command(maven_root)
        has_unit_tests = (
            _has_corresponding_unit_tests(state or {}, root, "backend")
            if "unit_test_affected_layers" in state
            else _has_backend_unit_tests(maven_root)
        )
        if maven_argv is None:
            results = [
                _missing_tool_result(
                    check_id=check_id,
                    name=name,
                    layer="backend",
                    language="java",
                    evidence="发现 Maven 工程，但未找到可用的 Maven wrapper 或全局 mvn。",
                    required=required,
                    on_progress=on_progress,
                )
                for check_id, name, required in (
                    ("backend_build", "后端构建检查", True),
                    ("backend_static_check", "后端静态检查通过", False),
                )
            ]
            if include_unit_tests:
                results.append(
                    _missing_tool_result(
                        check_id="backend_unit_tests",
                        name="后端单元测试",
                        layer="backend",
                        language="java",
                        evidence=(
                            "本次后端功能没有对应单元测试文件，按策略通过。"
                            if not has_unit_tests
                            else (
                                "本次变更未影响后端业务代码，跳过后端单元测试。"
                                if not unit_tests_affected
                                else "发现后端单元测试文件，但未找到可用的 Maven wrapper 或全局 mvn。"
                            )
                        ),
                        required=has_unit_tests and unit_tests_affected,
                        on_progress=on_progress,
                    )
                )
            return results

        build_result = _run_command_result(
            check_id="backend_build",
            name="后端构建检查",
            layer="backend",
            language="java",
            argv=[*maven_argv, "-B", "-Dmaven.test.skip=true", "clean", "install"],
            cwd=maven_root,
            root=root,
            log_root=log_root,
            required=True,
            on_progress=on_progress,
        )
        if not include_unit_tests:
            return [build_result]
        if not has_unit_tests:
            unit_test_result = _missing_tool_result(
                check_id="backend_unit_tests",
                name="后端单元测试",
                layer="backend",
                language="java",
                evidence="本次后端功能没有对应单元测试文件，按策略通过。",
                required=False,
                on_progress=on_progress,
            )
        elif not unit_tests_affected:
            unit_test_result = _missing_tool_result(
                check_id="backend_unit_tests",
                name="后端单元测试",
                layer="backend",
                language="java",
                evidence="本次变更未影响后端业务代码，跳过后端单元测试。",
                required=False,
                on_progress=on_progress,
            )
        elif not build_result["passed"]:
            unit_test_result = _missing_tool_result(
                check_id="backend_unit_tests",
                name="后端单元测试",
                layer="backend",
                language="java",
                evidence="后端构建检查失败，跳过本轮后端单元测试以避免重复失败。",
                required=False,
                on_progress=on_progress,
            )
        else:
            unit_test_result = _run_command_result(
                check_id="backend_unit_tests",
                name="后端单元测试",
                layer="backend",
                language="java",
                argv=[*maven_argv, "-B", "-DfailIfNoTests=true", "test"],
                cwd=maven_root,
                root=root,
                log_root=log_root,
                required=True,
                on_progress=on_progress,
            )
        return [build_result, unit_test_result]

    results = [
        _missing_tool_result(
            check_id="backend_build",
            name="后端构建检查",
            layer="backend",
            language=None,
            evidence="未发现 Maven 或 pom.xml 项目配置，跳过后端构建检查。",
            required=False,
            on_progress=on_progress,
        )
    ]
    if include_unit_tests:
        results.append(
            _missing_tool_result(
                check_id="backend_unit_tests",
                name="后端单元测试",
                layer="backend",
                language="java",
                evidence="未发现 Maven 工程，跳过后端单元测试。",
                required=False,
                on_progress=on_progress,
            )
        )
    return results


def _backend_unit_checks(
    root: Path,
    log_root: Path,
    *,
    state: dict[str, Any],
    unit_tests_affected: bool,
    on_progress: CheckProgressCallback | None,
) -> list[dict[str, Any]]:
    """在后端构建阶段完成后执行 Maven 单测，不重复执行生产构建。"""

    maven_root = _find_maven_project_root(root)
    if maven_root is None:
        return [
            _missing_tool_result(
                check_id="backend_unit_tests",
                name="后端单元测试",
                layer="backend",
                language="java",
                evidence="未发现 Maven 工程，跳过后端单元测试。",
                required=False,
                on_progress=on_progress,
            )
        ]
    maven_argv = _maven_command(maven_root)
    has_unit_tests = (
        _has_corresponding_unit_tests(state, root, "backend")
        if "unit_test_affected_layers" in state
        else _has_backend_unit_tests(maven_root)
    )
    if maven_argv is None:
        evidence = (
            "本次后端功能没有对应单元测试文件，按策略通过。"
            if not has_unit_tests
            else (
                "本次变更未影响后端业务代码，跳过后端单元测试。"
                if not unit_tests_affected
                else "发现后端单元测试文件，但未找到可用的 Maven wrapper 或全局 mvn。"
            )
        )
        return [
            _missing_tool_result(
                check_id="backend_unit_tests",
                name="后端单元测试",
                layer="backend",
                language="java",
                evidence=evidence,
                required=has_unit_tests and unit_tests_affected,
                on_progress=on_progress,
            )
        ]
    blocked_reason = _blocked_by_previous_check(
        state,
        check_ids=("backend_build",),
        evidence="后端构建检查未通过，跳过本轮后端单元测试。",
    )
    if not has_unit_tests:
        return [
            _missing_tool_result(
                check_id="backend_unit_tests",
                name="后端单元测试",
                layer="backend",
                language="java",
                evidence="本次后端功能没有对应单元测试文件，按策略通过。",
                required=False,
                on_progress=on_progress,
            )
        ]
    if not unit_tests_affected:
        return [
            _missing_tool_result(
                check_id="backend_unit_tests",
                name="后端单元测试",
                layer="backend",
                language="java",
                evidence="本次变更未影响后端业务代码，跳过后端单元测试。",
                required=False,
                on_progress=on_progress,
            )
        ]
    if blocked_reason:
        return [
            _missing_tool_result(
                check_id="backend_unit_tests",
                name="后端单元测试",
                layer="backend",
                language="java",
                evidence=blocked_reason,
                required=False,
                on_progress=on_progress,
            )
        ]
    return [
        _run_command_result(
            check_id="backend_unit_tests",
            name="后端单元测试",
            layer="backend",
            language="java",
            argv=[*maven_argv, "-B", "-DfailIfNoTests=true", "test"],
            cwd=maven_root,
            root=root,
            log_root=log_root,
            required=True,
            on_progress=on_progress,
        )
    ]


def _blocked_by_previous_check(
    state: dict[str, Any],
    *,
    check_ids: tuple[str, ...],
    evidence: str,
) -> str | None:
    """读取前一阶段检查结果，只有明确失败时才阻断对应单元测试。"""

    results_by_id = {
        str(result.get("id") or ""): result
        for result in state.get("test_results", [])
        if isinstance(result, dict)
    }
    if any(
        check_id in results_by_id and not bool(results_by_id[check_id].get("passed"))
        for check_id in check_ids
    ):
        return evidence
    return None



def _run_script_result(
    *,
    check_id: str,
    name: str,
    layer: str,
    language: str | None,
    package: PackageProject,
    script_name: str | None,
    root: Path,
    log_root: Path,
    required: bool,
    missing_evidence: str | None = None,
    on_progress: CheckProgressCallback | None,
) -> dict[str, Any]:
    if not script_name or script_name not in _scripts(package):
        return _missing_tool_result(
            check_id=check_id,
            name=name,
            layer=layer,
            language=language,
            evidence=missing_evidence or f"package.json 未声明 {script_name or '目标'} script。",
            required=required,
            on_progress=on_progress,
        )
    package_manager_command = shutil.which(package.package_manager)
    if not package_manager_command:
        return _missing_tool_result(
            check_id=check_id,
            name=name,
            layer=layer,
            language=language,
            evidence=f"未找到包管理器命令：{package.package_manager}。",
            required=required,
            on_progress=on_progress,
        )
    return _run_command_result(
        check_id=check_id,
        name=name,
        layer=layer,
        language=language,
        argv=[package_manager_command, "run", script_name],
        cwd=package.cwd,
        root=root,
        log_root=log_root,
        required=required,
        on_progress=on_progress,
    )


def _run_command_result(
    *,
    check_id: str,
    name: str,
    layer: str,
    language: str | None,
    argv: list[str],
    cwd: Path,
    root: Path,
    log_root: Path,
    required: bool,
    on_progress: CheckProgressCallback | None,
) -> dict[str, Any]:
    report_check_progress(
        on_progress,
        status="running",
        check={
            "id": check_id,
            "name": name,
            "required": required,
            "skipped": False,
            "evidence": "正在执行检查。",
        },
    )
    started_at = datetime.now(UTC).isoformat()
    stdout = ""
    stderr = ""
    returncode: int | None = None
    timed_out = False
    error: str | None = None
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
        stdout = subprocess_output_text(completed.stdout)
        stderr = subprocess_output_text(completed.stderr)
        returncode = completed.returncode
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired 即使在 text=True 下也可能携带 bytes，写日志前必须解码。
        stdout = subprocess_output_text(exc.stdout)
        stderr = subprocess_output_text(exc.stderr)
        timed_out = True
    except OSError as exc:
        error = str(exc)

    finished_at = datetime.now(UTC).isoformat()
    command_log_root = log_root / check_id
    command_log_root.mkdir(parents=True, exist_ok=True)
    stdout_log = command_log_root / "stdout.log"
    stderr_log = command_log_root / "stderr.log"
    stdout_log.write_text(stdout, encoding="utf-8")
    stderr_log.write_text(stderr, encoding="utf-8")
    passed = returncode == 0 and not timed_out and error is None
    passed_tests, total_tests = _extract_test_counts(
        check_id,
        stdout,
        stderr,
    )
    evidence = (
        f"命令执行通过：{' '.join(argv)}"
        if passed
        else f"命令执行失败：{' '.join(argv)}"
    )
    if error:
        evidence = f"{evidence}；错误：{error}"
    if timed_out:
        evidence = f"{evidence}；超过 {COMMAND_TIMEOUT_SECONDS}s 超时。"
    if returncode not in (0, None):
        evidence = f"{evidence}；退出码：{returncode}。"
    if not passed:
        truncated_stdout = stdout[-COMMAND_OUTPUT_SUMMARY_LIMIT:] if stdout else ""
        truncated_stderr = stderr[-COMMAND_OUTPUT_SUMMARY_LIMIT:] if stderr else ""
        if truncated_stderr.strip():
            evidence = f"{evidence}；stderr 末尾:\n{truncated_stderr}"
        if truncated_stdout.strip():
            evidence = f"{evidence}；stdout 末尾:\n{truncated_stdout}"
        total_evidence = len(evidence)
        if total_evidence > COMMAND_OUTPUT_SUMMARY_LIMIT * 2:
            evidence = evidence[:COMMAND_OUTPUT_SUMMARY_LIMIT * 2] + "\n…(evidence truncated)"

    result = {
        "id": check_id,
        "name": name,
        "layer": layer,
        "language": language,
        "passed": passed,
        "skipped": False,
        "required": required,
        "command": " ".join(argv),
        "evidence": evidence,
        "failure_category": None if passed else _failure_category(check_id),
        "execution": {
            "tool": "subprocess",
            "argv": argv,
            "cwd": _relative(cwd, root),
            "returncode": returncode,
            "timed_out": timed_out,
            "error": error,
            "started_at": started_at,
            "finished_at": finished_at,
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "stdout_log_virtual": f"/{_relative(stdout_log, root)}",
            "stderr_log_virtual": f"/{_relative(stderr_log, root)}",
            "stdout_tail": stdout[-COMMAND_OUTPUT_SUMMARY_LIMIT:],
            "stderr_tail": stderr[-COMMAND_OUTPUT_SUMMARY_LIMIT:],
        },
    }
    if passed_tests is not None and total_tests is not None:
        result["passed_tests"] = passed_tests
        result["total_tests"] = total_tests
    report_check_progress(on_progress, status="passed" if passed else "failed", check=result)
    return result


def _extract_test_counts(
    check_id: str,
    stdout: str,
    stderr: str,
) -> tuple[int | None, int | None]:
    """从常见测试运行器输出提取单元测试通过数和总数。"""

    if not check_id.endswith("_unit_tests"):
        return None, None
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", "\n".join((stdout, stderr)))

    # Jest: Tests: 3 passed, 1 failed, 4 total；无 Tests 行时兼容 Test Suites 摘要。
    jest_line = re.search(r"(?im)^\s*(?:Tests|Test Suites):\s*(?P<details>.+)$", output)
    if jest_line:
        counts = _counts_from_summary_line(jest_line.group("details"))
        if counts != (None, None):
            return counts

    # Vitest: Tests  3 passed (3) / Tests  3 passed | 1 failed (4)
    vitest_line = re.search(r"(?im)^\s*Tests\s+(?P<details>.+)$", output)
    if vitest_line:
        details = vitest_line.group("details")
        total_match = re.search(r"\((\d+)\)\s*$", details)
        passed_match = re.search(r"(\d+)\s+passed\b", details)
        if total_match and passed_match:
            return int(passed_match.group(1)), int(total_match.group(1))

    # Maven Surefire: Tests run: 3, Failures: 0, Errors: 0, Skipped: 0
    maven_matches = re.finditer(
        r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)",
        output,
        re.IGNORECASE,
    )
    maven_counts = [
        (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
        )
        for match in maven_matches
    ]
    if maven_counts:
        total = sum(item[0] for item in maven_counts)
        passed = max(0, total - sum(item[1] + item[2] + item[3] for item in maven_counts))
        return passed, total

    # pytest: 3 passed, 1 failed, 4 total / 3 passed in 0.12s
    pytest_line = re.search(r"(?im)^\s*(?P<details>\d+\s+passed.+)$", output)
    if pytest_line:
        counts = _counts_from_summary_line(pytest_line.group("details"))
        if counts != (None, None):
            return counts

    return None, None


def _counts_from_summary_line(details: str) -> tuple[int | None, int | None]:
    """解析包含 passed、failed、error、skipped 和 total 的摘要行。"""

    passed_match = re.search(r"(\d+)\s+passed\b", details, re.IGNORECASE)
    if not passed_match:
        return None, None
    passed = int(passed_match.group(1))
    total_match = re.search(r"(\d+)\s+total\b", details, re.IGNORECASE)
    if total_match:
        return passed, int(total_match.group(1))

    other_count = sum(
        int(match.group(1))
        for match in re.finditer(
            r"(\d+)\s+(?:failed|failure|failures|error|errors|skipped)\b",
            details,
            re.IGNORECASE,
        )
    )
    if other_count:
        return passed, passed + other_count
    if re.search(r"\bpassed\s+in\b", details, re.IGNORECASE):
        return passed, passed
    return None, None


def _missing_tool_result(
    *,
    check_id: str,
    name: str,
    layer: str,
    language: str | None,
    evidence: str,
    required: bool,
    on_progress: CheckProgressCallback | None,
) -> dict[str, Any]:
    passed = not required
    result = {
        "id": check_id,
        "name": name,
        "layer": layer,
        "language": language,
        "passed": passed,
        "skipped": True,
        "required": required,
        "command": None,
        "evidence": evidence,
        "failure_category": None if passed else "missing_test_tool",
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
    report_check_progress(
        on_progress,
        status="skipped" if passed else "failed",
        check=result,
    )
    return result


def _has_blocking_failure(results: list[dict[str, Any]]) -> bool:
    """判断当前顺序检查是否已出现阻塞失败，供后续步骤立即短路。"""

    return any(
        not bool(result.get("passed")) and bool(result.get("blocking", True))
        for result in results
        if isinstance(result, dict)
    )


def _backend_checks_skipped_after_frontend_failure(
    *,
    phase: IntegrationCheckPhase,
    on_progress: CheckProgressCallback | None,
) -> list[dict[str, Any]]:
    """前端阻塞失败后生成后端未执行证据，不再启动 Maven 或后端单测。"""

    checks: list[tuple[str, str, bool]] = []
    if phase in {"all", "build"}:
        checks.append(("backend_build", "后端构建检查", True))
    if phase in {"all", "unit"}:
        checks.append(("backend_unit_tests", "后端单元测试", False))
    return [
        _skipped_after_blocking_failure_result(
            check_id=check_id,
            name=name,
            layer="backend",
            language="java",
            evidence="前端检查已发生阻塞失败，本步骤未执行并直接进入修复任务。",
            required=required,
            on_progress=on_progress,
        )
        for check_id, name, required in checks
    ]


def _skipped_after_blocking_failure_result(
    *,
    check_id: str,
    name: str,
    layer: str,
    language: str | None,
    evidence: str,
    required: bool,
    on_progress: CheckProgressCallback | None,
) -> dict[str, Any]:
    """构造因前置阻塞失败而未执行的检查结果，且不新增第二个门禁失败。"""

    result = {
        "id": check_id,
        "name": name,
        "layer": layer,
        "language": language,
        "passed": True,
        "skipped": True,
        "required": required,
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
    report_check_progress(on_progress, status="skipped", check=result)
    return result


def _find_frontend_package(root: Path) -> PackageProject | None:
    # 直接平铺到根目录：frontend/package.json
    candidate_paths: list[Path] = [
        root / "frontend" / "package.json",
        root / "Frontend" / "package.json",
        root / "app" / "frontend" / "package.json",
        root / "package.json",
    ]
    for path in candidate_paths:
        package = _read_package_project(path)
        if package is not None:
            return package
    return None


def _read_package_project(path: Path) -> PackageProject | None:
    if not path.is_file():
        return None
    try:
        package_json = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(package_json, dict):
        return None
    return PackageProject(
        cwd=path.parent,
        package_json=package_json,
        package_json_path=path,
        package_manager=_package_manager(path.parent),
    )


def _package_manager(cwd: Path) -> str:
    if (cwd / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (cwd / "yarn.lock").is_file():
        return "yarn"
    return "pnpm"


def _find_maven_project_root(root: Path) -> Path | None:
    """按稳定顺序识别根目录及大小写不同的后端 Maven 工程。"""

    for candidate in (root, root / "backend", root / "Backend"):
        if (candidate / "pom.xml").is_file():
            return candidate
    return None


def _maven_command(cwd: Path) -> list[str] | None:
    """优先选择当前平台的 Maven wrapper，再回退到全局 Maven。"""

    if os.name == "nt" and (cwd / "mvnw.cmd").is_file():
        return [str(cwd / "mvnw.cmd")]
    if os.name != "nt" and (cwd / "mvnw").is_file():
        return [str(cwd / "mvnw")]
    command = shutil.which("mvn")
    return [command] if command else None


def _scripts(package: PackageProject) -> dict[str, Any]:
    """读取 package.json 中合法的 scripts 映射。"""

    scripts = package.package_json.get("scripts")
    return scripts if isinstance(scripts, dict) else {}


def _first_script(scripts: dict[str, Any], names: tuple[str, ...]) -> str | None:
    """按优先级返回首个已声明脚本名称。"""

    for name in names:
        if name in scripts:
            return name
    return None


def _has_frontend_unit_tests(frontend_root: Path) -> bool:
    """检测约定 tests 根目录内平铺的 TypeScript 单元测试文件。"""

    tests_root = frontend_root / "tests"
    if not tests_root.is_dir():
        return False
    return any(tests_root.glob("*.test.ts")) or any(tests_root.glob("*.test.tsx"))


def _has_corresponding_unit_tests(
    state: dict[str, Any],
    workspace_root_path: Path,
    layer: str,
) -> bool:
    """优先依据本轮生成映射判断测试目标，避免执行无关的旧测试。"""

    generation = state.get("unit_test_generation")
    if not isinstance(generation, dict):
        if layer == "frontend":
            for project_name in ("frontend", "Frontend", ""):
                if _has_frontend_unit_tests(workspace_root_path / project_name):
                    return True
            return False
        return _has_backend_unit_tests(workspace_root_path / "backend") or _has_backend_unit_tests(
            workspace_root_path / "Backend"
        ) or _has_backend_unit_tests(workspace_root_path)
    test_files = generation.get("test_files")
    if not isinstance(test_files, list):
        return False
    prefix = "frontend/tests/" if layer == "frontend" else "backend/src/test/java/"
    for value in test_files:
        path = str(value or "").replace("\\", "/").lstrip("/")
        normalized = path.casefold()
        valid_suffix = (
            normalized.endswith((".test.ts", ".test.tsx"))
            if layer == "frontend"
            else normalized.endswith("test.java")
        )
        if (
            normalized.startswith(prefix)
            and valid_suffix
            and (workspace_root_path / path).is_file()
        ):
            return True
    return False


def _has_backend_unit_tests(maven_root: Path) -> bool:
    """检测 Maven 标准测试源码目录内可由 Surefire 发现的测试类。"""

    tests_root = maven_root / "src" / "test" / "java"
    return tests_root.is_dir() and any(tests_root.rglob("*Test.java"))


def _failure_category(check_id: str) -> str:
    if check_id.endswith("_install"):
        return "dependency_install_failed"
    if "lint" in check_id:
        return "lint_failure"
    if "build" in check_id:
        return "compile_error"
    if "integration" in check_id:
        return "integration_test_failure"
    return "test_failure"


def _relative(path: Path, root: Path) -> str:
    """返回稳定的 POSIX 风格工作区相对路径，供跨平台事件和虚拟路径使用。"""

    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
