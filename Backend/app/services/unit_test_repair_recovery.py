"""在既有单测修复额度内恢复 Agent 输出协议错误。"""

from typing import Any

from app.services.unit_test_repair_budget import UNIT_TEST_REPAIRS_PER_CHECK


def recover_unit_test_output_failure(
    state: dict[str, Any],
    failure: dict[str, Any],
    batch_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """协议失败先复测再重新规划；业务失败、越权和额度耗尽仍明确停止。"""

    failed = [item for item in batch_results if item.get("status") == "failed"]
    if not failed or any(item.get("failureCode") != "invalid_agent_output" for item in failed):
        return failure
    failed_ids = {item.get("taskId") for item in failed}
    check_ids = {
        str(task.get("source_ref", {}).get("failed_check_id") or "")
        for task in failure.get("small_task_tasks", [])
        if task.get("id") in failed_ids
    }
    if not check_ids or "" in check_ids:
        return failure
    attempts = state.get("unit_test_repair_attempts") or {}
    exhausted = sorted(
        check for check in check_ids if attempts.get(check, 0) >= UNIT_TEST_REPAIRS_PER_CHECK
    )
    if exhausted:
        return {**failure, "message": (
            f"{failure['message']} 以下单元测试子步骤已用完各 {UNIT_TEST_REPAIRS_PER_CHECK} 次修复额度："
            f"{'、'.join(exhausted)}。"
        )[:2_000]}
    # 不把协议错误视作修复成功，也不重复派发旧任务；真实复测后由原 RepairPlanner 生成新计划。
    return {
        **failure,
        "status": "in_progress",
        "message": f"{failure['message']} 将重新执行单元测试，并在剩余额度内继续局部修复。"[:2_000],
        "small_task_route": "unit_test",
        "unit_test_next_action": "unit_test",
        "unit_test_repair_iteration": int(state.get("unit_test_repair_iteration", 0) or 0) + 1,
        "error": None,
    }
