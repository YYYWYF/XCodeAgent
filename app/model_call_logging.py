"""输出线程使用的统一落点；配置 ES 后写入索引，否则打印控制台。"""

import hashlib
import json
import sys
import threading
from typing import Any

from elasticsearch import Elasticsearch

from .config import Settings
from .observation_models import ExecutionError

_OUTPUT_LOCK = threading.RLock()


def print_record_json(payload_json: str) -> None:
    """在线程内写入已冻结的单行 JSON，异常交由输出组件计数并隔离。"""
    with _OUTPUT_LOCK:
        sys.stdout.write(payload_json + "\n")
        sys.stdout.flush()


def deliver_record(payload_json: str, *, es_client: Elasticsearch | None = None,
                   es_index: str = "model-call-observations") -> None:
    """统一输出入口；ES 与控制台共用外层投递开关，不在失败时切换落点。"""
    if es_client is not None:
        write_record_to_es(payload_json, client=es_client, index=es_index)
    else:
        print_record_json(payload_json)


def build_es_document(document: dict[str, Any]) -> dict[str, Any]:
    """各级字段名转小驼峰，再用下划线连接路径，保留原始字段值。"""
    flattened: dict[str, Any] = {}

    def visit(value: Any, path: tuple[str, ...]) -> None:
        # 无异常时也使用错误子字段，避免生成 mapping 中不存在的 data_error 父字段。
        if path == ("data", "error") and value is None:
            value = dict.fromkeys(ExecutionError.model_fields)
        if isinstance(value, dict):
            for name, item in value.items():
                first, *remaining = name.split("_")
                field = first + "".join(part[:1].upper() + part[1:] for part in remaining)
                visit(item, (*path, field))
            return
        field_name = "_".join(path)
        if field_name in flattened:
            raise ValueError("ES 字段平铺后名称冲突")
        flattened[field_name] = value

    visit(document, ())
    return flattened


def write_record_to_es(payload_json: str, *, client: Elasticsearch, index: str) -> None:
    """将冻结快照转换成 ES 平铺字段，以稳定文档 ID 通过 7.x 官方客户端写入。"""
    document = json.loads(payload_json)
    document_id = (
        document.get("record_id") or document["data"].get("span_id")
        or document["identity"].get("run_id")
    )
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("ES 模型统计缺少 record_id、span_id 和 run_id")
    # ES 的 _id 最多 512 字节；点路径和超长身份使用稳定摘要，原始身份仍在正文。
    if document_id in {".", ".."} or len(document_id.encode("utf-8")) > 512:
        document_id = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
    es_document = build_es_document(document)
    # 7.x 使用 body 参数；客户端序列化平铺后的字典，统计值来自已冻结的快照。
    result = client.index(index=index, id=document_id, body=es_document)
    if not isinstance(result, dict) or result.get("result") not in {"created", "updated"}:
        raise ValueError("ES 写入响应无效")
    if result.get("_shards", {}).get("failed", 0):
        raise ValueError("ES 写入存在失败分片")


class ModelCallWriter:
    """每个应用复用官方 ES 客户端，由客户端管理集群节点、连接池和重试。"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: Elasticsearch | None = None
        self._lock = threading.Lock()
        self._closed = False

    def _create_client(self) -> Elasticsearch:
        """用 ES 7.x API 配置节点列表、认证、单次超时和最多两次重试。"""
        settings = self._settings
        auth = None
        if settings.es_username:
            auth = (settings.es_username, settings.es_password.get_secret_value())
        return Elasticsearch(
            hosts=settings.es_hosts,
            http_auth=auth,
            timeout=settings.es_request_timeout_seconds,
            max_retries=2,
            retry_on_timeout=True,
            retry_on_status=(429, 502, 503, 504),
            verify_certs=True,
            sniff_on_start=False,
            sniff_on_connection_fail=False,
        )

    def __call__(self, payload_json: str) -> None:
        """只在输出线程初始化连接和执行写入，主请求只负责投递。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("模型统计输出已关闭")
            if self._settings.es_url and self._client is None:
                self._client = self._create_client()
            client = self._client
        deliver_record(payload_json, es_client=client, es_index=self._settings.es_index)

    def close(self) -> None:
        """在输出任务排空后释放应用自己的连接池；允许重复关闭。"""
        with self._lock:
            self._closed = True
            client, self._client = self._client, None
        if client is not None:
            client.close()
