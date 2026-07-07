from __future__ import annotations

from copy import deepcopy
from typing import Any


def _default_page_spec(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_id": page["id"],
        "page_name": page["name"],
        "path": page["path"],
        "page_goal": page["description"],
        "layout": {
            "structure": [
                "页面标题区",
                "主要内容区",
                "操作区",
                "状态反馈区",
            ],
            "responsive": "默认支持桌面端布局，后续可扩展移动端适配。",
        },
        "interactions": [
            "进入页面时加载所需数据。",
            "加载中展示 loading 状态。",
            "无数据时展示 empty 状态。",
            "接口失败时展示 error 状态和重试入口。",
            "用户执行主要操作后刷新页面数据。",
        ],
        "data_source_ids": page["data_dependencies"],
        "permissions": page["permissions"],
        "states": page["states"],
    }


def confirm_page_spec(
    page: dict[str, Any],
    confirmed_page_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a user-confirmed PageSpec payload.

    The runnable demo cannot pause for real user input yet. If upstream state
    provides `confirmed_page_spec`, this function treats it as the user's
    confirmed page spec. Otherwise it creates a default spec from the selected
    page and marks it as auto-confirmed.
    """

    default_spec = _default_page_spec(page)
    if confirmed_page_spec:
        spec = {**default_spec, **deepcopy(confirmed_page_spec)}
        spec["page_id"] = page["id"]
        spec["page_name"] = page["name"]
        spec["path"] = page["path"]
        status = "confirmed"
    else:
        spec = default_spec
        status = "auto_confirmed"

    return {
        "type": "page_spec_confirmation",
        "status": status,
        "prompt": "请确认该页面的目标、布局、交互、数据来源和权限。",
        "confirmed_page_spec": spec,
        "message": "当前最简版使用传入的 confirmed_page_spec；若未传入则根据页面计划自动确认默认 PageSpec。",
    }
