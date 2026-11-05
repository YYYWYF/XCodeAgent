"""在恢复查询边界保留操作和异常原因，仅把安全诊断投影到 AG-UI。"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from app.domain.execution_recovery import ExecutionRecoveryProjectionError, RecoveryReadDiagnostic
from app.observability.backend_log_redaction import LogRedactor


class RecoveryReadOperationError(Exception):
    """为查询异常附加实际操作，不改变原始异常链。"""

    def __init__(self, operation: str) -> None:
        """只记录受控操作名称，原异常由显式 cause 保留。"""
        super().__init__(operation)
        self.operation = operation


@contextmanager
def recovery_read_operation(operation: str) -> Iterator[None]:
    """标记查询失败阶段，让外层投影不必从堆栈猜测业务操作。"""
    try:
        yield
    except Exception as exc:
        raise RecoveryReadOperationError(operation) from exc


def recovery_read_error(
    exc: Exception,
    *,
    operation: str,
    source_run_id: str | None = None,
    thread_id: str | None = None,
) -> ExecutionRecoveryProjectionError:
    """提取原始异常并脱敏，生成可与完整后台日志对应的诊断编号。"""
    if isinstance(exc, RecoveryReadOperationError):
        operation = exc.operation
    root: BaseException = exc
    seen: set[int] = set()
    # 沿显式异常链读取真正原因；循环保护避免第三方异常链造成死循环。
    while root.__cause__ is not None and id(root) not in seen:
        seen.add(id(root))
        root = root.__cause__
    reason = LogRedactor().redact(str(root)).strip()
    # 异常消息可能自带 traceback；不向卡片传递堆栈及代码行位置。
    reason = reason.split("Traceback (most recent call last):", 1)[0]
    reason = re.sub(r'(?m)^\s*File ".*?", line \d+.*$', "", reason).strip()
    reason = reason[:2048] or "后台未提供具体原因，请复制诊断信息联系维护人员。"
    unknown_node = re.fullmatch(
        r"Found edge (?:starting at|ending at) unknown node ['\"]([^'\"]+)['\"]", reason
    )
    message = (
        f"工作流配置错误：连线引用了未注册的节点 {unknown_node.group(1)}。"
        if unknown_node else f"恢复状态读取失败：{reason}"
    )
    return ExecutionRecoveryProjectionError(
        message=message,
        diagnostic=RecoveryReadDiagnostic(
            diagnosticId=uuid4().hex,
            operation=operation,
            exceptionType=type(root).__name__[:256],
            reason=reason,
            sourceRunId=source_run_id,
            threadId=thread_id,
        ),
    )
