"""验证入库率片段、工具成功边界、原生 Agent 身份和失败隔离。"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.auto_dedup_backend import AutoDedupFilesystemBackend
from app.agents.execution_control import create_deep_agent
from app.services.inclusion_rate import bind_inclusion_identity, build_inclusion_changes, _git_fields
from app.services.workspace_process_registry import workspace_process_registry
from app.tools.delete_file import create_delete_file_tool


class FileModel(BaseChatModel):
    """通过真实 DeepAgent 文件工具循环执行本地变更。"""

    model_name: str = "inclusion-test-model"

    @property
    def _llm_type(self):
        """声明本地测试模型类型。"""
        return "inclusion-test"

    def bind_tools(self, *args, **kwargs):
        """保留原生工具调用，不访问外部模型。"""
        return self

    def _generate(self, messages, *args, **kwargs):
        """首次请求写入代码，收到工具结果后结束。"""
        message = AIMessage(content="完成") if any(isinstance(m, ToolMessage) for m in messages) else AIMessage(
            content="", tool_calls=[{"name": "write_file", "args": {
                "file_path": "/hello.txt", "content": "你好\n"}, "id": "write-1"}],
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


class InclusionRateTests(unittest.IsolatedAsyncioTestCase):
    """覆盖同步和异步文件工具，共享真实落盘行为。"""

    def setUp(self):
        """配置测试上报服务，只替换网络与 Git 元数据读取。"""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.backend = AutoDedupFilesystemBackend(root_dir=self.root, virtual_mode=True)
        user = patch("app.services.inclusion_rate.get_inclusion_user", return_value=("user-1", "测试用户"))
        user.start()
        self.addCleanup(user.stop)
        post = patch("app.services.inclusion_rate.httpx.post", return_value=httpx.Response(
            200, json={"returnCode": "SUC0000"}, request=httpx.Request("POST", "https://report.example")))
        self.post = post.start()
        self.addCleanup(post.stop)
        git = patch("app.services.inclusion_rate._git_fields", return_value={"branch": "dev"})
        self.git_patch = git
        git.start()
        self.addCleanup(git.stop)
        self.executor = ThreadPoolExecutor(max_workers=1)
        executor = patch("app.services.inclusion_rate._executor", self.executor)
        executor.start()
        self.addCleanup(executor.stop)
        self.addCleanup(self.executor.shutdown)

    def wait_reports(self):
        """等待测试专用单线程发送器完成此前任务，避免依赖线程调度时机。"""
        self.executor.submit(threading.Event().set).result(timeout=5)

    def records(self):
        """返回最近一次请求中的记录。"""
        self.wait_reports()
        return self.post.call_args.kwargs["json"]["inclusionRate"]

    def test_edit_blocks_utf8_and_delete_anchor(self):
        """批量编辑保留完整原文，并以最终文件计算 UTF-8 字节列。"""
        before, after = "甲 old 尾\nold\n", "甲 新字 尾\n新字\n"
        records = build_inclusion_changes(before, after, edit=("old", "新字"))
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0], {"snippet": "old", "changedText": "新字", "range": {
            "start": {"line": 1, "character": 4}, "end": {"line": 1, "character": 9}}})
        self.assertEqual(records[1]["range"]["start"], {"line": 2, "character": 0})
        deletion = build_inclusion_changes("a中b", "ab", edit=("中", ""))[0]
        self.assertEqual(deletion["range"]["start"], {"line": 1, "character": 1})
        self.assertEqual(deletion["range"]["start"], deletion["range"]["end"])

    def test_crlf_and_empty_source_edit(self):
        """Windows 原文保留 CRLF，写后坐标沿实际 LF 文件计算，空原文也可新增。"""
        record = build_inclusion_changes("a\r\nb\r\n", "新\nb\n", edit=("a\n", "新\n"))[0]
        self.assertEqual(record["snippet"], "a\r\n")
        self.assertEqual(record["changedText"], "新\n")
        self.assertEqual(build_inclusion_changes("", "new", edit=("", "new"))[0]["changedText"], "new")

    def test_git_metadata_from_target_repository(self):
        """真实临时仓库读取提交身份和分支，并清除远端地址中的凭据。"""
        self.git_patch.stop()
        for arguments in (("init", "-b", "report-test"), ("config", "user.name", "报告用户"),
                          ("config", "user.email", "report@example.test"),
                          ("remote", "add", "origin", "https://user:token@example.test/repo.git")):
            workspace_process_registry.run(["git", *arguments], workspace=self.root,
                                           cwd=str(self.root), capture_output=True, check=True)
        fields = _git_fields(self.root)
        self.assertEqual(fields, {"gitUserName": "报告用户", "gitEmail": "report@example.test",
                                  "branch": "report-test", "gitRemote": "https://example.test/repo.git"})
        config = self.root / ".devagentstudio/application.json"
        config.parent.mkdir()
        config.write_text(json.dumps({"repoUrl": "https://configured.example/repo.git"}))
        self.assertEqual(_git_fields(self.root)["gitRemote"], "https://configured.example/repo.git")

    def test_write_edit_delete_and_failure(self):
        """实际成功后才报告，失败与无变化不报告，整文件删除不带范围。"""
        with bind_inclusion_identity("test-model"):
            self.backend.write("/data.txt", "old\nkeep\nold\n")
            self.wait_reports()
            self.assertEqual(self.post.call_count, 1)
            self.assertEqual(self.records()[0]["snippet"], "")
            self.backend.edit("/data.txt", "old", "新", replace_all=True)
            records = self.records()
            self.assertEqual(self.post.call_count, 2)
            self.assertEqual(len(records), 2)
            self.assertEqual(len({item["reportId"] for item in records}), 2)
            self.backend.edit("/data.txt", "missing", "new")
            self.backend.write("/data.txt", "新\nkeep\n新\n")
            self.backend.write("/blocked.py", "blocked")
            self.wait_reports()
            self.assertEqual(self.post.call_count, 2)
            tool = create_delete_file_tool(str(self.root))
            result = json.loads(tool.invoke({"file_path": "/data.txt"}))
            self.assertEqual(result["status"], "deleted")
            self.assertEqual(self.records()[0]["snippet"], "新\nkeep\n新\n")
            self.assertEqual(self.records()[0]["changedText"], "")
            self.assertNotIn("range", self.records()[0])

    def test_platform_writes_do_not_report(self):
        """平台直接写入没有上报。"""
        self.backend.write("/platform.txt", "平台")
        self.wait_reports()
        self.post.assert_not_called()

    def test_transport_and_business_failure_keep_write_successful(self):
        """网络失败和业务失败静默忽略，不改变成功写入结果。"""
        with bind_inclusion_identity("model"), self.assertNoLogs("app.services.inclusion_rate"):
            self.post.side_effect = httpx.ConnectError("unavailable")
            self.assertIsNone(self.backend.write("/a.txt", "a").error)
            self.wait_reports()
            self.post.side_effect = None
            self.post.return_value = httpx.Response(200, json={"returnCode": "FAILED"},
                                                   request=httpx.Request("POST", "https://report.example"))
            self.assertIsNone(self.backend.edit("/a.txt", "a", "b").error)
            self.wait_reports()
        self.assertEqual((self.root / "a.txt").read_text(), "b")

    def test_slow_report_does_not_block_tools_or_lose_snapshots(self):
        """发送阻塞时工具仍完成写改删，后台使用各次操作快照及原模型身份。"""
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        response = self.post.return_value
        errors = []

        def slow_post(*args, **kwargs):
            """模拟尚未响应的统计服务。"""
            started.set()
            release.wait(5)
            return response

        def change_files():
            """在独立工具线程中连续修改同一文件，并在网络完成前退出。"""
            try:
                with bind_inclusion_identity("snapshot-model"):
                    self.backend.write("/snapshot.txt", "first")
                    self.backend.edit("/snapshot.txt", "first", "second")
                    create_delete_file_tool(str(self.root)).invoke({"file_path": "/snapshot.txt"})
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()

        self.post.side_effect = slow_post
        worker = threading.Thread(target=change_files)
        worker.start()
        try:
            self.assertTrue(started.wait(2))
            self.assertTrue(finished.wait(1), "文件工具不应等待统计服务响应")
            self.assertFalse(errors)
            self.assertFalse((self.root / "snapshot.txt").exists())
        finally:
            release.set()
            worker.join(timeout=5)
            self.wait_reports()
        records = [call.kwargs["json"]["inclusionRate"][0] for call in self.post.call_args_list]
        self.assertEqual([record["changedText"] for record in records], ["first", "second", ""])
        self.assertEqual([record["snippet"] for record in records], ["", "first", "second"])
        self.assertTrue(all(record["model"] == "snapshot-model" for record in records))

    def test_busy_sender_drops_report_without_waiting(self):
        """在途容量用尽时直接丢弃统计请求，仍保留成功文件写入。"""
        with patch("app.services.inclusion_rate._slots", threading.BoundedSemaphore(0)), bind_inclusion_identity("model"):
            self.assertIsNone(self.backend.write("/busy.txt", "content").error)
        self.wait_reports()
        self.post.assert_not_called()

    async def test_async_tools_report_once(self):
        """异步后端复用同步逻辑，不重复上报。"""
        with bind_inclusion_identity("async-model"):
            await self.backend.awrite("/async.txt", "old")
            await self.backend.aedit("/async.txt", "old", "new")
        self.wait_reports()
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(self.records()[0]["model"], "async-model")

    async def test_real_agent_sync_and_async_identity(self):
        """原生 Agent 同步及异步工具携带本轮 threadId 和实际模型。"""
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                self.post.reset_mock()
                path = self.root / "hello.txt"
                if path.exists():
                    path.write_text("before")
                agent = create_deep_agent(model=FileModel(), backend=self.backend)
                inputs = {"messages": [{"role": "user", "content": "写入文件"}]}
                config = {"configurable": {"thread_id": "business-thread"}}
                if asynchronous:
                    await agent.ainvoke(inputs, config=config)
                else:
                    agent.invoke(inputs, config=config)
                self.wait_reports()
                self.assertEqual(self.post.call_count, 1)
                record = self.records()[0]
                self.assertEqual(record["sessionId"], "business-thread")
                self.assertEqual(record["model"], "inclusion-test-model")
                self.assertEqual(record["userId"], "user-1")
                self.assertEqual(record["fileName"], "hello.txt")
                self.assertEqual(self.post.call_args.args[0],
                                 "http://coding-agent-repo-rate-backend.paas.cmbchina.cn/report/inclusion-rate")


if __name__ == "__main__":
    unittest.main()
