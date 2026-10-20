"""内置数据库 Java 进程在写日志前脱敏，原始凭据不进入工作区文件。"""

from pathlib import Path
from threading import Thread
from typing import BinaryIO

from app.services.backend_startup_diagnostics import redact_startup_text


def _copy_redacted(stream: BinaryIO, path: Path, environment: dict[str, str]) -> None:
    """持续消费单个输出管道，每行脱敏后落盘并及时供就绪检查读取。"""

    try:
        with path.open("a", encoding="utf-8") as output:
            for line in stream:
                output.write(redact_startup_text(line.decode("utf-8", errors="replace"), environment))
                output.flush()
    finally:
        stream.close()


def attach_redacted_logs(process, stdout_path: Path, stderr_path: Path, environment: dict[str, str]) -> list[Thread]:
    """为真实输出管道建立独立消费者，避免阻塞 Java 并只保留内存凭据。"""

    threads: list[Thread] = []
    for stream, path in ((process.stdout, stdout_path), (process.stderr, stderr_path)):
        if stream is not None:
            thread = Thread(target=_copy_redacted, args=(stream, path, environment), daemon=True)
            thread.start()
            threads.append(thread)
    return threads
