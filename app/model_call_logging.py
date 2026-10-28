"""输出线程使用的统一落点；当前打印控制台，ES 方法与调用以注释预留。"""

import sys
import threading

_OUTPUT_LOCK = threading.RLock()


def print_record_json(payload_json: str) -> None:
    """在线程内写入已冻结的单行 JSON，异常交由输出组件计数并隔离。"""
    with _OUTPUT_LOCK:
        sys.stdout.write(payload_json + "\n")
        sys.stdout.flush()


def deliver_record(payload_json: str) -> None:
    """统一输出入口；以后切换落点时仍由外层同一开关控制是否投递。"""
    print_record_json(payload_json)
    # write_record_to_es(payload_json)


# def write_record_to_es(payload_json: str) -> None:
#     """预留：将同一份响应体写入 ES；正式接入时补齐连接、索引、认证和超时。"""
#     # 在此处解析冻结 JSON，并按 span_id（缺失时 run_id）构造幂等文档 ID。
#     # 正式接入时实现写入，注释上面的控制台打印，再启用 ES 方法及调用。
#     # 当前没有 ES 客户端、依赖、连接或写入行为。
#     raise NotImplementedError("ES 输出尚未接入")
