from typing import Any

from langgraph.config import get_stream_writer

from app.graph.state import ProjectState
from app.graph.subgraphs.acceptance import run_acceptance_subgraph
from app.services.project_launcher import launch_project_preview
from app.workspace.spec_documents import workspace_root


def _stream_writer() -> Any:
    """获取 LangGraph custom writer，单元测试或非 Graph 调用时使用空实现。"""

    try:
        return get_stream_writer()
    except RuntimeError:
        return lambda _event: None


def launch_project(state: ProjectState) -> dict:
    """按应用权威数据源类型启动预览，并返回完整启动证据。"""

    root = workspace_root(state).resolve()
    writer = _stream_writer()

    def on_progress(stage: str, status: str, message: str) -> None:
        """把启动阶段转换为稳定的 Graph custom 事件，供前端实时推进步骤。"""

        writer(
            {
                "type": "launch_project.progress",
                "node_name": "launch_project",
                "message": message,
                "detail": {"stage": stage, "status": status},
            }
        )

    launch = launch_project_preview(root, on_progress=on_progress)
    if launch.get("status") == "failed":
        return _failed_project_launch(launch)
    preview_url = launch.get("preview_url")
    # 启动节点返回增量状态时显式携带审查结果和独立构建状态，
    # 让启动过程中的每个快照都能继续渲染三项检查，不依赖隐式 checkpoint 合并。
    review_context = {
        key: state[key]
        for key in (
            "code_review_result",
            "code_review_report_path",
            "code_review_repair_status",
            "code_review_repair_result",
            "code_review_build_results",
            "code_review_repair_iteration",
            "code_review_max_repair_iterations",
        )
        if state.get(key) is not None
    }
    return {
        **review_context,
        "phase": "launch_project",
        "status": "requires_user_input",
        "preview_url": preview_url,
        "launch_result": launch,
        "acceptance_request": {
            "status": "requires_user_input",
            "message": "项目已通过集成测试并启动预览，请用户验收。",
            "preview_url": preview_url,
            "package_json_path": launch.get("package_json_path"),
            "server": launch.get("server"),
        },
        "clarification": {
            "mode": "page_acceptance",
            "status": "requires_user_input",
            "message": "请预览页面并完成最终验收。",
            "questions": [],
        },
        "timeline": ["launch_project"],
    }


def _test_target_record(state: ProjectState) -> dict[str, str]:
    """根据当前构建范围生成测试确认卡需要展示的稳定目标摘要。"""

    scope = state.get("build_execution_scope")
    scope = scope if isinstance(scope, dict) else {}
    target_type = str(scope.get("type") or "application").strip() or "application"
    target_id = str(scope.get("targetId") or scope.get("target_id") or "").strip()
    scope_label = str(
        scope.get("targetLabel")
        or scope.get("target_label")
        or scope.get("label")
        or scope.get("name")
        or ""
    ).strip()
    project_plan = state.get("project_plan")
    project_plan = project_plan if isinstance(project_plan, dict) else {}

    def records(key: str) -> list[dict[str, Any]]:
        """读取项目计划中指定的结构化记录列表。"""

        value = project_plan.get(key)
        if not isinstance(value, list):
            value = state.get(key)
        return (
            [item for item in value if isinstance(item, dict)]
            if isinstance(value, list)
            else []
        )

    def page_records() -> list[dict[str, Any]]:
        """读取当前计划的页面记录，兼容菜单树根节点的运行时投影。"""

        pages = records("pages") or records("frontend_pages")
        flattened: list[dict[str, Any]] = []

        def visit(items: list[dict[str, Any]]) -> None:
            """递归收集页面叶子，保留稳定页面标识和名称。"""

            for item in items:
                children = records_from_value(item.get("children"))
                if children:
                    visit(children)
                else:
                    flattened.append(item)

        visit(pages)
        return flattened

    def record_label(record: dict[str, Any], *, endpoint: bool = False) -> str:
        """从页面、接口或数据源记录中选择用户可读名称。"""

        if endpoint:
            for key in ("label", "name", "title", "display_name"):
                value = str(record.get(key) or "").strip()
                if value:
                    return value
            method = str(
                record.get("method") or record.get("http_method") or ""
            ).strip().upper()
            path = str(
                record.get("path") or record.get("url") or record.get("name") or ""
            ).strip()
            if method and path:
                return f"{method} {path}"
        for key in ("label", "name", "title", "display_name", "path", "id"):
            value = str(record.get(key) or "").strip()
            if value:
                return value
        return ""

    label = ""
    if target_type == "page":
        label = next(
            (
                record_label(item)
                for item in page_records()
                if str(
                    item.get("pageId")
                    or item.get("page_id")
                    or item.get("key")
                    or item.get("id")
                    or ""
                )
                == target_id
            ),
            "",
        )
    elif target_type == "endpoint":
        for contract in records("api_contracts"):
            for endpoint in records_from_value(contract.get("endpoints")):
                endpoint_key = str(
                    endpoint.get("id")
                    or endpoint.get("endpointId")
                    or endpoint.get("endpoint_id")
                    or ""
                )
                if endpoint_key == target_id:
                    label = record_label(endpoint, endpoint=True)
                    break
            if label:
                break
    elif target_type == "data_source":
        label = next(
            (
                record_label(item)
                for item in records("data_sources")
                if str(
                    item.get("id")
                    or item.get("dataSourceId")
                    or item.get("data_source_id")
                    or ""
                )
                == target_id
            ),
            "",
        )

    application_name = str(state.get("application_name") or "").strip()
    return {
        "type": target_type,
        "id": target_id or "application",
        "label": scope_label or label or target_id or application_name or "当前应用",
    }


