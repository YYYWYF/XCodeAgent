"""跨迭代交接完整 ProductPlan 与稳定页面定义，避免新需求重写无关内容。

完整 `plan` 快照与保留的 RequirementSpec 共同构成新迭代联合 ChangeSet 基线；模型只修改
用户明确要求的节点，其余信息项、操作和验收内容原样保留。页面 `definition` 仍用于开发
变更快照，以及没有完整产品基线时的生成兜底，避免自由重写导致 `itemId` 漂移、旧 UI 设计稿
无法继承。

页面兜底沿用的判定以 RequirementSpec 页面身份与功能描述为准；只覆盖 goal、information_items、
actions、navigation_targets 等补充字段，path/name/module_id/description 始终取当前需求。

本文件（`.devagentstudio/product-plan-carryover.json`）刻意不放进清空清单：它是跨迭代事实，
不是本轮待确认产物。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR

logger = logging.getLogger("uvicorn.error")

PRODUCT_PLAN_CARRYOVER_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / "product-plan-carryover.json"

_SCHEMA_VERSION = 2

# 判断"用户改没改这一页"时比较的需求字段：决定**页面身份与路由**的那几个，
# 加上 description —— 它在本工作流里是 spec 中**用户对这页的需求描述**（"展示一个按钮，
# 按钮文案是打开github"），不是模型自由措辞。description 变化时意味着用户重写了
# 这页的功能定位（哪怕只是改措辞），沿用旧 definition 会让 ProductPlan 停留在旧语义，
# 后续 UI 设计稿继承、构建、技术规划都会基于错位的 product fact 推进。
#
# 之前刻意不含 description 是因为担心"模型每轮自由重写"导致继承失效；但实测发现
# 真正担心的是 description 不变但页面内容变化的场景（用户在迭代里完全推翻重做一页
# 而 pageId 不变，例如把"展示 hello world"改成"展示打开 GitHub 按钮"）—— 这种
# 语义级别的变更必须重新走 ProductPlan 生成，不能沿用。
#
# `module_id` 仍不参与比对：它是模型自由重写的分组标签，与页面业务事实无关。
_STRUCTURAL_REQUIREMENT_FIELDS = ("pageId", "name", "path", "description")

# 沿用的字段：模型在 ProductPlan 页面里补充、而 RequirementSpec 不提供的那部分。
# 与 `product_plan._normalized_pages` 读取的 `supplement` 完全对应。
# state_requirements 不再沿用：页面状态由 UI 设计按需决定，不作为产品规划沿用字段。
DEFINITION_FIELDS = (
    "goal",
    "information_items",
    "actions",
    "navigation_targets",
)

# 页面里需要保持稳定的 ID 字段：(ProductPlan 页面字段, 条目上的 ID 键)
# 前两者不同名是历史原因 —— 页面里叫 information_items / actions，ID 键叫 itemId / actionId。
_ID_FIELDS: tuple[tuple[str, str], ...] = (
    ("information_items", "itemId"),
    ("actions", "actionId"),
)


def product_plan_carryover_path(workspace: str | Path) -> Path:
    """返回工作区的 ProductPlan 交接记录文件路径。"""

    return Path(workspace).expanduser().resolve() / PRODUCT_PLAN_CARRYOVER_RELATIVE_PATH


def read_product_plan_carryover(workspace: str | Path) -> dict[str, dict[str, Any]]:
    """读取上一轮登记的页面定义。

    返回 `{pageId: {"requirement": {...}, "development_page": {...}, "definition": {...}}}`。

    文件缺失或损坏时按"没有记录"处理，而不是报错：沿用是让用户少做重复工作的增强，
    读不出来时退回"本轮按模型给的走"即可，不该因此阻断规划阶段。
    """

    try:
        payload = json.loads(
            product_plan_carryover_path(workspace).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, dict):
        return {}

    carried: dict[str, dict[str, Any]] = {}
    for page_id, record in raw_pages.items():
        if not isinstance(record, dict):
            continue
        requirement = record.get("requirement")
        definition = record.get("definition")
        # 两半缺一不可：没有 requirement 无从判断"变没变"，没有 definition 没有可沿用的内容。
        if not isinstance(requirement, dict) or not isinstance(definition, dict):
            continue
        carried[str(page_id)] = {
            "requirement": requirement,
            "development_page": record.get("development_page"),
            "definition": {
                field: definition.get(field)
                for field in DEFINITION_FIELDS
                if field in definition
            },
        }
    return carried


def read_product_plan_iteration_baseline(workspace: str | Path) -> dict[str, Any] | None:
    """读取新迭代联合 ChangeSet 所需的上一轮完整 ProductPlan 基线。"""

    try:
        payload = json.loads(
            product_plan_carryover_path(workspace).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    plan = payload.get("plan")
    return plan if isinstance(plan, dict) and plan else None


def write_product_plan_carryover(
    workspace: str | Path,
    *,
    source_branch: str,
    pages: dict[str, dict[str, Any]],
    plan: dict[str, Any] | None = None,
) -> None:
    """登记下一轮的产品页面定义及联合修订基线；写失败只记日志。

    `pages` 为空时**清掉**已有记录：那说明本轮没有可沿用的定义，
    留着旧记录会让下一轮沿用上一轮（或更早）的内容。
    """

    path = product_plan_carryover_path(workspace)
    if not pages:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("product_plan_carryover_clear_failed")
        return

    payload = {
        "schemaVersion": _SCHEMA_VERSION,
        "sourceBranch": str(source_branch or "").strip(),
        "pages": {
            str(page_id): {
                "requirement": record.get("requirement") or {},
                "development_page": record.get("development_page"),
                "definition": {
                    field: record["definition"].get(field)
                    for field in DEFINITION_FIELDS
                    if field in (record.get("definition") or {})
                },
            }
            for page_id, record in pages.items()
        },
    }
    if isinstance(plan, dict) and plan:
        # 新迭代直接修订需要完整业务基线，不能只靠页面定义重新猜出产品级内容。
        payload["plan"] = plan
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        # 增强能力：写不进去也不能中断迭代发起。
        logger.exception("product_plan_carryover_persist_failed")


def carried_definition_for_page(
    carried: dict[str, dict[str, Any]],
    page_id: str,
    requirement_page: Any,
) -> dict[str, Any] | None:
    """若这一页的**身份与功能定位**与上一轮相同，返回可沿用的上一轮定义。

    比对 `pageId` / `name` / `path` / `description`（见 `_STRUCTURAL_REQUIREMENT_FIELDS`）。
    description 关键：它承载"用户对这页的功能要求"（如"展示 hello world" vs
    "展示打开 GitHub 按钮"）。同一 pageId 下 description 变了 = 用户改需求了，
    沿用旧 definition 会让 ProductPlan 与 spec 脱节，UI 设计稿继承、技术规划都会错位。

    `module_id` 不参与比对：它是模型自由重写的分组标签，与页面业务事实无关。

    缺任一前提（没记录 / 没定义 / 任一身份字段变了）都返回 None，让 LLM 重新生成。
    """

    record = carried.get(str(page_id or "").strip())
    if not record:
        return None
    if not isinstance(requirement_page, dict):
        return None
    previous = record.get("requirement")
    if not isinstance(previous, dict):
        return None
    if any(
        not _deep_equal(previous.get(field), requirement_page.get(field))
        for field in _STRUCTURAL_REQUIREMENT_FIELDS
    ):
        return None
    definition = record.get("definition")
    return definition if isinstance(definition, dict) and definition else None


def reconcile_product_plan_ids(
    plan: dict[str, Any],
    carried: dict[str, dict[str, Any]],
) -> int:
    """把新计划里漂移的页面 ID 还原成上一轮的稳定 ID，返回改动的条目数。

    这条兜底路径服务的是**需求确实变了**的页面 —— 整份沿用的情形已在
    `_normalized_pages` 里处理掉了，那时 ID 本来就一致，这里自然无事可做。

    对每一页（`information_items` 与 `actions` 各做一次）用**保守规则**：

    1. 新旧 ID 完全相同的条目 → 直接保留（无歧义）。
    2. 剩下**恰好一个**旧 ID 未被认领、且**恰好一个**新条目未匹配上 → 把那个旧 ID 给它
       （单点改名，无歧义）。
    3. 其余情况（多个未匹配）→ 保持模型给的新 ID 不动。

    规则 3 是刻意保守的：多项同时变动说明这一页真的改了，此时宁可让继承失败、由用户
    重新生成，也不要把旧 ID 错配到新信息项上 —— 错配会静默传播到设计稿与生成代码，
    比让用户重做一次糟得多。

    就地修改 `plan`；`plan` 不是预期结构时原样返回。
    """

    if not isinstance(plan, dict) or not carried:
        return 0
    raw_pages = plan.get("pages")
    if not isinstance(raw_pages, list):
        return 0

    changed = 0
    for page in raw_pages:
        if not isinstance(page, dict):
            continue
        record = carried.get(str(page.get("pageId") or "").strip())
        if not record:
            continue
        definition = record.get("definition") or {}
        for page_field, entry_key in _ID_FIELDS:
            previous_ids = _entry_ids(definition.get(page_field), entry_key)
            changed += _reconcile_entries(page.get(page_field), entry_key, previous_ids)
    return changed


def _entry_ids(entries: Any, entry_key: str) -> list[str]:
    """按原顺序取出条目里的稳定 ID，跳过空值。"""

    if not isinstance(entries, list):
        return []
    return [
        str(entry.get(entry_key) or "").strip()
        for entry in entries
        if isinstance(entry, dict) and str(entry.get(entry_key) or "").strip()
    ]


def _reconcile_entries(entries: Any, entry_key: str, previous_ids: list[str]) -> int:
    """对一页里某一类条目（信息项 / 操作）做 ID 还原，返回改动数。"""

    if not isinstance(entries, list) or not entries or not previous_ids:
        return 0

    previous = [item_id for item_id in previous_ids if item_id]
    if not previous:
        return 0

    # 模型自己给的 ID 与上一轮相同的条目先落定。
    current_ids = [
        str(entry.get(entry_key) or "").strip() if isinstance(entry, dict) else ""
        for entry in entries
    ]
    unmatched = [index for index, item_id in enumerate(current_ids) if item_id not in previous]
    unclaimed = [item_id for item_id in previous if item_id not in current_ids]

    # 规则 3：不是"一对一改名"就整体放弃，避免错配。
    if len(unmatched) != 1 or len(unclaimed) != 1:
        return 0

    entries[unmatched[0]][entry_key] = unclaimed[0]
    return 1


def _deep_equal(left: Any, right: Any) -> bool:
    """JSON 语义下的深度相等：忽略键顺序，只比较内容。"""

    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False
    )
