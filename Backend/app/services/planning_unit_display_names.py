"""从冻结正式计划派生 Unit 名称，不读取磁盘或参与规划决策。"""

from app.services.frontend_page_tree import project_plan_page_records


_BUILTIN_UNIT_DISPLAY_NAMES = {
    "frontend:api-client": "前端接口调用封装",
    "frontend:data:static": "前端静态数据处理",
    "frontend:auth-guard": "页面访问权限配置",
    "frontend:shell": "前端应用环境",
    "frontend:route-registry": "前端页面路由配置",
    "backend:bootstrap": "后端基础环境配置",
    "application:root": "应用任务结构",
    "app:integration": "应用集成节点",
}


def project_plan_unit_display_names(project_plan: dict | None) -> dict[str, str]:
    """从同一份 TechnicalPlan 的业务名称生成只读 Unit 展示名称。"""

    labels = dict(_BUILTIN_UNIT_DISPLAY_NAMES)
    if not isinstance(project_plan, dict):
        return labels

    pages: list[tuple[str, str]] = []
    for index, page in enumerate(project_plan_page_records(project_plan), start=1):
        page_id = str(page.get("pageId") or page.get("id") or "").strip()
        if not page_id:
            continue
        name = _business_name(page.get("name"), excluded={page_id, f"page:{page_id}"})
        pages.append(
            (
                f"page:{page_id}",
                _append_business_suffix(name, "页面")
                if name
                else f"未命名页面（第 {index} 项）",
            )
        )
    labels.update(_unique_business_labels(pages, kind="页面"))

    endpoints: list[tuple[str, str]] = []
    endpoint_index = 0
    for contract in project_plan.get("api_contracts", []):
        if not isinstance(contract, dict):
            continue
        contract_id = str(contract.get("id") or "").strip()
        for endpoint in contract.get("endpoints", []):
            if not isinstance(endpoint, dict):
                continue
            endpoint_id = str(endpoint.get("id") or "").strip()
            if not contract_id or not endpoint_id:
                continue
            endpoint_index += 1
            unit_id = f"backend:endpoint:{contract_id}:{endpoint_id}"
            name = _business_name(
                endpoint.get("name"),
                excluded={endpoint_id, contract_id, unit_id},
            )
            label = (
                _append_business_suffix(name, "接口")
                if name
                else f"未命名接口（第 {endpoint_index} 项）"
            )
            endpoints.append((unit_id, label))
    labels.update(_unique_business_labels(endpoints, kind="接口"))
    return labels


def _business_name(value: object, *, excluded: set[str]) -> str:
    """只接受明确业务名称，避免把缺省 ID 误当作用户文案。"""

    name = str(value or "").strip()
    if not name or name in excluded:
        return ""
    return name


def _append_business_suffix(name: str, suffix: str) -> str:
    """补充页面或接口类别，避免重复添加已有类别词。"""

    return name if name.endswith(suffix) else f"{name}{suffix}"


def _unique_business_labels(
    entries: list[tuple[str, str]],
    *,
    kind: str,
) -> dict[str, str]:
    """为重复业务名称追加稳定的中文序号，不暴露内部身份。"""

    totals: dict[str, int] = {}
    for _, label in entries:
        totals[label] = totals.get(label, 0) + 1
    seen: dict[str, int] = {}
    result = {}
    for unit_id, label in entries:
        if totals[label] > 1:
            seen[label] = seen.get(label, 0) + 1
            label = f"{label}（第 {seen[label]} 个{kind}）"
        result[unit_id] = label
    return result
