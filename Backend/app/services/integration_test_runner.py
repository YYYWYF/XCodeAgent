from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.utils.subprocess_output import subprocess_output_text
from app.workspace.spec_documents import workflow_artifact_root, workspace_root


COMMAND_TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class PackageProject:
    cwd: Path
    package_json: dict[str, Any]
    package_json_path: Path
    package_manager: str


def run_integration_checks(state: dict[str, Any]) -> dict[str, Any]:
    root = workspace_root(state).resolve()
    log_root = workflow_artifact_root(state) / "runtime" / "tests"
    log_root.mkdir(parents=True, exist_ok=True)
    frontend = _find_frontend_package(root)
    workspace_package = _read_package_project(root / "package.json")

    results: list[dict[str, Any]] = []
    events: list[str] = []
    for result in _frontend_checks(root, log_root, frontend):
        results.append(result)
        events.append(result["id"])
    for result in _backend_checks(root, log_root):
        results.append(result)
        events.append(result["id"])
    for result in _joint_integration_checks(root, log_root, frontend, workspace_package):
        results.append(result)
        events.append(result["id"])
    print("---------test_results------------", results)
    return {"test_results": results, "test_events": events}


def _frontend_checks(
    root: Path,
    log_root: Path,
    frontend: PackageProject | None,
) -> list[dict[str, Any]]:
    if frontend is None:
        return [
            _missing_tool_result(
                check_id="frontend_install",
                name="前端依赖安装检查",
                layer="frontend",
                language="typescript",
                evidence="未找到前端 package.json，无法执行前端依赖安装和构建检查。",
                required=True,
            )
        ]

    scripts = _scripts(frontend)
    return [
        _run_command_result(
            check_id="frontend_install",
            name="前端依赖安装检查",
            layer="frontend",
            language="typescript",
            argv=[frontend.package_manager, "install"],
            cwd=frontend.cwd,
            root=root,
            log_root=log_root,
            required=True,
        ),
        _run_script_result(
            check_id="frontend_build",
            name="前端构建检查",
            layer="frontend",
            language="typescript",
            package=frontend,
            script_name="build",
            root=root,
            log_root=log_root,
            required=True,
        ),
        _run_script_result(
            check_id="frontend_lint",
            name="前端 lint 通过",
            layer="frontend",
            language="typescript",
            package=frontend,
            script_name="lint",
            root=root,
            log_root=log_root,
            required=False,
            missing_evidence="package.json 未声明 lint script，跳过前端 lint 检查。",
        ),
        _run_script_result(
            check_id="frontend_typecheck",
            name="前端 typecheck 通过",
            layer="frontend",
            language="typescript",
            package=frontend,
            script_name="typecheck",
            root=root,
            log_root=log_root,
            required=False,
            missing_evidence="package.json 未声明 typecheck script，跳过前端类型检查。",
        ),
        _run_script_result(
            check_id="frontend_unit_tests",
            name="前端单元测试通过",
            layer="frontend",
            language="typescript",
            package=frontend,
            script_name=_first_script(scripts, ("test:unit", "test")),
            root=root,
            log_root=log_root,
            required=False,
            missing_evidence="package.json 未声明 test 或 test:unit script，跳过前端单元测试。",
        ),
    ]


