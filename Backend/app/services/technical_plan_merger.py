"""把"LLM 重新生成的 TechnicalPlan"与"基线 TechnicalPlan"做保守增量合并。

**为什么需要它**：计划阶段每次都会让 LLM **整份重新生成** technical-plan.json。
即使用户这轮只加了一个页面、完全没提其他内容，LLM 也会顺手把继承自基线的对象
重写一遍 —— 实质是措辞润色，但会给下游带来真实的语义漂移：

- 页面接口依赖的 `trigger` 被改写；
- `required_for_initial_load` 从 true 被改成 false（**会影响页面初始加载的接口判定**）；
- 接口 `error_codes` 从 `["NOT_FOUND","INTERNAL_ERROR"]` 被削成 `["INTERNAL_ERROR"]`
  （**会改变错误码契约**）。

用户的需求没提这些对象，它们就应该是"继承自基线、未变更"。这与需求文档侧
`requirement_spec_merger` 的口径完全一致：**基线不动，只做按 ID 的增量合并**。

本模块提供的合并是保守的：
- 新增（added）：新输出里有、基线没有的对象 → 采用新内容，标记 `_change_status="added"`；
- 修改（modified）：两边都有且**确有语义差异** → 采用新内容，标记 `"modified"`；
- 未变（unchanged）：两边都有且内容一致 → **保留基线版本**，标记 `"unchanged"`；
- **保留**（默认）：基线有但新输出没提的对象 → **自动补回**，标记 `"unchanged"`。
  LLM 没"明确删除"的对象不能被静默删掉。

**关键约束**：只有本轮需求**明确涉及**的对象才允许被判成"修改"。
判断依据是从已确认的 RequirementSpec / ProductPlan 里取出的业务对象清单
（见 `relevant_object_ids`）。LLM 对未被涉及的既有对象做的任何改写都按
"保留基线"处理 —— 用户没要求改的东西，平台不能替他改。

合并产出**两份**，与需求文档一致：
- `strip_change_status` 后的干净版：走原有校验、写盘、入库全流程；
- 带 `_change_status` 的展示版：只用于前端"本次变更 vs 未变更内容"分区显示。
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from app.services.technical_plan_content_changes import (
    TextReplacement,
    confirmed_endpoint_text_replacements,
    replace_endpoint_text,
    replace_schema_text,
    schema_text_replacements,
)

# 合并时给对象打的变更标记字段名；写正式产物前必须剥离，不进入 git 历史。
CHANGE_STATUS_FIELD = "_change_status"

# TechnicalPlan 里参与增量合并的列表字段及其稳定身份键。
# - entities：按实体 id 对齐；
# - api_contracts：按接口分组 id 对齐；
# - pages：按 pageId 对齐。
_LIST_FIELDS: dict[str, str] = {
    "entities": "id",
    "api_contracts": "id",
    "pages": "pageId",
}

# 接口列表是 api_contracts 里的**嵌套**数组，需要单独按 id 合并。
ENDPOINTS_FIELD = "endpoints"

# 契约内的类型定义表（名称 → Schema）。接口通过 request_schema_ref /
# response_schema_ref 引用它，因此这里的任何改写都会让引用它的接口显示"修改"。
SCHEMAS_FIELD = "schemas"


def _canonical(value: Any) -> str:
    """JSON 语义下的规范表示：忽略键顺序，只比较内容。"""

    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _items(value: Any) -> list[dict[str, Any]]:
    """把未知值收敛成字典条目列表。"""

    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _item_id(item: dict[str, Any], id_key: str) -> str:
    """读取条目的稳定身份；没有身份键的条目无法对齐，只能原样保留。"""

    return str(item.get(id_key) or "").strip()


def relevant_object_ids(
    requirement_spec: dict[str, Any] | None,
    product_plan: dict[str, Any] | None,
    base_plan: dict[str, Any] | None,
    base_requirement_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """取本轮"用户明确要求改动"的对象 ID，作为放行修改的白名单。

    判断口径（保守优先）：
    - 基线技术规划里**不存在**的对象不需要白名单 —— 它们必然是新增，直接采用新内容；
    - 基线技术规划里**已存在**的对象，只有本轮 RequirementSpec 中该对象的条目
      与**基线 RequirementSpec**（同一次迭代登记的上一轮需求）的条目不一致时，
      才算"用户要求改过"，允许采用新内容；
    - 其余一律视为继承：不管 LLM 写了什么，都保留基线版本。
    例外：**基线需求里没有、本轮新增**的对象，没有可比对的上一版，视为放行（合并时
    内容一致会标 added / unchanged，不会凭空产生 modified）。

    **不能只看"本轮 RequirementSpec 里有没有这个对象"**：上一轮的页面当然会继续
    出现在本轮需求里（用户只是加了一个新页面）。用它当放行依据，LLM 对既有页面的
    任何措辞改写都会被当成"修改"，这正是要修的问题。

    ProductPlan 不参与判断：它同样是 LLM 整份重新生成的产物，措辞变化不能反证
    "用户要改这个对象"。RequirementSpec 才是本轮需求的权威表述。

    返回的集合只用于**放行**修改，不是"必须修改"——放行了还要内容确实不同才标 modified。
    """

    pages: set[str] = set()
    entities: set[str] = set()
    if not isinstance(requirement_spec, dict):
        return {"pages": set(), "entities": set()}

    def _index(source: dict[str, Any] | None, field: str, id_key: str) -> dict[str, Any]:
        """按身份键索引某个上游产物里的条目。"""

        return {
            _item_id(item, id_key): item
            for item in _items((source or {}).get(field))
            if _item_id(item, id_key)
        }

    spec_pages = _index(requirement_spec, "pages", "pageId")
    spec_entities = _index(requirement_spec, "entities", "id")
    base_spec_pages = _index(base_requirement_spec, "pages", "pageId")
    base_spec_entities = _index(base_requirement_spec, "entities", "id")

    # 基线需求里没有这一页 / 这个实体 → 本轮新增，放行（合并时会标 added 而非 modified）。
    # 基线需求里有但内容不同 → 用户改了它，放行。
    for page_id, page in spec_pages.items():
        previous = base_spec_pages.get(page_id)
        if previous is None or _canonical(previous) != _canonical(page):
            pages.add(page_id)
    for entity_id, entity in spec_entities.items():
        previous = base_spec_entities.get(entity_id)
        if previous is None or _canonical(previous) != _canonical(entity):
            entities.add(entity_id)
    endpoints = _relevant_endpoint_ids(requirement_spec, base_plan)
    return {
        "pages": pages,
        "entities": entities,
        "endpoints": endpoints,
        # 明确的 A → B 需求文本替换单独携带证据，并只作用于说明文字；
        # 不把“页面有修改”扩大成“该页接口的所有字段都可以由模型重写”。
        "endpointTextReplacements": confirmed_endpoint_text_replacements(
            requirement_spec,
            base_requirement_spec,
            base_plan,
            relevant_pages=pages,
        ),
        # 需求里点名、且基线中没有的接口路径：接口改写后的新路径必须落在这里，
        # 防止模型借着"页面被改过"把接口改到需求没提过的地址。
        "endpointPaths": _requested_interface_paths(requirement_spec, base_plan),
    }


# 需求文本里出现的 HTTP 接口路径，例如 `/api/hi`、`/api/access-cards/{id}`。
_INTERFACE_PATH_PATTERN = re.compile(r"/api/[A-Za-z0-9_\-/{}.:]+")


def _interface_paths(text: Any) -> set[str]:
    """从一段需求文本里取出明确写出的接口路径，用于识别"用户改了接口地址"。"""

    if not isinstance(text, str) or not text.strip():
        return set()
    return {
        match.group(0).rstrip("/.")
        for match in _INTERFACE_PATH_PATTERN.finditer(text)
        if match.group(0).rstrip("/.")
    }


def _page_requirement_text(page: dict[str, Any]) -> str:
    """拼出单页需求里所有会描述接口用法的文本字段。

    需求规格没有独立的接口字段，用户"把接口换成 /api/hi"这类要求只会出现在页面的
    描述 / 目标 / 验收标准里，所以这里把它们合起来做路径识别。
    """

    parts: list[Any] = [page.get("description"), page.get("goal"), page.get("name")]
    parts.extend(page.get("acceptance_criteria") or [])
    for item in _items(page.get("information_items")):
        parts.append(item.get("description"))
    for action in _items(page.get("actions")):
        parts.append(action.get("description"))
        behavior = action.get("behavior")
        if isinstance(behavior, dict):
            parts.append(behavior.get("expectedResult"))
    return "\n".join(str(part) for part in parts if isinstance(part, str))


def _baseline_endpoint_paths(base_plan: dict[str, Any] | None) -> set[str]:
    """基线技术规划里所有接口当前声明的路径（已做尾部斜杠归一）。"""

    return {
        str(endpoint.get("path") or "").strip().rstrip("/")
        for contract in _items((base_plan or {}).get("api_contracts"))
        for endpoint in _items(contract.get(ENDPOINTS_FIELD))
        if str(endpoint.get("path") or "").strip()
    }


def _requested_interface_paths(
    requirement_spec: dict[str, Any], base_plan: dict[str, Any] | None
) -> set[str]:
    """找出本轮需求文本里点名、但基线技术规划中不存在的接口路径。

    需求规格没有独立的接口字段，用户"把接口换成 /api/hi"这类要求只会写在页面描述里。
    这些新路径就是用户明确要求的接口地址，合并时据此校验模型给出的新路径。
    """

    base_paths = _baseline_endpoint_paths(base_plan)
    return {
        path
        for page in _items(requirement_spec.get("pages"))
        for path in _interface_paths(_page_requirement_text(page))
        if path not in base_paths
    }


def _relevant_endpoint_ids(
    requirement_spec: dict[str, Any],
    base_plan: dict[str, Any] | None,
) -> set[str]:
    """计算本轮允许被改写的既有接口 id 白名单。

    接口没有独立的需求字段，唯一可证明的"要改接口"信号是：该接口被某个页面引用，
    而这个页面的需求文本里点名了基线中不存在的接口路径（例如"把问候首页的接口换成
    /api/hi"）。只有这种页面引用的接口才放行。

    页面措辞的普通变化不足以放行接口：那可能只是描述改写，模型顺手重写接口会被误采纳。
    其它情况一律继承基线，避免 LLM 改写用户没要求的契约。
    """

    if not isinstance(base_plan, dict):
        return set()
    base_paths = _baseline_endpoint_paths(base_plan)
    spec_pages = {_item_id(item, "pageId"): item for item in _items(requirement_spec.get("pages"))}
    relevant: set[str] = set()
    for page in _items(base_plan.get("pages")):
        page_id = _item_id(page, "pageId")
        if not page_id:
            continue
        # 该页面在基线技术规划里引用的接口。
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        endpoint_ids = {
            _item_id(item, "endpoint_id")
            for item in _items(references.get("endpoint_dependencies"))
        } | {
            _item_id(item, "endpointId")
            for item in _items(references.get("action_implementations"))
        }
        endpoint_ids.discard("")
        if not endpoint_ids:
            continue
        current = spec_pages.get(page_id)
        if current is None:
            continue
        if _interface_paths(_page_requirement_text(current)) - base_paths:
            relevant |= endpoint_ids
    return relevant


def _schema_dependency_names(schemas: Any, roots: set[str]) -> set[str]:
    """递归收集本契约内的类型依赖，避免共享嵌套类型被局部修改连带改写。"""

    definitions = schemas if isinstance(schemas, dict) else {}
    names: set[str] = set()

    def visit(value: Any) -> None:
        """遍历类型中的本地引用，循环引用通过已访问集合终止。"""

        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                add(reference.strip().split("/")[-1])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    def add(name: str) -> None:
        """记录类型并展开其间接引用。"""

        if name and name not in names:
            names.add(name)
            visit(definitions.get(name))

    for name in roots:
        add(name)
    return names


def merge_direct_endpoint_revision(
    base_plan: dict[str, Any], new_plan: dict[str, Any],
    targets: dict[str, dict[str, Any]],
    *, schema_targets: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """只采纳精确点名接口及类型的修订，其他对象严格继承当前基线。"""

    schema_targets = schema_targets or {}
    base = strip_change_status(base_plan)
    candidate = strip_change_status(new_plan)
    scoped = deepcopy(base)
    contracts = {_item_id(item, "id"): item for item in _items(candidate.get("api_contracts"))}
    found: set[str] = set()
    for contract in _items(scoped.get("api_contracts")):
        contract_id = _item_id(contract, "id")
        selected = {key: target for key, target in targets.items() if target["contract_id"] == contract_id}
        selected_schemas = schema_targets.get(contract_id, set())
        if not selected and not selected_schemas:
            continue
        proposed = contracts.get(contract_id, {})
        endpoints = {_item_id(item, "id"): item for item in _items(proposed.get(ENDPOINTS_FIELD))}
        base_schemas = deepcopy(contract.get(SCHEMAS_FIELD) or {})
        new_schemas = proposed.get(SCHEMAS_FIELD) or {}
        for name in selected_schemas:
            if name not in new_schemas:
                raise ValueError(f"点名类型未完成修改：{contract_id}.{name}。未授权删除类型。")
            contract.setdefault(SCHEMAS_FIELD, {})[name] = deepcopy(new_schemas[name])
        # 非目标接口和仅要求删除的接口仍引用基线类型，不能被共享类型的改写波及。
        protected = _schema_dependency_names(base_schemas, _referenced_schema_names([
            item for item in _items(contract.get(ENDPOINTS_FIELD))
            if _item_id(item, "id") not in selected or selected[_item_id(item, "id")]["deleted"]
        ]))
        updates: list[dict[str, Any]] = []
        for endpoint in _items(contract.get(ENDPOINTS_FIELD)):
            endpoint_id = _item_id(endpoint, "id")
            target = selected.get(endpoint_id)
            if target is None:
                updates.append(endpoint)
                continue
            found.add(endpoint_id)
            if target["deleted"]:
                updates.append({**endpoint, CHANGE_STATUS_FIELD: "deleted"})
                continue
            changed = endpoints.get(endpoint_id)
            if changed is None or changed.get(CHANGE_STATUS_FIELD) == "deleted":
                raise ValueError(f"点名接口未完成修改：{target['method']} {target['path']}。模型省略接口不构成删除授权。")
            if str(changed.get("method") or "").upper() != target["method"]:
                raise ValueError(f"点名接口身份被改写：{target['method']} {target['path']}。本次未授权修改 HTTP 方法。")
            expected_path = str(target.get("new_path") or target["path"])
            actual_path = str(changed.get("path") or "").strip().rstrip("/")
            if actual_path != expected_path:
                if target.get("new_path"):
                    raise ValueError(
                        f"点名接口未按要求修改路径：{target['method']} {target['path']} "
                        f"应改为 {expected_path}，候选实际为 {actual_path or '空路径'}。"
                    )
                raise ValueError(f"点名接口身份被改写：{target['method']} {target['path']}。本次未授权修改路径。")
            needed = _schema_dependency_names(new_schemas, _referenced_schema_names([changed]))
            for name in needed:
                if name not in new_schemas:
                    raise ValueError(f"点名接口引用缺失类型：{contract_id}.{name}。")
                if name in protected and name not in selected_schemas and _canonical(base_schemas.get(name)) != _canonical(new_schemas[name]):
                    raise ValueError(f"点名接口修改影响未授权的共享类型：{contract_id}.{name}。请为目标接口拆分独立类型，保留其他接口基线。")
                contract.setdefault(SCHEMAS_FIELD, {})[name] = deepcopy(new_schemas[name])
            updates.append(deepcopy(changed))
        contract[ENDPOINTS_FIELD] = updates
    if found != set(targets):
        raise ValueError("点名接口与当前修订基线不一致，拒绝采用候选。")
    if _canonical(base) == _canonical(scoped) and any(
        _canonical(base.get(field)) != _canonical(candidate.get(field))
        for field in ("architecture", "entities", "api_contracts", "pages")
    ):
        raise ValueError("点名接口未产生有效变更：模型只改写了未授权对象，未将本次修订作为成功候选。")
    return technical_plan_revision_diff(base, scoped)


def technical_plan_revision_diff(
    base_plan: dict[str, Any] | None, candidate: dict[str, Any],
) -> dict[str, Any]:
    """仅给已受控采纳的内容重算审阅标记，不再用审阅基线覆盖事务修订。"""

    display = deepcopy(strip_change_status(candidate))
    base = strip_change_status(base_plan or {})
    for field, id_key in _LIST_FIELDS.items():
        previous = {_item_id(item, id_key): item for item in _items(base.get(field))}
        for item in _items(display.get(field)):
            old = previous.get(_item_id(item, id_key))
            if _is_deleted_item(item):
                continue
            item[CHANGE_STATUS_FIELD] = (
                "added" if old is None else "unchanged" if _canonical(old) == _canonical(item) else "modified"
            )
            if field != "api_contracts":
                continue
            old_endpoints = {_item_id(endpoint, "id"): endpoint for endpoint in _items((old or {}).get(ENDPOINTS_FIELD))}
            for endpoint in _items(item.get(ENDPOINTS_FIELD)):
                if _is_deleted_item(endpoint):
                    continue
                prior = old_endpoints.get(_item_id(endpoint, "id"))
                roots = _referenced_schema_names([endpoint])
                old_schemas = (old or {}).get(SCHEMAS_FIELD) or {}
                schemas = item.get(SCHEMAS_FIELD) or {}
                names = _schema_dependency_names(old_schemas, roots) | _schema_dependency_names(schemas, roots)
                schema_changed = any(_canonical(old_schemas.get(name)) != _canonical(schemas.get(name)) for name in names)
                endpoint[CHANGE_STATUS_FIELD] = (
                    "added" if prior is None else "modified" if _canonical(prior) != _canonical(endpoint) or schema_changed else "unchanged"
                )
    return display


def merge_technical_plan(
    base_plan: dict[str, Any] | None,
    new_plan: dict[str, Any],
    *,
    relevant: dict[str, Any] | None = None,
    deleted_page_ids: set[str] | None = None,
) -> dict[str, Any]:
    """把新规划与基线规划按 ID 保守合并，返回带 `_change_status` 的展示版。

    `base_plan` 为 None 表示首轮创建：全部标 added（与需求文档 `_tag_all_items` 一致）。

    `deleted_page_ids` 是本轮需求删除的页面。删除会**级联**：页面被删后，
    只有它用到的接口 / 实体 / 类型定义才跟着标 deleted；与其它保留页面共用的
    接口和实体不受影响（它们仍然有用，不能跟着删）。
    """

    if not isinstance(new_plan, dict):
        return new_plan
    relevant = relevant or {}
    if base_plan is None or not base_plan:
        return _tag_all(new_plan, "added")

    cascade = _cascade_deletion_targets(base_plan, deleted_page_ids)

    merged = deepcopy(new_plan)
    # 顶层快照字段（artifact_type / architecture / authorization_manifest 等）整体用新版：
    # 它们描述"应用当前状态"，其中 architecture 由模板固定，authorization_manifest 的
    # 内容由上游配置决定，不属于逐对象增量合并的范围。
    merged["entities"] = _merge_plain_list(
        base_items=_items(base_plan.get("entities")),
        new_items=_items(new_plan.get("entities")),
        id_key="id",
        # 实体只有被本轮需求点名时才允许判修改，否则保留基线。
        allowed_ids=relevant.get("entities"),
        force_deleted=cascade["entities"],
    )
    merged["pages"] = _merge_plain_list(
        base_items=_items(base_plan.get("pages")),
        new_items=_items(new_plan.get("pages")),
        id_key="pageId",
        allowed_ids=relevant.get("pages"),
        force_deleted=cascade["pages"],
    )
    merged["api_contracts"] = _merge_contracts(
        base_items=_items(base_plan.get("api_contracts")),
        new_items=_items(new_plan.get("api_contracts")),
        # 接口白名单来自 `relevant_object_ids`：页面本轮被明确改动、或需求文本点名了
        # 基线里不存在的接口路径（如"把问候首页的接口换成 /api/hi"）时，该页引用的
        # 接口才允许采用模型的新内容；其余既有接口继续继承基线。
        allowed_ids=relevant.get("endpoints", set()),
        cascade=cascade,
        # 需求点名的新接口路径：模型改写后的路径必须落在其中，否则退回基线。
        requested_paths=relevant.get("endpointPaths", set()),
        text_replacements=relevant.get("endpointTextReplacements", {}),
    )
    # Contract/Schema 已合并完成后，按实际保留下来的 entity_field_ref 闭合实体字段。
    # 新接口可以引用既有实体的新字段，但不能借这个机会重写整个实体。
    merged["entities"] = _merge_required_entity_fields(
        candidate_entities=_items(new_plan.get("entities")),
        merged_entities=merged["entities"],
        contracts=merged["api_contracts"],
    )
    return merged


def _cascade_deletion_targets(
    base_plan: dict[str, Any],
    deleted_page_ids: set[str] | None,
) -> dict[str, set[str]]:
    """计算删除页面后需要级联标记的接口 / 实体 / 类型定义。

    判定口径：
    - 页面：`deleted_page_ids` 本身；
    - 接口：**只被删除页面引用**的接口才级联删除。仍被保留页面引用的接口继续有效
      （例如"已完成界面"和"门禁卡主页面"共用同一个列表接口，那条接口不能删）；
    - 实体：`entity_ids` 只出现在"接口全被删光"的分组上的实体才级联删除���
    - 类型定义（schemas）：与所属分组同进退 —— 分组内不再有幸存接口时，其类型表
      也一并删除。

    返回的集合供合并时强制覆盖状态。
    """

    base_pages = _items(base_plan.get("pages"))
    deleted_pages = {
        str(item).strip() for item in (deleted_page_ids or set()) if str(item).strip()
    } | {_item_id(page, "pageId") for page in base_pages if _is_deleted_item(page)}
    # 同一轮续修必须沿用既有删除事实；模型再次输出对象不能撤销已确认的删除需求。
    previously_deleted_endpoints = {
        _item_id(endpoint, "id")
        for contract in _items(base_plan.get("api_contracts"))
        for endpoint in _items(contract.get(ENDPOINTS_FIELD))
        if _is_deleted_item(endpoint)
    }
    if not deleted_pages and not previously_deleted_endpoints:
        return {"pages": set(), "endpoints": set(), "entities": set(), "schemas": set()}

    # 页面 → 引用的接口集合（含组合 action 的各个 stepBindings）。
    page_endpoints: dict[str, set[str]] = {}
    for page in base_pages:
        page_id = _item_id(page, "pageId")
        if not page_id:
            continue
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        endpoints: set[str] = set()
        for dependency in _items(references.get("endpoint_dependencies")):
            endpoint_id = _item_id(dependency, "endpoint_id")
            if endpoint_id:
                endpoints.add(endpoint_id)
        for binding in _items(references.get("action_implementations")):
            for step in _items(binding.get("stepBindings")):
                endpoint_id = _item_id(step, "endpointId")
                if endpoint_id:
                    endpoints.add(endpoint_id)
        page_endpoints[page_id] = endpoints

    survivors = {
        endpoint_id
        for page_id, endpoints in page_endpoints.items()
        if page_id not in deleted_pages
        for endpoint_id in endpoints
    }
    removed_endpoints = previously_deleted_endpoints | {
        endpoint_id
        for page_id, endpoints in page_endpoints.items()
        if page_id in deleted_pages
        for endpoint_id in endpoints
        if endpoint_id not in survivors
    }

    # 分组内所有接口都被删光时，分组的实体与类型定义才一并删除。
    dead_contracts: set[str] = set()
    dead_entities: set[str] = set()
    for contract in _items(base_plan.get("api_contracts")):
        contract_id = _item_id(contract, "id")
        if not contract_id:
            continue
        endpoints = [_item_id(item, "id") for item in _items(contract.get("endpoints"))]
        endpoints = [item for item in endpoints if item]
        if endpoints and all(item in removed_endpoints for item in endpoints):
            dead_contracts.add(contract_id)
            for entity_id in contract.get("entity_ids") or []:
                name = str(entity_id or "").strip()
                if name:
                    dead_entities.add(name)

    # 共享实体只要仍被任一存活契约绑定就必须保留，不能随删除分组级联失效。
    live_entities = {
        str(entity_id).strip()
        for contract in _items(base_plan.get("api_contracts"))
        if _item_id(contract, "id") not in dead_contracts
        for entity_id in contract.get("entity_ids") or []
    }
    return {
        "pages": deleted_pages,
        "endpoints": removed_endpoints,
        "entities": dead_entities - live_entities,
        "schemas": dead_contracts,
    }


def strip_change_status(plan: dict[str, Any]) -> dict[str, Any]:
    """剥离 `_change_status` 标记，返回干净版（用于校验、写盘、入库）。

    标记是"本轮相对基线的视图"，不是产物业务事实，绝不能进正式产物：
    - 技术规划校验会拒绝实体 / 接口分组 / 接口上的未知字段（`包含非法字段：_change_status`）；
    - 它写进 git 后，下一轮从基线读回来会变成"多了一个键"的假修改。

    因此三个列表字段（entities / api_contracts / pages）以及 api_contracts 下嵌套的
    endpoints 都要逐层剥离 —— 只剥顶层会漏掉接口分组和接口上的标记。

    **`deleted` 是唯一例外，与 ProductPlan 保持一致直接保留**：删除页面后，正式产物
    必须继续带着 `{pageId, _change_status: "deleted"}`，否则开发阶段无从知道"这条已经
    不在了"。其余对象剥标后就是普通业务内容。下游（构建骨架、开发目录）必须显式跳过
    这些条目，见 `build_unit_skeleton` 与 `development_artifacts`。
    """

    if not isinstance(plan, dict):
        return plan
    cleaned = deepcopy(plan)
    for field in _LIST_FIELDS:
        items = cleaned.get(field)
        if isinstance(items, list):
            cleaned[field] = [_strip_item(item) for item in items]
    return cleaned


def _is_deleted_item(item: Any) -> bool:
    """判断条目是否带有"本轮已删除"的持久标记。"""

    return (
        isinstance(item, dict)
        and str(item.get(CHANGE_STATUS_FIELD) or "") == "deleted"
    )


def _strip_item(item: Any) -> Any:
    """剥掉单个条目及其嵌套 endpoints 上的变更标记；`deleted` 标记保留。"""

    if not isinstance(item, dict):
        return item
    stripped = {
        key: value for key, value in item.items()
        if key != CHANGE_STATUS_FIELD or _is_deleted_item(item)
    }
    endpoints = stripped.get(ENDPOINTS_FIELD)
    if isinstance(endpoints, list):
        stripped[ENDPOINTS_FIELD] = [
            (
                {key: value for key, value in endpoint.items()
                 if key != CHANGE_STATUS_FIELD or _is_deleted_item(endpoint)}
                if isinstance(endpoint, dict) else endpoint
            )
            for endpoint in endpoints
        ]
    return stripped


def _tag_all(plan: dict[str, Any], status: str) -> dict[str, Any]:
    """首轮创建：给所有可合并条目打统一标记。"""

    tagged = deepcopy(plan)
    for field in _LIST_FIELDS:
        items = tagged.get(field)
        if isinstance(items, list):
            tagged[field] = [
                {**item, CHANGE_STATUS_FIELD: status} if isinstance(item, dict) else item
                for item in items
            ]
    return tagged


def _merge_plain_list(
    *,
    base_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    id_key: str,
    allowed_ids: set[str] | None,
    force_deleted: set[str] | None = None,
) -> list[dict[str, Any]]:
    """按 ID 合并普通列表（实体 / 页面），返回带变更标记的结果。

    `allowed_ids` 为 None 表示不设白名单（全部放行）。设了白名单时，**不在白名单里的
    既有条目一律保留基线版本并标 unchanged** —— 本轮需求没提到它，就意味着继承。
    """

    forced = {str(item).strip() for item in (force_deleted or set()) if str(item).strip()}

    base_by_id: dict[str, dict[str, Any]] = {}
    base_order: list[str] = []
    for item in base_items:
        item_id = _item_id(item, id_key)
        if item_id and item_id not in base_by_id:
            base_by_id[item_id] = item
            base_order.append(item_id)

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in new_items:
        item_id = _item_id(item, id_key)
        if not item_id:
            # 没有身份键的条目无法对齐，原样保留并视为新增。
            merged.append({**item, CHANGE_STATUS_FIELD: "added"})
            continue
        seen.add(item_id)
        base_item = base_by_id.get(item_id)
        if base_item is None:
            merged.append({**item, CHANGE_STATUS_FIELD: "added"})
            continue
        if item_id in forced:
            # 删除记录描述移除前的事实，不能采纳模型对待删除对象的改写。
            merged.append({**base_item, CHANGE_STATUS_FIELD: "deleted"})
            continue
        if _canonical(base_item) == _canonical(item):
            # 内容一致：保留基线版本，防止 LLM 重新格式化产生假变化。
            merged.append({**base_item, CHANGE_STATUS_FIELD: "unchanged"})
            continue
        if allowed_ids is not None and item_id not in allowed_ids:
            # 本轮需求没涉及这个对象：LLM 的改写一律丢弃，继承基线。
            merged.append({**base_item, CHANGE_STATUS_FIELD: "unchanged"})
            continue
        merged.append({**item, CHANGE_STATUS_FIELD: "modified"})

    # 模型省略对象不代表业务删除；显式删除仍保留基线记录供审阅和开发清理使用。
    for item_id in base_order:
        if item_id not in seen:
            merged.append({
                **base_by_id[item_id],
                CHANGE_STATUS_FIELD: "deleted" if item_id in forced else "unchanged",
            })

    return merged


def _merge_contracts(
    *,
    base_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    allowed_ids: set[str] | None,
    cascade: dict[str, set[str]] | None = None,
    requested_paths: set[str] | None = None,
    text_replacements: dict[str, TextReplacement] | None = None,
) -> list[dict[str, Any]]:
    """按分组 ID 合并接口分组，并对组内 endpoints 做同样的增量合并。

    接口分组本身只有 name / base_path / entity_ids / authentication 等配置项，
    真正的业务变化在 endpoints 上。既有分组的外壳始终从基线继承，模型不能借整份
    重写或接口增删顺手润色名称、路径前缀等配置。分组的变更标记只由两件事决定：
    - 最终实际采纳的分组配置与基线有差异；
    - 或组内有新增 / 删除的接口。
    被丢弃的外壳改写不参与变更判定；仅当组内既有接口被 LLM 改写（措辞、
    error_codes 等）时也不算分组变更，那些改写会由 `_merge_endpoints` 丢弃。
    """

    base_by_id = {_item_id(item, "id"): item for item in base_items if _item_id(item, "id")}
    base_order = [_item_id(item, "id") for item in base_items if _item_id(item, "id")]

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for contract in new_items:
        contract_id = _item_id(contract, "id")
        base_contract = base_by_id.get(contract_id) if contract_id else None
        if base_contract is None:
            # 新分组：组内接口全部视为新增。
            merged.append(
                {
                    **contract,
                    ENDPOINTS_FIELD: [
                        {
                            **endpoint,
                            CHANGE_STATUS_FIELD: "deleted"
                            if _item_id(endpoint, "id") in (cascade or {}).get("endpoints", set())
                            else "added",
                        }
                        for endpoint in _items(contract.get(ENDPOINTS_FIELD))
                    ],
                    CHANGE_STATUS_FIELD: "added",
                }
            )
            if contract_id:
                seen.add(contract_id)
            continue
        seen.add(contract_id)
        merged.append(
            _merge_contract(
                base_contract, contract, allowed_ids=allowed_ids,
                cascade=cascade, requested_paths=requested_paths,
                text_replacements=text_replacements,
            )
        )

    for contract_id in base_order:
        if contract_id not in seen:
            base_contract = base_by_id[contract_id]
            # 级联删除的分组：组内每个接口都要标 deleted，而不是 unconditionally "unchanged"。
            # 否则快照里看不出这些接口已随页面删除，开发阶段仍会为它们安排任务。
            dead_endpoints = (cascade or {}).get("endpoints", set())
            merged.append(
                {
                    **base_contract,
                    ENDPOINTS_FIELD: [
                        {
                            **endpoint,
                            CHANGE_STATUS_FIELD: "deleted"
                            if _item_id(endpoint, "id") in dead_endpoints
                            else "unchanged",
                        }
                        for endpoint in _items(base_contract.get(ENDPOINTS_FIELD))
                    ],
                    CHANGE_STATUS_FIELD: "unchanged",
                }
            )
    return merged


def _merge_contract(
    base_contract: dict[str, Any],
    new_contract: dict[str, Any],
    *,
    allowed_ids: set[str] | None,
    cascade: dict[str, set[str]] | None = None,
    requested_paths: set[str] | None = None,
    text_replacements: dict[str, TextReplacement] | None = None,
) -> dict[str, Any]:
    """合并单个接口分组：分组配置项、类型定义与 endpoints 分别判定。"""

    cascade = cascade or {}
    text_replacements = text_replacements or {}
    deleted_endpoints = cascade.get("endpoints", set())
    endpoints = _merge_endpoints(
        base_items=_items(base_contract.get(ENDPOINTS_FIELD)),
        new_items=_items(new_contract.get(ENDPOINTS_FIELD)),
        allowed_ids=allowed_ids,
        force_deleted=deleted_endpoints,
        requested_paths=requested_paths,
        text_replacements=text_replacements,
    )
    # 保留接口实际引用的类型名：新增类型只有被它们用到才值得引入。
    referenced = _referenced_schema_names(endpoints)
    schemas = _merge_schemas(
        base_schemas=base_contract.get(SCHEMAS_FIELD),
        new_schemas=new_contract.get(SCHEMAS_FIELD),
        allowed_ids=allowed_ids,
        contract_deleted=_item_id(base_contract, "id") in cascade.get("schemas", set()),
        survivor_endpoints=deleted_endpoints,
        referenced=referenced,
        text_replacements=schema_text_replacements(base_contract, text_replacements),
    )
    # Schema 的说明文字可能承载着“返回什么内容”的业务事实。它发生确定性更新时，
    # 引用它的接口也必须标 modified，否则开发快照会错过后端重做。
    endpoints = _with_schema_content_changes(
        endpoints,
        base_schemas=base_contract.get(SCHEMAS_FIELD),
        merged_schemas=schemas,
    )
    # 分组级标记只看"实际采纳的配置"和"是否有接口增删"，不看被丢弃的模型改写。
    # schemas 不参与分组级判定：它是被接口引用的类型表，其变更会通过接口体现。
    # 比较分组配置时排除类型表与 endpoints，以及 `_change_status` 这类状态标记：
    # 基线来自交接记录（无标记），模型输出可能带标记，混进来会让每组都判成"修改"。
    skipped = (ENDPOINTS_FIELD, SCHEMAS_FIELD, CHANGE_STATUS_FIELD)
    base_config = {k: v for k, v in base_contract.items() if k not in skipped}
    structure_changed = any(
        item.get(CHANGE_STATUS_FIELD) in ("added", "deleted") for item in endpoints
    )
    # 既有 Contract 的稳定外壳以基线为唯一来源，不能直接展开 new_contract。
    # 否则模型把“门禁卡”润色成“门禁卡管理”就会被当成真实修改；即使本轮确有
    # 接口增删，也只能修改组内内容，不能顺带取得重写名称、base_path 等外壳的权限。
    merged_contract = {
        **base_contract,
        ENDPOINTS_FIELD: endpoints,
        SCHEMAS_FIELD: schemas,
    }
    # `entity_ids` 是"本组 schema 允许引用的实体"白名单，必须覆盖最终类型表实际用到的
    # 实体。模型在重写分组时常常漏写绑定的实体，而 schemas 是逐类型继承基线的——按
    # 新配置整组采用就会留下"schema 引用 access_card、分组却没绑定它"的悬空引用，
    # 校验必然拒绝，且自动修复循环会反复产出同样的结果。
    merged_contract["entity_ids"] = _merge_entity_bindings(
        _string_ids(base_contract.get("entity_ids")),
        _string_ids(new_contract.get("entity_ids")),
        schemas,
    )
    merged_config = {k: v for k, v in merged_contract.items() if k not in skipped}
    config_changed = _canonical(base_config) != _canonical(merged_config)
    return {
        **merged_contract,
        CHANGE_STATUS_FIELD: "modified" if structure_changed or config_changed else "unchanged",
    }


def _string_ids(value: Any) -> list[str]:
    """把 `entity_ids` 之类的标识列表规整成去空、去重的字符串列表。"""

    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _merge_entity_bindings(
    base_ids: list[str],
    new_ids: list[str],
    schemas: Any,
) -> list[str]:
    """合并分组绑定的实体：保留模型新增的绑定，同时补回类型表仍然需要的基线实体。

    顺序以模型给出的为准，补回的基线实体追加在后面，保证结果稳定可比较。
    """

    required = _schema_referenced_entities(schemas)
    merged = list(new_ids)
    for entity_id in [*base_ids, *sorted(required)]:
        if entity_id not in merged:
            merged.append(entity_id)
    return merged


def _schema_entity_field_refs(schemas: Any) -> set[str]:
    """递归收集类型表里的完整 `entity_field_ref` 引用。"""

    found: set[str] = set()

    def walk(node: Any) -> None:
        """遍历 Schema 的所有嵌套对象和数组。"""

        if isinstance(node, dict):
            reference = node.get("entity_field_ref")
            if isinstance(reference, str) and "." in reference:
                entity_id, field_name = reference.split(".", 1)
                if entity_id.strip() and field_name.strip():
                    found.add(reference.strip())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    if isinstance(schemas, dict):
        walk(schemas)
    return found


def _schema_referenced_entities(schemas: Any) -> set[str]:
    """收集类型表里所有 `entity_field_ref` 指向的实体 ID。

    这决定了分组必须绑定哪些实体，否则 schema 校验会报"引用了未绑定实体"。
    """

    return {
        reference.split(".", 1)[0].strip()
        for reference in _schema_entity_field_refs(schemas)
    }


def _merge_required_entity_fields(
    *,
    candidate_entities: list[dict[str, Any]],
    merged_entities: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """只补入最终 Schema 实际引用的候选实体字段，闭合合并后的引用。

    既有实体默认继承基线，但新增接口确实可以给它带来新的业务字段。如果整份实体
    直接采用候选，模型对旧字段的润色也会混入；如果完全禁止新增字段，又会留下
    `Schema -> 不存在实体字段` 的悬空引用。这里按最终合并后的 Contract 收集引用，
    只从候选补入缺失字段，旧字段内容、实体说明和未引用的新字段均不采纳。
    """

    candidate_by_id = {
        _item_id(entity, "id"): entity
        for entity in candidate_entities
        if _item_id(entity, "id")
    }
    result = [dict(entity) for entity in merged_entities]
    result_by_id = {
        _item_id(entity, "id"): (index, entity)
        for index, entity in enumerate(result)
        if _item_id(entity, "id")
    }
    required_refs = {
        reference
        for contract in contracts
        for reference in _schema_entity_field_refs(contract.get(SCHEMAS_FIELD))
    }
    for reference in sorted(required_refs):
        entity_id, field_name = reference.split(".", 1)
        located = result_by_id.get(entity_id.strip())
        candidate = candidate_by_id.get(entity_id.strip())
        if located is None or candidate is None:
            continue
        index, target = located
        if target.get(CHANGE_STATUS_FIELD) == "deleted":
            continue
        target_fields = _items(target.get("fields"))
        if field_name.strip() in {
            str(field.get("name") or "").strip() for field in target_fields
        }:
            continue
        candidate_field = next(
            (
                field
                for field in _items(candidate.get("fields"))
                if str(field.get("name") or "").strip() == field_name.strip()
            ),
            None,
        )
        if candidate_field is None:
            continue
        status = target.get(CHANGE_STATUS_FIELD)
        updated = {
            **target,
            "fields": [*target_fields, deepcopy(candidate_field)],
            CHANGE_STATUS_FIELD: "modified" if status in (None, "", "unchanged") else status,
        }
        result[index] = updated
        result_by_id[entity_id.strip()] = (index, updated)
    return result


def _referenced_schema_names(endpoints: list[dict[str, Any]]) -> set[str]:
    """收集接口通过 request/response 引用到的类型名（取 `$ref` 最后一段）。"""

    names: set[str] = set()
    for endpoint in endpoints:
        if str(endpoint.get(CHANGE_STATUS_FIELD) or "") == "deleted":
            continue
        for key in ("request_schema_ref", "response_schema_ref"):
            reference = endpoint.get(key)
            if isinstance(reference, str) and reference.strip():
                names.add(reference.strip().split("/")[-1])
    return names


def _with_schema_content_changes(
    endpoints: list[dict[str, Any]],
    *,
    base_schemas: Any,
    merged_schemas: Any,
) -> list[dict[str, Any]]:
    """把已采纳的 Schema 内容变化传播到引用它的接口变更标记。

    只有实际合并结果与基线不同才传播；模型试图润色但被拦截的 Schema 不会造成
    假修改。新增、删除或已经修改的接口保留原标记，只提升原来的 unchanged 条目。
    """

    base = base_schemas if isinstance(base_schemas, dict) else {}
    merged = merged_schemas if isinstance(merged_schemas, dict) else {}
    changed_names = {
        name
        for name in set(base) | set(merged)
        if _canonical(base.get(name)) != _canonical(merged.get(name))
    }
    if not changed_names:
        return endpoints
    result: list[dict[str, Any]] = []
    for endpoint in endpoints:
        if (
            endpoint.get(CHANGE_STATUS_FIELD) == "unchanged"
            and _referenced_schema_names([endpoint]) & changed_names
        ):
            result.append({**endpoint, CHANGE_STATUS_FIELD: "modified"})
        else:
            result.append(endpoint)
    return result


def _merge_schemas(
    *,
    base_schemas: Any,
    new_schemas: Any,
    allowed_ids: set[str] | None,
    contract_deleted: bool = False,
    survivor_endpoints: set[str] | None = None,
    referenced: set[str] | None = None,
    text_replacements: dict[str, TextReplacement] | None = None,
) -> dict[str, Any]:
    """按类型名合并契约内的类型定义表（`schemas`）。

    接口通过 `request_schema_ref` / `response_schema_ref` 引用这里的类型，前端 diff
    在比较接口时会把被引用的类型展开一起比。因此 LLM 只要重写了某个类型的
    description，**所有引用它的接口都会被判成"修改"** —— 这正是"用户没提却出现修改"
    的一个隐藏来源。

    口径与接口一致：需求没放行（不在 `allowed_ids` 里）的既有类型一律保留基线内容，
    只有本轮新增的类型才采用新内容。
    """

    base = base_schemas if isinstance(base_schemas, dict) else {}
    new = new_schemas if isinstance(new_schemas, dict) else {}
    if contract_deleted:
        # 分组内接口全被删光：整张类型表随分组一起失效，模型新加的类型也不采纳。
        return {name: schema for name, schema in base.items()}
    merged: dict[str, Any] = {}
    # 先按新输出的顺序，保持模型对类型表的组织方式。
    for name, schema in new.items():
        replacement = (text_replacements or {}).get(name)
        if name not in base:
            # 基线没有的类型：只有**被接口实际引用**时才值得引入。
            # 否则是模型顺手加的多余类型——用户没提、接口也没用，留着只会制造噪声。
            if referenced and name not in referenced:
                continue
            merged[name] = schema
        elif allowed_ids and f"schema:{name}" in allowed_ids:
            merged[name] = (
                replace_schema_text(schema, replacement) if replacement is not None else schema
            )
        elif replacement is not None:
            # 需求证据只允许平台替换说明文字，候选中的其它 Schema 改写不采纳。
            merged[name] = replace_schema_text(base[name], replacement)
        elif _canonical(base[name]) == _canonical(schema):
            merged[name] = base[name]
        else:
            # 本轮需求没涉及这个类型：丢弃 LLM 的改写，继承基线。
            merged[name] = base[name]
    # 补回基线里新输出没提的类型，防止接口引用的类型被静默删掉；有明确需求证据的
    # 说明文字替换同样应用于补回项，不能因模型漏写 Schema 而保留旧业务值。
    for name, schema in base.items():
        if name not in merged:
            replacement = (text_replacements or {}).get(name)
            merged[name] = (
                replace_schema_text(schema, replacement) if replacement is not None else schema
            )
    return merged


def _merge_endpoints(
    *,
    base_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    allowed_ids: set[str] | None,
    force_deleted: set[str] | None = None,
    requested_paths: set[str] | None = None,
    text_replacements: dict[str, TextReplacement] | None = None,
) -> list[dict[str, Any]]:
    """按接口 ID 合并组内 endpoints：需求没点名的既有接口一律继承基线。

    接口是**用户从没点名要改**的最典型对象：他只会说"再加一个中午好的接口"，
    绝不会说"把问候接口的 error_codes 去掉一项"。因此：
    - 新增 / 删除的接口如实标记；
    - 两边都有的接口，内容一致 → 保留基线并标 unchanged；
    - 两边都有但内容不同，且**该接口在 `allowed_ids` 白名单内** → 采用新内容并标 modified；
    - 两边都有但内容不同、且不在白名单 → **丢弃 LLM 改写，继承基线**并标 unchanged。
      这正是修复"用户没提却出现 7 个修改"的关键。
    """

    base_by_id = {_item_id(item, "id"): item for item in base_items if _item_id(item, "id")}
    base_order = [_item_id(item, "id") for item in base_items if _item_id(item, "id")]

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for endpoint in new_items:
        endpoint_id = _item_id(endpoint, "id")
        if not endpoint_id:
            merged.append({**endpoint, CHANGE_STATUS_FIELD: "added"})
            continue
        seen.add(endpoint_id)
        base_endpoint = base_by_id.get(endpoint_id)
        if endpoint_id in (force_deleted or set()):
            # 删除记录与保留的基线类型表必须同源，不能使用模型新命名的 Schema 引用。
            merged.append({**(base_endpoint or endpoint), CHANGE_STATUS_FIELD: "deleted"})
            continue
        if base_endpoint is None:
            merged.append({**endpoint, CHANGE_STATUS_FIELD: "added"})
            continue
        replacement = (text_replacements or {}).get(endpoint_id)
        if allowed_ids is not None and endpoint_id in allowed_ids:
            # 白名单内的接口可以采纳模型的新内容；但如果模型改了路径，新路径必须是
            # 需求文本里点名的那个地址，否则退回基线——避免"页面被改过"被当成
            # "可以随意改接口地址"的通行证。
            base_path = str(base_endpoint.get("path") or "").strip().rstrip("/")
            new_path = str(endpoint.get("path") or "").strip().rstrip("/")
            if (
                requested_paths is not None
                and new_path
                and new_path != base_path
                and new_path not in requested_paths
            ):
                merged.append({**base_endpoint, CHANGE_STATUS_FIELD: "unchanged"})
            else:
                candidate = (
                    replace_endpoint_text(endpoint, replacement)
                    if replacement is not None else endpoint
                )
                merged.append({
                    **candidate,
                    CHANGE_STATUS_FIELD: (
                        "unchanged" if _canonical(base_endpoint) == _canonical(candidate)
                        else "modified"
                    ),
                })
        elif replacement is not None:
            # 有明确 A → B 证据时由平台修改基线说明，不采纳候选夹带的其它字段变化。
            candidate = replace_endpoint_text(base_endpoint, replacement)
            merged.append({
                **candidate,
                CHANGE_STATUS_FIELD: (
                    "modified" if _canonical(candidate) != _canonical(base_endpoint)
                    else "unchanged"
                ),
            })
        elif _canonical(base_endpoint) == _canonical(endpoint):
            merged.append({**base_endpoint, CHANGE_STATUS_FIELD: "unchanged"})
        else:
            # 需求没涉及这个接口：LLM 的改写一律丢弃，继承基线。
            merged.append({**base_endpoint, CHANGE_STATUS_FIELD: "unchanged"})
    for endpoint_id in base_order:
        if endpoint_id not in seen:
            # 只有已确认删除页面的独占接口可删除；模型省略共用接口时仍继承基线。
            base_endpoint = base_by_id[endpoint_id]
            replacement = (text_replacements or {}).get(endpoint_id)
            candidate = (
                replace_endpoint_text(base_endpoint, replacement)
                if replacement is not None and endpoint_id not in (force_deleted or set())
                else base_endpoint
            )
            merged.append({
                **candidate,
                CHANGE_STATUS_FIELD: (
                    "deleted" if endpoint_id in (force_deleted or set())
                    else "modified" if _canonical(candidate) != _canonical(base_endpoint)
                    else "unchanged"
                ),
            })
    return merged
