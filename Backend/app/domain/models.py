from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal


TaskStatus = Literal["pending", "running", "completed", "failed"]
BuildUnitKind = Literal["application", "database", "backend", "frontend", "page"]
BuildTaskOwner = Literal["database", "backend", "frontend"]
BuildTaskExecutionStrategy = Literal["agent", "deterministic"]
BuildTaskPlatformExecutor = Literal["authorization.frontend_resources"]
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
DETERMINISTIC_PLATFORM_EXECUTOR_ALLOWLIST = frozenset(
    {"authorization.frontend_resources"}
)


class BuildTaskExecutionContractError(ValueError):
    """表示 Build Task 的执行策略或平台执行器不满足当前契约。"""


@dataclass(frozen=True)
class BuildTaskExecutionContract:
    """保存已校验的执行策略；owner 仍只属于代码领域模型。"""

    execution_strategy: BuildTaskExecutionStrategy
    platform_executor: BuildTaskPlatformExecutor | None = None


def resolve_build_task_execution_contract(
    task: Mapping[str, Any],
) -> BuildTaskExecutionContract:
    """解析当前 Task 的执行策略，并对 deterministic executor 执行显式白名单校验。"""

    task_id = str(task.get("id") or "<unknown>")
    strategy = task.get("execution_strategy", "agent")
    executor = task.get("platform_executor")
    if strategy not in {"agent", "deterministic"}:
        raise BuildTaskExecutionContractError(
            f"Task {task_id} execution_strategy {strategy!r} is unsupported."
        )
    if strategy == "agent":
        if executor is not None:
            raise BuildTaskExecutionContractError(
                f"Task {task_id} uses agent execution and must not declare platform_executor."
            )
        return BuildTaskExecutionContract(execution_strategy="agent")
    if executor not in DETERMINISTIC_PLATFORM_EXECUTOR_ALLOWLIST:
        raise BuildTaskExecutionContractError(
            f"Task {task_id} deterministic platform_executor {executor!r} is not allowlisted."
        )
    return BuildTaskExecutionContract(
        execution_strategy="deterministic",
        platform_executor=executor,
    )


@dataclass
class BuildTask:
    id: str
    owner: BuildTaskOwner
    description: str
    execution_strategy: BuildTaskExecutionStrategy = "agent"
    platform_executor: BuildTaskPlatformExecutor | None = None
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

    def __post_init__(self) -> None:
        """在领域模型构造时拒绝未知策略或未列入白名单的平台执行器。"""

        resolve_build_task_execution_contract(
            {
                "id": self.id,
                "execution_strategy": self.execution_strategy,
                "platform_executor": self.platform_executor,
            }
        )


@dataclass
class BuildUnit:
    """表示分层 Build DAG 中可准备、执行和聚合进度的业务单元。"""

    id: str
    kind: BuildUnitKind
    status: str = "not_prepared"
    task_ids: list[str] = field(default_factory=list)
    depends_on_unit_ids: list[str] = field(default_factory=list)
    source_refs: dict[str, Any] = field(default_factory=dict)
