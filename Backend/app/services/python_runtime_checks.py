"""Runtime 的确定性检查适配；复用现有命令执行、进度和质量门禁结果合同。"""

from collections.abc import Callable
import os
from pathlib import Path
import shutil
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.runtime_business_contract import serves_python_business
from app.services.workspace_bootstrap.template_layout import load_template_plan
from app.services.workspace_bootstrap.runtime_readiness import validate_runtime_entrypoints


def runtime_check_plan(root: Path, state: dict) -> dict | None:
    """只按当前磁盘正式拓扑选择 Python，不因目录存在或 checkpoint 旧投影切换实现。"""

    plan = load_template_plan(root)
    if not serves_python_business(plan):
        return None
    if plan.get("confirmation_status") != "confirmed":
        raise ValueError("Runtime 验收必须基于已确认的技术规划。")
    return plan


def resolve_runtime_uv() -> str | None:
    """查找现有 uv，不执行安装脚本、不修改用户全局工具环境。"""

    found = shutil.which("uv")
    if found:
        return found
    candidate = Path.home() / ".local/bin" / ("uv.exe" if os.name == "nt" else "uv")
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def runtime_verification_environment() -> dict[str, str]:
    """检查命令不继承平台模型、SaaS 服务凭据或宿主 Python 环境。"""

    environment = dict(os.environ)
    for key in tuple(environment):
        if (key.startswith(("MODEL_", "OPENAI_", "ANTHROPIC_", "AGENT_RUNTIME_", "XCODEAGENT_", "MYSQL_", "SPRING_DATASOURCE_"))
                or key in {"MANAGED_DATABASE_SERVICE_KEY", "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME",
                           "UV_ENV_FILE", "UV_PROJECT_ENVIRONMENT"}):
            environment.pop(key, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    environment["UV_NO_ENV_FILE"] = "1"
    return environment


def runtime_source_fingerprint(root: Path) -> str:
    """仅对当前 Runtime 源码、测试和依赖合同计算摘要，排除虚拟环境和日志。"""

    from hashlib import sha256

    runtime = root / "agent-runtime"
    files = {runtime / "pyproject.toml", runtime / "uv.lock"}
    files.update(root / WORKSPACE_ARTIFACT_DIR / relative for relative in (
        "application.json", "plans/technical-plan.json", "template-state.json", "template-contracts/route-projector.json",
    ))
    for directory in (runtime / "src", runtime / "tests", root / WORKSPACE_ARTIFACT_DIR / "datasource"):
        for current, directories, names in os.walk(directory, followlinks=False):
            directories[:] = sorted(name for name in directories if not name.startswith(".")
                                     and name != "__pycache__" and not (Path(current) / name).is_symlink())
            files.update(Path(current) / name for name in names if not name.endswith((".pyc", ".pyo")))
    digest = sha256()
    for path in sorted(files):
        if path.is_symlink():
            raise ValueError("Runtime 验收输入不能包含符号链接。")
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
    # 模型参数只进入摘要，不持久化凭据；换模型后不能复用上一模型环境的启动验收。
    from app.config import Settings
    import json
    settings = Settings.from_env()
    digest.update(json.dumps([settings.model_provider, settings.model_base_url, settings.model_api_name,
                              settings.model_api_key, settings.model_custom_headers,
                              settings.model_timeout_seconds, settings.model_max_retries,
                              settings.default_temperature, settings.default_max_tokens], sort_keys=True).encode())
    return digest.hexdigest()


# 用项目解释器解析完整源码，不 import 应用、不生成 __pycache__，不触发数据库或模型调用。
_SYNTAX_COMMAND = (
    "import ast,pathlib,tokenize; root=pathlib.Path('src'); "
    "files=sorted(root.rglob('*.py')); "
    "assert files, 'Runtime src 下未发现 Python 源文件'; "
    "[(ast.parse(tokenize.open(str(p)).read(), filename=str(p)), print(str(p))) for p in files]"
)


def run_python_runtime_checks(
    *, root: Path, log_root: Path, phase: str, include_unit_tests: bool,
    include_startup: bool, blocked: bool, on_progress: Callable | None,
    unit_tests_affected: bool,
    command_result: Callable, missing_result: Callable, skipped_result: Callable,
) -> list[dict[str, Any]]:
    """顺序执行一次依赖同步、源码解析及 pytest，首个阻塞失败后不执行后续命令。"""

    specs = []
    # 单测阶段同样需要锁文件同步，uv run --no-sync 不得借用宿主解释器或过期依赖。
    specs.append(("backend_install", "Python 依赖同步检查"))
    if phase in {"all", "build"}:
        specs.append(("backend_build", "Python 源码语法检查"))
    if phase == "unit" or include_unit_tests:
        specs.append(("backend_unit_tests", "Runtime Python 测试"))
    uv = resolve_runtime_uv()
    readiness_error = None
    fingerprint = None
    if not blocked:
        try:
            validate_runtime_entrypoints(root)
            fingerprint = runtime_source_fingerprint(root)
        except (OSError, ValueError) as exc:
            readiness_error = str(exc)
    results = []
    for check_id, name in specs:
        common = {"check_id": check_id, "name": name, "layer": "backend", "language": "python",
                  "required": True, "on_progress": on_progress}
        if blocked:
            result = skipped_result(**common, evidence="前置检查失败，本项未执行。")
        elif check_id == "backend_unit_tests" and not unit_tests_affected:
            result = skipped_result(**{**common, "required": False}, evidence="本轮没有受影响的 Runtime 单测目标，本项未执行。")
        elif readiness_error or not uv:
            result = missing_result(**common, evidence=readiness_error or "未找到 uv，请先安装并确保 Studio 可以访问该命令。")
            result["repairable"] = False
            result["failure_category"] = "runtime_template_invalid" if readiness_error else "missing_test_tool"
            blocked = True
        else:
            if check_id == "backend_install":
                argv = [uv, "sync", "--frozen"]
            elif check_id == "backend_build":
                argv = [uv, "run", "--no-sync", "--no-env-file", "python", "-c", _SYNTAX_COMMAND]
            else:
                argv = [uv, "run", "--no-sync", "--no-env-file", "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
            result = command_result(**common, argv=argv, cwd=root / "agent-runtime", root=root,
                                    log_root=log_root, environment=runtime_verification_environment())
            blocked = not result.get("passed")
        results.append(result)
    if include_startup and phase in {"all", "build"}:
        # 复用相同启动合同，但使用独立临时进程，不能接管正在运行的预览。
        common = {"check_id": "backend_startup", "name": "Agent Runtime 启动检查", "layer": "backend",
                  "language": "python", "required": True, "on_progress": on_progress}
        if blocked:
            startup = skipped_result(**common, evidence="前置检查失败，Runtime 启动检查未执行。")
        else:
            from app.services.agent_runtime_launcher import check_runtime_startup

            if on_progress:
                on_progress({"status": "running", "check": {"id": "backend_startup", "name": common["name"],
                              "layer": "backend", "language": "python", "required": True}})
            launch = check_runtime_startup(root, log_root)
            passed = launch.get("status") == "completed"
            startup = {"id": "backend_startup", "name": common["name"], "layer": "backend", "language": "python",
                       "passed": passed, "skipped": False, "required": True, "blocking": True,
                       "evidence": launch.get("message"), "startup": launch,
                       "execution": launch.get("execution") or {},
                       "failure_category": None if passed else launch.get("failed_stage"), "command": "uv run --no-sync --no-env-file agent-runtime"}
            if on_progress:
                on_progress({"status": "passed" if passed else "failed", "check": startup})
        startup["repairable"] = False
        startup["source_fingerprint"] = fingerprint
        results.append(startup)
    return results
