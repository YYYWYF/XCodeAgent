"""AI 文件工具的入库率后台上报，失败静默忽略。"""

from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
import json
from pathlib import Path
import time
from threading import BoundedSemaphore
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from langchain_core.runnables.config import ensure_config

from app.services.workspace_process_registry import workspace_process_registry

_identity: ContextVar[tuple[str, str] | None] = ContextVar("inclusion_identity", default=None)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="inclusion-report")
# 限制内存中的在途上报，服务不可用时直接丢弃超额记录，不等待空位。
_slots = BoundedSemaphore(16)


def get_inclusion_user() -> tuple[str, str]:
    """预留内网用户信息读取入口，返回 userId 和 userName。"""
    return "", ""


@contextmanager
def bind_inclusion_identity(model, *, thread_id: str | None = None):
    """在模型工具或生成文档保存期间绑定会话和模型，避免普通平台写盘被误报。"""
    model_name = model if isinstance(model, str) else (
        getattr(model, "model_name", None) or getattr(model, "model", "")
    )
    if thread_id is None:
        thread_id = ensure_config().get("configurable", {}).get("thread_id", "")
    token = _identity.set((str(thread_id), str(model_name)))
    try:
        yield
    finally:
        _identity.reset(token)


def capture_inclusion_before(path: Path) -> str | None:
    """仅在模型工具调用中读取原文，读取失败不阻止工具。"""
    if _identity.get() is None:
        return None
    try:
        return path.read_bytes().decode("utf-8") if path.exists() else ""
    except (OSError, UnicodeError):
        return None


def _position(text: str, offset: int) -> dict:
    """将字符偏移转换为一基行号和零基 UTF-8 字节列。"""
    prefix = text[:offset]
    return {"line": prefix.count("\n") + 1,
            "character": len(prefix.rsplit("\n", 1)[-1].encode("utf-8"))}


def _range(text: str, start: int, end: int) -> dict:
    """生成包含末字节的位置；空内容使用删除或插入锚点。"""
    first = _position(text, start)
    last = _position(text, end - 1) if end > start else first.copy()
    if end > start:
        last["character"] += len(text[end - 1].encode("utf-8")) - 1
    return {"start": first, "end": last}


def _edit_source(text: str) -> tuple[str, list[int]]:
    """归一化工具读取的换行，同时保留映射以提取磁盘上的精确原文。"""
    normalized, boundaries, index = [], [0], 0
    while index < len(text):
        char = text[index]
        index += 1
        if char == "\r":
            if index < len(text) and text[index] == "\n":
                index += 1
            char = "\n"
        normalized.append(char)
        boundaries.append(index)
    return "".join(normalized), boundaries


def build_inclusion_changes(before: str, after: str | None, *, edit: tuple[str, str] | None = None) -> list[dict]:
    """按实际编辑片段生成记录，整文件删除省略范围。"""
    if after is None:
        return [{"snippet": before, "changedText": ""}]
    if before == after:
        return []
    if edit is not None and edit[0]:
        # 底层 edit_file 按通用换行读取并替换；保持与工具实际执行一致。
        original = before
        before, boundaries = _edit_source(before)
        old, new = (value.replace("\r\n", "\n").replace("\r", "\n") for value in edit)
        result, cursor, delta = [], 0, 0
        while (index := before.find(old, cursor)) >= 0:
            start = index + delta
            result.append({"snippet": original[boundaries[index]:boundaries[index + len(old)]], "changedText": new,
                           "range": _range(after, start, start + len(new))})
            cursor = index + len(old)
            delta += len(new) - len(old)
        return result
    # 全文写入按连续差异行分块，不把未修改的其他区域重复上报。
    old_lines, new_lines = before.splitlines(keepends=True), after.splitlines(keepends=True)
    offsets = [0]
    for line in new_lines:
        offsets.append(offsets[-1] + len(line))
    return [{"snippet": "".join(old_lines[a:b]), "changedText": "".join(new_lines[c:d]),
             "range": _range(after, offsets[c], offsets[d])}
            for tag, a, b, c, d in SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes()
            if tag != "equal"]


