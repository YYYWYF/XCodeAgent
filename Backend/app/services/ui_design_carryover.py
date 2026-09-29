"""跨迭代交接「上一轮已确认的 UI 设计稿」，供新迭代直接继承。

**为什么需要单独一个文件**：`specs/` 与 `ui-design/` 都在发起新迭代时被清空
（见 `iteration_service._CLEARABLE_DIRS`）。而"上一轮哪些页面的设计稿已经确认过"
这个事实必须在清空**之后**仍然可读 —— 否则新迭代无从判断哪些页面可以直接沿用，
用户每轮都得把同样的页面重新生成一遍。

本文件（`.devagentstudio/ui-design-carryover.json`）与 `iteration-artifacts.json` 同理，
**刻意不放进清空清单**：它是跨迭代的交接事实，不是本轮产物。

只记录交接所需的**最小信息**（PageKey 与模板来源）。设计稿代码本体留在
`ui-design/pages/<PageKey>/`，由清空流程按本文件登记的 PageKey 一并保留，
读取时用 `ui_design_generator.load_page_code` 取回。

继承不是无条件的：新迭代可能增删了某一页的信息项/操作，旧设计稿就对不上了。
真正的取舍在 `ui_confirmation._carried_ui_page_manifest`，那里会按当前 ProductPlan
重新做一次一致性校验。本模块只负责搬运事实。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.branding import WORKSPACE_ARTIFACT_DIR

logger = logging.getLogger("uvicorn.error")

UI_DESIGN_CARRYOVER_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / "ui-design-carryover.json"

_SCHEMA_VERSION = 1

# 交接记录里每个页面携带的字段。缺一个都不算一条可用的继承记录。
_PAGE_FIELDS = ("pageKey", "templateId", "templateSourcePath")


def ui_design_carryover_path(workspace: str | Path) -> Path:
    """返回工作区的设计稿交接记录文件路径。"""

    return Path(workspace).expanduser().resolve() / UI_DESIGN_CARRYOVER_RELATIVE_PATH


def read_ui_design_carryover(workspace: str | Path) -> dict[str, dict[str, str]]:
    """读取上一轮登记的设计稿交接记录。

    返回 `{pageId: {"pageKey": ..., "templateId": ..., "templateSourcePath": ...}}`。

    文件缺失、损坏或没有 `pageKey` 的条目一律按"没有这条记录"处理，而不是报错：
    继承是让用户少做重复工作的增强，读不出来时退回"本轮重新生成"即可，
    不该因此阻断整个设计阶段（与 `iteration_artifacts.read_iteration_artifacts` 同样的取舍）。
    """

    try:
        payload = json.loads(ui_design_carryover_path(workspace).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, dict):
        return {}

    carried: dict[str, dict[str, str]] = {}
    for page_id, record in raw_pages.items():
        if not isinstance(record, dict):
            continue
        page_key = str(record.get("pageKey") or "").strip()
        if not page_key:
            # 没有 PageKey 就找不到设计稿代码，这条记录没有交接价值。
            continue
        carried[str(page_id)] = {
            "pageKey": page_key,
            "templateId": str(record.get("templateId") or "").strip(),
            "templateSourcePath": str(record.get("templateSourcePath") or "").strip(),
        }
    return carried


def write_ui_design_carryover(
    workspace: str | Path,
    *,
    source_branch: str,
    pages: dict[str, dict[str, str]],
) -> None:
    """把本轮已确认的设计稿登记为下一轮的继承来源；写失败只记日志。

    `pages` 为空时**清掉**已有记录：那说明本轮没有可继承的设计稿，
    留着上一轮（或更早）的记录会让下一轮继承到过期的来源。
    """

    path = ui_design_carryover_path(workspace)
    if not pages:
        clear_ui_design_carryover(workspace)
        return

    payload = {
        "schemaVersion": _SCHEMA_VERSION,
        "sourceBranch": str(source_branch or "").strip(),
        "pages": {
            str(page_id): {field: str(record.get(field) or "") for field in _PAGE_FIELDS}
            for page_id, record in pages.items()
            if str(record.get("pageKey") or "").strip()
        },
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        # 继承是增强：写不进去也不能中断迭代发起。
        logger.exception("ui_design_carryover_persist_failed")


def clear_ui_design_carryover(workspace: str | Path) -> None:
    """删除交接记录；缺失时静默返回。"""

    try:
        ui_design_carryover_path(workspace).unlink(missing_ok=True)
    except OSError:
        logger.exception("ui_design_carryover_clear_failed")


def carried_page_keys(workspace: str | Path) -> set[str]:
    """取出交接记录里登记的 PageKey 集合，供清空流程决定保留哪些设计稿目录。"""

    return {record["pageKey"] for record in read_ui_design_carryover(workspace).values()}