def _backend_checks(root: Path, log_root: Path) -> list[dict[str, Any]]:
    if (root / "mvnw").is_file() or (root / "pom.xml").is_file():
        mvn = "./mvnw" if (root / "mvnw").is_file() else "mvn"
        return [
            _run_command_result(
                check_id="backend_build",
                name="后端构建检查",
                layer="backend",
                language="java",
                argv=[mvn, "test", "-DskipTests"],
                cwd=root,
                root=root,
                log_root=log_root,
                required=True,
            ),
            _run_command_result(
                check_id="backend_static_check",
                name="后端静态检查通过",
                layer="backend",
                language="java",
                argv=[mvn, "checkstyle:check"],
                cwd=root,
                root=root,
                log_root=log_root,
                required=False,
            ),
            _run_command_result(
                check_id="backend_unit_tests",
                name="后端单元测试通过",
                layer="backend",
                language="java",
                argv=[mvn, "test"],
                cwd=root,
                root=root,
                log_root=log_root,
                required=True,
            ),
        ]

    if _has_pytest_project(root):
        return [
            _missing_tool_result(
                check_id="backend_build",
                name="后端构建检查",
                layer="backend",
                language="python",
                evidence="Python 项目没有独立构建步骤，已跳过后端构建检查。",
                required=False,
            ),
            _missing_tool_result(
                check_id="backend_static_check",
                name="后端静态检查通过",
                layer="backend",
                language="python",
                evidence="未发现统一静态检查命令，已跳过后端静态检查。",
                required=False,
            ),
            _run_command_result(
                check_id="backend_unit_tests",
                name="后端单元测试通过",
                layer="backend",
                language="python",
                argv=["python3", "-m", "pytest"],
                cwd=root,
                root=root,
                log_root=log_root,
                required=True,
            ),
        ]

    return [
        _missing_tool_result(
            check_id="backend_build",
            name="后端构建检查",
            layer="backend",
            language=None,
            evidence="未发现 Maven、pom.xml 或 pytest 项目配置，跳过后端构建检查。",
            required=False,
        ),
        _missing_tool_result(
            check_id="backend_static_check",
            name="后端静态检查通过",
            layer="backend",
            language=None,
            evidence="未发现后端静态检查工具配置，跳过后端静态检查。",
            required=False,
        ),
        _missing_tool_result(
            check_id="backend_unit_tests",
            name="后端单元测试通过",
            layer="backend",
            language=None,
            evidence="未发现后端单元测试工具配置，跳过后端单元测试。",
            required=False,
        ),
    ]


def _joint_integration_checks(
    root: Path,
    log_root: Path,
    frontend: PackageProject | None,
    workspace_package: PackageProject | None,
) -> list[dict[str, Any]]:
    package = _first_package_with_script(
        (workspace_package, frontend),
        ("test:integration", "integration"),
    )
    if package is None:
        return [
            _missing_tool_result(
                check_id="joint_integration",
                name="前后端集成测试通过",
                layer="joint",
                language=None,
                evidence="未发现 test:integration 或 integration script，跳过前后端集成测试。",
                required=False,
            )
        ]
    script_name = _first_script(_scripts(package), ("test:integration", "integration"))
    return [
        _run_script_result(
            check_id="joint_integration",
            name="前后端集成测试通过",
            layer="joint",
            language=None,
            package=package,
            script_name=script_name,
            root=root,
            log_root=log_root,
            required=True,
        )
    ]


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
) -> dict[str, Any]:
    if not script_name or script_name not in _scripts(package):
        return _missing_tool_result(
            check_id=check_id,
            name=name,
            layer=layer,
            language=language,
            evidence=missing_evidence or f"package.json 未声明 {script_name or '目标'} script。",
            required=required,
        )
    return _run_command_result(
        check_id=check_id,
        name=name,
        layer=layer,
        language=language,
        argv=[package.package_manager, "run", script_name],
        cwd=package.cwd,
        root=root,
        log_root=log_root,
        required=required,
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
) -> dict[str, Any]:
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

    return {
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
        },
    }


def _missing_tool_result(
    *,
    check_id: str,
    name: str,
    layer: str,
    language: str | None,
    evidence: str,
    required: bool,
) -> dict[str, Any]:
    passed = not required
    return {
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


def _find_frontend_package(root: Path) -> PackageProject | None:
    for path in (
        root / "Frontend" / "package.json",
        root / "frontend" / "package.json",
        root / "app" / "frontend" / "package.json",
        root / "package.json",
    ):
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
    return "npm"


def _scripts(package: PackageProject) -> dict[str, Any]:
    scripts = package.package_json.get("scripts")
    return scripts if isinstance(scripts, dict) else {}


def _first_script(scripts: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in scripts:
            return name
    return None


def _first_package_with_script(
    packages: tuple[PackageProject | None, ...],
    script_names: tuple[str, ...],
) -> PackageProject | None:
    for package in packages:
        if package is None:
            continue
        if _first_script(_scripts(package), script_names):
            return package
    return None


def _has_pytest_project(root: Path) -> bool:
    return any(
        (root / name).exists()
        for name in ("pytest.ini", "pyproject.toml", "setup.cfg")
    )


def _failure_category(check_id: str) -> str:
    if check_id.endswith("_install"):
        return "dependency_install_failed"
    if "lint" in check_id:
        return "lint_failure"
    if "typecheck" in check_id:
        return "type_error"
    if "build" in check_id:
        return "compile_error"
    if "integration" in check_id:
        return "integration_test_failure"
    return "test_failure"


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
