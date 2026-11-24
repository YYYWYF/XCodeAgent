"""把"LLM 重新生成的 RequirementSpec"与"基线 RequirementSpec"按 pageId 合并。

**为什么需要它**：LLM 在新迭代里**整份重新生成** spec，但它会"自由发挥"——
简化、重写、甚至丢失基线里已有的页面。这会让上一迭代实现的页面在新迭代里"消失"，
下游 UI 设计稿继承、技术规划、构建都基于错位的 spec 推进。

本模块提供的合并是**保守**的：
- 新增（added）：LLM 输出里有但基线没有的页面 → 追加，标记 `_change_status="added"`；
- 修改（modified）：两边都有但内容不同 → 用 LLM 的版本，标记 `_change_status="modified"`；
- 未变（unchanged）：两边都有且内容一致 → 保留基线版本，标记 `_change_status="unchanged"`；
- **保留**（默认）：基线有但 LLM 输出里没提的页面 → **自动补回**，标记 `_change_status="unchanged"`。
  这是防丢机制的关键——LLM 没"明确删除"的页面不能被静默删掉。

`_change_status` 字段是给前端做"本次变更 vs 未变更内容"分区展示用的。它写在
**页面级别**（不是 spec 顶层），让前端能逐页区分。在写正式 spec 文件前，
`_change_status` 会被剥离（见 `strip_change_status`），不污染正式产物。
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

# 合并时给页面打的变更标记字段名；写正式产物前必须剥离，不进入 git 历史。
CHANGE_STATUS_FIELD = "_change_status"

# 数组型字段的合并键。pages 数组按 pageId 对齐合并；其他数组（user_roles /
# feature_modules / business_flows）按 id 对齐合并。这些字段都是 spec 的核心
# 业务事实，需要按 ID 做增量更新。
_LIST_MERGE_KEYS: dict[str, str] = {
    "pages": "pageId",
    "user_roles": "id",
    "feature_modules": "id",
    "business_flows": "id",
}

# 应用级变更标记：描述"应用整体表述"（摘要、应用名等）本轮是否变化。
# 页面级标记回答不了这类问题（风格维度就取自需求全文），因此单独记录。
# 与 `_change_status` 一样只是展示视图，写正式产物前必须剥离。
APP_CHANGE_STATUS_FIELD = "_app_change_status"

# 参与应用级比对的内容字段：只比业务表述，不比生成/同步元数据。
_APP_LEVEL_FIELDS = ("app_info",)


def merge_requirement_spec(
    base_spec: dict[str, Any] | None,
    new_spec: dict[str, Any],
    *,
    explicit_deleted_ids: set[str] | None = None,
) -> dict[str, Any]:
    """把新 spec 与基线 spec 合并，返回带 `_change_status` 标记的合并结果。

    合并规则：
    - 顶层字段（app_info / summary / version / status / generated_at 等）**整体用新版**：
      它们是"应用当前状态"的快照，必须反映本轮最新需求；
    - 数组型字段（pages / user_roles / feature_modules / business_flows）**按 ID 合并**：
      新 ID 追加、同 ID 内容不同则用新版、同 ID 内容一致保留基线版；
    - 基线有、新输出没提的项：**只有 `explicit_deleted_ids` 里声明的才标"删除"**，
      其余一律原样继承并标"未变更"（防丢失）；
    - 其他顶层数组/对象字段（acceptance_criteria / authentication_requirements /
      authorization_requirements / entities / data_sources 等）**整体用新版**：
      这些字段的合并粒度更细，且通常由后续节点（product_planning / technical_planning）
      重新生成，需求文档阶段的合并以页面/角色/模块/流程为主。

    `base_spec` 为 None（首轮创建）时直接返回新 spec（不带 _change_status 标记，
    因为所有页面都是新增）。
    """

    if not isinstance(new_spec, dict):
        return new_spec

    if base_spec is None:
        # 首轮：所有列表项标 added，让前端能识别"全部是本轮新增"。
        return _tag_all_items(new_spec, "added")

    merged = deepcopy(new_spec)
    deleted_ids = explicit_deleted_ids or set()
    for field, id_key in _LIST_MERGE_KEYS.items():
        merged[field] = _merge_list_field(
            base_items=_list_items(base_spec.get(field)),
            new_items=_list_items(new_spec.get(field)),
            id_key=id_key,
            explicit_deleted_ids=deleted_ids,
        )
    # 应用级变更：页面级标记无法表达"应用整体表述变了"，风格维度需要它。
    merged[APP_CHANGE_STATUS_FIELD] = _app_change_status(base_spec, new_spec)
    return merged


def requirement_revision_diff(base_spec: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """直接修订只比较最终内容，缺失项生成展示删除记录，不补回业务事实。"""

    display = deepcopy(strip_change_status(candidate))
    base = strip_change_status(base_spec)
    for field, id_key in _LIST_MERGE_KEYS.items():
        previous = {str(row.get(id_key) or ""): row for row in _list_items(base.get(field))}
        current = _list_items(display.get(field))
        seen = set()
        for row in current:
            identity = str(row.get(id_key) or "")
            seen.add(identity)
            old = previous.get(identity)
            row[CHANGE_STATUS_FIELD] = "added" if old is None else "unchanged" if _deep_equal(old, row) else "modified"
        for identity, old in previous.items():
            if identity not in seen:
                current.append({**deepcopy(old), CHANGE_STATUS_FIELD: "deleted"})
        display[field] = current
    display[APP_CHANGE_STATUS_FIELD] = _app_change_status(base, candidate)
    return display


def _app_change_status(
    base_spec: dict[str, Any], new_spec: dict[str, Any]
) -> str:
    """比较应用级内容字段，返回 added / modified / unchanged。

    只比较 `app_info` 与 `summary`：它们承载应用整体表述（名称、摘要），
    风格维度的关键词就是从这些文本里提取的。生成/同步元数据不参与，
    否则每轮都会因为时间戳变化被判成改动。
    """

    previous = {key: (base_spec or {}).get(key) for key in _APP_LEVEL_FIELDS}
    current = {key: (new_spec or {}).get(key) for key in _APP_LEVEL_FIELDS}
    if not any(value for value in previous.values()):
        # 基线没有应用级内容（首轮）：本轮出现的内容都是新增。
        return "added" if any(value for value in current.values()) else "unchanged"
    return "unchanged" if _deep_equal(previous, current) else "modified"


def strip_change_status(spec: dict[str, Any]) -> dict[str, Any]:
    """剥离 `_change_status` 标记，返回干净的 spec（用于写正式文件 / 入库）。

    `_change_status` 是合并过程给前端展示用的临时标记，**不应该进 git**——
    它是"当前这轮相对基线的视图"，不是产物本身的业务事实。下一轮迭代时基线
    已经切换，旧的标记就过期了。
    """

    if not isinstance(spec, dict):
        return spec
    cleaned = deepcopy(spec)
    # 应用级标记与页面标记同属展示视图，不能进入正式产物。
    cleaned.pop(APP_CHANGE_STATUS_FIELD, None)
    for field in _LIST_MERGE_KEYS:
        items = cleaned.get(field)
        if isinstance(items, list):
            cleaned[field] = [
                {k: v for k, v in item.items() if k != CHANGE_STATUS_FIELD}
                if isinstance(item, dict)
                else item
                for item in items
            ]
    return cleaned


def _list_items(value: Any) -> list[dict[str, Any]]:
    """把未知值收敛成字典条目列表，过滤空项。"""

    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _merge_list_field(
    *,
    base_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    id_key: str,
    explicit_deleted_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """按 id_key 合并两个列表，返回带 `_change_status` 标记的合并结果。"""

    deleted_ids = explicit_deleted_ids or set()

    base_by_id: dict[str, dict[str, Any]] = {}
    base_order: list[str] = []
    for item in base_items:
        item_id = str(item.get(id_key) or item.get("id") or "").strip()
        if not item_id:
            continue
        base_by_id[item_id] = item
        base_order.append(item_id)

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 先按新输出的顺序处理：保留新输出的相对顺序，让"用户调整过的顺序"被尊重。
    for item in new_items:
        item_id = str(item.get(id_key) or item.get("id") or "").strip()
        if not item_id:
            continue
        seen.add(item_id)
        base_item = base_by_id.get(item_id)
        if base_item is None:
            merged.append({**item, CHANGE_STATUS_FIELD: "added"})
        elif _deep_equal(base_item, item):
            # 内容一致：保留基线版本（防止 LLM 重新格式化 / 改字段顺序导致假变化）。
            merged.append({**base_item, CHANGE_STATUS_FIELD: "unchanged"})
        else:
            merged.append({**item, CHANGE_STATUS_FIELD: "modified"})

    # 再处理基线里**未被新输出提及**的项，按"用户有没有明确说要删"分流：
    #
    # - 模型显式声明删除（`deleted_pages`）→ 标 `deleted`，条目仍保留在列表里，
    #   下游据此跳过它、构建阶段据此清理页面代码；
    # - 其余一律原样继承并标 `unchanged`。
    #
    # **为什么不能把"没提到"直接当成删除**：模型漏写一页和用户要删一页，在数据上
    # 完全无法区分；而模型整份重写时漏写是常态（这正是 merger 存在的原因）。
    # 若按"缺席即删除"处理，用户没提的内容会被静默删掉——那是比误留更严重的错误。
    for item_id in base_order:
        if item_id in seen:
            continue
        status = "deleted" if item_id in deleted_ids else "unchanged"
        merged.append({**base_by_id[item_id], CHANGE_STATUS_FIELD: status})

    return merged


def _tag_all_items(spec: dict[str, Any], status: str) -> dict[str, Any]:
    """给 spec 里所有数组合并字段统一打上 `_change_status` 标记（首轮创建用）。"""

    tagged = deepcopy(spec)
    for field in _LIST_MERGE_KEYS:
        items = tagged.get(field)
        if isinstance(items, list):
            tagged[field] = [
                {**item, CHANGE_STATUS_FIELD: status} if isinstance(item, dict) else item
                for item in items
            ]
    # 首轮的应用级内容同样是新增，风格维度据此显示"新增"。
    tagged[APP_CHANGE_STATUS_FIELD] = status
    return tagged


def _deep_equal(left: Any, right: Any) -> bool:
    """JSON 语义下的深度相等：忽略键顺序，只比较内容。"""

    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False
    )
