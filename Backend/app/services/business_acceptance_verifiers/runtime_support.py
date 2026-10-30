"""业务检查只读当前交付物、正式依赖和固定模板入口，不扫描工程或执行代码。"""

from pathlib import Path, PurePosixPath

from app.services.business_acceptance_verifiers.common import MAX_FILE_BYTES, MAX_FILES
from app.services.business_acceptance_verifiers.python_ast import PythonEvidenceUnavailable


def runtime_verification_files(files: dict, task: dict, check: dict, workspace: str | Path) -> dict:
    """限制依赖读取范围并拒绝任意中间链接；测试、注释和工程外类型不作为实现证据。"""

    if not workspace:
        raise PythonEvidenceUnavailable("Runtime 验收缺少工作区根目录。")
    root = Path(workspace).resolve()
    paths = set(check["target_paths"])
    paths.update((check.get("expected") or {}).get("support_paths") or [])
    if check["kind"] == "python.bootstrap_contract":
        paths.add("agent-runtime/pyproject.toml")
    if check["kind"] == "agent.module_contract":
        from app.services.agent_runtime_template_policy import load_agent_runtime_template_policy
        from app.services.runtime_agent_module_policy import module_task_errors

        policy = load_agent_runtime_template_policy(root / "agent-runtime")
        errors = module_task_errors(task, policy)
        if errors:
            raise PythonEvidenceUnavailable("；".join(errors))
        module = check["expected"]["module"]
        if module in {"skills", "knowledge"}:
            from app.services.runtime_agent_resources import agent_resource_files

            paths.update(agent_resource_files(module, check["expected"]["configuration"]))
        paths.update("agent-runtime/" + path for path in policy["modulePaths"][module]["readPaths"])
        if module in {"model", "memory", "context"}:
            paths.add("agent-runtime/src/app/server/agui.py")
        if module == "model":
            paths.add("agent-runtime/src/app/models/anthropic_transport.py")
        if module in {"memory", "context"}:
            paths.update("agent-runtime/" + path for path in (
                "src/app/agent/context.py", "src/app/persistence/checkpointer.py", "src/app/interaction/service.py",
                "src/app/interaction/schemas.py", "src/app/interaction/page_context.py",
                "src/app/security/service.py", "src/app/security/tokens.py",
            ))
        if module == "knowledge":
            paths.add("agent-runtime/src/app/tools/__init__.py")
    if len(paths) > MAX_FILES:
        raise PythonEvidenceUnavailable(f"Python 验收依赖超过 {MAX_FILES} 个文件，请缩小交付范围。")
    result = {}
    for relative in sorted(paths):
        parts = PurePosixPath(relative).parts
        if not relative.startswith("agent-runtime/") or str(PurePosixPath(relative)) != relative or any(
            part.startswith('.') or any(char in part for char in '*?[]\\') for part in parts
        ):
            raise PythonEvidenceUnavailable(f"Runtime 验收路径不合法：{relative}。")
        target = root
        for part in parts:
            target = target / part
            if target.is_symlink():
                raise PythonEvidenceUnavailable(f"Runtime 验收不能读取符号链接：{relative}。")
        if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
            raise PythonEvidenceUnavailable(f"Runtime 验收依赖缺失或超过大小限制：{relative}。")
        if relative.endswith('.py') and relative.startswith("agent-runtime/src/app/"):
            result[relative] = target.read_text(encoding="utf-8")
        elif relative in check["target_paths"] or relative == "agent-runtime/pyproject.toml" and check["kind"] == "python.bootstrap_contract":
            # 资源可作为真实交付文件存在，但绝不交给 Python AST 冒充可执行实现。
            if check["kind"] != "agent.module_contract" or relative.endswith(".md"):
                result[relative] = target.read_text(encoding="utf-8")
        elif check["kind"] == "agent.module_contract" and relative.endswith(".md"):
            result[relative] = target.read_text(encoding="utf-8")
    return result
