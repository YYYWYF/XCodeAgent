from dataclasses import dataclass, field
from typing import Any, Literal


TaskStatus = Literal["pending", "running", "completed", "failed"]
BuildUnitKind = Literal["application", "database", "backend", "frontend", "page"]
BuildTaskOwner = Literal["database", "backend", "frontend"]
BuildTaskType = Literal[
    "database.change",
    "database.seed",
    "database.verify",
    "backend.code",
    "backend.verify",
    "frontend.code",
    "frontend.verify",
]
BuildTaskRisk = Literal["low", "medium", "high"]


@dataclass
class BuildTask:
    id: str
    owner: BuildTaskOwner
    description: str
    dependencies: list[str] = field(default_factory=list)
    status: TaskStatus = "pending"
    unit_id: str = "application:root"
    task_type: BuildTaskType | None = None
    requires_capabilities: list[str] = field(default_factory=list)
    provides_capabilities: list[str] = field(default_factory=list)
    database_scope: dict[str, Any] = field(default_factory=dict)
    risk: BuildTaskRisk = "low"
    approval: dict[str, Any] = field(default_factory=dict)
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