def records_from_value(value: Any) -> list[dict[str, Any]]:
    """将任意列表值裁剪为结构化记录，避免测试目标摘要读取不可信对象。"""

    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def test_phase_confirmation(state: ProjectState) -> dict:
    """在 Build 与开发阶段单元测试门禁完成后暂停等待用户确认。"""

    target = _test_target_record(state)
    build_summary = state.get("build_summary")
    if (
        not isinstance(build_summary, dict)
        or build_summary.get("status") != "completed"
    ):
        return {
            "phase": "test_phase_confirmation",
            "status": "failed",
            "message": "Build 尚未完成，不能进入测试阶段。",
            "error": "只有 Build 完成后才能进入测试阶段确认。",
            "test_target": target,
            "timeline": ["test_phase_confirmation"],
        }
    if state.get("unit_test_gate_passed") is not True:
        return {
            "phase": "test_phase_confirmation",
            "status": "failed",
            "message": "单元测试门禁尚未完成，不能进入测试阶段。",
            "error": "只有单元测试通过或明确跳过后才能进入测试阶段确认。",
            "test_target": target,
            "timeline": ["test_phase_confirmation"],
        }
    submission = state.get("test_phase_confirmation")
    confirmed = isinstance(submission, dict) and submission.get("action") == "confirm"
    if confirmed:
        return {
            "phase": "test_phase_confirmation",
            "status": "completed",
            "clarification": {},
            "test_target": target,
            "integration_next_action": "integration_test",
            # Build 阶段可能已经消耗过自己的修复次数；进入测试阶段时必须重新
            # 开启独立的集成测试修复预算，不能把开发阶段计数带入 SmallTask。
            "repair_iteration": 0,
            "max_repair_iterations": 3,
            "repair_return_node": "integration_test",
            "timeline": ["test_phase_confirmation"],
        }
    return {
        "phase": "test_phase_confirmation",
        "status": "requires_user_input",
        "clarification": {
            "mode": "test_phase_confirmation",
            "status": "requires_user_input",
            "message": "代码生成、Build 与单元测试门禁已完成，确认后将进入测试阶段，执行测试与失败修复。",
            "testTarget": target,
            "questions": [],
        },
        "test_target": target,
        "integration_next_action": "await_user_input",
        "timeline": ["test_phase_confirmation"],
    }


def _failed_project_launch(launch: dict) -> dict:
    """将任一启动阶段失败统一映射为 Workflow 失败结果。"""

    failure_reason = str(launch.get("message") or "未知启动错误。")
    # 失败状态下前端不会自动导航，复用 preview_url 字段传递可见的失败原因。
    launch["preview_url"] = failure_reason
    return {
        "phase": "launch_project",
        "status": "failed",
        "preview_url": failure_reason,
        "launch_result": launch,
        "acceptance_request": {
            "status": "failed",
            "message": f"项目启动失败：{failure_reason}",
            "preview_url": failure_reason,
        },
        "timeline": ["launch_project"],
    }


def acceptance(state: ProjectState) -> dict:
    """执行验收子图，统一承接项目启动与用户验收等待。"""

    return run_acceptance_subgraph(state)


def finalize_project(state: ProjectState) -> dict:
    return {
        "phase": "completed",
        "status": "completed",
        "timeline": ["finalize_project"],
    }


def handle_failure(state: ProjectState) -> dict:
    """保留上游失败原因并统一结束失败工作流。"""

    return {
        "phase": "failed",
        "status": "failed",
        "message": state.get("message") or "Workflow 执行失败。",
        "error": state.get("error"),
        "timeline": ["handle_failure"],
    }
