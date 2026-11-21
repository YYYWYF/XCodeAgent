"""跨迭代交接已确认的 Endpoint 字段映射，避免新迭代把用户配好的映射清空。

**为什么需要它**：Endpoint 的字段映射正式产物写在 `.devagentstudio/plans/endpoints/`
下（`endpoint--<contract>--<endpoint>.json/.md`），而 `plans/` 在
`iteration_service._CLEARABLE_DIRS` 里，发起新迭代时会被整个删除。

这和产品规划、技术规划的处境完全一样：那些产物同样是用户在上一轮确认过的成果，
已经有各自的 carryover 把它们带进下一轮。唯独字段映射没有 —— 结果是用户进入新
迭代、点开一个**上轮已经开发完**的页面时，所有映射都显示"待配置"，
必须逐项重配一遍才能继续。

本文件在 `start_iteration` 清空 `plans/` 之前，把当轮 `plans/endpoints/` 下的
已确认设计（JSON + Markdown 成对）登记到
`.devagentstudio/endpoint-design-carryover.json`，新一轮启动后原样恢复到
`plans/endpoints/`，再由 `read_endpoint_design` 的内容级校验决定是否仍然有效。

**为什么恢复而不是由读取方直接回退读交接文件**：`endpoint_design_paths` 是唯一的
规范位置，其它诊断、导出与工作台都按这个路径读取。在启动时一次性恢复到规范位置，
比让每个读取方都懂"两处查找"要简单，也不会出现两份来源不一致的问题。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.repository_branch import read_workspace_branch_name

logger = logging.getLogger("uvicorn.error")

ENDPOINT_DESIGN_CARRYOVER_RELATIVE_PATH = WORKSPACE_ARTIFACT_DIR / "endpoint-design-carryover.json"

# 字段映射正式产物所在的目录（相对 .devagentstudio）。
ENDPOINT_DESIGN_DIR_RELATIVE = Path("plans") / "endpoints"

_SCHEMA_VERSION = 1


def endpoint_design_carryover_path(workspace: str | Path) -> Path:
    """返回工作区的 Endpoint 设计交接文件路径。"""

    return Path(workspace).expanduser().resolve() / ENDPOINT_DESIGN_CARRYOVER_RELATIVE_PATH


def read_endpoint_design_carryover(workspace: str | Path) -> dict[str, str]:
    """读取交接的映射文件内容（文件名 → 正文）；缺失或损坏返回空字典。"""

    try:
        payload = json.loads(endpoint_design_carryover_path(workspace).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        return {}
    return {
        str(name): str(content)
        for name, content in files.items()
        if isinstance(name, str) and isinstance(content, str) and content.strip()
    }


def write_endpoint_design_carryover(
    workspace: str | Path,
    *,
    files: dict[str, str] | None = None,
    source_branch: str = "",
) -> None:
    """登记本轮映射产物；`files` 为空时清掉旧记录。

    只登记 `.json` / `.md` 文件本身，不登记目录结构：规范路径是固定的
    `plans/endpoints/<name>`，恢复时按文件名放回即可。
    """

    path = endpoint_design_carryover_path(workspace)
    if not files:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("endpoint_design_carryover_clear_failed")
        return

    payload: dict[str, Any] = {
        "schemaVersion": _SCHEMA_VERSION,
        "sourceBranch": str(source_branch or "").strip(),
        "files": files,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.exception("endpoint_design_carryover_persist_failed")


def collect_endpoint_design_files(devagentstudio_dir: Path) -> dict[str, str]:
    """读取当前目录下全部映射产物内容，供新一轮恢复。

    只收 `.json` 与 `.md`；一份设计是成对使用的，只要有一半缺失就整对跳过 ——
    恢复出半套文件只会让 `read_endpoint_design` 判成"双文件不完整"。
    """

    directory = devagentstudio_dir / ENDPOINT_DESIGN_DIR_RELATIVE
    if not directory.is_dir():
        return {}
    by_stem: dict[str, dict[str, str]] = {}
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or entry.suffix not in {".json", ".md"}:
            continue
        try:
            by_stem.setdefault(entry.stem, {})[entry.suffix] = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            logger.warning("endpoint_design_carryover_read_failed: %s", entry.name)
    # 成对校验：设计 JSON 与 Markdown 缺一不可，否则恢复出来也是"双文件不完整"。
    result: dict[str, str] = {}
    for stem, parts in by_stem.items():
        if ".json" in parts and ".md" in parts and parts[".json"].strip() and parts[".md"].strip():
            result[f"{stem}.json"] = parts[".json"]
            result[f"{stem}.md"] = parts[".md"]
    return result


def restore_endpoint_design_files(workspace: str | Path) -> int:
    """把交接文件恢复到规范目录，返回恢复的文件数。

    已存在的同名文件不覆盖：新一轮可能已经生成了更新的设计，旧交接只是兜底。
    """

    files = read_endpoint_design_carryover(workspace)
    if not files:
        return 0
    directory = Path(workspace).expanduser().resolve() / WORKSPACE_ARTIFACT_DIR / ENDPOINT_DESIGN_DIR_RELATIVE
    restored = 0
    for name, content in files.items():
        target = directory / name
        if target.exists():
            continue
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            restored += 1
        except OSError:
            logger.exception("endpoint_design_carryover_restore_failed: %s", name)
    return restored
