"""开发目标的入口门禁：删除目标走删除节点，其余目标进入 Endpoint 映射门禁。"""

from __future__ import annotations

from typing import Any

from app.graph.state import ProjectState
from app.graph.nodes.api_design import api_design_readiness_gate
from app.graph.nodes.common import workspace_from_state


def development_readiness_gate(state: ProjectState) -> dict:
    """统一旧显式入口到 Endpoint 字段映射门禁，不再要求独立实体设计。"""

    return api_design_readiness_gate(state)


def delete_development_page_if_deleted(state: ProjectState) -> str:
    """判断当前开发目标是否为本轮已删除的页面，是则返回 pageId。

    **为什么要单独判断**：已删除页面不在开发产物目录里（`catalog_targets` 会剔除），
    常规开发流程既不会为它生成任务，也不会清理基线里遗留的代码与设计稿。
    用户点进它时应当执行"删除"而不是"开发"。
    """

    page_id = str(state.get("selectedPageId") or "").strip()
    if not page_id:
        return ""
    workspace = workspace_from_state(state)
    if not workspace:
        return ""
    from app.services.page_deletion import deleted_page_targets

    return page_id if page_id in deleted_page_targets(workspace) else ""


def delete_development_endpoint_if_deleted(state: ProjectState) -> str:
    """判断当前开发目标是否为本轮已删除的接口，是则返回 `contract::endpoint`。

    与页面同理：已删除接口退出开发产物目录后，基线里生成的前端 API 客户端与
    后端模块不会自己消失。用户点进它时应当执行"删除"而不是"开发"。

    已删除页面的专属接口会被级联标为 deleted；那种情况下页面自身已经是删除目标，
    页面节点会连带清理，这里仍返回接口键并不冲突——两者都只清理各自的产物。
    """

    endpoint_id = str(state.get("selected_endpoint_id") or "").strip()
    contract_id = str(state.get("selected_api_contract_id") or "").strip()
    if not endpoint_id or not contract_id:
        return ""
    workspace = workspace_from_state(state)
    if not workspace:
        return ""
    from app.services.endpoint_deletion import deleted_endpoint_targets

    return (
        f"{contract_id}::{endpoint_id}"
        if f"{contract_id}::{endpoint_id}" in deleted_endpoint_targets(workspace)
        else ""
    )


def delete_development_endpoint(state: ProjectState) -> dict:
    """删除本轮已删除接口的代码与契约残留。

    删除同样是确定性操作：目标由 `(contractId, endpointId)` 推导，不调用模型。
    执行后该接口已不在开发产物目录里，因此直接产出与页面删除相同的测试确认门，
    让本轮其余产物照常推进到测试 → 审查 → 验收。
    """

    import logging

    workspace = workspace_from_state(state)
    if not workspace:
        raise ValueError("删除接口必须提供 workspaceRoot。")
    contract_id = str(state.get("selected_api_contract_id") or "").strip()
    endpoint_id = str(state.get("selected_endpoint_id") or "").strip()
    if not contract_id or not endpoint_id:
        raise ValueError("删除接口缺少 apiContractId 或 endpointId。")

    from app.services.endpoint_deletion import delete_endpoint_artifacts, preview_endpoint_deletion

    # 重复进入或上一轮已清理时不报错：删除应当幂等。
    preview = preview_endpoint_deletion(workspace, contract_id, endpoint_id)
    result: dict[str, Any] = {
        "apiContractId": contract_id,
        "endpointId": endpoint_id,
        "endpointLabel": preview.endpoint_label,
        "files": [],
        "contractRemoved": False,
        "referencedBy": preview.referenced_by,
    }
    if preview.removable:
        executed = delete_endpoint_artifacts(workspace, contract_id, endpoint_id)
        result.update({
            "files": executed.get("files") or [],
            "contractRemoved": executed.get("contractRemoved") is True,
        })

    if result["referencedBy"]:
        # 只提示不自动改写：其它页面里对该接口函数的调用属于别人的业务代码。
        logging.getLogger("uvicorn.error").warning(
            "deleted_endpoint_still_referenced: %s -> %s", endpoint_id, result["referencedBy"]
        )

    # 清理后重新读取残留，部分接口尚未移除实现/调用时不能冒充开发完成。
    remaining = preview_endpoint_deletion(workspace, contract_id, endpoint_id)
    result["referencedBy"] = remaining.referenced_by
    result["completed"] = not remaining.removable and not remaining.referenced_by
    if result["files"]:
        message = (
            f"已删除接口「{result['endpointLabel']}」：{len(result['files'])} 项产物已清理。"
            + ("该接口分组已无接口，整组模块一并清理。" if result["contractRemoved"] else "")
        )
    else:
        message = (
            f"接口「{result['endpointLabel']}」所在分组仍有其它接口，本轮不改动生成代码；"
            "请按引用清单手动移除残留调用。"
        )
    return _development_deletion_result(
        state, phase="delete_development_endpoint", result_key="deleted_endpoint_result",
        summary_key="deletedEndpoint", result=result, message=message,
    )


