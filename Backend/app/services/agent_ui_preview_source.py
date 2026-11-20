"""开发预览来源选择和安全 JSONL 导入；不修改应用配置或正式源码。"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal
from collections.abc import Callable
from uuid import uuid4

from app.services.agent_ui_dependency_plan import safe_path
from app.services.workspace_process_registry import workspace_process_registry

PreviewSource = Literal['official-mock', 'real', 'replay']


def switch_preview_source(workspace: Path, attempt_id: str, source: PreviewSource | None, recording: str,
                          cancelled: Callable[[], bool], progress: Callable[[str, str], None]) -> dict[str, Any]:
    """在原启动锁内复核身份、校验录制并先停后启，其他启动不能穿插替换当前 attempt。"""
    from app.services.backend_process_registry import backend_launch_lock
    from app.services.preview_runtime_state import read_record
    from app.services.project_launcher import launch_project_preview

    with backend_launch_lock(workspace):
        if not source:
            raise ValueError('请选择预览来源。')
        if not attempt_id or attempt_id != read_record(workspace).get('attemptId'):
            raise ValueError('预览启动已变化，请读取当前状态后重新切换。')
        selection = prepare_preview_source(workspace, source, recording)
        if cancelled():
            raise ValueError('来源切换已停止，未启动新来源。')
        progress('source', '正在停止旧预览并切换来源；旧问题和待办不会转入新会话…')
        return launch_project_preview(workspace, force_restart=True, preview_source=selection,
                                      on_progress=lambda stage, status, message: progress(stage, message))


def prepare_preview_source(workspace: Path, source: PreviewSource, recording: str = '') -> dict[str, Any]:
    """切换前完成校验；失败不停止旧服务，原始录制只通过 stdin 交给公开解析器。"""
    if source not in {'official-mock', 'real', 'replay'} or (source != 'replay' and recording):
        raise ValueError('预览来源与录制输入不一致。')
    if source != 'replay':
        return {'source': source}
    if not recording or len(recording.encode()) > 1024 * 1024:
        raise ValueError('回放需要不超过 1 MiB 的单次完整 JSONL。')
    from app.services.agent_ui_design_workflow import design_environment
    from app.services.agent_ui_design_project import command_environment

    inspection, _ = design_environment(str(workspace))
    options = {'installationRoot': str(inspection.installation_root),
               'creatorVersion': inspection.creator_version, 'packageJsonSha256': inspection.package_json_sha256,
               'text': recording}
    try:
        result = workspace_process_registry.run(
            [str(inspection.node_executable), str(Path(__file__).with_name('agent_ui_preview_replay.mjs'))],
            workspace=workspace, run_id='preview-replay-' + uuid4().hex, cwd=inspection.installation_root,
            env=command_environment(), input=json.dumps(options), capture_output=True, text=True, timeout=30, check=False)
        if result.returncode != 0 or len(result.stdout) > 2 * 1024 * 1024:
            raise ValueError('回放校验失败')
        safe = json.loads(result.stdout)
        text, digest = safe['text'], safe['sha256']
        if hashlib.sha256(text.encode()).hexdigest() != digest:
            raise ValueError('回放摘要无效')
    except Exception:
        raise ValueError('录制校验失败：仅支持单次完整的文本、工具和运行生命周期，原内容未保存。') from None
    directory = safe_path(workspace, '.devagentstudio/runtime/agent-ui-replay')
    directory.mkdir(parents=True, exist_ok=True)
    path = safe_path(directory, digest + '.jsonl')
    path.write_text(text, encoding='utf-8')
    return {'source': source, 'replaySha256': digest, 'eventCount': safe['eventCount']}


def current_preview_source(workspace: Path) -> dict[str, Any] | None:
    """普通重启沿用本轮明确选择；不读取历史会话或恢复消息和待办。"""
    from app.services.preview_runtime_state import read_record

    marker = (read_record(workspace).get('frontend') or {}).get('agentUiPreview') or {}
    mode = marker.get('mode')
    if mode == 'replay':
        return {'source': 'replay', 'replaySha256': marker.get('replaySha256')}
    if mode == 'official-mock':
        return {'source': 'official-mock'}
    return None


def replay_path(workspace: Path, digest: str) -> Path:
    """固定目录内按内容身份取录制，拒绝客户端路径和启动后的字节替换。"""
    import re

    if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('回放身份无效，请重新导入。')
    path = safe_path(workspace, '.devagentstudio/runtime/agent-ui-replay/' + digest + '.jsonl')
    if path.stat().st_size > 2 * 1024 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('回放文件已变化，请重新导入。')
    return path
