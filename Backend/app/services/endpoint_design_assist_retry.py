"""保存本轮失败前的已验证步骤，短期内显式重试，不持久化或自动执行。"""

import copy
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from uuid import uuid4

from app.domain.endpoint_design_assist import GenerateMappingRequest, MappingCandidate
from app.domain.endpoint_design_assist_steps import OperationStepResult
from app.services.endpoint_design_assist_validation import digest

_TTL_SECONDS = 600
_MAX_CHECKPOINTS = 32
_lock = threading.Lock()


@dataclass
class GenerationCheckpoint:
    """只保存服务端已经校验通过的前序候选，不接受客户端中间映射。"""

    identity: str
    step_index: int
    candidates: list[MappingCandidate]
    questions: list[str]
    created_at: float
    operation: OperationStepResult | None


_checkpoints: OrderedDict[str, GenerationCheckpoint] = OrderedDict()


class MappingGenerationFailure(ValueError):
    """携带可重试的失败阶段，通过原 AG-UI 失败生命周期返回。"""

    def __init__(self, message: str, *, token: str, stage: str, completed: list[str]):
        """保留前端显式继续所需的最小恢复信息。"""
        super().__init__(message)
        self.failure = {"retryToken": token, "failedStage": stage, "completedStages": completed}


def generation_identity(request: GenerateMappingRequest, baseline: dict) -> str:
    """把目标、操作、契约、草稿、完整表约束和生成要求绑定到同一轮。"""
    return digest({"request": request.model_dump(mode="json", by_alias=True, exclude={"retry_token"}), "baseline": baseline})


def _prune() -> None:
    """惰性清理过期项并限制进程内存，服务重启后自然失效。"""
    now = time.monotonic()
    for token in list(_checkpoints):
        if now - _checkpoints[token].created_at >= _TTL_SECONDS:
            del _checkpoints[token]
    while len(_checkpoints) > _MAX_CHECKPOINTS:
        _checkpoints.popitem(last=False)


def save_checkpoint(identity: str, index: int, candidates: list[MappingCandidate], questions: list[str],
                    operation: OperationStepResult | None = None) -> str:
    """失败时缓存前序结果；不缓存失败步骤输出，也不写工作区。"""
    with _lock:
        token = uuid4().hex
        _checkpoints[token] = GenerationCheckpoint(identity, index, copy.deepcopy(candidates), list(questions), time.monotonic(), copy.deepcopy(operation))
        _prune()
        return token


def take_checkpoint(token: str, identity: str) -> GenerationCheckpoint:
    """基线一致才一次性消费恢复凭据，拒绝过期、重复或跨目标复用。"""
    with _lock:
        _prune()
        checkpoint = _checkpoints.get(token)
        if checkpoint is None or checkpoint.identity != identity:
            raise ValueError("本轮分步结果已过期或输入已变化，请重新生成完整映射。")
        del _checkpoints[token]
        return checkpoint
