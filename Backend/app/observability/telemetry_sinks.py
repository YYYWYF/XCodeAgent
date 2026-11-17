"""统一分析管道的互斥 LOCAL / HTTP 输出。"""

import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic.alias_generators import to_camel

from app.branding import DEFAULT_USER_DATA_DIRECTORY_NAME, USER_DATA_DIRECTORY_NAMES
from app.observability.telemetry_pipeline import now


def experiment_tag() -> str:
    """将实验标签限制在安全的目录名字符范围内。"""
    tag = os.getenv("DEVAGENTSTUDIO_OBSERVABILITY_EXPERIMENT", "default")
    return tag if re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", tag) else "default"


def local_root() -> Path:
    """采用 Studio 既有环境目录白名单定位本地输出。"""
    directory = os.getenv("DEVAGENTSTUDIO_WORKING_DIR", DEFAULT_USER_DATA_DIRECTORY_NAME).strip()
    if directory not in USER_DATA_DIRECTORY_NAMES:
        raise ValueError("invalid Studio directory")
    return Path.home() / directory / "observability"


class LocalSink:
    """按进程写唯一文件，保留每条事件的嵌套 snake_case 结构。"""

    success = "written"
    failure = "local_write_failed"

    def __init__(self):
        """生成文件名，实际解析目录和打开文件只在工作线程进行。"""
        self.filename = f"events-{uuid4().hex}.jsonl"
        self.tag = experiment_tag()

    def write(self, records):
        """每批追加并关闭文件，进程崩溃时最多影响末尾写入。"""
        directory = local_root() / self.tag
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / self.filename
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a",
                       encoding="utf-8") as stream:
            stream.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def _camel(value):
    """只转换传输字段名，保留显式空值和数组语义。"""
    if isinstance(value, dict):
        return {to_camel(key): _camel(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camel(item) for item in value]
    return value


class HttpSink:
    """从内存发送既有 camelCase 批次合同。"""

    success = "sent"
    failure = "http_failed"

    def __init__(self):
        """固定进程级地址，兼容旧 URL 配置。"""
        self.legacy = os.getenv("DEVAGENTSTUDIO_OBSERVABILITY_MODE") is None
        old = os.getenv("DEVAGENTSTUDIO_TELEMETRY_URL", "")
        self.url = (old if self.legacy else os.getenv("DEVAGENTSTUDIO_OBSERVABILITY_URL", old)).strip()

    def write(self, records):
        """在后台直接 POST 一次，失败由同一管道计数。"""
        url = urlsplit(self.url)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username is not None or url.password is not None or url.fragment):
            raise ValueError("invalid telemetry URL")
        if self.legacy:
            for record in records:
                identity = record.setdefault("identity", {})
                if identity is not None:
                    identity.setdefault("user_id", "Hupy/test")
        body = _camel({"schema_version": 1, "batch_id": uuid4().hex,
                       "sent_at": now(), "records": records})
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            with client.stream("POST", self.url, json=body) as response:
                response.raise_for_status()
