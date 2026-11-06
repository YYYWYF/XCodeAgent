"""T16 当前构建静态预览 owner；不暴露产品端点或任意工作区文件。"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from subprocess import Popen
from typing import Any, Callable
from urllib.parse import urlsplit

from app.services.agent_ui_design_project import (
    AgentUiDesignProject, command_environment, validate_design_project,
)
from app.services.workspace_process_registry import workspace_process_registry
from app.services.agent_ui_design_mock import AgentUiDesignMock, design_mock_options


@dataclass(frozen=True)
class AgentUiDesignLaunch:
    """当前进程的临时 launch 身份，供后续 AG-UI 设计流程投影。"""

    workspace: Path
    launch_id: str
    process_id: int
    url: str
    source_digest: str
    build_digest: str
    mock_evidence: dict[str, Any] | None = None


@dataclass
class _Preview:
    """持有原始受管上下文，回收进程组后才解除工作区登记。"""

    context: AbstractContextManager[Popen[Any]]
    process: Popen[Any]
    launch: AgentUiDesignLaunch
    node: Path
    options: dict[str, Any] | None


def _preview_options_identity(options: dict[str, Any] | None) -> dict[str, Any] | None:
    """切换同一工程的页面不改变服务身份，构建、修订、工具和场景仍须完全一致。"""
    if options is None:
        return None
    return {**options, "binding": {key: value for key, value in options["binding"].items()
                                  if key != "pageId"}}


class AgentUiDesignPreviews:
    """每个工作区拥有一个当前设计预览，关闭后拒绝迟到启动。"""

    def __init__(self) -> None:
        """初始化本进程 owner，不从历史文件猜测端口或 PID。"""
        self._lock = threading.RLock()
        self._previews: dict[Path, _Preview] = {}
        self._closed = False

    def start(
        self, project: AgentUiDesignProject, *, node_executable: Path,
        port: int = 0, run_id: str = "", timeout_seconds: float = 10,
        mock: AgentUiDesignMock | None = None,
        check_cancel: Callable[[], None] | None = None,
    ) -> AgentUiDesignLaunch:
        """复验当前构建后启动静态快照及可选官方 Mock，实际监听成功才返回 URL。"""
        if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
            raise ValueError("设计预览端口无效。")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("设计预览启动预算必须在 0 到 30 秒内。")
        node = node_executable.resolve(strict=True)
        if not node.is_file() or node.name.lower() not in {"node", "node.exe"}:
            raise ValueError("设计预览必须使用独立 Node 可执行文件。")
        with self._lock:
            if check_cancel:
                check_cancel()
            if self._closed:
                raise ValueError("设计预览 owner 已关闭。")
            validate_design_project(project)
            options = design_mock_options(project, mock, node, run_id) if mock else None
            previous = self._previews.get(project.workspace)
            if (previous and previous.process.poll() is None and previous.node == node
                    and previous.launch.source_digest == project.source_digest
                    and previous.launch.build_digest == project.build_digest
                    and (not port or urlsplit(previous.launch.url).port == port)
                    and _preview_options_identity(previous.options) == _preview_options_identity(options)):
                if workspace_process_registry.is_run_cancelled(run_id):
                    raise ValueError("当前设计生成已取消，不能接管已有预览。")
                return previous.launch
            launch_id = uuid.uuid4().hex
            bridge = Path(__file__).with_name("agent_ui_design_static.mjs")
            argv = [str(node), str(bridge)]
            if options:
                # eval 模块在受检工具 cwd 中正常解析公开 export；目标工程不安装 Creator。
                script = f"const server = await import({json.dumps(bridge.as_uri())}); " + \
                    "await server.startDesignStatic(() => import('@za38.03/agent-ui-creator/mock-agent'))"
                argv = [str(node), "--input-type=module", "--eval", script, "agent-ui-design-preview"]
            argv += [(project.project_dir / "dist").as_uri(), launch_id, str(port), project.build_digest,
                     json.dumps(options, ensure_ascii=False)]
            context = workspace_process_registry.managed_process(
                argv, workspace=project.workspace, run_id=run_id,
                cwd=mock.inspection.installation_root if mock else project.project_dir,
                env=command_environment(), stdout=-1, stderr=-3,
            )
            process = context.__enter__()
            frames: queue.Queue[bytes] = queue.Queue(maxsize=1)

            def read_ready() -> None:
                """读取唯一有界启动帧；退出/超时由受管上下文统一回收。"""
                if process.stdout is not None:
                    frames.put(process.stdout.readline(16385))

            reader = threading.Thread(target=read_ready, daemon=True)
            reader.start()
            try:
                frame = frames.get(timeout=timeout_seconds)
                if len(frame) > 16384:
                    raise ValueError("invalid frame")
                ready = json.loads(frame)
                parsed = urlsplit(ready["url"])
                if (ready["launchId"] != launch_id or ready["processId"] != process.pid
                        or parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                        or parsed.path != f"/{launch_id}/" or not parsed.port
                        or (port and parsed.port != port) or parsed.query or parsed.fragment
                        or parsed.username or parsed.password or process.poll() is not None
                        or workspace_process_registry.is_run_cancelled(run_id)):
                    raise ValueError("invalid launch")
                mock_evidence = ready.get("mockEvidence")
                if options and (not isinstance(mock_evidence, dict)
                        or mock_evidence.get("source") != "official-mock"
                        or mock_evidence.get("binding") != options["binding"]
                        or mock_evidence.get("scenarioId") != options["scenarioId"]):
                    raise ValueError("invalid mock identity")
                launch = AgentUiDesignLaunch(project.workspace, launch_id, process.pid, ready["url"],
                                             project.source_digest, project.build_digest, mock_evidence)
                if mock_evidence:
                    records = project.root / "mock-scenarios"
                    if records.is_symlink():
                        raise ValueError("invalid scenario record directory")
                    records.mkdir(exist_ok=True)
                    with (records / f"{launch_id}.json").open("x", encoding="utf-8") as output:
                        json.dump({"launchId": launch_id, "availability": mock_evidence,
                                   "validation": "not_run"}, output, ensure_ascii=False)
                # 新服务已经监听且身份校验通过，才回收旧服务；启动失败不影响已有画布。
                if check_cancel:
                    check_cancel()
                if previous is not None:
                    previous.context.__exit__(None, None, None)
                self._previews[project.workspace] = _Preview(context, process, launch, node, options)
                return launch
            except Exception:
                context.__exit__(None, None, None)
                reader.join(timeout=1)
                raise ValueError("设计预览未启动，可能端口被占用、构建无效或运行已取消。") from None

    def status(self, workspace: str | Path) -> AgentUiDesignLaunch | None:
        """只返回本 owner 中实际存活的进程身份，不复用磁盘历史。"""
        root = Path(workspace).resolve()
        with self._lock:
            preview = self._previews.get(root)
            if preview is None:
                return None
            if preview.process.poll() is not None:
                self.stop(root)
                return None
            return preview.launch

    def stop(self, workspace: str | Path, *, launch_id: str | None = None) -> None:
        """退出受管上下文并等待进程组清理，幂等关闭指定工作区预览。"""
        root = Path(workspace).resolve()
        with self._lock:
            preview = self._previews.get(root)
            if preview is not None and (launch_id is None or preview.launch.launch_id == launch_id):
                preview.context.__exit__(None, None, None)
                self._previews.pop(root, None)

    def close_all(self) -> None:
        """先封锁迟到启动，再逐个关闭；单项失败不跳过其他工作区。"""
        with self._lock:
            self._closed = True
            failures: list[Exception] = []
            for workspace in list(self._previews):
                try:
                    self.stop(workspace)
                except Exception as exc:
                    failures.append(exc)
            if failures:
                raise RuntimeError("部分设计预览进程尚未确认回收。") from failures[0]


agent_ui_design_previews = AgentUiDesignPreviews()


def stop_agent_ui_design_preview(workspace: str | Path) -> dict[str, str]:
    """向工作区原预览停机流程报告本 owner 的真实清理结果。"""
    try:
        agent_ui_design_previews.stop(workspace)
    except Exception:
        return {"status": "failed", "message": "Agent UI 设计预览进程尚未确认回收。"}
    return {"status": "stopped", "message": "Agent UI 设计预览已停止。"}


async def shutdown_agent_ui_design_previews() -> None:
    """后端关闭时等待当前设计预览退出，不阻塞 asyncio 事件循环。"""
    await asyncio.to_thread(agent_ui_design_previews.close_all)
