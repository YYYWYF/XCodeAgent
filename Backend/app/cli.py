import json
import sys
from time import monotonic
from typing import Any
from uuid import uuid4

from app.graph import graph

NODE_LABELS = {
    "classify_request_complexity": "判断需求复杂度",
    "requirements": "Main Agent 需求分析并生成 RequirementSpec",
    "direct_modification": "Main Agent 直接执行简单修改",
    "project_planning": "Main Agent 生成项目级计划",
    "detail_confirmation": "Main Agent 确认并生成页面细节设计",
    "prepare_build_tasks": "Main Agent 拆分构建任务",
    "build": "专业 Agent 执行前端/数据源构建任务",
    "integration_test": "测试验证子图：执行测试并检查质量门禁",
    "launch_project": "启动本地预览工程",
    "acceptance": "等待/记录用户验收",
    "finalize_project": "完成 workflow",
    "handle_failure": "处理失败状态",
}

STATIC_NEXT_NODES = {
    "requirements": ["project_planning"],
    "direct_modification": ["integration_test"],
    "project_planning": ["detail_confirmation"],
    "detail_confirmation": ["prepare_build_tasks"],
    "prepare_build_tasks": ["build"],
    "build": ["integration_test"],
    "launch_project": ["acceptance"],
    "acceptance": ["finalize_project"],
}


def _label(node_name: str) -> str:
    return NODE_LABELS.get(node_name, node_name)


def _next_nodes(node_name: str, update: dict[str, Any]) -> list[str]:
    if node_name == "classify_request_complexity":
        return (
            ["direct_modification"]
            if update.get("request_complexity") == "simple"
            else ["requirements"]
        )
    if node_name == "integration_test":
        return (
            ["launch_project"]
            if update.get("quality_gate_passed")
            else ["handle_failure"]
        )
    return STATIC_NEXT_NODES.get(node_name, [])


def _progress_detail(node_name: str, update: dict[str, Any]) -> str:
    if node_name == "classify_request_complexity":
        return f"复杂度={update.get('request_complexity')}，原因={update.get('complexity_reason')}"
    if node_name == "requirements":
        return f"需求文档={update.get('requirement_spec_path')}"
    if node_name == "project_planning":
        return (
            f"计划文档={update.get('project_plan_path')}，"
            f"结构化状态={update.get('project_plan_json_path')}"
        )
    if node_name == "detail_confirmation":
        return f"页面={update.get('selected_page_id')}，计划文档已更新"
    if node_name == "prepare_build_tasks":
        return (
            f"任务数={len(update.get('tasks', []))}，"
            f"任务DAG={update.get('build_task_plan_path')}"
        )
    if node_name == "build":
        summary = update.get("build_summary", {})
        return f"完成={summary.get('completed', 0)}，失败={summary.get('failed', 0)}"
    if node_name == "integration_test":
        report = update.get("test_report", {})
        summary = report.get("summary", {})
        return (
            f"通过={report.get('passed')}，"
            f"检查={summary.get('passed', 0)}/{summary.get('total', 0)}，"
            f"报告={update.get('test_report_path')}"
        )
    if node_name == "launch_project":
        return f"预览地址={update.get('preview_url')}"
    if node_name == "acceptance":
        return f"验收={update.get('accepted')}"
    if node_name in {"finalize_project", "handle_failure"}:
        return f"状态={update.get('status')}"
    return ""


def _print_progress(message: str) -> None:
    print(f"[app-demo] {message}", file=sys.stderr, flush=True)


def main() -> None:
    request = " ".join(sys.argv[1:]).strip()
    if not request:
        request = "Generate a minimal demo application."

    config = {"configurable": {"thread_id": str(uuid4())}}
    started_at = monotonic()

    _print_progress("▶ 开始 workflow")
    _print_progress(f"▶ 正在执行：{_label('classify_request_complexity')}")

    try:
        for chunk in graph.stream(
            {"request": request, "timeline": []},
            config=config,
            stream_mode="updates",
        ):
            for node_name, update in chunk.items():
                if not isinstance(update, dict):
                    continue

                detail = _progress_detail(node_name, update)
                suffix = f"；{detail}" if detail else ""
                _print_progress(f"✓ 完成：{_label(node_name)}{suffix}")

                for next_node in _next_nodes(node_name, update):
                    _print_progress(f"▶ 正在执行：{_label(next_node)}")
    except Exception as exc:
        elapsed = monotonic() - started_at
        _print_progress(f"✗ workflow 报错：{type(exc).__name__}: {exc}")
        _print_progress(f"⏱ 已运行 {elapsed:.1f}s")
        raise

    result = graph.get_state(config).values
    _print_progress(f"✓ workflow 结束，耗时 {monotonic() - started_at:.1f}s")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
