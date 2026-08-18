from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.agents.repair_planner import plan_repairs_with_repair_planner_agent
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.nodes.confirmation import extract_confirmation_answer, user_confirmed_text
from app.graph.state import ProjectState
from app.services.integration_test_runner import run_integration_checks
from app.services.test_validation import evaluate_quality_gate
from app.workspace.code_changes import code_change_state_update
from app.workspace.code_changes import merge_code_change_sets
from app.workspace.test_documents import write_test_report_json
from app.workspace.task_documents import write_repair_task_plan_json


INTEGRATION_TEST_PROGRESS_REPORTER_KEY = "integration_test_progress_reporter"
IntegrationTestProgressReporter = Callable[[dict[str, Any]], None]
_FRONTEND_SOURCE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
_BACKEND_SOURCE_SUFFIXES = {".java"}
_TEST_GENERATION_STATUSES = {"completed", "skipped", "failed"}
_UNIT_TEST_DECISIONS = {"skip", "run"}
_MAX_TEST_DIFF_ENTRIES = 20
_MAX_TEST_DIFF_CHARS = 12_000
_MAX_SINGLE_TEST_DIFF_CHARS = 6_000
_INTEGRATION_CHECK_ORDER = (
    "frontend_install",
    "frontend_typecheck",
    "frontend_build",
    "backend_build",
    "backend_static_check",
    "frontend_test_generation",
    "backend_test_generation",
    "frontend_unit_tests",
    "backend_unit_tests",
)


def _progress_reporter(config: RunnableConfig | None) -> IntegrationTestProgressReporter | None:
    """从运行配置读取瞬态进度回调，避免将回调写入可持久化 Graph State。"""

    configurable = config.get("configurable", {}) if config else {}
    reporter = configurable.get(INTEGRATION_TEST_PROGRESS_REPORTER_KEY)
    return reporter if callable(reporter) else None


def _check_progress_snapshot_writer() -> IntegrationTestProgressReporter:
    """将检查增量合并为小型快照，并通过 LangGraph custom stream 发送。"""

    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = lambda _: None
    checks: dict[str, dict[str, Any]] = {}

    def report(event: dict[str, Any]) -> None:
        """按稳定检查标识更新快照，确保前端不会为同一检查新增重复条目。"""

        check = event.get("check")
        if not isinstance(check, dict):
            return
        check_id = str(check.get("id") or "").strip()
        status = str(event.get("status") or "").strip()
        if not check_id or status not in {"running", "passed", "skipped", "failed"}:
            return
        checks[check_id] = {
            "id": check_id,
            "name": str(check.get("name") or check_id),
            "status": status,
            "required": bool(check.get("required")),
            "evidence": str(check.get("evidence") or "")[:1_000],
        }
        writer(
            {
                "type": "integration_test.checks",
                "checks": _ordered_integration_checks(list(checks.values())),
            }
        )

    return report


