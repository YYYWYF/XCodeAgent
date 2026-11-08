"""校验目录的业务覆盖，并计算向上修复后的候选依赖失效范围。"""

from copy import deepcopy
from app.agents.main.technical_plan_contract_budget import contract_part_prefix
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CatalogBinding(BaseModel):
    """一个页面内的业务动作或业务步骤必须由一个目录接口承接。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    pageId: str = Field(min_length=1)
    actionId: str = Field(min_length=1)
    stepId: str | None
    endpointId: str = Field(min_length=1)


class _CatalogIssue(BaseModel):
    """页面只能报告自己的业务动作或首次加载目录缺口。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    pageId: str = Field(min_length=1)
    kind: Literal["business_action", "initial_load"]
    actionId: str | None
    stepId: str | None
    reason: str = Field(min_length=1, max_length=2000)


class CatalogRepairRequired(ValueError):
    """保留已校验的目录缺口身份，交给独立有界的目录修复链。"""

    def __init__(self, issue: dict, *, diagnostic: str | None = None):
        """错误文本供原失败审阅展示，结构化事实仅留在内部候选。"""

        self.issue = issue
        super().__init__(diagnostic or f"页面 {issue['pageId']} 需要修复共享接口目录：{issue['reason']}")


def business_slots(product: dict) -> set[tuple[str, str, str | None]]:
    """只提取已确认产品中的业务动作及组合内业务步骤，不推断接口语义。"""

    slots = set()
    for page in product.get("pages", []):
        for action in page.get("actions", []):
            behavior = action.get("behavior", {})
            if behavior.get("type") == "business":
                slots.add((page["pageId"], action["actionId"], None))
            elif behavior.get("type") == "sequence":
                slots.update((page["pageId"], action["actionId"], step["stepId"])
                    for step in behavior.get("steps", []) if step.get("type") == "business")
    return slots


def validate_catalog_coverage(catalog: dict, product: dict) -> None:
    """完整覆盖产品业务槽位，拒绝漏项、重复、未知接口及错误页面归属。"""

    expected, actual = business_slots(product), set()
    endpoints = {endpoint["id"]: set(contract["page_ids"])
        for contract in catalog["contracts"] for endpoint in contract["endpoints"]}
    for binding in catalog["action_bindings"]:
        slot = (binding["pageId"], binding["actionId"], binding["stepId"])
        if slot not in expected or slot in actual:
            raise ValueError(f"目录业务覆盖包含未知或重复 action/step：{slot}。")
        if binding["pageId"] not in endpoints.get(binding["endpointId"], set()):
            raise ValueError(f"目录业务覆盖 {slot} 引用了未知接口或未声明所属页面。")
        actual.add(slot)
    if expected - actual:
        missing = sorted(expected - actual, key=str)
        raise ValueError(f"目录遗漏业务 action/step 的接口覆盖：{missing}。")


def raise_catalog_issue(value: dict, page: dict) -> None:
    """严格校验模型缺口报告，未知身份或混合正文不能触发目录失效。"""

    if set(value) != {"catalog_issue"}:
        raise ValueError("目录缺口只能返回单独的 catalog_issue。")
    issue = _CatalogIssue.model_validate(value["catalog_issue"]).model_dump()
    if issue["pageId"] != page["pageId"]:
        raise ValueError("目录缺口不得指向其他页面。")
    slot = (issue["pageId"], issue["actionId"], issue["stepId"])
    if issue["kind"] == "business_action" and slot not in business_slots({"pages": [page]}):
        raise ValueError("目录缺口必须引用本页已声明的业务 action/step。")
    if issue["kind"] == "initial_load" and (issue["actionId"] is not None or issue["stepId"] is not None):
        raise ValueError("首次加载目录缺口不能携带 actionId 或 stepId。")
    raise CatalogRepairRequired(issue)


def repaired_catalog_fragments(cache: dict, catalog: dict) -> dict:
    """目录修复与下游失效必须同批发布，未受影响的契约和页面保持可复用。"""

    repair = cache["catalog_repair"]
    previous, issue = repair["baseline"], repair["issue"]
    old_specs = {item["id"]: item for item in previous["contracts"]}
    new_specs = {item["id"]: item for item in catalog["contracts"]}
    old_entities = {item["id"]: item for item in previous["entities"]}
    new_entities = {item["id"]: item for item in catalog["entities"]}
    changed = set()
    for key in old_specs.keys() | new_specs.keys():
        old, new = old_specs.get(key), new_specs.get(key)
        entity_ids = set((old or {}).get("entity_ids", [])) | set((new or {}).get("entity_ids", []))
        if old != new or any(old_entities.get(entity) != new_entities.get(entity) for entity in entity_ids):
            changed.add(key)
    affected = {page_id for key in changed for specs in (old_specs, new_specs)
        for page_id in specs.get(key, {}).get("page_ids", [])}
    old_bindings = { (b["pageId"], b["actionId"], b["stepId"]): b["endpointId"] for b in previous["action_bindings"] }
    new_bindings = { (b["pageId"], b["actionId"], b["stepId"]): b["endpointId"] for b in catalog["action_bindings"] }
    affected.update(slot[0] for slot in old_bindings.keys() | new_bindings.keys()
        if old_bindings.get(slot) != new_bindings.get(slot))
    # 实际引用也构成依赖，即使目录 page_ids 漏写，也不能复用受影响的旧页面。
    endpoint_ids = {endpoint["id"] for key in changed for specs in (old_specs, new_specs)
        for endpoint in specs.get(key, {}).get("endpoints", [])}
    for key, page in cache.items():
        if key.startswith("page:") and any(dep["endpoint_id"] in endpoint_ids
                for dep in page["references"]["endpoint_dependencies"]):
            affected.add(page["pageId"])
    if issue["pageId"] not in affected:
        raise ValueError("共享目录修复未调整报告缺口的页面依赖，拒绝重复使用原错误目录。")
    retained = {key: value for key, value in cache.items()
        if key not in {"catalog", "catalog_repair"}
        and not (key.startswith("contract:") and key[9:] in changed)
        and not any(key.startswith(contract_part_prefix(contract_id)) for contract_id in changed)
        and not (key.startswith("page:") and key[5:] in affected)}
    return deepcopy({**retained, "catalog": catalog})
