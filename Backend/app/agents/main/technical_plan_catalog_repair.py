"""校验接口目录对已确认业务动作的引用与完整覆盖。"""

from pydantic import BaseModel, ConfigDict, Field


class CatalogBinding(BaseModel):
    """一个页面内的业务动作或业务步骤必须由一个目录接口承接。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    pageId: str = Field(min_length=1)
    actionId: str = Field(min_length=1)
    stepId: str | None
    endpointId: str = Field(min_length=1)


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
