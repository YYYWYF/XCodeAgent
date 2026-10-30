"""沿当前模板、内置数据库和预览生命周期启动 Runtime；不修补缺失模板。"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
import subprocess
from uuid import uuid4

from app.config import Settings
from app.services.agent_runtime_environment import build_runtime_environment
from app.services.agent_runtime_health import allocate_runtime_port, wait_runtime_ready
from app.services.agent_runtime_process import register_runtime, runtime_directory, stop_runtime, terminate_runtime_process
from app.services.backend_process_registry import backend_launch_lock
from app.services.backend_startup_diagnostics import read_log_tail, redact_startup_text, startup_root_cause
from app.services.database_credentials import DatabaseCredentialError, load_application_json
from app.services.managed_database_runtime import ManagedDatabaseStartupError, prepare_managed_startup
from app.services.python_runtime_checks import resolve_runtime_uv, runtime_check_plan, runtime_verification_environment, runtime_source_fingerprint
from app.services.runtime_log_redaction import attach_redacted_logs
from app.services.workspace_bootstrap.runtime_readiness import validate_runtime_entrypoints
from app.services.workspace_bootstrap.template_layout import compile_workspace_template_capabilities, RUNTIME_ROOTS
from app.services.workspace_bootstrap.readiness import _validate_requested_capabilities, _validate_template_roots
from app.services.template_state import load_template_state
from app.services.workspace_process_registry import workspace_process_registry
from app.utils.subprocess_output import subprocess_output_text


class RuntimeDependencyError(RuntimeError):
    """保留一次依赖同步的真实退出码和超时事实，不以日志文案反推状态。"""

    def __init__(self, message: str, returncode: int | None, timed_out: bool):
        """保存可公开的执行结果，禁止附带进程环境或凭据。"""

        super().__init__(message)
        self.returncode, self.timed_out = returncode, timed_out


def validate_runtime_launch(root: Path) -> None:
    """只接受当前已确认服务图及真实 TemplateState 能力，不按目录猜测或补模板。"""

    if runtime_check_plan(root, {}) is None:
        raise ValueError("当前已确认技术规划未选择 Agent Runtime。")
    validate_runtime_entrypoints(root)
    _validate_template_roots(root, RUNTIME_ROOTS)
    capabilities = compile_workspace_template_capabilities(root, load_application_json(root))
    state = load_template_state(root)
    _validate_requested_capabilities(state, {"capabilities": capabilities})


def launch_runtime(
    workspace: str | Path, *, on_progress: Callable[[str], None] | None = None,
    settings: Settings | None = None, skip_install: bool = False,
) -> dict:
    """在当前工作区锁内检查、同步依赖、一次取密并启动一个 Runtime 进程。"""

    root = Path(workspace).expanduser().resolve()
    with backend_launch_lock(root):
        return _launch_runtime_locked(root, on_progress=on_progress, settings=settings, skip_install=skip_install)


def _launch_runtime_locked(root: Path, *, on_progress: Callable | None, settings: Settings | None,
                           skip_install: bool, probe_root: Path | None = None, debug: dict | None = None,
                           target_agent_id: str | None = None) -> dict:
    """仅记录脱敏命令日志和进程事实，所有失败进入现有预览结果合同。"""

    profile = "probe" if probe_root is not None else "debug" if debug is not None else "preview"
    runtime_root = probe_root if probe_root is not None else runtime_directory(root, profile=profile)
    runtime_root.mkdir(parents=True, exist_ok=True)
    environment, process, log_threads = {}, None, []
    category = "agent_runtime_validation"
    result = {"status": "failed", "workspace": str(root), "message": "Runtime 启动失败。"}
    execution = {"tool": "subprocess", "argv": [], "cwd": "agent-runtime", "returncode": None,
                 "timed_out": False, "started_at": datetime.now(UTC).isoformat()}
    try:
        validate_runtime_launch(root)
        input_digest = runtime_source_fingerprint(root)
        stopped = stop_runtime(root, profile=profile) if probe_root is None else {"status": "stopped"}
        if stopped.get("status") == "failed":
            raise RuntimeError(stopped["message"])
        uv = resolve_runtime_uv()
        if not uv:
            raise RuntimeError("未找到 uv，请先安装并确保 Studio 可以访问该命令。")
        from app.services.preview_runtime_state import record_database_check

        category = "agent_runtime_database_resource"
        snapshot = prepare_managed_startup(root, on_readiness=(lambda report: record_database_check(root, report))
                                           if probe_root is None else None)
        category = "agent_runtime_install"
        if not skip_install:
            if on_progress:
                on_progress("正在按 uv.lock 同步 Runtime 依赖…")
            execution["argv"] = [uv, "sync", "--frozen"]
            _install(root, runtime_root, uv)
        category = "agent_runtime_configuration"
        launch_id = uuid4().hex
        port = allocate_runtime_port()
        environment = build_runtime_environment(root, port=port, launch_id=launch_id, snapshot=snapshot, settings=settings)
        from app.services.runtime_agent_contracts import agent_contract_index

        agent_ids = sorted(agent_contract_index(runtime_check_plan(root, {}))) or ["chat"]
        if target_agent_id is not None and target_agent_id not in agent_ids:
            raise ValueError("当前正式技术规划不存在所选 Agent。")
        if debug is not None:
            environment.update({"AGENT_RUNTIME_PROFILE": "debug", "AGENT_RUNTIME_NAMESPACE": "debug:" + launch_id,
                "AGENT_RUNTIME_DEBUG_TOKEN": debug["token"], "AGENT_RUNTIME_DEBUG_EXPIRES_AT": str(debug["expiresAt"]),
                "AGENT_RUNTIME_ALLOWED_ORIGINS": debug["origins"]})
        if input_digest != runtime_source_fingerprint(root):
            raise ValueError("Runtime 源码、依赖、模型或当前应用设计已变化，请重新启动。")
        if probe_root is not None:
            environment["AGENT_RUNTIME_NAMESPACE"] = "probe:" + launch_id
            environment["AGENT_RUNTIME_PROFILE"] = "probe"
        database_required = environment["AGENT_RUNTIME_DATABASE_ENABLED"] == "true"
        category = "agent_runtime_start"
        if on_progress:
            on_progress("正在启动 Agent Runtime 并检查数据库与路由就绪…")
        argv = [uv, "--directory", str(root / "agent-runtime"), "run", "--no-sync", "--no-env-file", "agent-runtime"]
        execution["argv"] = argv
        stdout, stderr = runtime_root / "agent-runtime.stdout.log", runtime_root / "agent-runtime.stderr.log"
        for path in (stdout, stderr):
            if path.is_symlink():
                raise ValueError("Runtime 日志不能是符号链接。")
            path.touch(mode=0o600, exist_ok=True)
        process = workspace_process_registry.start(root, argv, cwd=root / "agent-runtime", env=environment,
                                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if probe_root is None:
            register_runtime(root, process, profile=profile)
        log_threads = attach_redacted_logs(process, stdout, stderr, environment)
        url = f"http://127.0.0.1:{port}"
        passed, message, timed_out = wait_runtime_ready(process, url, launch_id, database_required=database_required,
                                                      expected_agent_ids=[target_agent_id] if target_agent_id else agent_ids, profile=profile,
                                                      allowed_agent_ids=agent_ids if target_agent_id else None)
        execution["timed_out"] = timed_out
        if not passed:
            raise RuntimeError(message)
        result.update(status="running", message=message, preview_url=url, failed_stage=None,
                      launch_id=launch_id, database_required=database_required, agent_ids=agent_ids,
                      server={"pid": process.pid, "port": port, "ready": True, "command": " ".join(argv)})
        if probe_root is not None:
            terminate_runtime_process(root, process)
            for thread in log_threads:
                thread.join(timeout=2)
            result.update(status="completed", preview_url=None, server=None)
        result["logs"] = {"stdout_log": str(stdout), "stderr_log": str(stderr)}
        execution.update(finished_at=datetime.now(UTC).isoformat(), cleanup_succeeded=probe_root is None or process.poll() is not None,
                         stdout_log="/" + stdout.relative_to(root).as_posix(), stderr_log="/" + stderr.relative_to(root).as_posix())
        result["execution"] = execution
        return result
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, ManagedDatabaseStartupError):
            category = "agent_runtime_database_resource"
        elif isinstance(exc, DatabaseCredentialError):
            category = "agent_runtime_database_config"
        cleanup = None
        execution["returncode"] = process.poll() if process is not None else None
        if isinstance(exc, RuntimeDependencyError):
            execution.update(returncode=exc.returncode, timed_out=exc.timed_out)
        if process is not None:
            if probe_root is None:
                cleanup = stop_runtime(root, profile=profile)
            else:
                try:
                    terminate_runtime_process(root, process)
                    cleanup = {"status": "stopped"}
                except (OSError, RuntimeError, subprocess.TimeoutExpired):
                    cleanup = {"status": "failed", "message": "临时 Runtime 回收失败，请停止当前任务并检查进程。"}
            for thread in log_threads:
                thread.join(timeout=2)
        logs = []
        for name in ("agent-runtime.stdout.log", "agent-runtime.stderr.log"):
            path = runtime_root / name
            if path.is_file() and not path.is_symlink():
                logs.append(read_log_tail(path))
        evidence = redact_startup_text(str(exc), environment)
        cause = startup_root_cause("\n".join(logs), "") if logs else ""
        result.update(message=evidence + ("\n" + cause if cause else ""), failed_stage=category,
                      cleanup=cleanup, repairable=False)
        result["logs"] = {"stdout_log": str(runtime_root / "agent-runtime.stdout.log"),
                          "stderr_log": str(runtime_root / "agent-runtime.stderr.log")}
        prefix = "agent-runtime-install" if category == "agent_runtime_install" else "agent-runtime"
        execution.update(finished_at=datetime.now(UTC).isoformat(), error=evidence,
                         cleanup_succeeded=process is None or process.poll() is not None,
                         stdout_log="/" + (runtime_root / f"{prefix}.stdout.log").relative_to(root).as_posix(),
                         stderr_log="/" + (runtime_root / f"{prefix}.stderr.log").relative_to(root).as_posix())
        result["execution"] = execution
        return result


def check_runtime_startup(root: Path, log_root: Path, *, agent_id: str | None = None) -> dict:
    """用独立端口和 namespace 验收并回收临时进程，不替换正在运行的预览。"""

    with backend_launch_lock(root):
        return _launch_runtime_locked(root, on_progress=None, settings=None, skip_install=True,
                                      probe_root=log_root / "backend_startup" / uuid4().hex, target_agent_id=agent_id)


def _install(root: Path, runtime_root: Path, uv: str) -> None:
    """只执行一次冻结依赖同步，失败保留退出码与完整日志，不自动安装工具或改锁文件。"""

    try:
        completed = workspace_process_registry.run(
            [uv, "sync", "--frozen"], workspace=root, cwd=root / "agent-runtime",
            env=runtime_verification_environment(), capture_output=True, timeout=300, check=False,
        )
        stdout, stderr = subprocess_output_text(completed.stdout), subprocess_output_text(completed.stderr)
        failed = completed.returncode != 0
        returncode, timed_out = completed.returncode, False
        message = f"Runtime 依赖同步失败，退出码 {completed.returncode}。"
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = subprocess_output_text(exc.stdout), subprocess_output_text(exc.stderr)
        failed, message = True, "Runtime 依赖同步超时（300 秒）。"
        returncode, timed_out = None, True
    for name, content in (("stdout", stdout), ("stderr", stderr)):
        (runtime_root / f"agent-runtime-install.{name}.log").write_text(
            redact_startup_text(content, {}), encoding="utf-8")
    if failed:
        raise RuntimeDependencyError(message + "\n" + startup_root_cause(stdout, stderr), returncode, timed_out)
