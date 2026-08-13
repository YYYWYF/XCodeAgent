from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.protocols import code_analysis as code_analysis_protocol
from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
    AgUiActionResult,
    build_ag_ui_action_stream,
)
from app.services import code_analysis
from app.agents.code_analyze.runner import _validate_required_tool_trace
from app.tools.code_audit_report import create_code_audit_tools


def _valid_report(file_count: int = 1, issue_count: int = 1) -> str:
    """生成用于持久化和协议测试的最小有效报告。"""

    return f"""# 前端代码检视报告

## 报告概览

| 项目信息 | 详情 |
| --- | --- |
| 项目名称 | example |
| 检视时间 | 2026-08-12 |
| 检视范围 | Frontend/src |
| 检视文件数 | {file_count} 个文件 |

## 问题统计

| 统计指标 | 数值 |
| --- | --- |
| 有问题文件数 | {1 if issue_count else 0} 个 |
| 无问题文件数 | {max(0, file_count - (1 if issue_count else 0))} 个 |
| 总检视问题数量 | {issue_count} 个 |
| 严重问题 | 0 个 |
| 高风险问题 | {issue_count} 个 |
| 中风险问题 | 0 个 |
| 低风险问题 | 0 个 |

## 文件分析详情

**问题**: 不安全输出

- **严重程度**: 高
- **文件路径**: Frontend/src/App.tsx
- **行号**: 1
- **问题描述**: 存在已验证风险
- **修复建议**: 使用安全输出

## 总体评估与建议

### 代码质量总体评价

需要修复已发现的问题。
"""


