"""标准预览各阶段共用的故障证据合同。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DiagnosticLocation(BaseModel):
    """保存已验证位于工作区内的文件位置。"""

    model_config = ConfigDict(extra="forbid")
    path: str
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)


class DiagnosticCommand(BaseModel):
    """保留实际执行的命令、工作目录和退出状态。"""

    model_config = ConfigDict(extra="forbid")
    argv: list[str] = Field(default_factory=list)
    cwd: str | None = None
    exitCode: int | None = None
    timedOut: bool = False


class PreviewDiagnostic(BaseModel):
    """区分错误来源并携带原始证据，不预设具体文件的修复方法。"""

    model_config = ConfigDict(extra="forbid")
    stage: str
    category: Literal["parse", "dependency", "compile", "process", "readiness"]
    message: str
    code: str | None = None
    location: DiagnosticLocation | None = None
    command: DiagnosticCommand | None = None
    logPaths: list[str] = Field(default_factory=list)
    excerpt: str = ""
