"""绑定单次代码审查运行的受控 Skill 加载与报告保存工具。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.builtin_skills import resolve_builtin_skills_root
from app.services.code_analysis import atomic_write_code_audit_report


MAYUN_CODE_REVIEW_SKILL_NAME = "mayun-frontend-code-review"


class SaveCodeAuditReportInput(BaseModel):
    """限制受控报告工具只接收 Markdown 正文。"""

    content: str = Field(min_length=1, max_length=512 * 1024)


def create_code_audit_tools(
    workspace_root: Path,
    report_relative_path: str,
    *,
    cancellation_requested: Callable[[], bool] | None = None,
) -> tuple[list[Any], dict[str, Any]]:
    """创建共享运行状态的强制 Skill 加载和原子报告保存工具。"""

    state: dict[str, Any] = {"skillLoaded": False, "loadCount": 0}
    is_cancelled = cancellation_requested or (lambda: False)

    @tool("load_mayun_frontend_code_review_skill")
    def load_mayun_frontend_code_review_skill() -> str:
        """按固定顺序加载前端审查 Skill、检查规则和报告模板。"""

        if is_cancelled():
            raise RuntimeError("代码审查运行已取消。")
        skill_root = resolve_builtin_skills_root() / MAYUN_CODE_REVIEW_SKILL_NAME
        relative_paths = (
            "SKILL.md",
            "references/security_checks.md",
            "references/report_template.md",
        )
        documents: list[str] = []
        for relative_path in relative_paths:
            path = skill_root / relative_path
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise RuntimeError(f"无法加载必选审查规范 {relative_path}。") from exc
            documents.append(f"\n\n--- {relative_path} ---\n{content}")
        state["skillLoaded"] = True
        state["loadCount"] = int(state["loadCount"]) + 1
        return "".join(documents).strip()

    @tool("save_code_audit_report", args_schema=SaveCodeAuditReportInput)
    def save_code_audit_report(content: str) -> str:
        """仅在必选 Skill 已加载后原子保存本次唯一正式报告。"""

        if is_cancelled():
            raise RuntimeError("代码审查运行已取消。")
        if not state["skillLoaded"]:
            raise RuntimeError("保存报告前必须先调用 mayun-frontend-code-review Skill。")
        target = atomic_write_code_audit_report(
            workspace_root,
            report_relative_path,
            content,
            cancellation_requested=is_cancelled,
        )
        state["reportSaved"] = True
        return json.dumps(
            {
                "status": "saved",
                "reportPath": target.relative_to(workspace_root).as_posix(),
                "sizeBytes": target.stat().st_size,
            },
            ensure_ascii=False,
        )

    return [load_mayun_frontend_code_review_skill, save_code_audit_report], state