def _development_deletion_result(
    state: ProjectState,
    *,
    phase: str,
    result_key: str,
    summary_key: str,
    result: dict[str, Any],
    message: str,
) -> dict:
    """删除类节点统一的收尾：要么产出测试确认门，要么结束本轮并交接开发目标。

    删除没有代码生成，也就没有 Build 任务计划。`test_phase_confirmation` 的前置
    契约要求 `build-task-plan.json`，直接跳过去会被判成"任务计划不完整"并触发自动重试，
    把用户重新带回开发门禁。因此这里自己产出同样的测试确认门，后续仍是
    测试 → 审查 → 验收，只是跳过"确认构建产物"这一步。

    `result_key` / `summary_key` 让页面与接口两种删除共用同一套收尾，同时各自
    保留前端已认识的结果字段名。
    """

    from app.graph.nodes.lifecycle import _test_target_record
    from app.services.development_artifacts import (
        complete_scope_target_development, refresh_development_artifacts, test_entry_gate,
    )

    workspace = workspace_from_state(state)
    if result.get("completed") is False:
        # 当前目标仍未完成，但不能把用户锁在一个没有下一步的通用确认页。
        # 与常规部分完成一致：结束当前 Run，保留变更快照与残留引用，交由“继续开发”
        # 卡片展示其它页面/接口；当前目标仍在 testEntryGate.blockers 中，不会被误标完成。
        gate = test_entry_gate(refresh_development_artifacts(workspace))
        target = _test_target_record(state)
        stopped_message = "接口删除尚未完成，可继续开发其他页面或接口。"
        return {
            "phase": phase,
            "status": "completed",
            result_key: result,
            "build_summary": {"status": "completed", "message": stopped_message, summary_key: result},
            "clarification": {
                "mode": "development_target_selection",
                "message": stopped_message + (f" {gate.reason}" if gate.reason else ""),
                "testEntryGate": gate.model_dump(mode="json", by_alias=True),
                "referencedBy": result.get("referencedBy") or [],
            },
            "test_target": target,
            "integration_next_action": "end",
            "message": stopped_message,
            "timeline": [phase],
        }
    result = {**result, "completed": True}
    # 删除也是本轮开发任务，清理成功后登记唯一目标；页面完成不能替接口删除记账。
    complete_scope_target_development(
        workspace, scope=state.get("build_execution_scope"),
        run_id=str(state.get("active_run_id") or ""), thread_id=str(state.get("active_thread_id") or ""),
    )
    gate = test_entry_gate(refresh_development_artifacts(workspace))
    target = _test_target_record(state)
    if not gate.allowed:
        # 其余产物还没开发完：与常规开发节点一致，结束本轮并引导用户继续开发其他目标。
        stopped_message = f"{message} 本轮开发已完成，可继续开发其他目标。"
        return {
            "phase": phase,
            "status": "completed",
            result_key: result,
            "build_summary": {"status": "completed", "message": stopped_message, summary_key: result},
            "clarification": {
                "mode": "development_target_selection",
                "message": gate.reason or "仍有产物未完成开发，请继续开发其它目标。",
                "testEntryGate": gate.model_dump(mode="json", by_alias=True),
            },
            "test_target": target,
            "integration_next_action": "end",
            "message": stopped_message,
            "timeline": [phase],
        }
    submission = state.get("test_phase_confirmation")
    confirmed = isinstance(submission, dict) and submission.get("action") == "confirm"
    return {
        "phase": "test_phase_confirmation",
        "status": "completed" if confirmed else "requires_user_input",
        result_key: result,
        "build_summary": {"status": "completed", "message": message, summary_key: result},
        "unit_test_gate_passed": True,
        "test_phase_confirmation": {},
        "test_target": target,
        "clarification": {} if confirmed else {
            "mode": "test_phase_confirmation",
            "status": "requires_user_input",
            "message": message + "全部开发产物已完成，确认后进入测试阶段。",
            "testEntryGate": gate.model_dump(mode="json", by_alias=True),
            "testTarget": target,
            "questions": [],
        },
        # 已确认则直接进入集成测试；否则停在确认门等待用户点击。
        "integration_next_action": "integration_test" if confirmed else "await_user_input",
        "repair_iteration": 0,
        "max_repair_iterations": 3,
        "repair_return_node": "integration_test",
        "timeline": [phase],
        "message": message,
    }


def delete_development_page(state: ProjectState) -> dict:
    """删除本轮已删除页面的代码、路由注册与 UI 设计稿。

    删除是确定性操作：目标文件由 pageId 推导，不调用模型。执行后该目标既不在
    开发产物目录里，也不会出现在构建范围内，因此直接进入测试阶段确认节点，
    让本轮其余（未变更即已完成）产物照常推进到测试 → 审查 → 验收。
    """

    workspace = workspace_from_state(state)
    if not workspace:
        raise ValueError("删除页面必须提供 workspaceRoot。")
    page_id = str(state.get("selectedPageId") or "").strip()
    if not page_id:
        raise ValueError("删除页面缺少 selectedPageId。")

    from app.services.page_deletion import delete_page_artifacts, preview_page_deletion

    # 已删过（重复进入、或上一轮已清理）时不再报错：删除应当幂等，
    # 否则用户重试就会看到一个"没有可删除内容"的失败。
    preview = preview_page_deletion(workspace, page_id)
    result: dict[str, Any] = {
        "pageId": page_id,
        "pageLabel": preview.page_label,
        "directories": [],
        "routeEntries": [],
        "referencedBy": preview.referenced_by,
    }
    if preview.removable:
        executed = delete_page_artifacts(workspace, page_id)
        result.update({
            "directories": executed.get("directories") or [],
            "routeEntries": executed.get("routeEntries") or [],
        })

    if result["referencedBy"]:
        # 只提示不自动改写：其它页面里指向被删页面的链接属于别人的业务代码。
        import logging

        logging.getLogger("uvicorn.error").warning(
            "deleted_page_still_referenced: %s -> %s", page_id, result["referencedBy"]
        )

    removed = len(result["directories"]) + len(result["routeEntries"])
    message = (
        f"已删除页面「{result['pageLabel']}」：{removed} 项产物已清理。"
        if removed
        else f"页面「{result['pageLabel']}」没有需要删除的产物，跳过清理。"
    )
    return _development_deletion_result(
        state, phase="delete_development_page", result_key="deleted_page_result",
        summary_key="deletedPage", result=result, message=message,
    )
