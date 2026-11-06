"""通过隔离 SDK 夹具验证 Node 包装生命周期，不作为真实 Creator 发行验收。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from app.services.agent_ui_tool_host import AgentUiToolHostError
from app.services.agent_ui_tool_lifecycle import AgentUiToolHostOptions, AgentUiToolHosts, shutdown_agent_ui_tool_hosts
from app.services.workspace_process_registry import WorkspaceProcessRegistry
from app.utils.process_liveness import pid_is_running
from app.config import Settings


SDK_FIXTURE = r'''
import { readFile, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
const behaviorURL = new URL('./behavior.json', import.meta.url);
async function behavior() { return JSON.parse(await readFile(behaviorURL, 'utf8')); }
export class CreatorProcessManager {
  constructor(options) { this.options = options; this.child = undefined; }
  get processId() { return this.child?.pid ?? (this.started ? process.pid : undefined); }
  async ensureStarted() {
    const b = await behavior();
    await writeFile(new URL('./observed.json', import.meta.url), JSON.stringify(this.options));
    if (b.mode === 'error') throw new Error('fixture-secret');
    if (b.mode === 'delay') await new Promise(r => setTimeout(r, 10000));
    if (b.child) {
      this.child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
        detached: process.platform !== 'win32', stdio: 'ignore'
      });
      await writeFile(new URL('./child.json', import.meta.url), JSON.stringify({pid:this.child.pid}));
    }
    this.started = true;
    return {host:'127.0.0.1',port:12345,authToken:'fixture-secret',agentMode:'echo'};
  }
  async isBusy() { return Boolean((await behavior()).busy); }
  async dispose() {
    if ((await behavior()).mode === 'stop-error') throw new Error('fixture-secret');
    if (this.child) {
      const child = this.child;
      await new Promise(resolve => { child.once('exit', resolve); child.kill(); });
      this.child = undefined;
    }
    this.started = false;
    await writeFile(new URL('./disposed.json', import.meta.url), '{}');
  }
}
'''


@unittest.skipUnless(shutil.which("node"), "本机未提供可运行的 Node")
class AgentUiToolLifecycleTests(unittest.TestCase):
    """只把测试夹具装入临时工具目录，不读 A 私有实现、不接模型。"""

    def setUp(self) -> None:
        """创建包含空格和中文路径的工具、目标及固定配置根。"""
        self.temporary = TemporaryDirectory(prefix="tool lifecycle 中文 ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "应用一"
        self.other = self.root / "应用二"
        self.installation = self.root / "工具安装"
        for path in (self.workspace, self.other, self.installation):
            path.mkdir()
        self.package = self.installation / "node_modules/@za38.03/agent-ui-creator"
        self.package.mkdir(parents=True)
        self.metadata = {"name":"@za38.03/agent-ui-creator", "version":"1.2.3", "type":"module",
                         "engines":{"node":"^22.0.0 || ^24.0.0"},
                         "exports":{".":"./process.mjs", "./process":"./process.mjs", "./package.json":"./package.json"}}
        (self.package / "package.json").write_text(json.dumps(self.metadata), encoding="utf-8")
        (self.package / "process.mjs").write_text(SDK_FIXTURE, encoding="utf-8")
        self.set_behavior()
        self.node = Path(shutil.which("node")).resolve()
        self.options = AgentUiToolHostOptions(self.workspace, self.installation, self.node, "1.2.3")
        self.registry = WorkspaceProcessRegistry()
        self.patches = [patch("app.services.agent_ui_tool_host.workspace_process_registry", self.registry),
                        patch("app.services.agent_ui_tool_process.workspace_process_registry", self.registry),
                        patch("app.services.agent_ui_tool_process.Settings.from_env", return_value=Settings(
                            model_base_url="https://fixture.example/v1", model_api_key="fixture-secret", model_name="fixture-model"))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.hosts = AgentUiToolHosts()
        self.addCleanup(self.hosts.close_all)

    def set_behavior(self, **values: object) -> None:
        """更新测试夹具行为，不作为生产 SDK 配置。"""
        (self.package / "behavior.json").write_text(json.dumps(values), encoding="utf-8")

    def test_public_sdk_start_status_stop_and_registry_cleanup(self) -> None:
        """实际 Node 包装只用公开 exports，安全状态不包含端点或凭据。"""
        status = self.hosts.start(self.options)
        self.assertEqual(status.workspace, self.workspace)
        self.assertFalse(status.busy)
        self.assertNotIn("fixture-secret", repr(status))
        self.assertEqual(self.registry.active_process_ids(self.workspace), [status.process_id])
        observed = json.loads((self.package / "observed.json").read_text())
        self.assertNotIn("configRoot", observed)
        self.assertEqual(observed["environment"]["CREATOR_MODEL_API_KEY"], "fixture-secret")
        self.assertEqual(observed["environment"]["CREATOR_MODEL_NAME"], "fixture-model")
        self.assertFalse(observed["allowExternalEndpoint"])
        self.assertEqual(observed["environment"]["CREATOR_HOT_RELOAD"], "false")
        self.assertFalse(self.hosts.status(self.workspace).busy)
        self.hosts.stop(self.workspace)
        self.assertTrue((self.package / "disposed.json").exists())
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])

    def test_switch_checks_busy_and_uses_platform_configuration(self) -> None:
        """忙碌不能切工程；空闲切换先回收旧进程，再从平台取得配置。"""
        first = self.hosts.start(self.options)
        other = AgentUiToolHostOptions(self.other, self.installation, self.node, "1.2.3")
        self.set_behavior(busy=True)
        with self.assertRaises(AgentUiToolHostError) as caught:
            self.hosts.switch(self.workspace, other)
        self.assertEqual(caught.exception.code, "tool_busy")
        self.assertEqual(self.hosts.status(self.workspace).process_id, first.process_id)
        self.set_behavior()
        second = self.hosts.switch(self.workspace, other)
        self.assertEqual(second.workspace, self.other)
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])
        self.assertNotIn("configRoot", json.loads((self.package / "observed.json").read_text()))

    def test_existing_installation_cannot_be_silently_rebound(self) -> None:
        """同一工具安装已有 owner 时，新 start 不覆盖或复用另一工程。"""
        first = self.hosts.start(self.options)
        with self.assertRaises(AgentUiToolHostError):
            self.hosts.start(self.options)
        self.assertEqual(self.hosts.status(self.workspace).process_id, first.process_id)

    def test_sdk_error_is_redacted_and_process_reaped(self) -> None:
        """SDK 原始异常不跨进程回显；失败启动同样执行 dispose。"""
        self.set_behavior(mode="error")
        with self.assertRaises(AgentUiToolHostError) as caught:
            self.hosts.start(self.options)
        self.assertNotIn("fixture-secret", str(caught.exception))
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])
        self.assertTrue((self.package / "disposed.json").exists())

    def test_missing_public_process_export_fails_closed(self) -> None:
        """缺少公开 /process 不改读私有模块或调用私有 HTTP。"""
        del self.metadata["exports"]["./process"]
        (self.package / "package.json").write_text(json.dumps(self.metadata), encoding="utf-8")
        with self.assertRaises(AgentUiToolHostError):
            self.hosts.start(self.options)
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])

    def test_timeout_disposes_starting_sdk_and_reaps_wrapper(self) -> None:
        """启动超时触发真实包装的停止消息，等待 SDK 清理而非只断流。"""
        self.set_behavior(mode="delay")
        options = AgentUiToolHostOptions(self.workspace, self.installation, self.node,
                                         "1.2.3", startup_timeout_seconds=0.3)
        with self.assertRaises(AgentUiToolHostError) as caught:
            self.hosts.start(options)
        self.assertEqual(caught.exception.code, "start_timeout")
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])
        self.assertTrue((self.package / "disposed.json").exists())

    def test_cancel_during_start_closes_owned_process(self) -> None:
        """启动尚未 ready 时也能观察 run 取消并完成回收。"""
        self.set_behavior(mode="delay")
        options = AgentUiToolHostOptions(self.workspace, self.installation, self.node,
                                         "1.2.3", run_id="cancel-tool")
        failures: list[Exception] = []

        def start() -> None:
            """在测试线程等待启动，记录取消后的安全失败。"""
            try:
                self.hosts.start(options)
            except Exception as exc:
                failures.append(exc)

        worker = threading.Thread(target=start)
        worker.start()
        deadline = time.monotonic() + 4
        while not (self.package / "observed.json").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.registry.cancel_run("cancel-tool")
        worker.join(8)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])

    def test_eof_runs_sdk_dispose_and_never_reports_a_live_host(self) -> None:
        """父进程关闭管道时，Node 必须调用 SDK 清理。"""
        status = self.hosts.start(self.options)
        owned = self.hosts._hosts[self.installation]
        owned.process.stdin.close()
        owned.process.wait(timeout=8)
        self.assertTrue((self.package / "disposed.json").exists())
        with self.assertRaises(AgentUiToolHostError):
            self.hosts.status(status.workspace)
        self.hosts.stop(self.workspace)

    def test_shutdown_closes_hosts_and_fences_new_start(self) -> None:
        """后端关闭调用同一个清理 owner，关闭后拒绝迟到启动。"""
        self.hosts.start(self.options)
        with patch("app.services.agent_ui_tool_lifecycle.agent_ui_tool_hosts", self.hosts):
            asyncio.run(shutdown_agent_ui_tool_hosts())
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])
        with self.assertRaises(AgentUiToolHostError):
            self.hosts.start(self.options)

    def test_stop_waits_for_sdk_detached_child_cleanup(self) -> None:
        """夹具创建独立子进程组，验证 SDK 回收先于停止成功回执。"""
        self.set_behavior(child=True)
        self.hosts.start(self.options)
        child = json.loads((self.package / "child.json").read_text())["pid"]
        self.hosts.stop(self.workspace)
        self.assertFalse(pid_is_running(child))

    def test_sdk_dispose_failure_keeps_unconfirmed_owner(self) -> None:
        """即使包装进程已退出，缺少 SDK 清理确认也不能解除安装绑定。"""
        self.hosts.start(self.options)
        self.set_behavior(mode="stop-error")
        with self.assertRaises(AgentUiToolHostError) as caught:
            self.hosts.stop(self.workspace)
        self.assertEqual(caught.exception.code, 'stop_unconfirmed')
        self.assertEqual(self.hosts.close_all(), (self.workspace,))
        self.assertIn(self.installation, self.hosts._hosts)
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])

    def test_shutdown_during_start_prevents_late_ready(self) -> None:
        """关闭栅栏能在启动等待中生效，迟到 ready 不能重新发布为可用。"""
        self.set_behavior(mode='delay')
        failures: list[Exception] = []

        def start() -> None:
            """模拟后端正在退出时尚未 ready 的工具启动。"""
            try:
                self.hosts.start(self.options)
            except Exception as exc:
                failures.append(exc)

        worker = threading.Thread(target=start)
        worker.start()
        deadline = time.monotonic() + 4
        while not (self.package / 'observed.json').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.hosts.close_all(), ())
        worker.join(8)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.registry.active_process_ids(self.workspace), [])

    def test_sdk_stop_ack_does_not_override_a_live_child_pid(self) -> None:
        """SDK 自报停止而子进程仍存活时，不释放安装绑定或误报全部回收。"""
        self.hosts.start(self.options)
        with patch('app.services.agent_ui_tool_process.pid_is_running', return_value=True):
            with self.assertRaises(AgentUiToolHostError) as caught:
                self.hosts.stop(self.workspace)
        self.assertEqual(caught.exception.code, 'stop_unconfirmed')
        self.assertIn(self.installation, self.hosts._hosts)


class AgentUiBackendShutdownTests(unittest.TestCase):
    """验证实际 FastAPI 生命周期接入及既有清理的最终保证。"""

    def test_tool_cleanup_failure_does_not_skip_checkpoint_cleanup(self) -> None:
        """工具关闭意外失败时，原有 Graph/检查点清理仍必须执行。"""
        from app.main import app, lifespan

        async def exercise() -> None:
            """仅进入实际 lifespan，不启动网络或业务运行。"""
            async with lifespan(app):
                pass

        with patch('app.main.ensure_database_platform_key'), patch('app.main.ensure_agents_document'), \
                patch('app.main.initialize_backend_instance'), \
                patch('app.main.shutdown_agent_ui_tool_hosts', new_callable=AsyncMock,
                      side_effect=RuntimeError('fixture cleanup')) as shutdown, \
                patch('app.main.clear_workflow_graph_cache') as workflow, \
                patch('app.main.clear_application_planning_graph_cache') as planning, \
                patch('app.main.clear_direct_modification_graph_cache') as conversation, \
                patch('app.main.close_workflow_checkpointer', new_callable=AsyncMock) as checkpoints:
            with self.assertRaises(RuntimeError):
                asyncio.run(exercise())
        shutdown.assert_awaited_once()
        workflow.assert_called_once()
        planning.assert_called_once()
        conversation.assert_called_once()
        checkpoints.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