def _ordered_integration_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按构建、测试生成、单元测试的稳定顺序排列检查，同时保留未知检查的原顺序。"""

    positions = {
        check_id: index for index, check_id in enumerate(_INTEGRATION_CHECK_ORDER)
    }
    return [
        check
        for _, check in sorted(
            enumerate(checks),
            key=lambda item: (
                positions.get(
                    str(item[1].get("id") or ""),
                    len(_INTEGRATION_CHECK_ORDER),
                ),
                item[0],
            ),
        )
    ]


def _generation_progress_check(
    layer: str,
    *,
    evidence: str,
    required: bool,
) -> dict[str, Any]:
    """构造可在生成阶段提前展示的逐层单元测试生成检查。"""

    layer_label = "前端" if layer == "frontend" else "后端"
    return {
        "id": f"{layer}_test_generation",
        "name": f"{layer_label}单元测试生成检查",
        "required": required,
        "evidence": evidence,
    }


def _report_generation_progress(
    reporter: IntegrationTestProgressReporter | None,
    *,
    check: dict[str, Any],
    status: str,
) -> None:
    """安全发布测试生成检查进度，展示通道异常不得中断测试流程。"""

    if reporter is None:
        return
    try:
        reporter({"check": check, "status": status})
    except Exception:
        return


def _unit_test_decision(state: ProjectState | dict[str, Any]) -> str:
    """读取并规范化用户对单元测试的 skip/run 决策。"""

    raw_decision = state.get("unit_test_decision")
    if isinstance(raw_decision, dict) and "selected" in raw_decision:
        selected = raw_decision.get("selected")
        raw_decision = (
            selected[0]
            if isinstance(selected, list) and selected
            else selected
        )
    normalized = str(raw_decision or "").strip().casefold()
    if normalized in {"skip", "是", "yes", "true", "跳过"}:
        return "skip"
    if normalized in {"run", "否", "no", "false", "继续"}:
        return "run"
    return ""


def _unit_test_confirmation_payload() -> dict[str, Any]:
    """构造构建完成后的单元测试可选确认载荷。"""

    return {
        "mode": "unit_test_confirmation",
        "status": "requires_user_input",
        "message": "构建检查已完成。单元测试不是必需步骤，可能耗时较长，是否跳过单元测试？",
        "questions": [
            {
                "id": "unit_test_confirmation",
                "header": "单元测试",
                "question": "是否跳过单元测试？",
                "type": "choice",
                "allowOther": False,
                "options": [
                    {
                        "label": "是，跳过单元测试",
                        "value": "skip",
                        "description": "不生成、不执行单元测试。",
                    },
                    {
                        "label": "否，继续执行",
                        "value": "run",
                        "description": "继续生成并执行单元测试。",
                    },
                ],
            }
        ],
    }


def _progress_status_for_result(check: dict[str, Any]) -> str:
    """把最终检查结果转换为实时矩阵可识别的状态。"""

    if check.get("skipped") and check.get("passed"):
        return "skipped"
    return "passed" if check.get("passed") else "failed"


def _report_result_snapshot(
    reporter: IntegrationTestProgressReporter | None,
    checks: list[dict[str, Any]],
) -> None:
    """恢复已完成的构建检查快照，避免确认恢复时矩阵短暂丢失。"""

    for check in checks:
        if not isinstance(check, dict):
            continue
        _report_generation_progress(
            reporter,
            check=check,
            status=_progress_status_for_result(check),
        )


def collect_unit_test_targets(state: ProjectState) -> dict[str, Any]:
    """从本轮真实代码差异收集需要生成或更新单元测试的业务源码。"""

    if state.get("unit_test_generation_enabled") is False:
        return {
            "unit_test_generation_context": {
                "enabled": False,
                "source_files": [],
                "affected_layers": [],
                "has_targets": False,
                "max_test_files": 5,
            },
            "unit_test_affected_layers": [],
            "test_events": ["unit_test_targets:disabled"],
        }
    previous_context = state.get("unit_test_generation_context")
    previous_context = previous_context if isinstance(previous_context, dict) else {}
    if state.get("unit_test_build_checks_completed") and previous_context:
        return {
            "unit_test_generation_context": previous_context,
            "unit_test_affected_layers": _string_list(
                previous_context.get("affected_layers"), limit=2
            ),
            "test_events": ["unit_test_targets:reused_after_build"],
        }

    source_paths: list[str] = []
    changed_paths: list[str] = []
    primary = state.get("test_generation_input_code_changes") or state.get("code_changes")
    change_sets = [
        item
        for item in state.get("test_generation_input_code_change_sets", [])
        if isinstance(item, dict)
    ]
    if isinstance(primary, dict) and primary.get("files"):
        change_sets.insert(0, primary)
    change_sets.extend(
        item
        for key in ("direct_code_change_sets", "small_task_code_change_sets")
        for item in state.get(key, [])
        if isinstance(item, dict)
    )
    for change_set in change_sets:
        files = change_set.get("files", [])
        if not isinstance(files, list):
            continue
        for item in files:
            if not isinstance(item, dict):
                continue
            path = _normalized_workspace_path(item.get("path"))
            if path:
                changed_paths.append(path)
            if path and _source_layer(path) and _is_behavioral_change(item):
                source_paths.append(path)
    if not changed_paths:
        # 某些旧 Build 节点只把 changed_files 放在 build_results 中；仅在没有
        # 更权威的真实变更集时回退读取，避免把历史结果误判为本轮目标。
        for build_result in state.get("build_results", []):
            if not isinstance(build_result, dict):
                continue
            files = build_result.get("changed_files") or build_result.get("changedFiles")
            if not isinstance(files, list):
                continue
            for item in files:
                item = item if isinstance(item, dict) else {"path": item}
                path = _normalized_workspace_path(item.get("path"))
                if path:
                    changed_paths.append(path)
                if path and _source_layer(path) and _is_behavioral_change(item):
                    source_paths.append(path)
    source_paths = list(dict.fromkeys(source_paths))[:100]
    changed_paths = list(dict.fromkeys(changed_paths))
    affected_layers = list(
        dict.fromkeys(
            layer
            for path in source_paths
            if (layer := _source_layer(path)) is not None
        )
    )
    existing_test_files = [
        path
        for path in changed_paths
        if _test_file_layer(path) in {"frontend", "backend"}
    ]
    if not source_paths and existing_test_files:
        source_paths = _string_list(previous_context.get("source_files"), limit=100)
        affected_layers = list(
            dict.fromkeys(
                [
                    *affected_layers,
                    *_string_list(state.get("unit_test_affected_layers"), limit=2),
                    *(
                        _test_file_layer(path)
                        for path in existing_test_files
                        if _test_file_layer(path)
                    ),
                ]
            )
        )
    context = {
        "enabled": True,
        "source_files": source_paths,
        "code_diff": _bounded_test_generation_diff(change_sets, source_paths),
        "affected_layers": affected_layers,
        "has_targets": bool(source_paths),
        "existing_test_files": existing_test_files,
        "max_test_files": 5,
        "skipped_candidates": source_paths[5:]
        if len(source_paths) > 5
        else [],
        "build_execution_scope": state.get("build_execution_scope", {}),
        "build_execution_slice": state.get("build_execution_slice", {}),
        "build_task_plan_path": state.get("build_task_plan_path")
        or ".xcodeagent/plans/build-task-plan.json",
        "project_plan_path": state.get("project_plan_path")
        or ".xcodeagent/plans/project-plan.md",
        "project_plan_json_path": state.get("project_plan_json_path")
        or ".xcodeagent/plans/project-plan.json",
        "requirement_spec_path": state.get("requirement_spec_path")
        or ".xcodeagent/specs/requirement-spec.md",
        "requirement_spec_json_path": state.get("requirement_spec_json_path")
        or ".xcodeagent/specs/requirement-spec.json",
        "code_graph_index": state.get("code_graph_index", {}),
        "page_selection": state.get("page_selection", {}),
        "detail_selection": state.get("detail_selection", {}),
        "detail_plans": state.get("detail_plans", [])[:20]
        if isinstance(state.get("detail_plans"), list)
        else [],
    }
    return {
        "unit_test_generation_context": context,
        "test_events": ["unit_test_targets:collected"],
    }


def generate_unit_tests(
    state: ProjectState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """调用专用 TestGeneration Agent，并把异常降级为零测试的可审计跳过结果。"""

    context = state.get("unit_test_generation_context")
    context = context if isinstance(context, dict) else {}
    context_layers = [
        layer
        for layer in _string_list(context.get("affected_layers"), limit=2)
        if layer in {"frontend", "backend"}
    ]
    reporter = _progress_reporter(config)
    for layer in context_layers:
        _report_generation_progress(
            reporter,
            check=_generation_progress_check(
                layer,
                evidence="正在调用 TestGeneration Agent 生成或更新受影响的单元测试文件。",
                required=True,
            ),
            status="running",
        )
    if context.get("enabled") is False:
        result = _skipped_generation_result("快速修改流程未启用单元测试生成。")
    elif not context.get("has_targets") and context.get("existing_test_files"):
        result = {
            "status": "completed",
            "summary": "本轮已有单元测试文件发生变更，直接执行受影响测试。",
            "affected_layers": context.get("affected_layers", []),
            "test_files": _string_list(context.get("existing_test_files"), limit=5),
            "warnings": [],
            "validation": {"valid": True, "source": "test_change"},
            "code_change_sets": [],
            "mapping_path": state.get("unit_test_mapping_path"),
        }
    elif not context.get("has_targets"):
        result = _skipped_generation_result("本轮没有可测试的前后端业务源码变更。")
    else:
        try:
            result = _invoke_test_generation_agent(
                state={**state, "unit_test_generation_context": context},
                workspace=workspace_from_state(state),
                selected_skill_names=state.get("selected_skill_names"),
            )
        except Exception as exc:
            result = _skipped_generation_result(
                f"TestGeneration Agent 执行异常，按零个测试文件继续：{type(exc).__name__}: {exc}"
            )
    normalized = _normalize_generation_result(result, context=context)
    if context_layers:
        normalized["affected_layers"] = context_layers
    skipped_candidates = _string_list(context.get("skipped_candidates"), limit=20)
    if skipped_candidates:
        normalized["warnings"] = [
            *normalized.get("warnings", []),
            f"测试文件预算最多 5 个，以下候选未生成：{'、'.join(skipped_candidates)}",
        ]
    previous_sets = [
        item
        for item in (
            state.get("unit_test_generation_code_change_sets")
            or state.get("unit_test_code_change_sets", [])
        )
        if isinstance(item, dict)
    ]
    generation_change_sets = [
        *previous_sets,
        *normalized.get("code_change_sets", []),
    ]
    return {
        "unit_test_generation": normalized,
        "unit_test_affected_layers": context_layers,
        "unit_test_mapping_path": normalized.get("mapping_path"),
        "unit_test_code_change_sets": generation_change_sets,
        "unit_test_generation_code_change_sets": generation_change_sets,
        "test_events": [f"unit_test_generation:{normalized['status']}"],
    }


def validate_generated_unit_tests(
    state: ProjectState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """把测试生成结果转换为质量门可消费的逐层结构化检查。"""

    generation = state.get("unit_test_generation")
    generation = (
        generation
        if isinstance(generation, dict)
        else _skipped_generation_result("未获得测试生成结果，按零个测试文件继续。")
    )
    context = state.get("unit_test_generation_context")
    context = context if isinstance(context, dict) else {}
    layers = [
        layer
        for layer in _string_list(
            context.get("affected_layers") or generation.get("affected_layers"),
            limit=2,
        )
        if layer in {"frontend", "backend"}
    ]
    status = str(generation.get("status") or "skipped")
    if status not in _TEST_GENERATION_STATUSES:
        status = "failed"
    validation = generation.get("validation")
    validation = validation if isinstance(validation, dict) else {}
    validation = _validate_generation_files(state, generation, validation)
    validation_failed = validation.get("valid") is False or any(
        validation.get(key)
        for key in (
            "unauthorized_paths",
            "invalid_paths",
            "invalid_contents",
            "missing_files",
            "too_many_files",
            "unaffected_layer_paths",
        )
    )
    if not layers and (status == "failed" or validation_failed):
        # 即使 Agent 未能归属到具体端，安全/格式失败也必须进入质量门禁。
        layers = ["workspace"]
    if status == "completed" and not generation.get("test_files") and not validation_failed:
        status = "skipped"
    passed = status != "failed" and not validation_failed
    skipped = status == "skipped" and not validation_failed
    summary = str(generation.get("summary") or "未生成单元测试文件。")[:2_000]
    checks = [
        {
            "id": f"{layer}_test_generation",
            "name": (
                f"{'前端' if layer == 'frontend' else '后端' if layer == 'backend' else '项目'}"
                "单元测试生成检查"
            ),
            "layer": None if layer == "workspace" else layer,
            "language": (
                "typescript"
                if layer == "frontend"
                else "java"
                if layer == "backend"
                else None
            ),
            "passed": passed,
            "skipped": skipped,
            "required": bool(generation.get("test_files")) and not skipped,
            "command": None,
            "evidence": summary,
            "failure_category": None if passed else "test_generation_failure",
            "execution": {
                "tool": "test-generation-agent" if not skipped else "none",
                "argv": [],
                "cwd": ".",
                "returncode": None,
                "timed_out": False,
                "stdout_log": None,
                "stderr_log": None,
            },
        }
        for layer in layers
    ]
    reporter = _progress_reporter(config)
    for check in checks:
        progress_status = (
            "skipped"
            if check["skipped"]
            else "passed"
            if check["passed"]
            else "failed"
        )
        _report_generation_progress(
            reporter,
            check=check,
            status=progress_status,
        )
    return {
        "test_results": [*state.get("test_results", []), *checks],
        "test_events": [check["id"] for check in checks],
    }


def _validate_generation_files(
    state: ProjectState,
    generation: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    """在节点边界再次确认 Agent 声明的测试文件确实存在且路径合法。"""

    workspace = workspace_from_state(state)
    test_files = _string_list(generation.get("test_files"), limit=5)
    if not workspace or not test_files:
        return validation
    from pathlib import Path

    root = Path(workspace).expanduser().resolve()
    missing: list[str] = []
    for path in test_files:
        try:
            resolved = (root / path).resolve()
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            missing.append(path)
            continue
        if _test_file_layer(path) not in {"frontend", "backend"} or not resolved.is_file():
            missing.append(path)
    if not missing:
        from app.agents.test_generation.generator import _validate_test_files

        context = state.get("unit_test_generation_context")
        context = context if isinstance(context, dict) else {}
        source_files = _string_list(context.get("source_files"), limit=100)
        content_validation = _validate_test_files(
            str(root),
            test_files,
            [],
            source_files=source_files,
        )
        merged = dict(validation)
        for key in ("invalid_paths", "invalid_contents", "missing_files"):
            values = _string_list(
                [
                    *_string_list(merged.get(key), limit=20),
                    *_string_list(content_validation.get(key), limit=20),
                ],
                limit=20,
            )
            if values:
                merged[key] = values
        if not content_validation.get("valid", True):
            merged["valid"] = False
        return merged
    merged = dict(validation)
    merged["valid"] = False
    merged["missing_files"] = list(
        dict.fromkeys([*_string_list(merged.get("missing_files"), limit=20), *missing])
    )
    return merged


def _invoke_test_generation_agent(
    *,
    state: dict[str, Any],
    workspace: str | None,
    selected_skill_names: list[str] | None,
) -> dict[str, Any]:
    """延迟导入 TestGeneration Agent，避免 Agent bundle 初始化污染确定性收集阶段。"""

    from app.agents.test_generation import generate_or_update_unit_tests_with_agent

    return generate_or_update_unit_tests_with_agent(
        state,
        workspace,
        selected_skill_names=selected_skill_names,
        on_tool_activity=None,
    )


def _normalize_generation_result(
    value: Any,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    """收敛 Agent 返回值并限制测试文件和代码变更集合的数量。"""

    payload = value if isinstance(value, dict) else {}
    status = str(payload.get("status") or "failed").strip().lower()
    if status not in _TEST_GENERATION_STATUSES:
        status = "failed"
    test_files = _string_list(payload.get("test_files"), limit=5)
    affected_layers = [
        layer
        for layer in _string_list(
            payload.get("affected_layers") or context.get("affected_layers"),
            limit=2,
        )
        if layer in {"frontend", "backend"}
    ]
    if status == "completed" and not test_files:
        status = "skipped"
    validation = (
        payload.get("validation")
        if isinstance(payload.get("validation"), dict)
        else {}
    )
    # 没有留下测试文件时尽力放行；越权写入或无效测试文件仍是硬失败。
    if status == "failed" and not test_files and not any(
        validation.get(key)
        for key in (
            "unauthorized_paths",
            "invalid_paths",
            "invalid_contents",
            "missing_files",
            "too_many_files",
            "unaffected_layer_paths",
        )
    ):
        status = "skipped"
    return {
        "status": status,
        "summary": str(
            payload.get("summary") or "TestGeneration Agent 未返回有效摘要。"
        )[:2_000],
        "affected_layers": list(dict.fromkeys(affected_layers)),
        "test_files": test_files,
        "warnings": _string_list(payload.get("warnings"), limit=20),
        "validation": validation,
        "code_change_sets": (
            [
                item
                for item in payload.get("code_change_sets", [])[:5]
                if isinstance(item, dict)
            ]
            if isinstance(payload.get("code_change_sets"), list)
            else []
        ),
        "mapping_path": str(payload.get("mapping_path") or "") or None,
    }


def _skipped_generation_result(summary: str) -> dict[str, Any]:
    """构造没有测试目标或 Agent 异常时的稳定跳过结果。"""

    return {
        "status": "skipped",
        "summary": summary[:2_000],
        "affected_layers": [],
        "test_files": [],
        "warnings": [summary[:2_000]],
        "validation": {},
        "code_change_sets": [],
        "mapping_path": None,
    }


def _source_layer(path: str) -> str | None:
    """识别允许触发测试生成的前后端业务源码，排除测试、样式和配置文件。"""

    normalized = path.casefold()
    suffix = PurePosixPath(normalized).suffix
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in {"test", "tests", "__tests__"} for part in parts):
        return None
    if any(
        part in {
            "asset",
            "assets",
            "resource",
            "resources",
            "public",
            "static",
            "locale",
            "locales",
            "i18n",
            "style",
            "styles",
            "theme",
            "themes",
            "config",
            "configs",
            "configuration",
            "configurations",
        }
        for part in parts
    ):
        return None
    if normalized.endswith((".d.ts", ".stories.ts", ".stories.tsx")):
        return None
    if parts[0] == "frontend" and suffix in _FRONTEND_SOURCE_SUFFIXES and "src" in parts:
        if PurePosixPath(normalized).stem.casefold().endswith(
            ("config", "configuration", "style", "styles", "theme", "themes")
        ):
            return None
        return "frontend"
    if (
        parts[0] == "backend"
        and suffix in _BACKEND_SOURCE_SUFFIXES
        and tuple(parts[1:4]) == ("src", "main", "java")
    ):
        stem = PurePosixPath(normalized).stem.casefold()
        if (
            "infrastructure" in parts[4:]
            or "config" in parts[4:]
            or stem in {"application", "applicationconfig"}
            or stem.endswith(
                ("config", "configuration")
            )
            or stem.endswith(("dto", "request", "response", "vo", "entity"))
        ):
            return None
        return "backend"
    return None


def _is_behavioral_change(file_item: dict[str, Any]) -> bool:
    """过滤只有注释或空白变化的源码差异，避免为文档整理生成测试。"""

    diff = file_item.get("diff")
    if not isinstance(diff, str) or not diff.strip():
        return True
    changed_lines = []
    for line in diff.splitlines():
        if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
            continue
        content = line[1:].strip()
        if content:
            changed_lines.append(content)
    if not changed_lines:
        return True
    return any(
        not line.startswith(("//", "/*", "*", "*/", "{/*", "<!--", "#"))
        for line in changed_lines
    )


def _bounded_test_generation_diff(
    change_sets: list[dict[str, Any]],
    source_paths: list[str],
) -> dict[str, Any]:
    """提取本轮目标源码的真实 diff，并按条目与字符预算裁剪测试上下文。"""

    target_paths = set(source_paths)
    change_set_ids = list(
        dict.fromkeys(
            str(change_set.get("id") or "").strip()
            for change_set in change_sets
            if str(change_set.get("id") or "").strip()
        )
    )[:20]
    files: list[dict[str, Any]] = []
    seen_entries: set[tuple[str, str, str]] = set()
    remaining_chars = _MAX_TEST_DIFF_CHARS
    for change_set in change_sets:
        raw_files = change_set.get("files")
        if not isinstance(raw_files, list):
            continue
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            path = _normalized_workspace_path(item.get("path"))
            diff = item.get("diff")
            if (
                path not in target_paths
                or not isinstance(diff, str)
                or not diff.strip()
            ):
                continue
            signature = (path, str(item.get("changeType") or ""), diff)
            if signature in seen_entries:
                continue
            seen_entries.add(signature)
            excerpt_limit = min(remaining_chars, _MAX_SINGLE_TEST_DIFF_CHARS)
            if excerpt_limit <= 0 or len(files) >= _MAX_TEST_DIFF_ENTRIES:
                break
            excerpt = diff[:excerpt_limit]
            files.append(
                {
                    "path": path,
                    "changeType": str(item.get("changeType") or "modified"),
                    "additions": item.get("additions", 0),
                    "deletions": item.get("deletions", 0),
                    "diff": excerpt,
                    "truncated": bool(item.get("truncated")) or len(diff) > len(excerpt),
                }
            )
            remaining_chars -= len(excerpt)
        if remaining_chars <= 0 or len(files) >= _MAX_TEST_DIFF_ENTRIES:
            break
    return {
        "change_set_ids": change_set_ids,
        "files": files,
        "summary": {
            "files": len({item["path"] for item in files}),
            "diff_entries": len(files),
            "truncated": (
                any(bool(item["truncated"]) for item in files)
                or remaining_chars <= 0
                or len(files) >= _MAX_TEST_DIFF_ENTRIES
            ),
        },
    }


def _normalized_workspace_path(value: Any) -> str:
    """规范化工作区相对路径并拒绝上跳路径。"""

    path = str(value or "").strip().replace("\\", "/").lstrip("/")
    return "" if not path or ".." in PurePosixPath(path).parts else path


def _string_list(value: Any, *, limit: int) -> list[str]:
    """把不可信 Agent 列表裁剪为有界、去重的字符串列表。"""

    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            str(item).strip()[:1_000] for item in value if str(item).strip()
        )
    )[:limit]


def build_project_checks(
    state: ProjectState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """先执行依赖、类型和构建检查，为后续测试生成建立完成门。"""

    reporter = _progress_reporter(config)
    decision = _unit_test_decision(state)
    cached_results = state.get("unit_test_build_results")
    if (
        state.get("unit_test_build_checks_completed")
        and decision
        and isinstance(cached_results, list)
        and cached_results
    ):
        cached_results = _ordered_integration_checks(
            [item for item in cached_results if isinstance(item, dict)]
        )
        _report_result_snapshot(reporter, cached_results)
        return {
            "test_results": cached_results,
            "unit_test_build_results": cached_results,
            "unit_test_build_checks_completed": True,
            "test_events": ["unit_test_build:reused_after_confirmation"],
        }

    result = run_integration_checks(
        state,
        on_progress=reporter,
        phase="build",
    )
    test_results = _ordered_integration_checks(
        [
            *state.get("test_results", []),
            *result.get("test_results", []),
        ]
    )
    return {
        "test_results": test_results,
        "unit_test_build_results": test_results,
        "unit_test_build_checks_completed": True,
        "test_events": result.get("test_events", []),
    }


def unit_test_confirmation(state: ProjectState) -> dict[str, Any]:
    """在构建检查完成后暂停，等待用户选择是否执行可选单元测试。"""

    if state.get("unit_test_generation_enabled") is False:
        return {
            "status": "in_progress",
            "clarification": {},
            "unit_test_decision": "skip",
            "integration_next_action": "skip_unit_tests",
            "test_events": ["unit_test_confirmation:auto_skipped_disabled"],
        }
    decision = _unit_test_decision(state)
    if decision in _UNIT_TEST_DECISIONS:
        return {
            "status": "in_progress",
            "clarification": {},
            "unit_test_decision": decision,
            "integration_next_action": (
                "skip_unit_tests" if decision == "skip" else "run_unit_tests"
            ),
            "test_events": [f"unit_test_confirmation:{decision}"],
        }
    return {
        "status": "requires_user_input",
        "clarification": _unit_test_confirmation_payload(),
        "integration_next_action": "await_user_input",
        "test_events": ["unit_test_confirmation:requires_user_input"],
    }


def _route_unit_test_confirmation(state: ProjectState) -> str:
    """根据用户确认结果选择跳过、继续或暂停测试子图。"""

    decision = _unit_test_decision(state)
    if decision == "skip":
        return "skip_unit_tests"
    if decision == "run":
        return "generate_unit_tests"
    return "await_user_input"


def _unit_test_layers(state: ProjectState) -> list[str]:
    """读取本轮受影响的前后端层，供单元测试确认和跳过结果复用。"""

    context = state.get("unit_test_generation_context")
    context = context if isinstance(context, dict) else {}
    layers = _string_list(
        context.get("affected_layers") or state.get("unit_test_affected_layers"),
        limit=2,
    )
    return [layer for layer in layers if layer in {"frontend", "backend"}]


def _skipped_unit_test_check(
    *,
    layer: str,
    check_type: str,
) -> dict[str, Any]:
    """构造用户主动跳过时的单元测试生成或执行检查结果。"""

    layer_label = "前端" if layer == "frontend" else "后端"
    if check_type == "generation":
        check_id = f"{layer}_test_generation"
        name = f"{layer_label}单元测试生成检查"
    else:
        check_id = f"{layer}_unit_tests"
        name = f"{layer_label}单元测试"
    return {
        "id": check_id,
        "name": name,
        "layer": layer,
        "language": "typescript" if layer == "frontend" else "java",
        "passed": True,
        "skipped": True,
        "required": False,
        "command": None,
        "evidence": "用户选择跳过单元测试，本轮不生成、不执行单元测试。",
        "failure_category": None,
        "execution": {
            "tool": "none",
            "argv": [],
            "cwd": ".",
            "returncode": None,
            "timed_out": False,
            "stdout_log": None,
            "stderr_log": None,
        },
    }


def skip_unit_tests(
    state: ProjectState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """记录用户主动跳过的生成和单元测试检查，并直接进入质量门禁。"""

    checks = [
        _skipped_unit_test_check(layer=layer, check_type=check_type)
        for layer in _unit_test_layers(state)
        for check_type in ("generation", "unit")
    ]
    reporter = _progress_reporter(config)
    for check in checks:
        _report_generation_progress(reporter, check=check, status="skipped")
    return {
        "test_results": _ordered_integration_checks(
            [*state.get("test_results", []), *checks]
        ),
        "unit_test_decision": "skip",
        "test_events": [
            "unit_test_generation:skipped_by_user",
            "unit_tests:skipped_by_user",
        ],
    }


def actual_project_checks(
    state: ProjectState,
    config: RunnableConfig,
) -> dict:
    """在测试生成完成后执行前后端单元测试并流式展示命令状态。"""

    result = run_integration_checks(
        state,
        on_progress=_progress_reporter(config),
        phase="unit",
    )
    test_results = _ordered_integration_checks(
        [
            *state.get("test_results", []),
            *result.get("test_results", []),
        ]
    )
    return {
        "test_results": test_results,
        "test_events": result.get("test_events", []),
    }


def main_quality_gate(state: ProjectState) -> dict:
    report = evaluate_quality_gate(
        test_results=state.get("test_results", []),
    )
    report_path = write_test_report_json(state, report)
    return {
        "phase": "integration_test",
        "test_report": report,
        "test_report_path": report_path,
        "quality_gate_passed": report["passed"],
        "needs_revision": report["needs_revision"],
        "revision_requests": report["revision_requests"],
        "test_events": ["main_quality_gate"],
    }


def repair_planning(state: ProjectState) -> dict:
    """根据质量门禁结果选择 RepairPlanner 修复任务或终止路径。"""

    if state.get("quality_gate_passed"):
        return {
            "repair_task_plan": {},
            "repair_tasks": [],
            "integration_next_action": "launch_project",
            "test_events": ["repair_planning:skipped"],
        }

    security_failure = _generation_security_failure(state)
    if security_failure:
        return {
            "repair_task_plan": {
                "version": "0.1.0",
                "status": "terminal_failure",
                "decision": "terminal_failure",
                "reason": security_failure,
                "tasks": [],
            },
            "repair_tasks": [],
            "integration_next_action": "handle_failure",
            "test_events": ["repair_planning:security_failure"],
        }

    if state.get("integration_repair_enabled") is False:
        return {
            "repair_task_plan": {},
            "repair_tasks": [],
            "integration_next_action": "handle_failure",
            "test_events": ["repair_planning:disabled"],
        }

    existing_plan = state.get("repair_task_plan")
    request = str(state.get("request") or "")
    if (
        isinstance(existing_plan, dict)
        and existing_plan.get("decision") == "requires_user_confirmation"
    ):
        answer = extract_confirmation_answer(request).replace(" ", "")
        if any(signal in answer for signal in ("拒绝", "不同意", "不批准")):
            rejected_plan = {
                **existing_plan,
                "status": "terminal_failure",
                "decision": "terminal_failure",
                "tasks": [],
            }
            return {
                "repair_task_plan": rejected_plan,
                "repair_tasks": [],
                "integration_next_action": "handle_failure",
                "clarification": {},
                "test_events": ["repair_planning:scope_rejected"],
            }
        if user_confirmed_text(
            request,
            positive_signals=("批准", "同意", "确认"),
            negative_signals=("拒绝", "不同意", "不批准"),
        ):
            approved_tasks = [
                task
                for task in existing_plan.get("candidateTasks", [])
                if isinstance(task, dict)
            ]
            approved_plan = {
                **existing_plan,
                "status": "ready" if approved_tasks else "terminal_failure",
                "decision": "repair" if approved_tasks else "terminal_failure",
                "tasks": approved_tasks,
                "approvedPlanId": existing_plan.get("planId"),
            }
            return {
                "repair_task_plan": approved_plan,
                "repair_tasks": approved_tasks,
                "integration_next_action": "small_task_repair" if approved_tasks else "handle_failure",
                "clarification": {},
                "test_events": ["repair_planning:scope_approved"],
            }

    repair_iteration = int(state.get("repair_iteration", 0) or 0)
    max_repair_iterations = int(state.get("max_repair_iterations", 3) or 3)
    if repair_iteration >= max_repair_iterations:
        repair_task_plan = {
            "version": "0.1.0",
            "status": "terminal_failure",
            "decision": "terminal_failure",
            "reason": "Integration repair iteration budget exhausted.",
            "tasks": [],
        }
        repair_task_plan_path = write_repair_task_plan_json(state, repair_task_plan)
        return {
            "repair_task_plan": repair_task_plan,
            "repair_task_plan_path": repair_task_plan_path,
            "repair_tasks": [],
            "repair_iteration": repair_iteration,
            "max_repair_iterations": max_repair_iterations,
            "integration_next_action": "handle_failure",
            "test_events": ["repair_planning:budget_exhausted"],
        }

    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="repair_planner.deep_agent",
        action=lambda: plan_repairs_with_repair_planner_agent(
            test_report=state.get("test_report", {}),
            revision_requests=state.get("revision_requests", []),
            build_task_plan=state.get("build_task_plan"),
            build_execution_scope=state.get("build_execution_scope"),
            scoped_tasks=_repair_scoped_tasks(state),
            repair_attempt=repair_iteration + 1,
            workspace=workspace,
            selected_skill_names=state.get("selected_skill_names"),
        ),
    )
    repair_task_plan = captured.value
    repair_task_plan_path = write_repair_task_plan_json(state, repair_task_plan)
    next_action = _next_action_for_repair_plan(repair_task_plan)
    return {
        **code_change_state_update(captured.code_change_set),
        "repair_task_plan": repair_task_plan,
        "repair_task_plan_path": repair_task_plan_path,
        "repair_tasks": repair_task_plan.get("tasks", []),
        "repair_iteration": repair_iteration,
        "max_repair_iterations": max_repair_iterations,
        "integration_next_action": next_action,
        "clarification": (
            _repair_scope_confirmation_payload(repair_task_plan)
            if next_action == "await_user_input"
            else {}
        ),
        "test_events": ["repair_planning"],
    }


def _repair_scope_confirmation_payload(repair_task_plan: dict[str, Any]) -> dict[str, Any]:
    """构造集成测试修复范围的 AG-UI 确认载荷。"""

    plan_id = str(repair_task_plan.get("planId") or "")
    requested_paths = [
        str(path) for path in repair_task_plan.get("requestedPaths", []) if str(path).strip()
    ]
    requested_resources = [
        dict(item)
        for item in repair_task_plan.get("requestedResources", [])
        if isinstance(item, dict)
    ]
    reason = str(repair_task_plan.get("reason") or "修复需要用户批准范围。")
    return {
        "mode": "repair_scope_confirmation",
        "status": "requires_user_input",
        "message": "测试修复计划请求确认代码修改范围。",
        "planId": plan_id,
        "requestedPaths": requested_paths,
        "requestedResources": requested_resources,
        "reason": reason,
        "questions": [
            {
                "id": "repair_scope_confirmation",
                "header": "修复范围",
                "question": (
                    f"计划 {plan_id} 请求修改：{'、'.join(requested_paths) or '未提供额外路径'}。"
                    f"原因：{reason}。是否批准？"
                ),
                "type": "text",
                "placeholder": "回复“批准修复范围”或“拒绝修复范围”。",
            }
        ],
    }


def _next_action_for_repair_plan(repair_task_plan: dict) -> str:
    decision = repair_task_plan.get("decision")
    status = repair_task_plan.get("status")
    if decision == "requires_user_confirmation" or status == "requires_user_confirmation":
        return "await_user_input"
    if decision == "terminal_failure" or status == "terminal_failure":
        return "handle_failure"
    if repair_task_plan.get("tasks"):
        return "small_task_repair"
    return "handle_failure"


def _repair_scoped_tasks(state: ProjectState) -> list[dict[str, Any]]:
    """把生成测试文件并入同层修复授权，使 SmallTask 能判断测试或业务代码错误。"""

    execution_slice = state.get("build_execution_slice")
    execution_slice = execution_slice if isinstance(execution_slice, dict) else {}
    tasks = [
        dict(item)
        for item in execution_slice.get("tasks", [])
        if isinstance(item, dict)
    ]
    generation = state.get("unit_test_generation")
    generation = generation if isinstance(generation, dict) else {}
    test_files = _string_list(generation.get("test_files"), limit=5)
    context = state.get("unit_test_generation_context")
    context = context if isinstance(context, dict) else {}
    source_files = _string_list(context.get("source_files"), limit=100)
    for owner in ("frontend", "backend"):
        owner_tests = [path for path in test_files if _test_file_layer(path) == owner]
        owner_sources = [path for path in source_files if _source_layer(path) == owner]
        if not owner_tests and not owner_sources:
            continue
        owner_task = next((task for task in tasks if task.get("owner") == owner), None)
        if owner_task is None:
            owner_task = {"id": f"unit-tests:{owner}", "owner": owner, "unit_id": owner}
            tasks.append(owner_task)
        for key in ("allowed_paths", "target_files"):
            owner_task[key] = list(
                dict.fromkeys(
                    [
                        *_string_list(owner_task.get(key), limit=100),
                        *owner_sources,
                        *owner_tests,
                    ]
                )
            )
    return tasks


def _generation_security_failure(state: ProjectState) -> str | None:
    """越权写入测试目录外时立即终止，避免 SmallTask 接管安全边界。"""

    generation = state.get("unit_test_generation")
    if not isinstance(generation, dict):
        return None
    validation = generation.get("validation")
    if not isinstance(validation, dict):
        return None
    paths = _string_list(validation.get("unauthorized_paths"), limit=20)
    if not paths:
        return None
    return f"测试生成 Agent 检测到测试目录外实际写入：{'、'.join(paths)}"


def _test_file_layer(path: str) -> str | None:
    """根据约定测试目录识别测试文件所属层。"""

    normalized = path.casefold().replace("\\", "/").lstrip("/")
    if normalized.startswith("frontend/tests/") and normalized.endswith(
        (".test.ts", ".test.tsx")
    ):
        return "frontend"
    if normalized.startswith("backend/src/test/java/") and normalized.endswith(
        "test.java"
    ):
        return "backend"
    return None


def build_testing_subgraph():
    builder = StateGraph(ProjectState)

    builder.add_node("collect_unit_test_targets", collect_unit_test_targets)
    builder.add_node("build_project_checks", build_project_checks)
    builder.add_node("unit_test_confirmation", unit_test_confirmation)
    builder.add_node("skip_unit_tests", skip_unit_tests)
    builder.add_node("generate_unit_tests", generate_unit_tests)
    builder.add_node("validate_generated_unit_tests", validate_generated_unit_tests)
    builder.add_node("actual_project_checks", actual_project_checks)
    builder.add_node("main_quality_gate", main_quality_gate)
    builder.add_node("repair_planning", repair_planning)

    builder.add_edge(START, "collect_unit_test_targets")
    builder.add_edge("collect_unit_test_targets", "build_project_checks")
    builder.add_edge("build_project_checks", "unit_test_confirmation")
    builder.add_conditional_edges(
        "unit_test_confirmation",
        _route_unit_test_confirmation,
        {
            "skip_unit_tests": "skip_unit_tests",
            "generate_unit_tests": "generate_unit_tests",
            "await_user_input": END,
        },
    )
    builder.add_edge("skip_unit_tests", "main_quality_gate")
    builder.add_edge("generate_unit_tests", "validate_generated_unit_tests")
    builder.add_edge("validate_generated_unit_tests", "actual_project_checks")
    builder.add_edge("actual_project_checks", "main_quality_gate")
    builder.add_edge("main_quality_gate", "repair_planning")
    builder.add_edge("repair_planning", END)

    return builder.compile()


_testing_subgraph = build_testing_subgraph()


def integration_test(state: ProjectState) -> dict:
    """运行测试子图，并把内部检查的增量状态转发到主 Graph 流。"""

    previous_small_task_changes = [
        item
        for item in state.get("small_task_code_change_sets", [])
        if isinstance(item, dict)
    ]
    previous_test_changes = [
        item
        for item in (
            state.get("unit_test_generation_code_change_sets")
            or state.get("unit_test_code_change_sets", [])
        )
        if isinstance(item, dict)
    ]
    input_code_changes = state.get("code_changes")
    input_code_changes = input_code_changes if isinstance(input_code_changes, dict) else {}
    input_code_change_sets = [
        item for item in state.get("code_change_sets", []) if isinstance(item, dict)
    ]
    for key in ("direct_code_change_sets", "small_task_code_change_sets"):
        input_code_change_sets.extend(
            item for item in state.get(key, []) if isinstance(item, dict)
        )
    input_code_change_sets = list(
        {
            str(item.get("id") or id(item)): item
            for item in input_code_change_sets
        }.values()
    )
    decision = _unit_test_decision(state)
    reuse_build_checks = bool(
        state.get("unit_test_build_checks_completed")
        and decision in _UNIT_TEST_DECISIONS
    )
    cached_build_results = (
        state.get("unit_test_build_results")
        if isinstance(state.get("unit_test_build_results"), list)
        else []
    )
    result = _testing_subgraph.invoke(
        {
            **state,
            "test_generation_input_code_changes": input_code_changes,
            "test_generation_input_code_change_sets": input_code_change_sets,
            "unit_test_generation_enabled": state.get(
                "unit_test_generation_enabled", True
            ),
            "unit_test_code_change_sets": previous_test_changes,
            "test_results": cached_build_results if reuse_build_checks else [],
            "unit_test_build_results": cached_build_results if reuse_build_checks else [],
            "unit_test_build_checks_completed": reuse_build_checks,
            # 每轮测试必须从未决状态开始，避免重试时沿用上一轮质量门结果越过确认节点。
            "test_report": {},
            "test_report_path": None,
            "quality_gate_passed": False,
            "needs_revision": False,
            "revision_requests": [],
            "repair_task_plan": {},
            "repair_task_plan_path": None,
            "repair_tasks": [],
            "integration_next_action": "",
            "clarification": {},
            # 集成测试位于启动预览之前；重试该节点时必须清空上一轮启动与验收状态，
            # 否则投影层会把单元测试确认误识别成项目预览验收。
            "preview_url": "",
            "launch_result": {},
            "acceptance_request": {},
            "acceptance_decision": "",
            "accepted": False,
            "test_events": [],
            "code_changes": {},
            "code_change_sets": [],
            "timeline": [],
        },
        config={
            "configurable": {
                INTEGRATION_TEST_PROGRESS_REPORTER_KEY: _check_progress_snapshot_writer(),
            }
        },
    )
    current_test_changes = [
        item
        for item in (
            result.get("unit_test_generation_code_change_sets")
            or result.get("unit_test_code_change_sets", [])
        )
        if isinstance(item, dict)
    ]
    new_test_changes = current_test_changes[len(previous_test_changes):]
    repair_planner_changes = [
        item for item in result.get("code_change_sets", []) if isinstance(item, dict)
    ]
    new_code_change_sets = [*new_test_changes, *repair_planner_changes]
    waiting_for_unit_test_decision = (
        result.get("status") == "requires_user_input"
        and result.get("integration_next_action") == "await_user_input"
        and isinstance(result.get("clarification"), dict)
        and result.get("clarification", {}).get("mode") == "unit_test_confirmation"
    )
    quality_gate_passed = (
        False
        if waiting_for_unit_test_decision
        else bool(result.get("quality_gate_passed", False))
    )
    terminal_status = (
        "requires_user_input"
        if waiting_for_unit_test_decision
        else "completed"
        if quality_gate_passed
        else "failed"
    )
    return {
        "phase": "integration_test",
        "status": terminal_status,
        "clarification": (
            result.get("clarification", {})
            if waiting_for_unit_test_decision
            else {}
        ),
        "test_results": result.get("test_results", []),
        "test_events": result.get("test_events", []),
        "test_report": result.get("test_report", {}),
        "test_report_path": result.get("test_report_path"),
        "quality_gate_passed": quality_gate_passed,
        "needs_revision": result.get("needs_revision", False),
        "revision_requests": result.get("revision_requests", []),
        "repair_task_plan": result.get("repair_task_plan", {}),
        "repair_task_plan_path": result.get("repair_task_plan_path"),
        "repair_tasks": result.get("repair_tasks", []),
        "unit_test_generation_context": result.get("unit_test_generation_context", {}),
        "unit_test_generation": result.get("unit_test_generation", {}),
        "unit_test_affected_layers": result.get("unit_test_affected_layers", []),
        "unit_test_decision": (
            "" if not waiting_for_unit_test_decision else decision
        ),
        "unit_test_build_checks_completed": (
            bool(result.get("unit_test_build_checks_completed"))
            if waiting_for_unit_test_decision
            else False
        ),
        "unit_test_build_results": (
            result.get("unit_test_build_results", [])
            if waiting_for_unit_test_decision
            else []
        ),
        "unit_test_mapping_path": result.get("unit_test_mapping_path"),
        "unit_test_code_change_sets": current_test_changes,
        "unit_test_generation_code_change_sets": current_test_changes,
        "small_task_tasks": result.get("repair_tasks", []),
        "small_task_results": state.get("small_task_results", []),
        "small_task_code_change_sets": [
            *previous_small_task_changes,
            *repair_planner_changes,
        ],
        "small_task_handoff": state.get("small_task_handoff", {}),
        "small_task_handoff_submission": state.get("small_task_handoff_submission", {}),
        "small_task_route": "small_task_repair"
        if result.get("repair_tasks")
        else result.get("integration_next_action", "handle_failure"),
        "repair_iteration": result.get("repair_iteration", state.get("repair_iteration", 0)),
        "max_repair_iterations": result.get(
            "max_repair_iterations", state.get("max_repair_iterations", 3)
        ),
        "integration_next_action": result.get(
            "integration_next_action",
            "launch_project" if result.get("quality_gate_passed", False) else "handle_failure",
        ),
        # 集成测试的公开更新也要显式覆盖 checkpoint 中的旧预览字段。
        "preview_url": "",
        "launch_result": {},
        "acceptance_request": {},
        "acceptance_decision": "",
        "accepted": False,
        "code_changes": merge_code_change_sets(
            [
                *([input_code_changes] if input_code_changes else []),
                *input_code_change_sets,
                *previous_small_task_changes,
                *new_code_change_sets,
            ]
        ) or result.get("code_changes", {}),
        "code_change_sets": new_code_change_sets,
        "timeline": ["integration_test"],
    }
