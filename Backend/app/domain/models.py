from dataclasses import dataclass, field
from typing import Any, Literal


TaskStatus = Literal["pending", "running", "completed", "failed"]
BuildUnitKind = Literal["application", "page", "data_source", "endpoint"]


@dataclass
class BuildTask:
    id: str
    owner: Literal["frontend", "data_source"]
    description: str
    dependencies: list[str] = field(default_factory=list)
    status: TaskStatus = "pending"
    unit_id: str = "application:root"
    source_refs: dict[str, Any] = field(default_factory=dict)


@dataclass
class BuildUnit:
    """表示分层 Build DAG 中可准备、执行和聚合进度的业务单元。"""

    id: str
    kind: BuildUnitKind
    status: str = "not_prepared"
    task_ids: list[str] = field(default_factory=list)
    depends_on_unit_ids: list[str] = field(default_factory=list)
    source_refs: dict[str, Any] = field(default_factory=dict)
