from dataclasses import dataclass, field
from typing import Literal


TaskStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class BuildTask:
    id: str
    owner: Literal["frontend", "data_source"]
    description: str
    dependencies: list[str] = field(default_factory=list)
    status: TaskStatus = "pending"
