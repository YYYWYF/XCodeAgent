"""所有 Endpoint 双文件读写及草稿提交共用一把可重入锁。"""
from functools import wraps
from threading import RLock

ENDPOINT_DESIGN_LOCK = RLock()


def endpoint_design_locked(function):
    """使读取和最后提交点互斥，服务内消费者不能观察半次文件替换。"""
    @wraps(function)
    def locked(*args, **kwargs):
        """在相同临界区内执行完整读取、校验或提交。"""
        with ENDPOINT_DESIGN_LOCK:
            return function(*args, **kwargs)
    return locked
