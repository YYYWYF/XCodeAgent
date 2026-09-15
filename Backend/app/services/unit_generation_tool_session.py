"""单 Unit Attempt 内受控 Frozen Contract Tool Session。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from app.services.frozen_contract_reader import (
    FROZEN_CONTRACT_READER_TOOL_NAME,
    FrozenContractReader,
    create_frozen_contract_reader_tool,
    read_frozen_contract_fragment,
)
from app.services.frozen_contract_reader_contracts import (
    FrozenContractReadError,
    FrozenContractReadInput,
)
from app.services.frozen_contract_store import FrozenContractStore
from app.services.planning_issues import ValidationIssue
from app.services.unit_generation_contracts import AttemptIdentity, UnitAttemptJob


PlatformStage = Literal["contract_reader_setup", "contract_read"]


class UnitGenerationPlatformError(RuntimeError):
    """标记冻结合同来源或 Reader 执行损坏，供外层立即终止 PlanningRun。"""

    def __init__(
        self,
        *,
        identity: AttemptIdentity,
        stage: PlatformStage,
        cause: Exception,
    ) -> None:
        """保留 Attempt、平台失败阶段和原始异常类型，不投影合同正文。"""

        self.category = "platform"
        self.identity = identity
        self.stage = stage
        self.cause_type = type(cause).__name__
        super().__init__(
            f"Unit generation platform failure at {stage}: "
            f"attempt_id={identity.attempt_id}, cause={self.cause_type}"
        )


def _content_issue(
    code: str,
    message: str,
    *,
    unit_id: str,
    details: Mapping[str, Any] | None = None,
) -> ValidationIssue:
    """把受控 Tool Session 的策略或调用错误归因成当前 Unit 的内容失败。"""

    return ValidationIssue(
        code=code,
        level="unit",
        category="generation",
        unit_ids=(unit_id,),
        task_ids=(),
        retry_unit_ids=(unit_id,),
        retryable=True,
        message=message,
        details=details or {},
    )


def model_turn_limit_issue(*, unit_id: str, model_turn_limit: int) -> ValidationIssue:
    """构造模型轮数预算耗尽且尚未产出最终 Candidate 的内容失败。"""

    return _content_issue(
        "UNIT_GENERATION_MODEL_TURN_LIMIT_EXCEEDED",
        "模型轮数预算已耗尽，当前 Unit Generation Session 未产生完整 Candidate。",
        unit_id=unit_id,
        details={"model_turn_limit": model_turn_limit},
    )


def _read_failure_issue(error: FrozenContractReadError, *, unit_id: str) -> ValidationIssue:
    """将 Reader 的策略耗尽与其他模型调用错误映射为不同的内容失败代码。"""

    exhausted = error.code in {
        "FROZEN_CONTRACT_READ_COUNT_EXCEEDED",
        "FROZEN_CONTRACT_ACCUMULATED_SIZE_EXCEEDED",
    }
    return _content_issue(
        (
            "UNIT_GENERATION_CONTRACT_READ_BUDGET_EXHAUSTED"
            if exhausted
            else "UNIT_GENERATION_CONTRACT_TOOL_CALL_INVALID"
        ),
        (
            "冻结合同读取预算已耗尽，当前 Unit Generation Session 未产生完整 Candidate。"
            if exhausted
            else "模型的冻结合同读取请求无效，当前 Unit Generation Session 未产生完整 Candidate。"
        ),
        unit_id=unit_id,
        details={"reader_error": error.as_dict()},
    )


@dataclass(frozen=True, slots=True)
class ContractToolTurn:
    """表示一个模型响应是否请求了工具，以及工具阶段是否产生内容失败。"""

    requested: bool
    issue: ValidationIssue | None = None


class ContractToolSession:
    """维护一个 Attempt 独享的 Reader、消息历史、工具定义与 ToolCall 身份。"""

    def __init__(
        self,
        job: UnitAttemptJob,
        *,
        frozen_contract_store: FrozenContractStore,
        prompt: str,
    ) -> None:
        """验证 Store/Run/catalog/read policy 并建立隔离会话，失败属于平台 fatal。"""

        try:
            if not isinstance(frozen_contract_store, FrozenContractStore):
                raise TypeError("frozen_contract_store 必须是 FrozenContractStore instance。")
            if frozen_contract_store.planning_run_id != job.context.planning_run_id:
                raise ValueError("FrozenContractStore 与当前 PlanningRun 身份不一致。")
            self.reader = FrozenContractReader(
                frozen_contract_store=frozen_contract_store,
                contract_catalog=job.context.contract_catalog,
                read_policy=job.policy.frozen_contract_read_limits,
            )
        except (TypeError, ValueError) as exc:
            raise UnitGenerationPlatformError(
                identity=job.identity,
                stage="contract_reader_setup",
                cause=exc,
            ) from exc
        self.identity = job.identity
        self.unit_id = job.context.unit_id
        self.messages: list[object] = [HumanMessage(content=prompt)]
        self.tools = (create_frozen_contract_reader_tool(self.reader),)
        self._seen_call_ids: set[str] = set()

    def _normalized_calls(
        self,
        response: object,
    ) -> tuple[tuple[str, FrozenContractReadInput], ...]:
        """校验模型 ToolCall 形状、唯一 ID、唯一工具名和严格 Reader 参数。"""

        invalid_calls = getattr(response, "invalid_tool_calls", None) or ()
        if invalid_calls:
            raise ValueError("模型返回了无法解析的 contract tool call。")
        raw_calls = getattr(response, "tool_calls", None) or ()
        if not isinstance(raw_calls, (list, tuple)):
            raise ValueError("模型 tool_calls 必须是数组。")
        calls: list[tuple[str, FrozenContractReadInput]] = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, Mapping):
                raise ValueError("模型 tool call 必须是对象。")
            call_id = raw_call.get("id")
            if not isinstance(call_id, str) or not call_id.strip() or call_id != call_id.strip():
                raise ValueError("模型 tool call 缺少稳定非空 id。")
            if call_id in self._seen_call_ids:
                raise ValueError("模型 tool call id 在当前 Session 内重复。")
            if raw_call.get("name") != FROZEN_CONTRACT_READER_TOOL_NAME:
                raise ValueError("模型调用了未授权工具。")
            try:
                arguments = FrozenContractReadInput.model_validate(raw_call.get("args"))
            except ValidationError as exc:
                raise ValueError("模型 contract tool 参数无效。") from exc
            self._seen_call_ids.add(call_id)
            calls.append((call_id, arguments))
        return tuple(calls)

    @staticmethod
    def _assistant_message(
        response: object,
        calls: Sequence[tuple[str, FrozenContractReadInput]],
    ) -> AIMessage:
        """把兼容模型响应规整为可安全回传给下一 Model Turn 的 AIMessage。"""

        if isinstance(response, AIMessage):
            return response
        response_metadata = getattr(response, "response_metadata", None)
        return AIMessage(
            content=getattr(response, "content", "") or "",
            tool_calls=[
                {
                    "name": FROZEN_CONTRACT_READER_TOOL_NAME,
                    "args": arguments.model_dump(mode="json"),
                    "id": call_id,
                    "type": "tool_call",
                }
                for call_id, arguments in calls
            ],
            response_metadata=(response_metadata if isinstance(response_metadata, dict) else {}),
        )

    def _tool_message(
        self,
        *,
        call_id: str,
        arguments: FrozenContractReadInput,
    ) -> ToolMessage:
        """执行一次受控读取并构造与原 ToolCall ID 精确关联的 ToolMessage。"""

        fragment = read_frozen_contract_fragment(
            self.reader,
            ref_id=arguments.ref_id,
            selector=arguments.selector,
            cursor=arguments.cursor,
        )
        return ToolMessage(
            content=json.dumps(
                fragment.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            tool_call_id=call_id,
            name=FROZEN_CONTRACT_READER_TOOL_NAME,
        )

    def accept(self, response: object) -> ContractToolTurn:
        """接收一轮模型响应，顺序执行其全部 Reader 调用并追加标准消息。"""

        try:
            calls = self._normalized_calls(response)
        except ValueError as exc:
            return ContractToolTurn(
                requested=True,
                issue=_content_issue(
                    "UNIT_GENERATION_CONTRACT_TOOL_CALL_INVALID",
                    "模型的冻结合同工具调用格式无效，当前 Session 未产生完整 Candidate。",
                    unit_id=self.unit_id,
                    details={"reason": str(exc)},
                ),
            )
        if not calls:
            return ContractToolTurn(requested=False)

        self.messages.append(self._assistant_message(response, calls))
        for call_id, arguments in calls:
            try:
                message = self._tool_message(call_id=call_id, arguments=arguments)
            except FrozenContractReadError as exc:
                return ContractToolTurn(
                    requested=True,
                    issue=_read_failure_issue(exc, unit_id=self.unit_id),
                )
            except Exception as exc:
                raise UnitGenerationPlatformError(
                    identity=self.identity,
                    stage="contract_read",
                    cause=exc,
                ) from exc
            self.messages.append(message)
        return ContractToolTurn(requested=True)
