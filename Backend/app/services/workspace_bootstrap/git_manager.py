"""首次模板物化所需的独立 Git baseline 创建器。"""

from __future__ import annotations

from pathlib import Path

from app.branding import WORKSPACE_ARTIFACT_DIR_NAME
from app.services.git_commit_message import template_baseline_commit_message
from app.services.git_identity import apply_commit_identity
from app.services.workspace_bootstrap.models import WorkspaceBootstrapError
from app.services.workspace_process_registry import workspace_process_registry
from app.services.template_state import TEMPLATE_STATE_RELATIVE_PATH, load_template_state
from app.services.workspace_bootstrap.template_layout import STANDARD_ROOTS, template_roots


class BootstrapGitError(WorkspaceBootstrapError):
    """表示首次 Git 初始化或 baseline 提交未完成。"""

    code = "WORKSPACE_BOOTSTRAP_GIT_FAILED"


# .devagentstudio 下的运行时产物目录，不应进入版本控制。
_RUNTIME_ARTIFACT_DIRS = ("runtime", "cache", "checkpoints")

# 依赖目录与构建产物：发布时 `git add -A` 会把工作区所有未忽略文件加进暂存区，
# 不排除 node_modules 会导致数万文件被加入，git 同时输出大量 LF/CRLF warning 把
# stderr 缓冲区塞满，最终让整个 add 命令超时/失败（用户看到"无法暂存工作区变更"）。
_GITIGNORE_CONTENT = "\n".join(
    [
        # 运行时产物（日志、缓存、checkpoint sqlite）。
        *(f"{WORKSPACE_ARTIFACT_DIR_NAME}/{name}/" for name in _RUNTIME_ARTIFACT_DIRS),
        # 前端依赖与构建产物。
        "node_modules/",
        "dist/",
        "build/",
        # 后端依赖与构建产物（Java/Maven + Python）。
        "target/",
        "__pycache__/",
        "*.pyc",
        ".venv/",
        # 操作系统/编辑器产物。
        ".DS_Store",
        "Thumbs.db",
    ]
) + "\n"


class BootstrapGitManager:
    """为新工作区创建模板 baseline（含 .devagentstudio 规划产物）。"""

    def initialize_baseline(self, workspace: str | Path) -> str:
        """初始化独立仓库、固定本地身份并提交 frontend/backend/.devagentstudio 规划产物。"""

        root = Path(workspace).expanduser().resolve()
        self._run(root, ["git", "init"])
        # 提交身份必须用仓库服务端认可的邮箱：公司内 Gitee 会校验 author email，
        # 未授权就拒绝推送并报 git002。这里不再硬编码占位身份。
        apply_commit_identity(root, self._run)
        # 排除运行时产物，避免日志、缓存和 checkpoint 污染后续提交检查。
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE_CONTENT, encoding="utf-8")
        # 提交 frontend/backend 和 .devagentstudio 规划产物（运行时目录已被 gitignore 排除）。
        state_path = root / TEMPLATE_STATE_RELATIVE_PATH
        roots = template_roots(load_template_state(root)["requested"]) if state_path.exists() else STANDARD_ROOTS
        add_paths = [*roots, ".gitignore"]
        if (root / WORKSPACE_ARTIFACT_DIR_NAME).is_dir():
            add_paths.append(WORKSPACE_ARTIFACT_DIR_NAME)
        self._run(root, ["git", "add", "--", *add_paths])
        self._run(root, ["git", "commit", "-m", template_baseline_commit_message()])
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
