"""application.json 的工作区级读取与原子持久化实现。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.services.application_config.schema import (
    ApplicationConfigError,
    validate_application_configuration,
    validate_application_snapshot,
)


class ApplicationConfigRepository:
    """封装 canonical application.json 的文件定位、读取和原子替换。"""

    def __init__(self, workspace_root: str | Path) -> None:
        """绑定单一工作区，避免调用方自行拼接配置文件路径。"""

        self._target = Path(workspace_root).expanduser().resolve() / ".xcodeagent" / "application.json"

    def load(self) -> dict[str, Any]:
        """读取并校验当前完整配置，不执行任何写入。"""

        if not self._target.is_file():
            raise ApplicationConfigError("当前工作区缺少 .xcodeagent/application.json。")
        try:
            application = json.loads(self._target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApplicationConfigError("当前工作区 application.json 无法读取或格式无效。") from exc
        return validate_application_snapshot(application)

    def save(self, application: dict[str, Any]) -> None:
        """校验后以 fsync 与同目录 replace 原子替换完整配置。"""

        validate_application_configuration(application)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".application.json.", suffix=".tmp", dir=self._target.parent, text=True
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(application, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._target)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
