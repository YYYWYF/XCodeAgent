"""按迭代（分支）记录 UI 设计稿的产出与计划事实。

**为什么需要单独一个文件**：`specs/` 与 `ui-design/` 都在发起新迭代时被清空
（见 `iteration_service._CLEARABLE_DIRS`），而"某个页面是在哪一轮设计过的"
这个事实必须**跨迭代保留** —— 否则进入 v1.1 后，界面上无法回答
"这个页面是 v1.0 做的还是本轮新增的"、"哪个版本该设计却没设计"。

本文件（`.devagentstudio/iteration-artifacts.json`）**刻意不放进清空清单**，
它是版本维度的历史，随应用长期存在。

只记录**事实**，不参与门禁与执行范围判定。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.repository_branch import read_workspace_branch_name

logger = logging.getLogger("uvicorn.error")

ITERATION_ARTIFACTS_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / "iteration-artifacts.json"

_SCHEMA_VERSION = 2


def iteration_artifacts_path(workspace: str | Path) -> Path:
    """返回工作区的迭代产物记录文件路径。"""

    return Path(workspace).expanduser().resolve() / ITERATION_ARTIFACTS_RELATIVE_PATH


def read_iteration_artifacts(workspace: str | Path) -> list[dict[str, Any]]:
    """读取全部轮次记录（由旧到新）；文件缺失或损坏时返回空列表。

    返回 `[{"branch": "v1.0", "plannedPageIds": [...], "designedPageIds": [...]}, ...]`。

    读不出来时**按空历史处理**而不是报错：这只是标注用的辅助信息，
    不该因为它缺失就阻断设计阶段。空历史会让界面退化成"未知归属"，不会误标。
    """

    path = iteration_artifacts_path(workspace)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    raw_rounds = payload.get("rounds")
    if not isinstance(raw_rounds, list):
        return []

    rounds: list[dict[str, Any]] = []
    for record in raw_rounds:
        if not isinstance(record, dict):
            continue
        rounds.append(
            {
                "branch": str(record.get("branch") or "").strip(),
                "plannedPageIds": _string_list(record.get("plannedPageIds")),
                "designedPageIds": _string_list(record.get("designedPageIds")),
            }
        )
    return rounds


def begin_iteration_round(workspace: str | Path, branch_name: str) -> None:
    """登记新一轮的开始：追加一条空轮次记录。

    这是"新一轮开始"的**唯一信号**。不能靠"末轮分支名是否等于当前分支"来判断 ——
    用户选「继续当前版本」时分支名不变，那样会把新一轮的产出写进上一轮、抹掉上一轮的事实。
    追加之后 `record_iteration_artifacts` 只更新末轮。

    写失败只记日志：归属标注是辅助信息，不该阻断发起迭代。
    """

    rounds = read_iteration_artifacts(workspace)
    rounds.append(
        {
            "branch": str(branch_name or "").strip(),
            "plannedPageIds": [],
            "designedPageIds": [],
        }
    )
    _write_rounds(workspace, rounds)


def record_iteration_artifacts(
    workspace: str | Path,
    *,
    planned_page_ids: list[str] | None = None,
    designed_page_ids: list[str] | None = None,
) -> None:
    """把本轮的页面清单写进**末轮**记录；写失败只记日志，不影响设计流程。

    `planned_page_ids` 传 None 表示本次不改动该字段（保留已有值），
    传列表则整体替换 —— 设计与计划都会演进，要的是"当前这一轮的事实"。

    只更新末轮：轮次由 `begin_iteration_round` 在发起迭代时追加，这里不再按分支名
    去认领记录（同一分支上的多轮必须各记各的）。末轮缺失时补一条，保证单独调用也可用。
    """

    rounds = read_iteration_artifacts(workspace)
    if not rounds:
        rounds.append(
            {
                "branch": read_workspace_branch_name(workspace),
                "plannedPageIds": [],
                "designedPageIds": [],
            }
        )
    record = rounds[-1]
    if planned_page_ids is not None:
        record["plannedPageIds"] = _dedupe(planned_page_ids)
    if designed_page_ids is not None:
        record["designedPageIds"] = _dedupe(designed_page_ids)
    _write_rounds(workspace, rounds)


def _write_rounds(workspace: str | Path, rounds: list[dict[str, Any]]) -> None:
    """把轮次列表落盘；写失败只记日志，不中断设计流程。"""

    payload = {
        "schemaVersion": _SCHEMA_VERSION,
        "rounds": rounds,
    }
    try:
        path = iteration_artifacts_path(workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        # 标注是辅助信息：写不进去也不能中断设计阶段。
        logger.exception("iteration_artifacts_persist_failed")


def iteration_origins_for_pages(
    workspace: str | Path, page_ids: list[str]
) -> dict[str, dict[str, str]]:
    """算出每个页面在**历史轮次**（不含本轮）里的归属事实。

    返回 `{pageId: {"designedIn": "v1.0"}}` 或 `{pageId: {"plannedButUndesignedIn": "v1.0"}}`；
    两样都不占的页面不出现在结果里（调用方按"本轮新增"处理）。

    - `designedIn`：最近一次**真的产出了设计稿**的历史轮次 —— 回答"这个页面上次是哪个版本做的"
    - `plannedButUndesignedIn`：最近一次**计划了却没设计**的历史轮次 —— 回答"哪个版本该设计没设计"

    同一页面两者都占时以 `designedIn` 为准：它确实被做出来过，比"没做"更值得说。

    判据是"**末轮之外**的全部轮次"，不是"分支名不等于当前分支"：用户选「继续当前版本」时
    上一轮的产出正记在**同一个分支名**下，按分支名排除会把它们一起排掉，标注就永远出不来。

    历史为空（老工作区、或首轮）时返回空字典 —— 调用方据此退化成"本轮新增"，
    不会误标成旧迭代。
    """

    wanted = {str(page_id).strip() for page_id in page_ids if str(page_id).strip()}
    if not wanted:
        return {}

    # 末轮即本轮（由 begin_iteration_round 追加），算归属时排除。
    history = read_iteration_artifacts(workspace)[:-1]

    designed_in: dict[str, str] = {}
    undesigned_in: dict[str, str] = {}
    # 由旧到新遍历，后者覆盖前者 → 最终留下的是**最近**那一轮。
    for record in history:
        branch_name = record["branch"]
        for page_id in record["designedPageIds"]:
            if page_id in wanted:
                designed_in[page_id] = branch_name
        for page_id in record["plannedPageIds"]:
            if page_id in wanted and page_id not in record["designedPageIds"]:
                undesigned_in[page_id] = branch_name

    origins: dict[str, dict[str, str]] = {}
    for page_id in wanted:
        if page_id in designed_in:
            origins[page_id] = {"designedIn": designed_in[page_id]}
        elif page_id in undesigned_in:
            origins[page_id] = {"plannedButUndesignedIn": undesigned_in[page_id]}
    return origins


def _string_list(value: Any) -> list[str]:
    """把未知值收敛成去重后的非空字符串列表。"""

    if not isinstance(value, list):
        return []
    return _dedupe([str(item) for item in value])


def _dedupe(values: list[str]) -> list[str]:
    """去重并去掉空串，保持原有顺序。"""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
