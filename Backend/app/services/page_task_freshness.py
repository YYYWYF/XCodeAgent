"""把页面任务的覆盖声明绑定到生成时的正式页面合同。"""

from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Any

from app.services.frontend_page_tree import project_plan_page_records
from app.services.planning_frozen import plain_json


def page_task_input_digest(unit_id: str, project_plan: Mapping[str, Any]) -> str:
    """摘要当前页面实现合同和技术方案；其他页面变化不使本页任务失效。"""

    page_id = unit_id.removeprefix("page:")
    plan = plain_json(project_plan)
    payload = {
        "frontend_architecture": plan.get("architecture", {}).get("frontend"),
        "page": next((page for page in project_plan_page_records(plan)
                      if page.get("pageId") == page_id), None),
        "contract": next((contract for contract in plan.get("page_implementation_contracts", [])
                          if contract.get("pageId") == page_id), None),
    }
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def page_task_covers_current_input(task: Mapping[str, Any], project_plan: Mapping[str, Any]) -> bool:
    """旧页面任务仍保留，但只有合同摘要一致时才能覆盖本轮页面职责。"""

    unit_id = task["unit_id"]
    if not unit_id.startswith("page:"):
        return True
    refs = task.get("source_refs")
    return isinstance(refs, Mapping) and refs.get("page_input_sha256") == page_task_input_digest(unit_id, project_plan)
