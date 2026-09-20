"""首次模板物化所需的独立 Git baseline 创建器。"""

from __future__ import annotations

from pathlib import Path

from app.services.workspace_bootstrap.models import WorkspaceBootstrapError
from app.services.workspace_process_registry import workspace_process_registry


class BootstrapGitError(WorkspaceBootstrapError):
    """表示首次 Git 初始化或 baseline 提交未完成。"""

    code = "WORKSPACE_BOOTSTRAP_GIT_FAILED"


# .xcodeagent 下的运行时产物目录，不应进入版本控制。
_RUNTIME_ARTIFACT_DIRS = ("runtime", "cache", "checkpoints")

_GITIGNORE_CONTENT = "\n".join(
    f".xcodeagent/{name}/"
    for name in _RUNTIME_ARTIFACT_DIRS
) + "\n"


class BootstrapGitManager:
    """为新工作区创建模板 baseline（含 .xcodeagent 规划产物）。"""

    def initialize_baseline(self, workspace: str | Path) -> str:
        """初始化独立仓库、固定本地身份并提交 frontend/backend/.xcodeagent 规划产物。"""

        root = Path(workspace).expanduser().resolve()
        self._run(root, ["git", "init"])
        self._run(root, ["git", "config", "--local", "user.name", "XcodeAgent"])
        self._run(root, ["git", "config", "--local", "user.email", "xcodeagent@local"])
        # 排除运行时产物，避免日志、缓存和 checkpoint 污染后续提交检查。
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE_CONTENT, encoding="utf-8")
        # 提交 frontend/backend 和 .xcodeagent 规划产物（运行时目录已被 gitignore 排除）。
        add_paths = ["frontend", "backend", ".gitignore"]
        if (root / ".xcodeagent").is_dir():
            add_paths.append(".xcodeagent")
        self._run(root, ["git", "add", "--", *add_paths])
        self._run(root, ["git", "commit", "-m", "chore: 模板初始化"])
        return self._run(root, ["git", "rev-parse", "HEAD"]).strip()

    def verify_baseline(self, workspace: str | Path) -> str:
        """确认 baseline 已存在且工作树干净。"""

        root = Path(workspace).expanduser().resolve()
        head = self._run(root, ["git", "rev-parse", "--verify", "HEAD"]).strip()
        if not head:
            raise BootstrapGitError("Git baseline 缺少 HEAD 提交。")
        if self._run(root, ["git", "status", "--porcelain"]).strip():
            raise BootstrapGitError("Git baseline 提交后工作树必须保持干净。")
        return head

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