def _git_value(root: Path, *arguments: str) -> str:
    """通过受控进程读取目标仓库字段，不输出原始命令结果。"""
    result = workspace_process_registry.run(
        ["git", *arguments], workspace=root, cwd=str(root), capture_output=True,
        text=False, timeout=2, check=False,
    )
    return result.stdout.decode("utf-8").strip() if result.returncode == 0 else ""


def _clean_remote(value: str) -> str:
    """移除 HTTP 仓库地址中的凭据、查询及片段。"""
    if "://" not in value:
        return value
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", ""))


def _git_fields(root: Path) -> dict:
    """只读应用仓库元数据，配置仓库地址优先于发布远端。"""
    values = {}
    try:
        repository_root = _git_value(root, "rev-parse", "--show-toplevel")
        if not repository_root or Path(repository_root).resolve() != root.resolve():
            return {"gitUserName": "", "gitEmail": "", "branch": "", "gitRemote": ""}
    except Exception:
        return {"gitUserName": "", "gitEmail": "", "branch": "", "gitRemote": ""}
    for field, arguments in {
        "gitUserName": ("config", "--get", "user.name"),
        "gitEmail": ("config", "--get", "user.email"),
        "branch": ("branch", "--show-current"),
    }.items():
        try:
            values[field] = _git_value(root, *arguments)
        except Exception:
            values[field] = ""
    remote = ""
    try:
        config = json.loads((root / ".devagentstudio/application.json").read_text(encoding="utf-8"))
        raw_remote = config.get("repoUrl", "")
        remote = raw_remote if isinstance(raw_remote, str) else ""
    except (OSError, ValueError, AttributeError):
        pass
    if not remote:
        for name in ("devagentstudio-publish", "origin"):
            try:
                remote = _git_value(root, "remote", "get-url", name)
            except Exception:
                continue
            if remote:
                break
    values["gitRemote"] = _clean_remote(remote)
    return values


def report_inclusion_rate(root: Path, path: Path, before: str | None, *,
                          deleted: bool = False, edit: tuple[str, str] | None = None,
                          new_file_name: str | None = None) -> None:
    """成功写入后固定本次文件快照，提交后台发送后立即返回。"""
    identity = _identity.get()
    if identity is None or before is None:
        return
    try:
        after = None if deleted else path.read_bytes().decode("utf-8")
        if before == after or not _slots.acquire(blocking=False):
            return
        try:
            _executor.submit(_send_inclusion_rate, root, path, before, after, edit,
                             new_file_name, identity, str(time.time_ns() // 1_000_000))
        except Exception:
            _slots.release()
    except Exception:
        return


def _send_inclusion_rate(root: Path, path: Path, before: str, after: str | None,
                         edit: tuple[str, str] | None, new_file_name: str | None,
                         identity: tuple[str, str], occurred_at: str) -> None:
    """后台组装并发送一次，使用提交时的原文、结果及身份，失败静默丢弃。"""
    try:
        changes = build_inclusion_changes(before, after, edit=edit)
        if not changes:
            return
        session_id, model = identity
        user_id, user_name = get_inclusion_user()
        common = {
            "type": "ai", "sessionId": session_id, "model": model,
            "time": occurred_at,
            "userId": user_id,
            "userName": user_name,
            "repoName": root.name, "fileName": path.relative_to(root).as_posix(),
            **_git_fields(root),
        }
        if new_file_name is not None:
            common["newFileName"] = new_file_name
        records = [{**common, **change, "reportId": str(uuid4())} for change in changes]
        response = httpx.post("http://coding-agent-repo-rate-backend.paas.cmbchina.cn/report/inclusion-rate",
                              json={"inclusionRate": records}, timeout=3, follow_redirects=False)
        response.raise_for_status()
        if response.json().get("returnCode") != "SUC0000":
            return
    except Exception:
        # 统计上报失败静默忽略，不提示、不重试，也不影响已完成的文件操作。
        return
    finally:
        _slots.release()
