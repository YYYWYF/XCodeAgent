"""开发单元测试按检查 ID 分别计费，确认恢复不重复消耗同轮额度。"""

from typing import Any

UNIT_TEST_REPAIRS_PER_CHECK = 4


def unit_test_repair_attempts(state: dict[str, Any]) -> dict[str, int]:
    """读取服务端保存的各检查修复次数，返回独立副本。"""
    return dict(state.get("unit_test_repair_attempts") or {})


def failed_check_repair_iteration(state: dict[str, Any]) -> int:
    """仅当前失败检查参与预算判断，其他检查用完额度不影响本次修复。"""
    attempts = unit_test_repair_attempts(state)
    return max((
        attempts.get(str(check.get("id") or ""), 0)
        for check in state.get("test_results", [])
        if not check.get("passed") and check.get("blocking", True)
    ), default=0)


def charge_unit_test_repair_batch(
    state: dict[str, Any], tasks: list[dict[str, Any]],
) -> str | None:
    """派发前按实际修复目标计费；同轮多任务及范围确认续接只扣一次。"""
    check_ids = {
        str(task.get("source_ref", {}).get("failed_check_id") or "")
        for task in tasks
    }
    if "" in check_ids:
        return "单元测试修复任务缺少失败检查 ID，不能派发。"
    charged = set(state.get("unit_test_repair_charged_checks") or [])
    new_checks = check_ids - charged
    attempts = unit_test_repair_attempts(state)
    exhausted = sorted(check for check in new_checks if attempts.get(check, 0) >= UNIT_TEST_REPAIRS_PER_CHECK)
    if exhausted:
        return f"以下单元测试子步骤已用完各 4 次修复额度：{'、'.join(exhausted)}。"
    for check in new_checks:
        attempts[check] = attempts.get(check, 0) + 1
    state["unit_test_repair_attempts"] = attempts
    state["unit_test_repair_charged_checks"] = sorted(charged | check_ids)
    return None