class CodeAnalysisServiceTests(unittest.TestCase):
    """验证前端源码边界和报告文件安全契约。"""

    def test_discovery_only_returns_frontend_business_sources(self) -> None:
        """源码发现排除 Backend、依赖、构建目录和符号链接。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            frontend = root / "Frontend" / "src"
            frontend.mkdir(parents=True)
            (frontend / "App.tsx").write_text("export default 1\n", encoding="utf-8")
            dependency = frontend / "node_modules"
            dependency.mkdir()
            (dependency / "ignored.js").write_text("ignored\n", encoding="utf-8")
            backend = root / "Backend" / "src"
            backend.mkdir(parents=True)
            (backend / "secret.ts").write_text("secret\n", encoding="utf-8")

            inventory = code_analysis.discover_frontend_sources(root)

            self.assertEqual(inventory.roots, ["Frontend/src"])
            self.assertEqual(inventory.files, ["Frontend/src/App.tsx"])

    def test_atomic_report_write_overwrites_only_daily_report(self) -> None:
        """同日写入原子覆盖正式文件，非法目录保持拒绝。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            relative = ".xcodeagent/codeAudit/code_review_20260812.md"
            target = code_analysis.atomic_write_code_audit_report(
                root, relative, _valid_report()
            )
            code_analysis.atomic_write_code_audit_report(
                root, relative, _valid_report(issue_count=2)
            )

            self.assertEqual(target, root.resolve() / relative)
            self.assertIn("| 总检视问题数量 | 2 个 |", target.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "codeAudit"):
                code_analysis.resolve_code_audit_report_path(
                    root,
                    ".xcodeagent/other/code_review_20260812.md",
                    require_exists=False,
                )

    def test_report_path_traversal_and_invalid_template_are_rejected(self) -> None:
        """路径穿越和残留模板占位符不会被接受。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            with self.assertRaisesRegex(ValueError, "逃逸"):
                code_analysis.resolve_code_audit_report_path(
                    root,
                    ".xcodeagent/codeAudit/../code_review_20260812.md",
                    require_exists=False,
                )

    def test_cancelled_atomic_write_preserves_existing_report(self) -> None:
        """取消发生在替换前时保留上一份完整日报告。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            relative = ".xcodeagent/codeAudit/code_review_20260812.md"
            target = code_analysis.atomic_write_code_audit_report(
                root, relative, _valid_report(issue_count=1)
            )
            with self.assertRaisesRegex(RuntimeError, "未替换"):
                code_analysis.atomic_write_code_audit_report(
                    root,
                    relative,
                    _valid_report(issue_count=2),
                    cancellation_requested=lambda: True,
                )
            self.assertIn(
                "| 总检视问题数量 | 1 个 |",
                target.read_text(encoding="utf-8"),
            )
            with self.assertRaisesRegex(ValueError, "占位符"):
                code_analysis.validate_code_audit_report_content(
                    _valid_report().replace("example", "[项目名称]")
                )

    def test_controlled_tools_load_skill_then_save_exact_report(self) -> None:
        """受控工具一次加载三个 Skill 文件后才能保存唯一报告。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root).resolve()
            relative = ".xcodeagent/codeAudit/code_review_20260812.md"
            tools, state = create_code_audit_tools(root, relative)

            skill_documents = tools[0].invoke({})
            save_result = tools[1].invoke({"content": _valid_report()})

            self.assertIn("--- SKILL.md ---", skill_documents)
            self.assertIn("--- references/security_checks.md ---", skill_documents)
            self.assertIn("--- references/report_template.md ---", skill_documents)
            self.assertIn(relative, save_result)
            self.assertEqual(state["loadCount"], 1)
            self.assertTrue(state["reportSaved"])

    def test_required_tool_trace_rejects_non_skill_first_call(self) -> None:
        """轨迹校验拒绝任何先于强制 Skill 的源码读取。"""

        activities = [
            {"callId": "1", "tool": "read_file", "status": "running"},
            {
                "callId": "2",
                "tool": "load_mayun_frontend_code_review_skill",
                "status": "running",
            },
            {"callId": "3", "tool": "save_code_audit_report", "status": "running"},
        ]
        with self.assertRaisesRegex(RuntimeError, "首个工具"):
            _validate_required_tool_trace(
                activities,
                {"loadCount": 1, "reportSaved": True},
            )


class CodeAnalysisAgUiTests(unittest.TestCase):
    """验证 scan 和 get-report 均发送完整 AG-UI 生命周期。"""

    def test_scan_stream_emits_progress_and_structured_result(self) -> None:
        """扫描成功时发送阶段、状态快照和最终结果。"""

        async def collect() -> list[str]:
            """收集测试流的全部 SSE 帧。"""

            with patch.object(
                code_analysis_protocol,
                "run_frontend_code_analysis",
                return_value={
                    "action": "scan",
                    "reportPath": ".xcodeagent/codeAudit/code_review_20260812.md",
                    "scannedFiles": 3,
                    "issueCount": 1,
                    "problemFileCount": 1,
                    "severityCounts": {"critical": 0, "high": 1, "medium": 0, "low": 0},
                    "generatedAt": "2026-08-12T10:00:00+08:00",
                    "sizeBytes": 1024,
                },
            ):
                stream = code_analysis_protocol.build_code_analysis_ag_ui_stream(
                    payload={
                        "threadId": "code-analysis-thread",
                        "runId": "code-analysis-run",
                        "forwardedProps": {
                            "codeAnalysis": {
                                "action": "scan",
                                "workspaceRoot": "/workspace",
                            }
                        },
                    },
                    accept="text/event-stream",
                )
                return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))
        self.assertIn("RUN_STARTED", payload)
        self.assertIn("validating_workspace", payload)
        self.assertIn("discovering_sources", payload)
        self.assertIn('"scannedFiles":3', payload)
        self.assertIn("STATE_SNAPSHOT", payload)
        self.assertIn("RUN_FINISHED", payload)

    def test_invalid_report_path_finishes_with_structured_failure(self) -> None:
        """非法报告路径仍正常结束 AG-UI 生命周期。"""

        async def collect() -> list[str]:
            """收集非法读取请求的全部 SSE 帧。"""

            stream = code_analysis_protocol.build_code_analysis_ag_ui_stream(
                payload={
                    "forwardedProps": {
                        "codeAnalysis": {
                            "action": "get-report",
                            "workspaceRoot": "/does-not-exist",
                            "reportPath": "../../secret.md",
                        }
                    }
                },
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))
        self.assertIn('"status":"failed"', payload)
        self.assertIn('"action":"get-report"', payload)
        self.assertIn("RUN_FINISHED", payload)

    def test_client_stream_close_cancels_running_operation(self) -> None:
        """客户端关闭流时取消仍在执行的公共长任务。"""

        async def verify() -> bool:
            """消费首个进度后关闭流，并等待操作任务执行清理。"""

            operation_cancelled = asyncio.Event()

            async def operation(report) -> AgUiActionResult:
                """模拟持续运行直到收到任务取消的扫描操作。"""

                try:
                    await report(AgUiActionProgress("analyzing", "正在扫描", 50))
                    await asyncio.Event().wait()
                finally:
                    operation_cancelled.set()
                return AgUiActionResult(data={}, message="不会完成")

            stream = build_ag_ui_action_stream(
                payload={},
                event_name="code-analysis",
                state_key="codeAnalysis",
                run_id_prefix="code-analysis",
                progress_operation=operation,
                error_message_prefix="扫描失败",
            )
            await anext(stream)
            await anext(stream)
            await anext(stream)
            await stream.aclose()
            await asyncio.wait_for(operation_cancelled.wait(), timeout=1)
            return operation_cancelled.is_set()

        self.assertTrue(asyncio.run(verify()))


if __name__ == "__main__":
    unittest.main()
