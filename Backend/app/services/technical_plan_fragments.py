"""保存当前技术生成的已验证候选片段，不授予正式产物或工作流推进权限。"""

from pathlib import Path
import json
import os
import re
import tempfile
from threading import RLock
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.workspace.content_artifacts import put_artifact, read_artifact


_LOCK = RLock()


class TechnicalPlanFragmentStore:
    """一个工作区只保留当前输入的片段目录，正文复用已有内容寻址存储。"""

    def __init__(self, workspace: str | Path):
        """把候选存储固定在受管运行目录，禁止符号链接越界。"""

        self.workspace = Path(workspace).expanduser().resolve()
        self.directory = self.workspace / WORKSPACE_ARTIFACT_DIR / "runtime"
        self.path = self.directory / "technical-plan-generation.json"
        for path in (self.directory.parent, self.directory, self.path):
            if path.is_symlink():
                raise ValueError("技术规划片段目录不能使用符号链接。")

    def _read(self) -> dict[str, Any] | None:
        """严格读取唯一当前目录，不扫描历史文件或猜测损坏格式。"""

        if not self.path.exists():
            return None
        if self.path.is_symlink():
            raise ValueError("技术规划片段文件不能使用符号链接。")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or set(value) != {"inputSha256", "completed"}
                or not isinstance(value["inputSha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", value["inputSha256"]) is None
                or not isinstance(value["completed"], dict)
                or any(not isinstance(key, str) or not isinstance(ref, dict) for key, ref in value["completed"].items())):
            raise ValueError("技术规划片段目录不符合当前合同。")
        return value

    def _sync_directory(self) -> None:
        """同步目录条目，避免成功回执先于原子替换真正持久化。"""

        if os.name != "nt":
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _write(self, value: dict) -> None:
        """完整写入临时文件、同步字节并原子替换当前片段目录。"""

        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".technical-plan-generation-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            self._sync_directory()
        finally:
            Path(name).unlink(missing_ok=True)

    def load(self, input_sha256: str) -> dict[str, dict]:
        """仅复用完全相同的当前输入，变化时原子替换目录并清空旧引用。"""

        if re.fullmatch(r"[0-9a-f]{64}", input_sha256) is None:
            raise ValueError("技术规划片段输入摘要无效。")
        with _LOCK:
            value = self._read()
            if value is None or value["inputSha256"] != input_sha256:
                self._write({"inputSha256": input_sha256, "completed": {}})
                return {}
            result = {key: read_artifact(self.workspace, reference) for key, reference in value["completed"].items()}
            if any(not isinstance(fragment, dict) for fragment in result.values()):
                raise ValueError("技术规划片段必须是完整对象。")
            return result

    def save(self, input_sha256: str, key: str, fragment: dict) -> None:
        """先发布已验证正文，再持久化引用；下一次模型调用必须等待本次保存完成。"""

        with _LOCK:
            current = self._read()
            if current is None or current["inputSha256"] != input_sha256:
                raise ValueError("技术规划生成输入已改变，拒绝写入迟到片段。")
            reference = put_artifact(self.workspace, fragment)
            current["completed"][key] = reference
            self._write(current)

    def clear(self, input_sha256: str) -> None:
        """正式提交成功后只清理对应输入的当前目录，不删除已引用的不可变正文。"""

        if not input_sha256:
            return
        with _LOCK:
            current = self._read()
            if current is not None and current["inputSha256"] == input_sha256:
                self.path.unlink()
                self._sync_directory()

    def replace(self, input_sha256: str, fragments: dict[str, dict]) -> None:
        """原子发布目录修复及依赖失效，进程退出后不能读到混合版本。"""

        with _LOCK:
            current = self._read()
            if current is None or current["inputSha256"] != input_sha256:
                raise ValueError("技术规划生成输入已改变，拒绝写入迟到片段。")
            references = {key: put_artifact(self.workspace, fragment) for key, fragment in fragments.items()}
            self._write({"inputSha256": input_sha256, "completed": references})
