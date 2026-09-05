"""严格解析单 Unit 的模型候选正文，不执行归一化、校验编排或重试。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, NoReturn

from app.services.planning_issues import ValidationIssue


class _DuplicateJsonKey(ValueError):
    """标记 JSON object 中会被标准解码器覆盖的重复字段。"""

    def __init__(self, key: str) -> None:
        """保存重复字段名，供外层转换为结构化问题。"""

        self.key = key
        super().__init__(f"duplicate JSON key: {key}")


class _InvalidJsonConstant(ValueError):
    """标记 Python JSON 解码器默认接受的非标准数值常量。"""

    def __init__(self, value: str) -> None:
        """保存非法常量，供外层生成不包含原始正文的诊断详情。"""

        self.value = value
        super().__init__(f"invalid JSON constant: {value}")


class RawUnitCandidateParseError(ValueError):
    """携带严格解析产生的结构化 Unit generation 问题。"""

    def __init__(self, issues: Sequence[ValidationIssue]) -> None:
        """冻结并重新验证全部问题，禁止调用方误用未校验的部分结果。"""

        self.issues = tuple(ValidationIssue.model_validate(issue) for issue in issues)
        super().__init__("；".join(issue.message for issue in self.issues))


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """构造 JSON object，并在字段覆盖发生前拒绝重复键。"""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> NoReturn:
    """拒绝 JSON 标准之外的 NaN 与 Infinity 常量。"""

    raise _InvalidJsonConstant(value)


def _generation_issue(
    code: str,
    message: str,
    *,
    unit_id: str,
    task_ids: Sequence[str] = (),
    **details: Any,
) -> ValidationIssue:
    """把原始模型结构错误归因到当前 Unit，并声明可进行本 Unit 内容重试。"""

    return ValidationIssue(
        code=code,
        level="unit",
        category="generation",
        unit_ids=(unit_id,),
        task_ids=tuple(task_ids),
        retry_unit_ids=(unit_id,),
        retryable=True,
        message=message,
        details=details,
    )


def parse_raw_unit_candidate(
    raw_text: str,
    *,
    unit_id: str,
) -> list[dict[str, Any]]:
    """严格解析 ``{"tasks": [...]}``，失败时不返回任何部分任务。

    本函数只验证 Raw Candidate envelope、Task object 和模型 Task ID 身份。
    Task 的 owner、Unit、职责、路径及依赖由后续 Local Validator 校验；这里既不
    修复也不删除任何模型字段，并且不调用旧 ``build_task_planner`` normalize 路径。
    """

    if not isinstance(raw_text, str):
        raise RawUnitCandidateParseError(
            [
                _generation_issue(
                    "RAW_CANDIDATE_TEXT_TYPE_INVALID",
                    "Unit Candidate 原始响应必须是字符串。",
                    unit_id=unit_id,
                    actual_type=type(raw_text).__name__,
                )
            ]
        )

    try:
        document = json.loads(
            raw_text,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise RawUnitCandidateParseError(
            [
                _generation_issue(
                    "RAW_CANDIDATE_JSON_MALFORMED",
                    "Unit Candidate 原始响应不是完整且合法的 JSON。",
                    unit_id=unit_id,
                    line=exc.lineno,
                    column=exc.colno,
                    position=exc.pos,
                )
            ]
        ) from exc
    except _DuplicateJsonKey as exc:
        raise RawUnitCandidateParseError(
            [
                _generation_issue(
                    "RAW_CANDIDATE_JSON_KEY_DUPLICATE",
                    "Unit Candidate JSON object 不得包含重复字段。",
                    unit_id=unit_id,
                    key=exc.key,
                )
            ]
        ) from exc
    except _InvalidJsonConstant as exc:
        raise RawUnitCandidateParseError(
            [
                _generation_issue(
                    "RAW_CANDIDATE_JSON_CONSTANT_INVALID",
                    "Unit Candidate 原始响应包含非标准 JSON 数值。",
                    unit_id=unit_id,
                    value=exc.value,
                )
            ]
        ) from exc

    if not isinstance(document, dict):
        raise RawUnitCandidateParseError(
            [
                _generation_issue(
                    "RAW_CANDIDATE_ENVELOPE_TYPE_INVALID",
                    "Unit Candidate 顶层必须是 JSON object。",
                    unit_id=unit_id,
                    actual_type=type(document).__name__,
                )
            ]
        )

    envelope_issues: list[ValidationIssue] = []
    if "tasks" not in document:
        envelope_issues.append(
            _generation_issue(
                "RAW_CANDIDATE_TASKS_MISSING",
                "Unit Candidate 顶层缺少必需的 tasks 字段。",
                unit_id=unit_id,
            )
        )
    unsupported_keys = sorted(key for key in document if key != "tasks")
    if unsupported_keys:
        envelope_issues.append(
            _generation_issue(
                "RAW_CANDIDATE_ENVELOPE_UNSUPPORTED",
                "Unit Candidate 顶层只允许 tasks 字段。",
                unit_id=unit_id,
                unsupported_keys=unsupported_keys,
            )
        )
    if envelope_issues:
        raise RawUnitCandidateParseError(envelope_issues)

    raw_tasks = document["tasks"]
    if not isinstance(raw_tasks, list):
        raise RawUnitCandidateParseError(
            [
                _generation_issue(
                    "RAW_CANDIDATE_TASKS_TYPE_INVALID",
                    "Unit Candidate tasks 必须是 JSON array。",
                    unit_id=unit_id,
                    actual_type=type(raw_tasks).__name__,
                )
            ]
        )

    task_issues: list[ValidationIssue] = []
    task_ids: set[str] = set()
    for index, task in enumerate(raw_tasks):
        if not isinstance(task, dict):
            task_issues.append(
                _generation_issue(
                    "RAW_CANDIDATE_TASK_TYPE_INVALID",
                    f"Unit Candidate tasks[{index}] 必须是 JSON object。",
                    unit_id=unit_id,
                    index=index,
                    actual_type=type(task).__name__,
                )
            )
            continue

        if "id" not in task or task["id"] == "":
            task_issues.append(
                _generation_issue(
                    "RAW_CANDIDATE_TASK_ID_MISSING",
                    f"Unit Candidate tasks[{index}] 缺少非空 Task ID。",
                    unit_id=unit_id,
                    index=index,
                )
            )
            continue
        task_id = task["id"]
        if not isinstance(task_id, str):
            task_issues.append(
                _generation_issue(
                    "RAW_CANDIDATE_TASK_ID_TYPE_INVALID",
                    f"Unit Candidate tasks[{index}].id 必须是字符串。",
                    unit_id=unit_id,
                    index=index,
                    actual_type=type(task_id).__name__,
                )
            )
            continue
        if not task_id.strip() or task_id != task_id.strip():
            task_issues.append(
                _generation_issue(
                    "RAW_CANDIDATE_TASK_ID_INVALID",
                    f"Unit Candidate tasks[{index}].id 必须是无首尾空白的非空身份。",
                    unit_id=unit_id,
                    index=index,
                    task_ids=(task_id,),
                )
            )
            continue
        if task_id in task_ids:
            task_issues.append(
                _generation_issue(
                    "RAW_CANDIDATE_TASK_ID_DUPLICATE",
                    f"Unit Candidate 包含重复 Task ID：{task_id}。",
                    unit_id=unit_id,
                    task_ids=(task_id,),
                    duplicate_id=task_id,
                    index=index,
                )
            )
            continue
        task_ids.add(task_id)

    if task_issues:
        raise RawUnitCandidateParseError(task_issues)
    return raw_tasks
