"""Agent 设置确认的同锁文件事务，失败保留原正式产物与可再次操作的草稿。"""

from contextlib import contextmanager
from pathlib import Path

from app.services.application_lifecycle import _application_lifecycle_lock, application_lifecycle_path
from app.workspace.revision_draft_documents import revision_draft_directory, _write_text_atomically
from app.workspace.task_documents import build_task_plan_lifecycle_lock


@contextmanager
def agent_settings_commit(root: Path, change_id: str, artifact_key: str):
    """沿 Pending→Application 锁序提交，任一步异常时恢复本次事务实际涉及的七个文件。"""

    lifecycle = application_lifecycle_path(root)
    draft = revision_draft_directory(root, change_id=change_id, artifact_key=artifact_key)
    plans = root / ".devagentstudio/plans"
    paths = [plans / "technical-plan.json", plans / "technical-plan.md", plans / "build-task-plan.json",
             lifecycle, *(draft / name for name in ("artifact.md", "artifact.json", "metadata.json"))]
    with build_task_plan_lifecycle_lock(root), _application_lifecycle_lock(lifecycle):
        before = {path: path.read_bytes() if path.exists() else None for path in paths}
        try:
            yield
        except BaseException:
            errors = []
            for path, content in before.items():
                try:
                    if content is None:
                        path.unlink(missing_ok=True)
                    elif not path.is_file() or path.read_bytes() != content:
                        _write_text_atomically(path, content.decode("utf-8"))
                except OSError:
                    errors.append(path.relative_to(root).as_posix())
            if errors:
                raise RuntimeError("Agent 设置确认失败，以下文件未能恢复：" + "、".join(errors))
            raise
