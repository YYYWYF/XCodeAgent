"""首次模板物化所需的独立 Git baseline 创建器。"""

from __future__ import annotations

from pathlib import Path

from app.services.workspace_bootstrap.models import WorkspaceBootstrapError
from app.services.workspace_process_registry import workspace_process_registry


class BootstrapGitError(WorkspaceBootstrapError):
    """表示首次 Git 初始化或 baseline 提交未完成。"""

    code = "WORKSPACE_BOOTSTRAP_GIT_FAILED"


class BootstrapGitManager:
    """只为新工作区创建不含 `.xcodeagent` 的模板 baseline。"""

    def initialize_baseline(self, workspace: str | Path) -> str:
        """初始化独立仓库、固定本地身份并提交 frontend/backend。"""

        root = Path(workspace).expanduser().resolve()
        self._run(root, ["git", "init"])
        self._run(root, ["git", "config", "--local", "user.name", "XcodeAgent"])
        self._run(root, ["git", "config", "--local", "user.email", "xcodeagent@local"])
        exclude = root / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(".xcodeagent/\n", encoding="utf-8")
        self._run(root, ["git", "add", "--", "frontend", "backend"])
        self._run(root, ["git", "commit", "-m", "chore: initialize workspace from template"])
        return self._run(root, ["git", "rev-parse", "HEAD"]).strip()

    def _run(self, workspace: Path, arguments: list[str]) -> str:
        """经工作区进程登记执行 Git，并将失败收敛为 Bootstrap 错误。"""

        try:
            result = workspace_process_registry.run(
                arguments,
                workspace=workspace,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:
            raise BootstrapGitError("执行 Git baseline 命令失败。") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown error").strip()
            raise BootstrapGitError(f"Git baseline 命令失败：{detail[:512]}")
        return str(result.stdout or "")
