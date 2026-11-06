"""设计草稿的真实 Creator 委托；不消费模型文字作为修改或验证结果。"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from app.config import Settings
from app.services.agent_ui_creator_environment import creator_process_environment
from app.services.agent_ui_tool_host import AgentUiToolHostInspection
from app.services.workspace_process_registry import workspace_process_registry


def run_design_creator(inspection: AgentUiToolHostInspection, project: Path, request: str, *,
                       run_id: str, should_cancel: Callable[[], bool] | None = None,
                       source_root: str = '',
                       on_progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """通过公开 SDK 执行一次受管草稿任务，取消和退出均等待 Creator 清理。"""
    from app.services.ui_design_generator import UiDesignStreamCancelled

    if (should_cancel and should_cancel()) or workspace_process_registry.is_run_cancelled(run_id):
        raise UiDesignStreamCancelled('本轮 Creator 设计调整已取消，未提交任务。')
    script = Path(__file__).with_suffix('.mjs').read_text(encoding='utf-8')
    environment = creator_process_environment(Settings.from_env())
    frames: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=8)
    with workspace_process_registry.managed_process(
        [str(inspection.node_executable), '--input-type=module', '--eval', script],
        workspace=inspection.workspace, run_id=run_id, cwd=inspection.installation_root,
        env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ) as process:
        def read_frames() -> None:
            """只接收有界固定帧，未知 SDK 输出不跨入工作流错误或日志。"""
            try:
                assert process.stdout is not None
                for line in iter(lambda: process.stdout.readline(4097), b''):
                    value = json.loads(line) if len(line) <= 4096 and line.endswith(b'\n') else None
                    if (not isinstance(value, dict) or set(value) - {'type', 'code', 'verificationStatus'}
                            or value.get('type') not in {'progress', 'completed', 'failed', 'stopped'}):
                        break
                    frames.put_nowait(value)
            except (ValueError, OSError, queue.Full):
                pass
            finally:
                try:
                    frames.put_nowait(None)
                except queue.Full:
                    pass

        reader = threading.Thread(target=read_frames, daemon=True, name='design-creator-control')
        reader.start()
        assert process.stdin is not None
        process.stdin.write((json.dumps({'projectRoot': str(project), 'request': request,
            'sourceRoot': source_root,
            'sourceValidator': Path(__file__).with_name('agent_ui_design_creator_source.mjs').as_uri(),
            'nodeVersion': inspection.node_version, 'packageJsonSha256': inspection.package_json_sha256},
            ensure_ascii=False) + '\n').encode())
        process.stdin.flush()
        receipt: dict[str, Any] | None = None
        failure = ''
        try:
            while True:
                if (should_cancel and should_cancel()) or workspace_process_registry.is_run_cancelled(run_id):
                    raise UiDesignStreamCancelled('本轮 Creator 设计调整已取消。')
                try:
                    frame = frames.get(timeout=0.05)
                except queue.Empty:
                    continue
                if frame is None:
                    break
                if frame['type'] == 'progress':
                    message = {'creator_running': 'Creator 正在执行设计修改',
                               'creator_verifying': '正在校验 Creator 的源码和组件组合'}.get(frame.get('code'))
                    if message and on_progress:
                        on_progress(message)
                elif frame['type'] == 'completed':
                    receipt = frame
                elif frame['type'] == 'failed':
                    failure = str(frame.get('code') or 'creator_failed')
                elif frame['type'] == 'stopped':
                    process.wait(timeout=3)
                    if failure == 'needs_user_input':
                        raise ValueError('Creator 需要补充信息或额外授权，本轮未提交新设计；请明确修改要求后重试。')
                    if failure == 'creator_read_only':
                        raise ValueError('Creator 将本轮请求识别为检查，未执行设计修改；请明确描述需要实施的改动。')
                    if failure == 'composition_invalid':
                        raise ValueError('Creator 修改后的 AppUIModel/插件组合未通过官方校验，已保留原设计。')
                    if failure == 'no_project_change':
                        raise ValueError('Creator 未返回实际设计文件变更回执，本轮未标记成功，已保留原设计。')
                    if failure == 'source_boundary_failed':
                        raise ValueError('Creator 修改超出了 Agent UI 展示范围，涉及传输、存储或额外 Runtime；已保留原设计。')
                    if failure == 'source_validation_unavailable':
                        raise ValueError('目标工程的 TypeScript 展示代码校验工具不可用，本轮未提交新设计；已保留原稿。')
                    if failure == 'source_syntax_failed':
                        raise ValueError('Creator 修改的应用层代码未通过目标 TypeScript 语法检查，已保留原设计。')
                    if failure == 'creator_timeout':
                        raise ValueError('Creator 的模型请求超时，本轮未提交新设计；已保留原稿，请稍后重试。')
                    if failure == 'creator_rate_limited':
                        raise ValueError('Creator 的模型服务正在限流，本轮未提交新设计；请稍后重试。')
                    if failure == 'creator_auth_failed':
                        raise ValueError('Creator 的模型服务鉴权失败，请检查平台当前 UI 模型配置；已保留原设计。')
                    if failure == 'creator_connection_failed':
                        raise ValueError('Creator 的模型服务连接失败，本轮未提交新设计；已保留原稿，请稍后重试。')
                    if failure or receipt is None or receipt.get('verificationStatus') == 'failed':
                        raise ValueError('Creator 未完成本轮设计调整，已保留原设计；请检查模型配置或稍后重试。')
                    return receipt
            raise ValueError('Creator 工具控制连接已中断，未确认修改结果；已保留原设计，不会自动重复提交。')
        finally:
            if process.poll() is None:
                try:
                    process.stdin.write(b'{"action":"stop"}\n')
                    process.stdin.flush()
                    process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    # 上下文继续回收受管进程组，绝不发布未停止任务的新草稿。
                    pass
            reader.join(timeout=1)
