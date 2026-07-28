from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.agents.tool_activity_stream import (
    invoke_agent_with_tool_activity,
    normalized_tool_activity,
)
from app.services.build_tool_activity import task_ids_for_tool_activity


class StreamingAgent:
    """提供可控 messages/values 流的最小 Deep Agent 替身。"""

    def stream(self, payload, *, stream_mode, subgraphs):
        del payload
        self.stream_mode = stream_mode
        self.subgraphs = subgraphs
        yield "messages", (
            SimpleNamespace(
                tool_calls=[],
                tool_call_chunks=[
                    {"id": "call-1", "index": 0, "name": "read_file", "args": '{"file_'}
                ],
            ),
            {},
        )
        yield "messages", (
            SimpleNamespace(
                tool_calls=[],
                tool_call_chunks=[
                    {"id": None, "index": 0, "name": None, "args": 'path":"/src/Page.tsx"}'}
                ],
            ),
            {},
        )
        yield "values", {"messages": [SimpleNamespace(content="completed")]}


class FailingToolAgent:
    """模拟文件工具返回错误但最终 Agent 仍形成文本结果。"""

    def stream(self, payload, *, stream_mode, subgraphs):
        del payload, stream_mode, subgraphs
        yield "messages", (
            SimpleNamespace(
                tool_calls=[
                    {
                        "id": "call-error",
                        "name": "edit_file",
                        "args": {"file_path": "/src/Page.tsx", "old_string": "private-code"},
                    }
                ],
                tool_call_chunks=[],
            ),
            {},
        )
        yield "messages", (
            SimpleNamespace(
                tool_calls=[],
                tool_call_chunks=[],
                tool_call_id="call-error",
                status="error",
                content="raw private tool error",
            ),
            {},
        )
        yield "values", {"messages": [SimpleNamespace(content="handled")]}


class SubgraphStreamingAgent:
    """模拟主代理把连续文件操作委派给多个 Deep Agent 子图。"""

    def stream(self, payload, *, stream_mode, subgraphs):
        del payload, stream_mode
        self.subgraphs = subgraphs
        yield ("tools:reader",), "messages", (
            SimpleNamespace(
                tool_calls=[
                    {
                        "id": "shared-call-id",
                        "name": "read_file",
                        "args": {"file_path": "/src/first.ts"},
                    }
                ],
                tool_call_chunks=[],
            ),
            {},
        )
        yield ("tools:writer",), "messages", (
            SimpleNamespace(
                tool_calls=[
                    {
                        "id": "shared-call-id",
                        "name": "edit_file",
                        "args": {"file_path": "/src/second.ts"},
                    }
                ],
                tool_call_chunks=[],
            ),
            {},
        )
        yield (), "values", {"messages": [SimpleNamespace(content="root completed")]}
        yield ("tools:writer",), "values", {
            "messages": [SimpleNamespace(content="child completed")]
        }


class MessageOnlyFinalAgent:
    """模拟最终 values 不含 messages、正文仅存在根 messages 流的真实情况。"""

    def stream(self, payload, *, stream_mode, subgraphs):
        del payload, stream_mode, subgraphs
        yield "messages", (
            SimpleNamespace(
                content='{"task_results":[',
                tool_calls=[],
                tool_call_chunks=[],
            ),
            {},
        )
        yield "messages", (
            SimpleNamespace(
                content="]}",
                tool_calls=[],
                tool_call_chunks=[],
            ),
            {},
        )
        yield "values", {"todos": []}


class NamespacedFinalAgent:
    """模拟 Deep Agent 最终文本仅出现在嵌套 namespace 的流形态。"""

    def stream(self, payload, *, stream_mode, subgraphs):
        """返回仅在模型子图中携带最终消息的测试流。"""

        del payload, stream_mode, subgraphs
        yield (), "values", {"todos": []}
        yield ("model:terminal",), "messages", (
            SimpleNamespace(
                content='{"task_results":[]}',
                tool_calls=[],
                tool_call_chunks=[],
            ),
            {},
        )
        yield ("model:terminal",), "values", {
            "messages": [SimpleNamespace(content='{"task_results":[]}')]
        }


class RootMessageAndChildStateAgent:
    """模拟根消息与子图 values 同时存在，根终态必须优先。"""

    def stream(self, payload, *, stream_mode, subgraphs):
        """返回根消息先于子图状态结束的测试流。"""

        del payload, stream_mode, subgraphs
        yield "messages", (
            SimpleNamespace(content="root result", tool_calls=[], tool_call_chunks=[]),
            {},
        )
        yield "values", {"todos": []}
        yield ("child",), "values", {
            "messages": [SimpleNamespace(content="child result")]
        }


