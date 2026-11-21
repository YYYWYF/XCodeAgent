"""跨迭代交接 RequirementSpec 的"上一迭代基线"，供新一轮做增量合并与变更标记。

**为什么需要它**：`iteration_service.start_iteration` 会保留 `specs/requirement-spec.json`
作为新迭代 LLM 的种子（见 `_CLEARABLE_DIRS` 注释）。但 LLM 在重新生成 spec 时可能
**简化、重写或丢失**基线里已有的页面/角色/模块，让旧迭代实现的页面在新迭代里"消失"。

本文件（`.devagentstudio/requirement-spec-carryover.json`）与 `product-plan-carryover.json`
同理，**刻意不放进 `_CLEARABLE_DIRS`**：它是跨迭代的基线事实，不是本轮产物。

新一轮 `requirements` 节点在 LLM 输出后调 `requirement_spec_merger.merge_requirement_spec`，
把"LLM 重新生成的 spec"与"本文件登记的基线 spec"按 `pageId` 合并 —— 未在 LLM 输出里
出现的基线页面会被自动补回（_change_status=unchanged），防止"被静默删除"。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR

logger = logging.getLogger("uvicorn.error")

REQUIREMENT_SPEC_CARRYOVER_RELATIVE_PATH = (
    WORKSPACE_ARTIFACT_DIR / "requirement-spec-carryover.json"
)

_SCHEMA_VERSION = 1


def requirement_spec_carryover_path(workspace: str | Path) -> Path:
    """返回工作区的需求文档交接记录文件路径。"""

    return Path(workspace).expanduser().resolve() / REQUIREMENT_SPEC_CARRYOVER_RELATIVE_PATH


def read_requirement_spec_carryover(workspace: str | Path) -> dict[str, Any] | None:
    """读取基线 RequirementSpec（完整一份），失败返回 None。

    返回 None 表示"没有基线"（首轮创建 / 上一迭代没产出 / 文件被外部破坏），
    调用方按"从零创建"处理，不做合并。
    """

    try:
        payload = json.loads(
            requirement_spec_carryover_path(workspace).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    spec = payload.get("spec")
    return spec if isinstance(spec, dict) else None


def write_requirement_spec_carryover(
    workspace: str | Path,
    *,
    source_branch: str,
    spec: dict[str, Any] | None,
) -> None:
    """登记本轮的 RequirementSpec 作为下一轮的基线；写失败只记日志。

    `spec` 为 None 时**清掉**已有记录：那说明本轮没有可沿用的产物（比如首轮
    创建还没生成 spec），留着旧记录会让下一轮错误地以为有基线。
    """

    path = requirement_spec_carryover_path(workspace)
    if spec is None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("requirement_spec_carryover_clear_failed")
        return

    payload = {
        "schemaVersion": _SCHEMA_VERSION,
        "sourceBranch": str(source_branch or "").strip(),
        "spec": spec,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        logger.exception("requirement_spec_carryover_persist_failed")
