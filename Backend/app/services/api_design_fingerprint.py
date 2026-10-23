"""接口映射的业务正文摘要，供任务复用与业务验收使用同一口径。"""

from collections.abc import Mapping
from typing import Any


def api_design_content(design: Mapping[str, Any]) -> dict[str, Any]:
    """去除确认签名与全局依赖声明，实际字段、规则和来源快照全部参与摘要。"""
    return {key: value for key, value in design.items()
            if key not in {"artifactRevision", "confirmedAt", "basedOn", "status", "confirmationStatus"}}