class BuildToolActivityTests(unittest.TestCase):
    def test_visible_workspace_tools_have_safe_chinese_messages(self) -> None:
        """七类工作区工具都应生成稳定、安全的一行中文状态。"""

        cases = [
            ("ls", {"path": "/src"}, "正在浏览目录：/src"),
            ("read_file", {"file_path": "/src/a.ts"}, "正在读取文件：/src/a.ts"),
            ("glob", {"pattern": "/src/**/*.ts"}, "正在查找文件：/src/**/*.ts"),
            ("grep", {"pattern": "useState", "path": "/src"}, "正在搜索代码：useState · /src"),
            ("write_file", {"file_path": "/src/a.ts", "content": "secret"}, "正在写入文件：/src/a.ts"),
            ("edit_file", {"file_path": "/src/a.ts", "old_string": "secret"}, "正在编辑文件：/src/a.ts"),
            ("delete_file", {"file_path": "/src/a.ts"}, "正在删除文件：/src/a.ts"),
        ]

        for tool_name, args, expected in cases:
            with self.subTest(tool_name=tool_name):
                activity = normalized_tool_activity(
                    call_id=f"call-{tool_name}",
                    tool_name=tool_name,
                    args=args,
                    workspace="/tmp/workspace",
                )
                self.assertIsNotNone(activity)
                self.assertEqual(activity["message"], expected)
                self.assertNotIn("secret", str(activity))

    def test_internal_tools_and_host_paths_are_not_exposed(self) -> None:
        """内部编排工具不展示，工作区外宿主机路径必须被替换。"""

        for tool_name in ("write_todos", "task", "execute"):
            self.assertIsNone(
                normalized_tool_activity(
                    call_id="internal",
                    tool_name=tool_name,
                    args={"command": "cat /etc/passwd"},
                    workspace="/tmp/workspace",
                )
            )
        activity = normalized_tool_activity(
            call_id="host-path",
            tool_name="read_file",
            args={"file_path": "/Users/person/private.ts"},
            workspace="/tmp/workspace",
        )
        self.assertEqual(activity["path"], "工作区路径")
        self.assertNotIn("person", str(activity))

    def test_streamed_arguments_replace_generic_activity_and_keep_final_text(self) -> None:
        """工具参数分片补全后应覆盖通用状态，同时保留 Agent 最终文本。"""

        agent = StreamingAgent()
        activities: list[dict] = []

        result = invoke_agent_with_tool_activity(
            agent,
            {"messages": []},
            workspace="/tmp/workspace",
            on_tool_activity=activities.append,
        )

        self.assertEqual(result, "completed")
        self.assertEqual(agent.stream_mode, ["messages", "values"])
        self.assertTrue(agent.subgraphs)
        self.assertGreaterEqual(len(activities), 2)
        self.assertEqual(activities[-1]["path"], "/src/Page.tsx")
        self.assertEqual(activities[-1]["message"], "正在读取文件：/src/Page.tsx")

    def test_failed_tool_activity_does_not_expose_raw_arguments_or_result(self) -> None:
        """工具失败只展示归一化失败状态，不泄露替换内容和底层错误。"""

        activities: list[dict] = []

        result = invoke_agent_with_tool_activity(
            FailingToolAgent(),
            {"messages": []},
            workspace="/tmp/workspace",
            on_tool_activity=activities.append,
        )

        self.assertEqual(result, "handled")
        self.assertEqual(activities[-1]["status"], "failed")
        self.assertIn("工具操作失败", activities[-1]["message"])
        self.assertNotIn("private-code", str(activities))
        self.assertNotIn("raw private tool error", str(activities))

    def test_subgraph_tool_calls_continue_updating_and_keep_root_result(self) -> None:
        """子代理工具流应持续上报，且同名调用 ID 不得覆盖其他子图或最终主图结果。"""

        agent = SubgraphStreamingAgent()
        activities: list[dict] = []

        result = invoke_agent_with_tool_activity(
            agent,
            {"messages": []},
            workspace="/tmp/workspace",
            on_tool_activity=activities.append,
        )

        self.assertTrue(agent.subgraphs)
        self.assertEqual(result, "root completed")
        self.assertEqual(len(activities), 2)
        self.assertNotEqual(activities[0]["callId"], activities[1]["callId"])
        self.assertEqual(activities[0]["message"], "正在读取文件：/src/first.ts")
        self.assertEqual(activities[1]["message"], "正在编辑文件：/src/second.ts")

    def test_root_message_stream_is_final_text_fallback_when_values_omit_messages(self) -> None:
        """根 values 缺少 messages 时仍应拼接保留最终 Agent 结构化报告。"""

        result = invoke_agent_with_tool_activity(
            MessageOnlyFinalAgent(),
            {"messages": []},
            workspace="/tmp/workspace",
            on_tool_activity=lambda activity: None,
        )

        self.assertEqual(result, '{"task_results":[]}')

    def test_namespaced_final_message_is_preserved_when_root_values_have_no_messages(self) -> None:
        """根 values 为空时必须从最浅层 Agent namespace 恢复最终报告。"""

        result = invoke_agent_with_tool_activity(
            NamespacedFinalAgent(),
            {"messages": []},
            workspace="/tmp/workspace",
            on_tool_activity=lambda activity: None,
        )

        self.assertEqual(result, '{"task_results":[]}')

    def test_root_message_wins_over_nested_state_text(self) -> None:
        """子图晚到的文本不得覆盖主 Agent 已形成的最终报告。"""

        result = invoke_agent_with_tool_activity(
            RootMessageAndChildStateAgent(),
            {"messages": []},
            workspace="/tmp/workspace",
            on_tool_activity=lambda activity: None,
        )

        self.assertEqual(result, "root result")

    def test_activity_matches_authorized_task_paths_or_falls_back_to_batch(self) -> None:
        """具体文件只归属命中任务，技能等范围外读取回退当前批次。"""

        tasks = [
            {"id": "page-a", "allowed_paths": ["src/pages/A/**"]},
            {"id": "page-b", "change_scope": [{"path": "src/pages/B/index.tsx"}]},
        ]

        matched = task_ids_for_tool_activity(
            {"path": "/src/pages/B/index.tsx"},
            tasks,
        )
        fallback = task_ids_for_tool_activity(
            {"path": "/.xcodeagent/builtin-skills/react/SKILL.md"},
            tasks,
        )

        self.assertEqual(matched, ["page-b"])
        self.assertEqual(fallback, ["page-a", "page-b"])


if __name__ == "__main__":
    unittest.main()
