"""从独立工具安装调用 Creator 成品包的公开初始化与组合校验。"""

import json
from pathlib import Path
import shutil
from typing import Any

from app.services.agent_ui_dependency_plan import REPOSITORY, digest, read_object, safe_path
from app.services.agent_ui_design_project import command_environment
from app.services.agent_ui_tool_host import inspect_agent_ui_tool_host
from app.services.workspace_process_registry import workspace_process_registry
from app.services.preview_runtime_state import redact
from app.services.agent_ui_design_confirmation import _installed_package_digest


def tool_identity() -> dict[str, str]:
    """把公开工具的实际成品资源绑定到审阅计划，版本号相同也不能替换字节。"""
    modules = REPOSITORY / "tools/agent-ui/node_modules"
    package = (modules / "@za38.03/agent-ui-creator").resolve(strict=True)
    if not package.is_relative_to(modules):
        raise ValueError("Creator 必须来自独立工具的成品安装。")
    metadata = read_object(package / "package.json")
    resources = {name: Path(__file__).with_name(name).read_text(encoding='utf-8') for name in (
        'agent_ui_host_integration.py', 'agent_ui_host_registry.mjs', 'agent_ui_connection_requirements.py',
        'agent_ui_plain_text_preview.py',
        'agent_ui_rsbuild_adapter.py', 'agent_ui_rsbuild_loader.cjs', 'agent_ui_transfer_tool.mjs')}
    return {"creatorVersion": metadata["version"], "installedDigest": _installed_package_digest(package),
        'hostIntegrationSha256': digest(resources)}


def run_public_tool(action: str, target: Path, source_root: str, *, workspace: Path,
                    run_id: str, directory: Path, expected_identity: dict[str, str]) -> dict[str, Any]:
    """固定工具动作不调用模型，不读取目标凭据，不接受命令或私有模块路径。"""
    if action not in {"initialize", "verify"}:
        raise ValueError("不支持的转入工具动作。")
    if tool_identity() != expected_identity:
        raise ValueError("公开工具资源已变化，请重新审阅转入计划。")
    tool_root = REPOSITORY / "tools/agent-ui"
    metadata = read_object(tool_root / "node_modules/@za38.03/agent-ui-creator/package.json")
    node = shutil.which("node")
    if not node:
        raise ValueError("转入需要已有独立 Node。")
    inspection = inspect_agent_ui_tool_host(workspace=workspace, installation_root=tool_root,
        node_executable=Path(node).resolve(), creator_version=metadata["version"], run_id=run_id)
    script = Path(__file__).with_suffix(".mjs")
    result = workspace_process_registry.run([str(inspection.node_executable), "--input-type=module", "--eval", script.read_text()],
        workspace=workspace, run_id=run_id, cwd=tool_root, env=command_environment(),
        input=json.dumps({"action": action, "projectRoot": str(target), "sourceRoot": source_root}),
        capture_output=True, text=True, timeout=120, check=False)
    from app.services.agent_ui_dependency_install import _write
    _write(safe_path(directory, action + "-tool.log"), redact(result.stderr[-64000:]).encode())
    if result.returncode != 0 or len(result.stdout) > 64000:
        raise ValueError(f"公开初始化或组合校验失败，已有文件保留，请检查 {action}-tool.log。")
    value = json.loads(result.stdout)
    if value.get("status") != "passed":
        raise ValueError("公开工具拒绝当前组合，已改文件尚未验收：" + ", ".join(value.get("codes", [])[:12]))
    _write(safe_path(directory, action + "-tool.json"), json.dumps(value, ensure_ascii=False).encode())
    return {**value, **expected_identity, "packageJsonSha256": inspection.package_json_sha256}
