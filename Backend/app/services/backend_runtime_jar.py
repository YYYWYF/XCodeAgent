"""管理独立于 Maven target 的 Java 运行副本及其磁盘记录。"""

import errno
import json
import re
import shutil
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


BACKEND_JAR_DIRECTORY = "backend-jar"
BACKEND_PROCESS_FILENAME = "backend-process.json"


@dataclass(frozen=True)
class BackendProcessRecord:
    """绑定预览 PID 和该次启动独有的 JAR 副本。"""

    pid: int
    jar: Path


def write_backend_process_record(path: Path, *, pid: int, jar: Path) -> None:
    """原子发布 PID 与副本相对路径，避免状态订阅读到不完整身份。"""

    relative = jar.relative_to(path.parent).as_posix()
    temporary = path.with_name(f".backend-process-{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps({"pid": pid, "jar": relative}), encoding="utf-8")
        temporary.replace(path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def read_backend_process_record(path: Path) -> BackendProcessRecord | None:
    """读取本工作区的进程身份，拒绝越界、符号链接和非运行副本路径。"""

    if path.is_symlink():
        raise ValueError("后端进程记录不能是符号链接。")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or type(data.get("pid")) is not int or data["pid"] <= 0:
        raise ValueError("后端进程记录缺少有效 PID。")
    raw_jar = data.get("jar")
    if not isinstance(raw_jar, str) or not re.fullmatch(
        rf"{BACKEND_JAR_DIRECTORY}/[0-9a-f]{{32}}/[^/\\:]+\.jar", raw_jar
    ):
        raise ValueError("后端进程记录的 JAR 副本路径无效。")
    jar = path.parent.resolve() / raw_jar
    if jar.resolve() != jar:
        raise ValueError("后端进程记录的 JAR 副本路径不能经过符号链接。")
    return BackendProcessRecord(data["pid"], jar)


def stage_backend_jar(source: Path, directory: Path) -> Path:
    """将主 JAR 实体复制到全新的进程工作目录，复制完成后才交给启动器。"""

    if directory.parent.resolve() != directory.parent:
        raise OSError("JAR 运行目录不能是符号链接。")
    directory.mkdir(parents=True, exist_ok=False)
    destination = directory / source.name
    try:
        shutil.copyfile(source, destination)
    except OSError:
        with suppress(OSError):
            remove_backend_jar(destination)
        raise
    return destination


def remove_backend_jar(jar_path: Path) -> None:
    """清理运行副本和空目录，保留应用在工作目录生成的文件。"""

    if jar_path.parent.resolve() != jar_path.parent:
        raise OSError("JAR 运行目录不能经过符号链接。")
    jar_path.unlink(missing_ok=True)
    try:
        jar_path.parent.rmdir()
    except FileNotFoundError:
        pass
    except OSError as exc:
        if exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
            raise
