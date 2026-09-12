"""执行 Engine 声明的只读后置条件；启动验收由平台根据实际 ChangeSet 规划。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.services.template_reconcile.protocol_v2 import (
    StrategyUpdatePackageV2,
    TemplateStateV2,
    ValidationPlanItemV2,
    assert_reconcile_state_invariant_v2,
)


class ValidationV2Error(ValueError):
    """表示 Validation Plan 不满足当前 V2 的安全执行约束。"""


@dataclass(frozen=True)
class ValidationResultV2:
    """保存一项验证的可持久化结构化结果，不内嵌无限制命令输出。"""

    validation_id: str
    passed: bool
    blocking: bool
    execution_mode: str
    duration_ms: int
    exit_code: int | None
    error_code: str | None
    message: str
    stdout_log_ref: str | None = None
    stderr_log_ref: str | None = None
    details: dict[str, Any] | None = None




def execute_validation_plan_v2(
    workspace: str | Path,
    validation_plan: list[ValidationPlanItemV2],
    *,
    attempt_id: str | None = None,
) -> list[ValidationResultV2]:
    """按 package 顺序执行验证；失败结果保留，调用方据 blocking 决定是否提交 State。"""

    root = Path(workspace).expanduser().resolve()
    results: list[ValidationResultV2] = []
    for item in validation_plan:
        started = time.monotonic()
        try:
            result = (
                _ignored_command_validation_result(item)
                if item.type in {"NPM_BUILD", "NPM_TEST", "MAVEN_TEST", "MAVEN_PACKAGE"}
                else _run_read_only_validation(root, item)
            )
        except ValidationV2Error as exc:
            result = ValidationResultV2(
                validation_id=item.validationId,
                passed=False,
                blocking=item.blocking,
                execution_mode=item.executionMode,
                duration_ms=0,
                exit_code=None,
                error_code="VALIDATION_CONTRACT_INVALID",
                message=str(exc),
            )
        results.append(_with_duration(result, started))
        if item.blocking and not results[-1].passed:
            break
    return results


def validation_plan_passed_v2(results: list[ValidationResultV2]) -> bool:
    """仅当所有 blocking 验证通过时允许调用方推进 nextTemplateState。"""

    return all(result.passed or not result.blocking for result in results)


def execute_reconcile_validation_v2(
    workspace: str | Path,
    package: StrategyUpdatePackageV2,
    current_template_state: TemplateStateV2,
    *,
    attempt_id: str | None = None,
) -> list[ValidationResultV2]:
    """执行 RECONCILE 的唯一验收入口，并先强制 current/next State 内容不变式。"""

    if package.mode != "RECONCILE":
        raise ValidationV2Error("ReconcileValidationCoordinator 仅接受 RECONCILE Package。")
    try:
        assert_reconcile_state_invariant_v2(current_template_state, package.nextTemplateState)
    except ValueError as exc:
        raise ValidationV2Error(str(exc)) from exc
    return execute_validation_plan_v2(workspace, package.validationPlan, attempt_id=attempt_id)




def _run_read_only_validation(root: Path, item: ValidationPlanItemV2) -> ValidationResultV2:
    """执行只读文件、文本、JSON 与 Capability 后置条件断言。"""

    if item.executionMode != "REAL_WORKSPACE":
        raise ValidationV2Error("只读 Validation 必须使用 REAL_WORKSPACE。")
    if item.type == "FILE_EXISTS":
        path = _validation_path(root, str(item.path))
        return _assertion_result(item, path.is_file(), f"文件存在：{path.relative_to(root)}")
    if item.type == "STRUCTURE_CHECK":
        return _structure_result(root, item)
    if item.type == "JSON_STRUCTURE_CHECK":
        return _json_structure_result(root, item)
    if item.type == "CAPABILITY_POSTCONDITION":
        return _capability_postcondition_result(root, item)
    raise ValidationV2Error(f"不支持的只读 Validation 类型：{item.type}。")


def _ignored_command_validation_result(item: ValidationPlanItemV2) -> ValidationResultV2:
    """兼容读取历史命令校验项，但不创建 Sandbox 或启动任何命令进程。"""

    return ValidationResultV2(
        item.validationId,
        True,
        item.blocking,
        item.executionMode,
        0,
        None,
        None,
        "历史命令校验已由真实项目重启验收替代，当前项未执行。",
    )


def _capability_postcondition_result(root: Path, item: ValidationPlanItemV2) -> ValidationResultV2:
    """逐项执行 Capability 声明的 FILE_EXISTS、STRUCTURE_CHECK 或 JSON_STRUCTURE_CHECK。"""

    if not item.checks:
        raise ValidationV2Error("CAPABILITY_POSTCONDITION 必须声明非空 checks。")
    for check in item.checks:
        child = item.model_copy(update={"type": check.type, "path": check.path, "containsAll": check.containsAll, "pointer": check.pointer, "expected": check.expected, "checks": None})
        child_result = _run_read_only_validation(root, child)
        if not child_result.passed:
            return _assertion_result(item, False, f"Capability {item.capabilityId} 后置条件失败：{child_result.message}")
    return _assertion_result(item, True, f"Capability {item.capabilityId} 后置条件全部通过。")


def _structure_result(root: Path, item: ValidationPlanItemV2) -> ValidationResultV2:
    """校验一个文本文件同时包含全部指定片段，适用于受管理的代码结构标记。"""

    path = _validation_path(root, str(item.path))
    expected = item.containsAll
    if not expected:
        raise ValidationV2Error("STRUCTURE_CHECK 必须提供非空 containsAll 字符串数组。")
    if not path.is_file():
        return _assertion_result(item, False, f"结构检查文件不存在：{path.relative_to(root)}")
    content = path.read_text(encoding="utf-8")
    missing = [value for value in expected if value not in content]
    return _assertion_result(item, not missing, "结构检查通过。" if not missing else f"结构检查缺少：{missing[0]}")


def _json_structure_result(root: Path, item: ValidationPlanItemV2) -> ValidationResultV2:
    """按 JSON pointer 校验值相等，避免通过文本匹配误判 package 配置结构。"""

    path = _validation_path(root, str(item.path))
    pointer = str(item.pointer)
    if not path.is_file():
        return _assertion_result(item, False, f"JSON 检查文件不存在：{path.relative_to(root)}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _assertion_result(item, False, "JSON 检查目标不是合法 JSON。")
    found, value = _json_pointer(document, pointer)
    passed = found and value == item.expected
    return _assertion_result(item, passed, "JSON 结构检查通过。" if passed else f"JSON pointer 不匹配：{pointer}")




def _validation_path(root: Path, raw_path: str) -> Path:
    """解析受限相对路径，防止 Validation Plan 跨越当前 Workspace。"""

    path = PurePosixPath(raw_path)
    if raw_path == ".":
        return root
    if not raw_path or "\\" in raw_path or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValidationV2Error("Validation 路径无效。")
    return root.joinpath(*path.parts)


def _required_string(parameters: dict[str, Any], key: str) -> str:
    """读取 Validation 必需的非空字符串参数。"""

    value = parameters.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationV2Error(f"Validation 参数 {key} 必须是非空字符串。")
    return value


def _json_pointer(document: Any, pointer: str) -> tuple[bool, Any]:
    """解析 RFC 6901 风格的对象/数组指针，并区分缺失与值为 null。"""

    if not pointer.startswith("/"):
        raise ValidationV2Error("JSON pointer 必须以 / 开头。")
    value = document
    for segment in pointer[1:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            return False, None
    return True, value


def _assertion_result(item: ValidationPlanItemV2, passed: bool, message: str) -> ValidationResultV2:
    """构造不依赖命令进程的统一断言结果。"""

    return ValidationResultV2(item.validationId, passed, item.blocking, item.executionMode, 0, None, None if passed else "VALIDATION_ASSERTION_FAILED", message)


def _with_duration(result: ValidationResultV2, started: float) -> ValidationResultV2:
    """以单调时钟覆盖执行耗时，保证异常路径也给出可排序的诊断数据。"""

    return ValidationResultV2(**{**result.__dict__, "duration_ms": int((time.monotonic() - started) * 1000)})
