"""跨迭代交接 TechnicalPlan 的"上一迭代基线"，供新一轮做变更对比展示。

**为什么需要它**：`iteration_service.start_iteration` 会清掉 `.devagentstudio/plans/`
（见 `_CLEARABLE_DIRS`），其中就包括上一轮**已确认**的 `plans/technical-plan.json`。
而计划阶段的 `technical_planning_begin` 节点把基线取自 `state["technical_plan"]`——
启动值里已经没有任何东西能填它了，于是基线是空 dict。前端 `TechnicalPlanReviewPanel`
凭基线是否为空决定要不要出"本次变更 / 完整规划"切换与"新增/修改/未变更"标识，
基线一空，用户在新迭代的计划阶段就看不到任何变更标记。

本文件（`.devagentstudio/technical-plan-carryover.json`）与 `requirement-spec-carryover.json`、
`product-plan-carryover.json` 同理，**刻意不放进 `_CLEARABLE_DIRS`**：它是跨迭代的
基线事实，不是本轮产物。新一轮 `technical_planning_begin` 在 state 里没有
`technical_plan` 时回退读本文件，把上一轮已确认的技术规划作为对比基线。

**只登记已确认版本**：`pending_user_confirmation` 的待确认稿描述的是"本轮没做完的事"，
它不是任何迭代的正式产物，拿来当基线会把半成品当成"上一轮的现状"。

`drop_generated_fields` 剥掉的是**每一轮都会变的派生字段**：确认状态、以及两个上游产物的
sha256（`product_plan_sha256` / `ui_designs_sha256`）。它们在新一轮必然不同，留着会让
前端 diff 把架构层之外的东西全部标成"修改"，掩盖真正的业务变更。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.technical_plan_merger import CHANGE_STATUS_FIELD, strip_change_status

logger = logging.getLogger("uvicorn.error")

TECHNICAL_PLAN_CARRYOVER_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / "technical-plan-carryover.json"

_SCHEMA_VERSION = 1

# 每轮必然变化、不承载业务语义的派生字段；对比基线时必须剥掉。
GENERATED_FIELDS = frozenset({"confirmation_status", "product_plan_sha256", "ui_designs_sha256"})


def technical_plan_carryover_path(workspace: str | Path) -> Path:
    """返回工作区的技术规划交接记录文件路径。"""

    return Path(workspace).expanduser().resolve() / TECHNICAL_PLAN_CARRYOVER_RELATIVE_PATH


def read_technical_plan_carryover(workspace: str | Path) -> dict[str, Any] | None:
    """读取基线 TechnicalPlan（完整一份），失败返回 None。

    返回 None 表示"没有基线"（首轮创建 / 上一迭代没产出已确认技术规划 / 文件被外部破坏），
    调用方按"首轮创建"处理：不做跨迭代对比，也不展示变更标识。
    """

    try:
        payload = json.loads(
            technical_plan_carryover_path(workspace).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    plan = payload.get("plan")
    return plan if isinstance(plan, dict) and plan else None


def write_technical_plan_carryover(
    workspace: str | Path,
    *,
    source_branch: str,
    plan: dict[str, Any] | None,
) -> None:
    """登记本轮的技术规划作为下一轮的基线；写失败只记日志。

    `plan` 为 None 时**清掉**已有记录：那说明本轮没有可沿用的已确认产物（首轮创建、
    或本轮还没确认技术规划），留着旧记录会让下一轮错误地以为有基线。
    """

    path = technical_plan_carryover_path(workspace)
    if plan is None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("technical_plan_carryover_clear_failed")
        return

    payload = {
        "schemaVersion": _SCHEMA_VERSION,
        "sourceBranch": str(source_branch or "").strip(),
        "plan": plan,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        logger.exception("technical_plan_carryover_persist_failed")


def drop_generated_fields(plan: dict[str, Any] | None) -> dict[str, Any]:
    """剥掉每轮必然变化的派生字段，只留可做业务对比的内容。

    同时剥掉 `_change_status`：外部数据源（例如配置仓库里的 `plans/technical-plan.json`）
    可能残留上一轮的展示标记。它在基线里是噪音 —— 如果当前输出没有这个键，每个对象都会
    因为"少了一个键"被判成修改；如果两边都有，又会被判成"标记不同"。基线只保留业务事实。
    """

    if not isinstance(plan, dict):
        return {}
    cleaned = {
        key: value for key, value in plan.items()
        if key not in GENERATED_FIELDS and key != CHANGE_STATUS_FIELD
    }
    return strip_change_status(cleaned)
