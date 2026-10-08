from __future__ import annotations

import hashlib
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.agents.screenshot_requirements.models import RequirementInput
from app.config import Settings
from app.services.product_plan import require_current_product_plan
from app.services.ui_design_generator import (
    _auto_fix_component_import_sources,
    _auto_fix_missing_imports,
    _auto_fix_preview_only_review_buttons,
    _extract_tsx_code,
    derive_page_key,
    load_page_code,
    persist_page_code,
    validate_page_code,
    validate_tsx,
)
from app.services.ui_design_manifest import (
    UI_MANIFEST_SCHEMA_VERSION,
    build_ui_page_manifest,
    inspect_ui_code_bindings,
)
from app.services.ui_design_project_setup import setup_ui_design_project
from app.workspace.spec_documents import (
    load_ui_designs_json,
    ui_designs_json_path,
    workspace_root,
    write_ui_designs_json,
)

from .images import (
    PreparedUiScreenshot,
    load_ui_reference_images,
    page_images,
)
from .contract_scaffold import (
    repair_contract_attributes,
    repair_equivalent_action_aliases,
)
from .dynamic_bindings import (
    repair_literal_collection_bindings,
    repair_mapped_card_bindings,
    repair_mapped_interface_effects,
)
from .finite_bindings import repair_finite_collection_bindings
from .semantic_bindings import repair_equivalent_visible_information
from .models import (
    ScreenshotPageMapping,
    ScreenshotSingleImagePageDecision,
    ScreenshotUiAnalysis,
    ScreenshotVisualAudit,
    ScreenshotVisualObservation,
)
from .prompts import (
    AUDIT_SYSTEM_PROMPT,
    CODE_SYSTEM_PROMPT,
    CONTRACT_PATCH_SYSTEM_PROMPT,
    MAPPING_SYSTEM_PROMPT,
    build_observation_prompt,
    build_single_image_mapping_prompt,
    build_static_contract_repair_prompt,
    build_targeted_contract_repair_prompt,
    build_visual_audit_prompt,
)
from .transport import invoke_vision_json, invoke_vision_text
from .render_prompt import (
    build_screenshot_render_prompt,
    build_screenshot_visual_repair_prompt,
)
from .runtime_styles import validate_screenshot_runtime_styles
from .shell_reuse import (
    extract_sidebar_controls_for_shared_shell,
    refreshed_shared_shell_code,
)
from .shared_shell import (
    SharedShell,
    compose_shared_shell,
    derive_shared_shell,
    remove_generated_sidebar,
    split_page_contract,
    validate_content_without_shell,
)
from .visibility_contract import validate_visible_bindings
from .targeted_repair import TsxRepairPatch, apply_tsx_repair_patch


REFERENCE_FILE_NAME = "screenshot-ui-reference.json"
PAGE_MAP_FILE_NAME = "screenshot-page-map.json"
APP_SHELL_FILE_NAME = "screenshot-app-shell-reference.json"
VERIFICATION_FILE_NAME = "screenshot-ui-verification.json"
REFERENCE_VIRTUAL_PATH = f"/.xcodeagent/specs/{REFERENCE_FILE_NAME}"
APP_SHELL_VIRTUAL_PATH = f"/.xcodeagent/specs/{APP_SHELL_FILE_NAME}"
SCREENSHOT_UI_PIPELINE_VERSION = "screenshot-render-v3"

ProgressCallback = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class NormalizedReference:
    """保存经过白名单校验的截图观察和逐页映射。"""

    observations: tuple[ScreenshotVisualObservation, ...]
    mappings: dict[str, ScreenshotPageMapping]
    global_style_summary: str
    unresolved_questions: tuple[str, ...]


def _page_id(page: dict[str, Any]) -> str:
    """读取 ProductPlan 页面稳定标识。"""

    return str(page.get("pageId") or page.get("id") or "").strip()


def _product_plan_hash(product_plan: dict[str, Any]) -> str:
    """按原 UI 确认节点的算法计算 ProductPlan 依赖摘要。"""

    return hashlib.sha256(
        json.dumps(product_plan, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _write_json_atomically(path: Path, value: dict[str, Any]) -> None:
    """通过同目录临时文件原子写入截图 UI 辅助产物。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _read_json_object(path: Path) -> dict[str, Any]:
    """读取 JSON 对象，缺失、损坏或类型不符时返回空对象。"""

    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def should_prepare_screenshot_ui(state: dict[str, Any]) -> bool:
    """判断当前创建规划是否来自截图需求输入。"""

    value = state.get("requirement_input")
    return isinstance(value, dict) and value.get("mode") == "screenshot"


def _source_manifest(screenshots: list[PreparedUiScreenshot]) -> list[dict[str, Any]]:
    """生成不包含图片字节的安全截图来源清单。"""

    return [
        {
            "sha256": item.reference.sha256,
            "name": item.reference.name,
            "relativePath": item.reference.relative_path,
            "mimeType": item.reference.mime_type,
            "size": item.reference.size,
            "width": item.overview.width,
            "height": item.overview.height,
            "preparedViews": [
                {
                    "label": view.label,
                    "width": view.width,
                    "height": view.height,
                    "mimeType": view.mime_type,
                    "operations": list(view.operations),
                }
                for view in (item.overview, *item.details)
            ],
        }
        for item in screenshots
    ]


def _analyze_references(
    pages: list[dict[str, Any]],
    screenshots: list[PreparedUiScreenshot],
    settings: Settings,
    existing: NormalizedReference | None = None,
) -> ScreenshotUiAnalysis:
    """逐图识别主页面并复用已成功来源，避免模型重复生成截图摘要。"""

    valid_page_ids = {_page_id(page) for page in pages if _page_id(page)}
    matches: dict[str, list[tuple[str, float, str]]] = {}
    mapped_sources: set[str] = set()
    style_summaries: list[str] = []
    unresolved: list[str] = []
    current_sources = {item.reference.sha256 for item in screenshots}
    if existing is not None:
        for page_id, mapping in existing.mappings.items():
            if page_id not in valid_page_ids:
                continue
            for source in mapping.screenshot_sha256s:
                if source in current_sources:
                    matches.setdefault(page_id, []).append(
                        (source, mapping.confidence, mapping.rationale)
                    )
                    mapped_sources.add(source)
        if existing.global_style_summary.strip():
            style_summaries.append(existing.global_style_summary.strip())
    for screenshot in screenshots:
        source = screenshot.reference.sha256
        if source in mapped_sources:
            continue
        image = screenshot.mapping_overview
        try:
            result = invoke_vision_json(
                ScreenshotSingleImagePageDecision,
                settings=settings,
                system_prompt=MAPPING_SYSTEM_PROMPT,
                user_prompt=build_single_image_mapping_prompt(
                    pages,
                    {
                        "sha256": source,
                        "name": screenshot.reference.name,
                        "width": image.width,
                        "height": image.height,
                    },
                ),
                images=[image],
                max_tokens=min(settings.screenshot_max_output_tokens, 2500),
                response_name="xcodeagent_screenshot_single_image_page",
            )
            if result.page_id not in valid_page_ids:
                unresolved.append(
                    f"截图 {screenshot.reference.name} 未得到有效的主内容页面映射。"
                )
                unresolved.extend(result.unresolved_questions)
                continue
            matches.setdefault(result.page_id, []).append(
                (source, result.confidence, result.rationale)
            )
            mapped_sources.add(source)
            if result.global_style_summary.strip():
                style_summaries.append(result.global_style_summary.strip())
            unresolved.extend(result.unresolved_questions)
        except Exception as exc:  # noqa: BLE001 - 单张图映射失败不应抹掉其他图的映射
            unresolved.append(
                f"截图 {screenshot.reference.name} 的页面映射失败：{str(exc)[:160]}"
            )
    page_mappings = [
        ScreenshotPageMapping(
            page_id=_page_id(page),
            screenshot_sha256s=[source for source, _, _ in matches[_page_id(page)]],
            primary_screenshot_sha256=max(
                matches[_page_id(page)], key=lambda item: item[1]
            )[0],
            confidence=max(item[1] for item in matches[_page_id(page)]),
            rationale="；".join(
                rationale for _, _, rationale in matches[_page_id(page)]
            ),
        )
        for page in pages
        if _page_id(page) in matches
    ]
    observations: list[ScreenshotVisualObservation] = [
        item
        for item in (existing.observations if existing is not None else ())
        if item.screenshot_sha256 in mapped_sources
    ]
    observed_sources = {item.screenshot_sha256 for item in observations}
    for screenshot in screenshots:
        source = screenshot.reference.sha256
        if source not in mapped_sources or source in observed_sources:
            continue
        image = screenshot.mapping_overview
        matched_pages = [
            {
                "pageId": _page_id(page),
                "name": page.get("name"),
                "goal": page.get("goal"),
            }
            for page in pages
            if any(
                item.page_id == _page_id(page)
                and source in item.screenshot_sha256s
                for item in page_mappings
            )
        ]
        try:
            observation = invoke_vision_json(
                ScreenshotVisualObservation,
                settings=settings,
                system_prompt=MAPPING_SYSTEM_PROMPT,
                user_prompt=build_observation_prompt(
                    {
                        "screenshot_sha256": source,
                        "source_viewport_width": image.width,
                        "source_viewport_height": image.height,
                    },
                    matched_pages,
                ),
                images=[image],
                max_tokens=min(settings.screenshot_max_output_tokens, 7000),
                response_name="xcodeagent_screenshot_visual_observation",
            )
            if (
                observation.screenshot_sha256 != source
                or observation.source_viewport_width != image.width
                or observation.source_viewport_height != image.height
            ):
                raise ValueError("视觉观察的截图摘要或坐标系与原图不一致")
            observations.append(observation)
        except Exception as exc:  # noqa: BLE001 - 单图观察失败不应让其他页面一起失败
            unresolved.append(
                f"截图 {screenshot.reference.name} 的视觉细节未能提取，"
                f"页面仍可依据原图生成并在确认页审查：{str(exc)[:160]}"
            )
    return ScreenshotUiAnalysis(
        global_style_summary="；".join(dict.fromkeys(style_summaries))[:1200],
        observations=observations,
        page_mappings=page_mappings,
        unresolved_questions=unresolved[:12],
    )


def _normalize_reference(
    analysis: ScreenshotUiAnalysis,
    pages: list[dict[str, Any]],
    screenshots: list[PreparedUiScreenshot],
) -> NormalizedReference:
    """拒绝模型虚构的页面和截图标识，并按 ProductPlan 顺序归一化映射。"""

    page_ids = [_page_id(page) for page in pages if _page_id(page)]
    valid_page_ids = set(page_ids)
    source_order = list(
        dict.fromkeys(item.reference.sha256 for item in screenshots)
    )
    valid_sources = set(source_order)

    observations: list[ScreenshotVisualObservation] = []
    observed_sources: set[str] = set()
    for observation in analysis.observations:
        source = observation.screenshot_sha256
        if source not in valid_sources or source in observed_sources:
            continue
        observations.append(observation)
        observed_sources.add(source)

    raw_by_page: dict[str, ScreenshotPageMapping] = {}
    for mapping in analysis.page_mappings:
        if mapping.page_id in valid_page_ids and mapping.page_id not in raw_by_page:
            raw_by_page[mapping.page_id] = mapping

    normalized: dict[str, ScreenshotPageMapping] = {}
    for page_id in page_ids:
        raw = raw_by_page.get(page_id)
        if raw is None:
            # 只有“单页 + 单截图”时映射是确定的；多页或多图缺口必须显式留待确认。
            if len(page_ids) == 1 and len(source_order) == 1:
                normalized[page_id] = ScreenshotPageMapping(
                    page_id=page_id,
                    screenshot_sha256s=[source_order[0]],
                    primary_screenshot_sha256=source_order[0],
                    confidence=0.5,
                    rationale="单页单截图的确定性回退映射。",
                )
            continue
        selected = [source for source in source_order if source in raw.screenshot_sha256s]
        if not selected:
            continue
        primary = (
            raw.primary_screenshot_sha256
            if raw.primary_screenshot_sha256 in selected
            else selected[0]
        )
        # 单页图片配额不足时也必须优先保留模型明确选出的主参考图。
        selected = [primary, *(source for source in selected if source != primary)]
        normalized[page_id] = ScreenshotPageMapping(
            page_id=page_id,
            screenshot_sha256s=selected,
            primary_screenshot_sha256=primary,
            confidence=raw.confidence,
            rationale=raw.rationale,
        )
    return NormalizedReference(
        observations=tuple(observations),
        mappings=normalized,
        global_style_summary=analysis.global_style_summary,
        unresolved_questions=tuple(analysis.unresolved_questions),
    )


def _empty_reference(error: str) -> NormalizedReference:
    """在映射调用失败时构造不猜测页面关系的空引用结果。"""

    return NormalizedReference(
        observations=(),
        mappings={},
        global_style_summary="",
        unresolved_questions=(f"截图与页面的自动映射失败：{error[:300]}",),
    )


def _audit_code(
    *,
    page: dict[str, Any],
    mapping: ScreenshotPageMapping,
    reference: NormalizedReference,
    images: list,
    code: str,
    settings: Settings,
) -> ScreenshotVisualAudit:
    """用参考截图对生成 TSX 执行多模态语义视觉审查。"""

    return invoke_vision_json(
        ScreenshotVisualAudit,
        settings=settings,
        system_prompt=AUDIT_SYSTEM_PROMPT,
        user_prompt=build_visual_audit_prompt(
            page,
            mapping,
            list(reference.observations),
            reference.global_style_summary,
            code,
        ),
        images=images,
        max_tokens=min(settings.screenshot_max_output_tokens, 5000),
        response_name="xcodeagent_screenshot_ui_audit",
    )


def _audit_failure_reasons(
    audit: ScreenshotVisualAudit,
    minimum_similarity: int,
) -> list[str]:
    """按整体相似度与核心美观维度执行宽松视觉门禁。

    组件实现和像素差异不单独阻断；布局、颜色、字体只在明显偏离整体目标时
    触发一次高影响修复。模型列出的阻断项仅在数值门禁也失败时作为修复证据，
    避免把细小尺寸建议误当成必须重生成的错误。
    """

    target = max(0, min(100, minimum_similarity))
    dimension_floors = {
        "布局": (audit.layout_similarity, max(0, target - 10)),
        "颜色": (audit.color_similarity, max(0, target - 8)),
        "字体": (audit.typography_similarity, max(0, target - 8)),
    }
    failures: list[str] = []
    if audit.overall_similarity < target:
        failures.append(f"整体相似度 {audit.overall_similarity} 低于目标 {target}")
    failures.extend(
        f"{label}相似度 {score} 明显低于底线 {floor}"
        for label, (score, floor) in dimension_floors.items()
        if score < floor
    )
    if failures:
        failures.extend(audit.blocking_mismatches)
    return failures


def _validate_with_static_fixes(
    project_dir: str,
    code: str,
    page: dict[str, Any],
) -> tuple[str, bool, str]:
    """执行校验，并确定性修复契约属性、漏 import 与评审按钮标记。"""

    code, _ = repair_contract_attributes(page, code)
    code, _ = repair_equivalent_action_aliases(page, code)
    code, _ = repair_finite_collection_bindings(page, code)
    code, _ = repair_literal_collection_bindings(page, code)
    code, _ = repair_mapped_card_bindings(page, code)
    code, _ = repair_mapped_interface_effects(page, code)
    code, _ = repair_equivalent_visible_information(page, code)
    code, _ = _auto_fix_component_import_sources(code)
    ok, validation_error = validate_page_code(project_dir, code, page)
    if not ok and "未 import 或未定义" in validation_error:
        fixed_code, _ = _auto_fix_missing_imports(code)
        if fixed_code != code:
            code = fixed_code
            ok, validation_error = validate_page_code(project_dir, code, page)
    if (
        not ok
        and "没有绑定 ProductPlan actionId" in validation_error
        and "Button" in validation_error
    ):
        fixed_code, fixed_count = _auto_fix_preview_only_review_buttons(code)
        if fixed_count:
            code = fixed_code
            ok, validation_error = validate_page_code(project_dir, code, page)
    # 产品绑定、运行时样式和可见性错误一次性反馈，避免修复一个缺口后才
    # 发现下一个隐藏控件，导致复杂页面多轮模型调用。
    extra_errors = []
    runtime_style_error = validate_screenshot_runtime_styles(code)
    if runtime_style_error:
        extra_errors.append(runtime_style_error)
    extra_errors.extend(validate_visible_bindings(code))
    if extra_errors:
        return code, False, "；".join(
            ([validation_error] if not ok and validation_error else []) + extra_errors
        )
    return code, ok, validation_error


def _validate_screenshot_content(
    project_dir: str,
    code: str,
    content_page: dict[str, Any],
    pages: list[dict[str, Any]],
    shell: SharedShell,
) -> tuple[str, bool, str]:
    """先校验页面内容，再拒绝与任务级侧边栏重复的全局导航。"""

    if shell.enabled:
        # 模型偶尔把截图里的全局侧栏连同内容一起输出；只有侧栏能明确
        # 单独分离时才去掉旧壳，随后仍按完整 ProductPlan 校验共享壳。
        without_sidebar = remove_generated_sidebar(code, pages)
        if without_sidebar:
            code = without_sidebar
    fixed, ok, error = _validate_with_static_fixes(project_dir, code, content_page)
    if ok and shell.enabled:
        shell_error = validate_content_without_shell(fixed, pages)
        if shell_error:
            return fixed, False, shell_error
    return fixed, ok, error


def _sidebar_contains_unique_actions(
    code: str,
    pages: list[dict[str, Any]],
    shell_actions: list[dict[str, Any]],
) -> bool:
    """判断剥离模型侧栏是否会丢失共享壳尚未承接的真实产品操作。"""

    stripped = remove_generated_sidebar(code, pages)
    if not stripped:
        return False
    before = set(inspect_ui_code_bindings(code)["actions"])
    after = set(inspect_ui_code_bindings(stripped)["actions"])
    shared = {str(action.get("actionId") or "") for action in shell_actions}
    return bool((before - after) - shared)


def _usable_content_candidate(project_dir: str, code: str) -> bool:
    """只复用语法完整的内容候选，允许后续补齐缺失的产品绑定。"""

    if len(code.strip()) < 300 or "export default" not in code:
        return False
    syntax_ok, _ = validate_tsx(project_dir, code)
    return syntax_ok


def _emit_generation_progress(
    callback: ProgressCallback | None,
    message: str,
    **detail: Any,
) -> None:
    """向可选进度回调发送单页阶段，回调异常不得中断正式产物生成。"""

    if callback is None:
        return
    try:
        callback(message, detail)
    except Exception:
        return


def _failed_candidate_path(project_dir: str, page_key: str) -> Path:
    """返回失败候选代码的受控保存位置。"""

    return Path(project_dir).expanduser().resolve() / "failed" / page_key / "candidate.tsx"


def _persist_failed_candidate(
    project_dir: str,
    page_key: str,
    code: str,
) -> str:
    """原子保存仍可修复的模型候选代码，避免下次从零生成复杂页面。"""

    if not code.strip():
        return ""
    target = _failed_candidate_path(project_dir, page_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tsx.tmp")
    temporary.write_text(code, encoding="utf-8")
    temporary.replace(target)
    return str(target)


def _load_failed_candidate(
    entry: dict[str, Any],
    project_dir: str,
    page_key: str,
) -> str:
    """从受控 UI 目录读取上一次失败候选，拒绝 Manifest 注入的越界路径。"""

    ui_root = Path(project_dir).expanduser().resolve()
    raw_path = str(entry.get("candidate_code_path") or "").strip()
    candidate = Path(raw_path).expanduser() if raw_path else _failed_candidate_path(project_dir, page_key)
    try:
        resolved = candidate.resolve()
        resolved.relative_to(ui_root)
        if resolved.is_file() and resolved.stat().st_size <= 2_000_000:
            return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return ""
    return ""


def _generate_page_entry(
    *,
    page: dict[str, Any],
    pages: list[dict[str, Any]],
    shell: SharedShell,
    page_key: str,
    project_dir: str,
    screenshots: list[PreparedUiScreenshot],
    reference: NormalizedReference,
    settings: Settings,
    initial_code: str = "",
    run_visual_audit: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """生成、静态校验并视觉审查一个页面，返回 Manifest 条目和审查记录。"""

    page_id = _page_id(page)
    content_page, shell_actions, shell_items = split_page_contract(page, pages, shell)
    mapping = reference.mappings.get(page_id)
    common_metadata = {
        "visual_source": "screenshot",
        "visual_reference_path": REFERENCE_VIRTUAL_PATH,
        "app_shell_reference_path": APP_SHELL_VIRTUAL_PATH,
        "screenshot_ui_pipeline_version": SCREENSHOT_UI_PIPELINE_VERSION,
    }
    if mapping is None:
        error = "没有得到可信的截图到当前 pageId 映射，请在 UI 确认页人工处理。"
        entry = build_ui_page_manifest(
            page,
            page_key=page_key,
            status="generation_failed",
            error=error,
        )
        entry.update(common_metadata)
        return entry, {
            "pageId": page_id,
            "status": "mapping_unresolved",
            "error": error,
        }

    images = page_images(
        screenshots,
        mapping.screenshot_sha256s,
        limit=max(1, settings.screenshot_ui_max_images_per_page),
    )
    if not images:
        error = "页面映射引用的截图不存在或未通过输入校验。"
        entry = build_ui_page_manifest(
            page,
            page_key=page_key,
            status="generation_failed",
            error=error,
        )
        entry.update(common_metadata)
        return entry, {"pageId": page_id, "status": "failed", "error": error}

    max_retries = max(0, settings.screenshot_ui_max_retries)
    min_similarity = max(0, min(100, settings.screenshot_ui_min_similarity))
    code = ""
    last_error = ""
    audit: ScreenshotVisualAudit | None = None
    audit_error = ""
    audit_failures: list[str] = []
    visual_repair_applied = False
    contract_fallback_applied = False
    failed_candidate_path = ""
    audit_deferred = not run_visual_audit
    try:
        cached_code = initial_code.strip()
        if cached_code and not _usable_content_candidate(project_dir, cached_code):
            # 产品绑定缺失可增量修复，语法截断稿则绝不能作为下一次的基底。
            cached_code = ""
        if cached_code:
            code = cached_code
            _emit_generation_progress(
                progress_callback,
                f"{page.get('name') or page_id}：正在修复上次候选设计稿",
                pageId=page_id,
                stage="repairing_cached_candidate",
            )
        else:
            _emit_generation_progress(
                progress_callback,
                f"{page.get('name') or page_id}：正在生成视觉设计",
                pageId=page_id,
                stage="generating",
            )
            try:
                raw = invoke_vision_text(
                    settings=settings,
                    system_prompt=CODE_SYSTEM_PROMPT,
                    user_prompt=build_screenshot_render_prompt(
                        content_page,
                        page_key,
                        mapping,
                        list(reference.observations),
                        reference.global_style_summary,
                    ),
                    # 映射阶段已经逐图冻结视觉观察；代码阶段不再重复编码图片，
                    # 把输出预算留给完整的 TSX 与所有 ProductPlan 绑定。
                    images=[],
                    max_tokens=max(
                        settings.ui_design_max_tokens,
                        settings.screenshot_max_output_tokens,
                    ),
                )
                code = _extract_tsx_code(raw)
            except Exception as exc:  # noqa: BLE001 - 禁止把通用契约骨架冒充视觉稿
                raise RuntimeError(f"视觉稿生成失败：{str(exc)[:500]}") from exc
        if shell.enabled and _sidebar_contains_unique_actions(code, pages, shell_actions):
            # 旧侧栏中的独有操作迁入共同外壳后统一绑定；无法安全提取时明确失败，
            # 不再静默关闭该页共享壳并交付一套外观不同的侧栏。
            extracted = extract_sidebar_controls_for_shared_shell(
                code,
                page=page,
                pages=pages,
                content_page=content_page,
                shell_actions=shell_actions,
                shell_items=shell_items,
            )
            if extracted is None:
                raise ValueError(
                    f"{page.get('name') or page_id} 的侧栏含无法安全迁移的产品控件；"
                    "为保持跨页外壳一致，本页未生成不一致的设计稿。"
                )
            code, content_page, shell_actions, shell_items = extracted
        for attempt in range(max_retries + 1):
            code, content_ok, validation_error = _validate_screenshot_content(
                project_dir, code, content_page, pages, shell
            )
            composed_code = ""
            if content_ok:
                try:
                    composed_code = compose_shared_shell(
                        code,
                        page=page,
                        pages=pages,
                        shell=shell,
                        shell_actions=shell_actions,
                        shell_items=shell_items,
                    )
                    composed_code, ok, validation_error = _validate_with_static_fixes(
                        project_dir, composed_code, page
                    )
                except ValueError as exc:
                    ok, validation_error = False, str(exc)
            else:
                ok = False
            repair_instruction = ""
            repair_prompt = ""
            repair_images = []
            if not ok:
                last_error = f"TSX/ProductPlan 校验失败：{validation_error}"
                repair_instruction = last_error
                # 产品契约/语法修复不需要再次上传截图，使用纯文本请求显著降低
                # 多模态 token、网络传输和视觉编码耗时。
                repair_prompt = build_static_contract_repair_prompt(
                    content_page,
                    page_key,
                    code,
                    validation_error,
                )
                repair_images = []
            elif audit_deferred:
                audit_failures = ["视觉风格审查已转入后台，不阻塞设计稿预览与人工确认。"]
                break
            else:
                _emit_generation_progress(
                    progress_callback,
                    f"{page.get('name') or page_id}：正在审查布局、字体与配色",
                    pageId=page_id,
                    stage="auditing",
                )
                try:
                    audit = _audit_code(
                        page=page,
                        mapping=mapping,
                        reference=reference,
                        images=images,
                        code=composed_code,
                        settings=settings,
                    )
                    audit_error = ""
                except Exception as exc:  # noqa: BLE001 - 审查不可用时保留人工确认入口
                    audit = None
                    audit_error = str(exc)[:500]
                if audit is None:
                    audit_failures = [
                        "视觉审查暂不可用，代码已通过静态校验，请在 UI 确认页人工检查："
                        + audit_error
                    ]
                    break
                audit_failures = _audit_failure_reasons(audit, min_similarity)
                if not audit_failures:
                    break
                last_error = (
                    "截图 UI 的整体风格或核心美观维度偏离参考："
                    + "；".join(audit_failures)
                )
                repair_instruction = (
                    last_error + "\n" + audit.repair_instruction
                ).strip()
                repair_prompt = build_screenshot_visual_repair_prompt(
                    content_page,
                    page_key,
                    mapping,
                    list(reference.observations),
                    reference.global_style_summary,
                    code,
                    repair_instruction,
                )
            if attempt >= max_retries:
                if ok and audit is not None:
                    # 模型基于 TSX 的风格审查不是浏览器像素证据。达到目标记 passed；
                    # 未达到时保留 review_required 证据并交给原 UI 人工确认。
                    break
                failed_candidate_path = _persist_failed_candidate(
                    project_dir, page_key, code
                )
                raise ValueError(last_error or "截图 UI 未通过产品契约或视觉审查")
            if not ok and _usable_content_candidate(project_dir, code):
                # 语法完整的视觉稿优先局部补齐契约。模型只给出精确替换片段，
                # 服务端逐轮复核完整契约；首轮补齐字段后才显露的隐藏控件等错误，
                # 允许再做一轮局部修复，不因一次只报告部分错误而退回整页重写。
                patch_code = code
                patch_error = validation_error
                patch_succeeded = False
                for patch_round in range(2):
                    try:
                        _emit_generation_progress(
                            progress_callback,
                            f"{page.get('name') or page_id}：正在局部修复产品绑定（{patch_round + 1}/2）",
                            pageId=page_id,
                            stage="repairing_contract",
                        )
                        patch = invoke_vision_json(
                            TsxRepairPatch,
                            settings=settings,
                            system_prompt=CONTRACT_PATCH_SYSTEM_PROMPT,
                            user_prompt=build_targeted_contract_repair_prompt(
                                content_page, page_key, patch_code, patch_error
                            ),
                            images=[],
                            max_tokens=min(
                                max(settings.ui_design_max_tokens, settings.screenshot_max_output_tokens),
                                8_000,
                            ),
                            response_name="screenshot_ui_contract_patch",
                        )
                        patched = apply_tsx_repair_patch(patch_code, patch)
                        if not _usable_content_candidate(project_dir, patched):
                            break
                        patched, patched_ok, patched_error = _validate_screenshot_content(
                            project_dir, patched, content_page, pages, shell
                        )
                        if patched_ok:
                            patched_full = compose_shared_shell(
                                patched,
                                page=page,
                                pages=pages,
                                shell=shell,
                                shell_actions=shell_actions,
                                shell_items=shell_items,
                            )
                            _, patched_ok, patched_error = _validate_with_static_fixes(
                                project_dir, patched_full, page
                            )
                        if patched_ok:
                            code = patched
                            patch_succeeded = True
                            break
                        if patched_error == patch_error:
                            break
                        patch_code, patch_error = patched, patched_error
                    except Exception:  # noqa: BLE001 - 局部方案失败时仍可调用完整修复
                        break
                if patch_succeeded:
                    continue
                if patch_code != code:
                    # 局部补齐的有效进展可作为整页修复基底，并把最新错误给模型。
                    code = patch_code
                    repair_prompt = build_static_contract_repair_prompt(
                        content_page, page_key, code, patch_error
                    )
            before_visual_repair = code if ok and audit is not None else ""
            try:
                repair_raw = invoke_vision_text(
                    settings=settings,
                    system_prompt=CODE_SYSTEM_PROMPT,
                    user_prompt=repair_prompt,
                    images=repair_images,
                    max_tokens=max(
                        settings.ui_design_max_tokens,
                        settings.screenshot_max_output_tokens,
                    ),
                )
                repaired_code = _extract_tsx_code(repair_raw)
                if not _usable_content_candidate(project_dir, repaired_code):
                    raise ValueError("模型修复返回空白或截断的 TSX，保留修复前完整候选")
                code = repaired_code
            except Exception as exc:  # noqa: BLE001 - 修复失败必须显式失败，不能输出通用稿
                if before_visual_repair:
                    code = before_visual_repair
                    audit_failures.append("视觉修复服务暂不可用，请在确认页人工检查布局与字体。")
                    break
                # 静态修复可能已把候选稿补齐；修复模型返回截断时先复核现有
                # 完整稿，只有原 ProductPlan 全契约仍不通过才真正判定失败。
                recovered_code, recovered_ok, _ = _validate_screenshot_content(
                    project_dir, code, content_page, pages, shell
                )
                if recovered_ok:
                    try:
                        recovered_full = compose_shared_shell(
                            recovered_code,
                            page=page,
                            pages=pages,
                            shell=shell,
                            shell_actions=shell_actions,
                            shell_items=shell_items,
                        )
                        _, recovered_ok, _ = _validate_with_static_fixes(
                            project_dir, recovered_full, page
                        )
                    except ValueError:
                        recovered_ok = False
                if recovered_ok:
                    code = recovered_code
                    audit = None
                    audit_deferred = False
                    audit_failures.append(
                        "模型修复输出不完整；原候选已重新通过完整产品契约，请在确认页预览。"
                    )
                    break
                failed_candidate_path = _persist_failed_candidate(
                    project_dir, page_key, code
                )
                raise RuntimeError(f"设计稿修复调用失败：{str(exc)[:500]}") from exc
            if ok and audit is not None:
                # 视觉修复后先做确定性的代码/产品契约校验；通过即交给人工预览，
                # 不再追加一次主观视觉模型复审。审查记录明确标注分数来自修复前。
                code, repaired_ok, repaired_error = _validate_screenshot_content(
                    project_dir, code, content_page, pages, shell
                )
                if repaired_ok:
                    try:
                        repaired_composed = compose_shared_shell(
                            code,
                            page=page,
                            pages=pages,
                            shell=shell,
                            shell_actions=shell_actions,
                            shell_items=shell_items,
                        )
                        _, repaired_ok, repaired_error = _validate_with_static_fixes(
                            project_dir, repaired_composed, page
                        )
                    except ValueError as exc:
                        repaired_ok, repaired_error = False, str(exc)
                if not repaired_ok:
                    failed_candidate_path = _persist_failed_candidate(
                        project_dir, page_key, code
                    )
                    code = before_visual_repair
                    audit_failures.append(
                        "视觉修复破坏了 ProductPlan 契约，已保留修复前的完整候选："
                        + repaired_error
                    )
                    break
                visual_repair_applied = True
                break

        code, ok, validation_error = _validate_screenshot_content(
            project_dir, code, content_page, pages, shell
        )
        if ok:
            try:
                code = compose_shared_shell(
                    code,
                    page=page,
                    pages=pages,
                    shell=shell,
                    shell_actions=shell_actions,
                    shell_items=shell_items,
                )
                code, ok, validation_error = _validate_with_static_fixes(
                    project_dir, code, page
                )
            except ValueError as exc:
                ok, validation_error = False, str(exc)
        if not ok:
            raise ValueError(f"最终 TSX/ProductPlan 校验失败：{validation_error}")
        code_path = persist_page_code(project_dir, page_key, code)
        entry = build_ui_page_manifest(
            page,
            page_key=page_key,
            code_path=code_path,
            code=code,
            status="confirmed",
        )
        audit_payload = (
            {
                "status": "passed" if not audit_failures else "review_required",
                "method": "multimodal_style_composition_audit",
                "minimumSimilarity": min_similarity,
                "issues": list(audit_failures),
                "repairApplied": visual_repair_applied,
                "contractFallbackApplied": contract_fallback_applied,
                "scoreRepresents": (
                    "before_last_repair" if visual_repair_applied else "current_code"
                ),
                **audit.model_dump(),
            }
            if audit is not None
            else {
                "status": "pending" if audit_deferred else "review_required",
                "method": (
                    "deferred_multimodal_style_composition_audit"
                    if audit_deferred
                    else "multimodal_style_composition_audit"
                ),
                "minimumSimilarity": min_similarity,
                "issues": list(audit_failures),
                "repairApplied": False,
                "contractFallbackApplied": contract_fallback_applied,
                "scoreRepresents": "unavailable",
            }
        )
        entry.update(
            {
                **common_metadata,
                "source_screenshot_sha256s": list(mapping.screenshot_sha256s),
                "visual_verification": audit_payload,
                **(
                    {"candidate_code_path": failed_candidate_path}
                    if failed_candidate_path
                    else {}
                ),
            }
        )
        return entry, {
            "pageId": page_id,
            "status": (
                "passed" if audit_payload.get("status") == "passed" else "review_required"
            ),
            "pageKey": page_key,
            "sourceScreenshotSha256s": list(mapping.screenshot_sha256s),
            "staticVerification": entry.get("verification", {}),
            "visualVerification": audit_payload,
            "contractFallbackApplied": contract_fallback_applied,
            **(
                {"candidateCodePath": failed_candidate_path}
                if failed_candidate_path
                else {}
            ),
        }
    except Exception as exc:  # noqa: BLE001 - 单页失败必须保留其他页面结果
        error = str(exc)[:500] or type(exc).__name__
        failed_candidate_path = _persist_failed_candidate(
            project_dir, page_key, code
        )
        entry = build_ui_page_manifest(
            page,
            page_key=page_key,
            status="generation_failed",
            error=error,
        )
        entry.update(
            {
                **common_metadata,
                "source_screenshot_sha256s": list(mapping.screenshot_sha256s),
                **(
                    {"candidate_code_path": failed_candidate_path}
                    if failed_candidate_path
                    else {}
                ),
            }
        )
        return entry, {
            "pageId": page_id,
            "status": "failed",
            "pageKey": page_key,
            "error": error,
            **(
                {"candidateCodePath": failed_candidate_path}
                if failed_candidate_path
                else {}
            ),
        }


def _persist_reference_artifacts(
    *,
    specs_dir: Path,
    product_plan_hash: str,
    settings: Settings,
    screenshots: list[PreparedUiScreenshot],
    pages: list[dict[str, Any]],
    reference: NormalizedReference,
    analysis_error: str,
) -> dict[str, Any]:
    """分别持久化页面映射、完整视觉参考和全局应用壳参考。"""

    created_at = datetime.now(UTC).isoformat()
    sources = _source_manifest(screenshots)
    page_rows = []
    for page in pages:
        page_id = _page_id(page)
        mapping = reference.mappings.get(page_id)
        page_rows.append(
            {
                "pageId": page_id,
                "status": "mapped" if mapping is not None else "unresolved",
                "screenshotSha256s": (
                    list(mapping.screenshot_sha256s) if mapping is not None else []
                ),
                "primaryScreenshotSha256": (
                    mapping.primary_screenshot_sha256 if mapping is not None else ""
                ),
                "confidence": mapping.confidence if mapping is not None else 0.0,
                "rationale": (
                    mapping.rationale
                    if mapping is not None
                    else "视觉模型未返回可验证的页面映射。"
                ),
            }
        )
    page_map = {
        "schemaVersion": "screenshot-page-map.v1",
        "createdAt": created_at,
        "productPlanSha256": product_plan_hash,
        "model": settings.screenshot_model_api_name,
        "pages": page_rows,
        "unresolvedQuestions": list(reference.unresolved_questions),
        "analysisError": analysis_error,
    }
    reference_spec = {
        "schemaVersion": "screenshot-ui-reference.v1",
        "createdAt": created_at,
        "productPlanSha256": product_plan_hash,
        "model": settings.screenshot_model_api_name,
        "sourceScreenshots": sources,
        "globalStyleSummary": reference.global_style_summary,
        "observations": [item.model_dump() for item in reference.observations],
        "pages": page_rows,
        "unresolvedQuestions": list(reference.unresolved_questions),
        "analysisError": analysis_error,
    }
    shell_spec = {
        "schemaVersion": "screenshot-app-shell-reference.v1",
        "createdAt": created_at,
        "productPlanSha256": product_plan_hash,
        "globalStyleSummary": reference.global_style_summary,
        "sources": [
            {
                "screenshotSha256": item.screenshot_sha256,
                "appShell": item.app_shell,
                "colorPalette": item.color_palette,
                "typography": item.typography,
                "spacingAndShape": item.spacing_and_shape,
            }
            for item in reference.observations
            if item.app_shell.strip()
        ],
        "implementationBoundary": (
            "页面 TSX 只还原 Outlet 内容区；全局 Header、Sider、菜单和主题由最终前端"
            "实现阶段在模板边界允许的范围内参考本文件处理，禁止在每页重复生成。"
        ),
    }
    _write_json_atomically(specs_dir / PAGE_MAP_FILE_NAME, page_map)
    _write_json_atomically(specs_dir / REFERENCE_FILE_NAME, reference_spec)
    _write_json_atomically(specs_dir / APP_SHELL_FILE_NAME, shell_spec)
    return reference_spec


def _reusable_manifest(
    state: dict[str, Any],
    product_plan_hash: str,
    page_ids: list[str],
) -> dict[str, Any]:
    """读取与当前 ProductPlan 完全匹配的截图 UI Manifest，支持节点幂等重放。"""

    candidate = state.get("ui_designs")
    if not isinstance(candidate, dict):
        candidate = load_ui_designs_json(ui_designs_json_path(state))
    if not isinstance(candidate, dict):
        return {}
    if candidate.get("product_plan_sha256") != product_plan_hash:
        return {}
    pages = candidate.get("pages")
    if not isinstance(pages, list):
        return {}
    actual_ids = [_page_id(item) for item in pages if isinstance(item, dict)]
    if actual_ids != page_ids:
        return {}
    if not pages or any(
        not isinstance(item, dict)
        or item.get("visual_source") != "screenshot"
        or item.get("screenshot_ui_pipeline_version") != SCREENSHOT_UI_PIPELINE_VERSION
        for item in pages
    ):
        return {}
    if any(
        item.get("status") != "confirmed"
        or not str(item.get("code") or "").strip()
        for item in pages
        if isinstance(item, dict)
    ):
        # 失败或未完成的截图设计不是可复用终态；工作流重试必须重新调用视觉模型。
        return {}
    return candidate


def _existing_screenshot_manifest(
    state: dict[str, Any],
    product_plan_hash: str,
    page_ids: list[str],
) -> dict[str, Any]:
    """读取同一 ProductPlan 的现有截图 Manifest，并优先使用磁盘水合源码。"""

    disk = load_ui_designs_json(ui_designs_json_path(state))
    memory = state.get("ui_designs")
    candidates = [disk, memory] if isinstance(memory, dict) else [disk]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("product_plan_sha256") != product_plan_hash:
            continue
        rows = candidate.get("pages")
        if not isinstance(rows, list):
            continue
        actual_ids = [_page_id(item) for item in rows if isinstance(item, dict)]
        if actual_ids == page_ids:
            return candidate
    return {}


def _reusable_page_entry(
    *,
    page: dict[str, Any],
    page_key: str,
    project_dir: str,
    existing: dict[str, Any],
) -> dict[str, Any]:
    """重新校验单页成功产物，只有源码与当前契约都有效时才复用。"""

    if (
        existing.get("visual_source") != "screenshot"
        or existing.get("status") != "confirmed"
        or existing.get("screenshot_ui_pipeline_version")
        != SCREENSHOT_UI_PIPELINE_VERSION
    ):
        return {}
    code = str(existing.get("code") or "") or str(
        load_page_code(project_dir, page_key) or ""
    )
    if not code.strip():
        return {}
    fixed, ok, _ = _validate_with_static_fixes(project_dir, code, page)
    if not ok:
        return {}
    code_path = persist_page_code(project_dir, page_key, fixed)
    refreshed = build_ui_page_manifest(
        page,
        page_key=page_key,
        code_path=code_path,
        code=fixed,
        status="confirmed",
    )
    for key in (
        "visual_source",
        "visual_reference_path",
        "app_shell_reference_path",
        "source_screenshot_sha256s",
        "visual_verification",
        "candidate_code_path",
        "screenshot_ui_pipeline_version",
    ):
        if key in existing:
            refreshed[key] = existing[key]
    return refreshed


def _adopt_current_generated_page(
    *,
    page: dict[str, Any],
    pages: list[dict[str, Any]],
    shell: SharedShell,
    page_key: str,
    project_dir: str,
    existing: dict[str, Any],
    reference: NormalizedReference,
    verification: dict[str, Any],
) -> dict[str, Any]:
    """将当前任务已有的有效页面内容接入统一侧栏，并恢复单页重试的成功产物。"""

    if not shell.enabled:
        return {}
    code = str(load_page_code(project_dir, page_key) or "")
    if not code.strip():
        return {}
    page_id = _page_id(page)
    visual_verification = verification.get("visualVerification")
    if "function ScreenshotSharedPage()" in code:
        # 单页重试先写 TSX 后由调用方写清单；恢复时重新执行完整产品契约校验。
        candidate = code
        if not isinstance(visual_verification, dict):
            visual_verification = {}
    elif (
        existing.get("status") == "confirmed"
        and existing.get("visual_source") == "screenshot"
        and existing.get("screenshot_ui_pipeline_version") == "screenshot-render-v2"
    ):
        content = remove_generated_sidebar(code, pages)
        if not content:
            return {}
        content_page, shell_actions, shell_items = split_page_contract(page, pages, shell)
        content, ok, _ = _validate_screenshot_content(
            project_dir, content, content_page, pages, shell
        )
        if not ok:
            return {}
        try:
            candidate = compose_shared_shell(
                content,
                page=page,
                pages=pages,
                shell=shell,
                shell_actions=shell_actions,
                shell_items=shell_items,
            )
        except ValueError:
            return {}
        # 旧视觉分数描述的是已移除的侧栏，不能冒充新组合页面的审查结果。
        visual_verification = {
            "status": "review_required",
            "method": "shared_shell_content_reuse",
            "issues": ["页面内容已复用并通过产品契约校验；统一侧栏请在 UI 确认页人工预览。"],
            "scoreRepresents": "unavailable",
        }
    else:
        return {}
    candidate, ok, _ = _validate_with_static_fixes(project_dir, candidate, page)
    if not ok:
        return {}
    code_path = persist_page_code(project_dir, page_key, candidate)
    refreshed = build_ui_page_manifest(
        page,
        page_key=page_key,
        code_path=code_path,
        code=candidate,
        status="confirmed",
    )
    mapping = reference.mappings.get(page_id)
    refreshed.update(
        {
            "visual_source": "screenshot",
            "visual_reference_path": REFERENCE_VIRTUAL_PATH,
            "app_shell_reference_path": APP_SHELL_VIRTUAL_PATH,
            "source_screenshot_sha256s": (
                list(mapping.screenshot_sha256s) if mapping else []
            ),
            "visual_verification": visual_verification,
            "screenshot_ui_pipeline_version": SCREENSHOT_UI_PIPELINE_VERSION,
        }
    )
    return refreshed


def _load_cached_reference(
    *,
    specs_dir: Path,
    product_plan_hash: str,
    pages: list[dict[str, Any]],
    screenshots: list[PreparedUiScreenshot],
) -> NormalizedReference | None:
    """校验并恢复已成功的截图映射，重试失败页时不再重复分析全部截图。"""

    payload = _read_json_object(specs_dir / REFERENCE_FILE_NAME)
    if (
        payload.get("schemaVersion") != "screenshot-ui-reference.v1"
        or payload.get("productPlanSha256") != product_plan_hash
        or str(payload.get("analysisError") or "").strip()
    ):
        return None
    source_rows = payload.get("sourceScreenshots")
    source_rows = source_rows if isinstance(source_rows, list) else []
    cached_sources = {
        str(item.get("sha256") or "").strip()
        for item in source_rows
        if isinstance(item, dict)
    }
    current_sources = {item.reference.sha256 for item in screenshots}
    if not current_sources or cached_sources != current_sources:
        return None
    try:
        observations = tuple(
            ScreenshotVisualObservation.model_validate(item)
            for item in payload.get("observations", [])
            if isinstance(item, dict)
        )
        mappings: dict[str, ScreenshotPageMapping] = {}
        for item in payload.get("pages", []):
            if not isinstance(item, dict) or item.get("status") != "mapped":
                continue
            page_id = str(item.get("pageId") or "").strip()
            mappings[page_id] = ScreenshotPageMapping(
                page_id=page_id,
                screenshot_sha256s=list(item.get("screenshotSha256s") or []),
                primary_screenshot_sha256=str(
                    item.get("primaryScreenshotSha256") or ""
                ),
                confidence=float(item.get("confidence") or 0.0),
                rationale=str(item.get("rationale") or ""),
            )
    except (TypeError, ValueError):
        return None
    valid_page_ids = {_page_id(page) for page in pages if _page_id(page)}
    if not mappings or not set(mappings).issubset(valid_page_ids):
        return None
    return NormalizedReference(
        observations=observations,
        mappings=mappings,
        global_style_summary=str(payload.get("globalStyleSummary") or ""),
        unresolved_questions=tuple(
            str(item)
            for item in payload.get("unresolvedQuestions", [])
            if str(item).strip()
        ),
    )


def _schedule_background_visual_audits(
    *,
    state: dict[str, Any],
    workspace: str,
    specs_dir: Path,
    pages: list[dict[str, Any]],
    entries_by_page: dict[str, dict[str, Any]],
    screenshots: list[PreparedUiScreenshot],
    reference: NormalizedReference,
    settings: Settings,
    product_plan_hash: str,
) -> int:
    """异步审查新生成页面并原子回写证据，首屏确认不等待视觉评分。"""

    from .background_audit import submit_background_audit, update_manifest_atomically

    scheduled = 0
    page_by_id = {_page_id(page): page for page in pages if _page_id(page)}
    for page_id, entry in entries_by_page.items():
        verification = entry.get("visual_verification")
        if (
            entry.get("status") != "confirmed"
            or not isinstance(verification, dict)
            or verification.get("status") != "pending"
        ):
            continue
        page = page_by_id.get(page_id)
        mapping = reference.mappings.get(page_id)
        code = str(entry.get("code") or "")
        code_sha256 = str(entry.get("code_sha256") or "")
        if not isinstance(page, dict) or mapping is None or not code.strip():
            continue
        images = page_images(
            screenshots,
            mapping.screenshot_sha256s,
            limit=max(1, settings.screenshot_ui_max_images_per_page),
        )
        if not images:
            continue

        def audit_job(
            *,
            target_page: dict[str, Any] = page,
            target_page_id: str = page_id,
            target_mapping: ScreenshotPageMapping = mapping,
            target_code: str = code,
            target_code_sha256: str = code_sha256,
            target_images: list = images,
            target_contract_fallback: bool = bool(
                verification.get("contractFallbackApplied")
            ),
        ) -> None:
            """执行一个页面的后台视觉审查，并仅更新同一代码版本。"""

            try:
                audit = _audit_code(
                    page=target_page,
                    mapping=target_mapping,
                    reference=reference,
                    images=target_images,
                    code=target_code,
                    settings=settings,
                )
                failures = _audit_failure_reasons(
                    audit,
                    max(0, min(100, settings.screenshot_ui_min_similarity)),
                )
                audit_payload: dict[str, Any] = {
                    "status": "passed" if not failures else "review_required",
                    "method": "background_multimodal_style_composition_audit",
                    "minimumSimilarity": settings.screenshot_ui_min_similarity,
                    "issues": failures,
                    "repairApplied": False,
                    "contractFallbackApplied": target_contract_fallback,
                    "scoreRepresents": "current_code",
                    **audit.model_dump(),
                }
            except Exception as exc:  # noqa: BLE001 - 后台审查失败不撤销可预览设计稿
                audit_payload = {
                    "status": "review_required",
                    "method": "background_multimodal_style_composition_audit",
                    "minimumSimilarity": settings.screenshot_ui_min_similarity,
                    "issues": [f"后台视觉审查暂不可用，请人工检查：{str(exc)[:300]}"],
                    "repairApplied": False,
                    "contractFallbackApplied": target_contract_fallback,
                    "scoreRepresents": "unavailable",
                }

            def persist_audit() -> None:
                """在锁内回写当前代码版本的 Manifest 与视觉审查报告。"""

                manifest = load_ui_designs_json(ui_designs_json_path(state))
                if manifest.get("product_plan_sha256") != product_plan_hash:
                    return
                manifest_pages = manifest.get("pages")
                if not isinstance(manifest_pages, list):
                    return
                updated = False
                for item in manifest_pages:
                    if not isinstance(item, dict) or _page_id(item) != target_page_id:
                        continue
                    if str(item.get("code_sha256") or "") != target_code_sha256:
                        return
                    item["visual_verification"] = audit_payload
                    updated = True
                    break
                if not updated:
                    return
                write_ui_designs_json(state, manifest)
                report = _read_json_object(specs_dir / VERIFICATION_FILE_NAME)
                report_pages = report.get("pages")
                report_pages = report_pages if isinstance(report_pages, list) else []
                found = False
                for item in report_pages:
                    if isinstance(item, dict) and _page_id(item) == target_page_id:
                        item["status"] = (
                            "passed"
                            if audit_payload.get("status") == "passed"
                            else "review_required"
                        )
                        item["visualVerification"] = audit_payload
                        found = True
                        break
                if not found:
                    report_pages.append(
                        {
                            "pageId": target_page_id,
                            "status": audit_payload.get("status"),
                            "visualVerification": audit_payload,
                        }
                    )
                report["pages"] = report_pages
                report["updatedAt"] = datetime.now(UTC).isoformat()
                _write_json_atomically(specs_dir / VERIFICATION_FILE_NAME, report)

            update_manifest_atomically(workspace, persist_audit)

        submit_background_audit(audit_job)
        scheduled += 1
    return scheduled


def prepare_screenshot_ui_designs(
    state: dict[str, Any],
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """增量生成截图 TSX：复用成功页、修复失败候选并同步审查视觉。"""

    if not should_prepare_screenshot_ui(state):
        return {}
    requirement_spec = state.get("requirement_spec")
    if not isinstance(requirement_spec, dict):
        raise ValueError("截图 UI 准备必须读取已确认 RequirementSpec。")
    product_plan = require_current_product_plan(
        state.get("product_plan"),
        requirement_spec,
    )
    if product_plan.get("confirmation_status") != "confirmed":
        raise ValueError("截图 UI 准备必须基于已确认 ProductPlan。")
    pages = [
        page
        for page in product_plan.get("pages", [])
        if isinstance(page, dict) and _page_id(page)
    ]
    product_hash = _product_plan_hash(product_plan)
    page_ids = [_page_id(page) for page in pages]
    workspace = str(workspace_root(state))
    settings = Settings.from_env()
    specs_dir = ui_designs_json_path(state).parent
    setup = setup_ui_design_project(workspace)
    project_dir = str(setup.get("project_dir") or "")
    if setup.get("status") != "ready" or not project_dir:
        raise ValueError(str(setup.get("message") or "截图 UI 设计目录准备失败"))

    used_keys: set[str] = {"DefaultPage"}
    targets = [(page, derive_page_key(page, used_keys)) for page in pages]
    existing_manifest = _existing_screenshot_manifest(
        state,
        product_hash,
        page_ids,
    )
    existing_rows = existing_manifest.get("pages")
    existing_by_page = {
        _page_id(item): item
        for item in (existing_rows if isinstance(existing_rows, list) else [])
        if isinstance(item, dict) and _page_id(item)
    }
    entries_by_page: dict[str, dict[str, Any]] = {}
    for page, page_key in targets:
        page_id = _page_id(page)
        reusable = _reusable_page_entry(
            page=page,
            page_key=page_key,
            project_dir=project_dir,
            existing=existing_by_page.get(page_id, {}),
        )
        if reusable:
            entries_by_page[page_id] = reusable

    reused_count = len(entries_by_page)
    parsed_input = RequirementInput.model_validate(state.get("requirement_input"))
    screenshots = load_ui_reference_images(
        workspace,
        parsed_input,
        overview_max_side=settings.screenshot_ui_page_image_max_side,
    )
    reference = _load_cached_reference(
        specs_dir=specs_dir,
        product_plan_hash=product_hash,
        pages=pages,
        screenshots=screenshots,
    )
    mapped_sources = (
        {
            source
            for mapping in reference.mappings.values()
            for source in mapping.screenshot_sha256s
        }
        if reference is not None
        else set()
    )
    analysis_error = ""
    if reference is None or mapped_sources != {item.reference.sha256 for item in screenshots}:
        _emit_generation_progress(
            progress_callback,
            "正在补齐截图与页面的对应关系",
            stage="mapping",
            total=len(targets),
            reused=reused_count,
        )
        try:
            reference = _normalize_reference(
                _analyze_references(pages, screenshots, settings, existing=reference),
                pages,
                screenshots,
            )
        except Exception as exc:  # noqa: BLE001 - 映射失败必须形成可见产物而非猜测
            analysis_error = str(exc)[:500]
            reference = _empty_reference(analysis_error)
        _persist_reference_artifacts(
            specs_dir=specs_dir,
            product_plan_hash=product_hash,
            settings=settings,
            screenshots=screenshots,
            pages=pages,
            reference=reference,
            analysis_error=analysis_error,
        )
    else:
        _emit_generation_progress(
            progress_callback,
            "已复用截图分析结果，只补充未成功页面",
            stage="mapping_reused",
            total=len(targets),
            reused=reused_count,
        )

    shell = derive_shared_shell(list(reference.observations), pages, screenshots)
    refreshed_shell_pages: set[str] = set()
    for page, page_key in targets:
        page_id = _page_id(page)
        reused = entries_by_page.get(page_id)
        if reused is None:
            continue
        candidate = refreshed_shared_shell_code(
            str(reused.get("code") or ""),
            page=page,
            pages=pages,
            shell=shell,
        )
        if not candidate:
            continue
        candidate, valid, _ = _validate_with_static_fixes(project_dir, candidate, page)
        if not valid:
            # 内容提取或壳重组异常时交给正常单页生成路径，不复用风格不一致的页面。
            del entries_by_page[page_id]
            continue
        code_path = persist_page_code(project_dir, page_key, candidate)
        refreshed = build_ui_page_manifest(
            page,
            page_key=page_key,
            code_path=code_path,
            code=candidate,
            status="confirmed",
        )
        refreshed.update(
            {
                key: value
                for key, value in reused.items()
                if key in (
                    "visual_source",
                    "visual_reference_path",
                    "app_shell_reference_path",
                    "source_screenshot_sha256s",
                    "screenshot_ui_pipeline_version",
                )
            }
        )
        refreshed["visual_verification"] = {
            "status": "review_required",
            "method": "shared_shell_refresh",
            "issues": ["截图观察已更新，共享侧栏已统一；请在确认页预览最终页面。"],
            "scoreRepresents": "unavailable",
        }
        entries_by_page[page_id] = refreshed
        refreshed_shell_pages.add(page_id)

    previous_report = _read_json_object(specs_dir / VERIFICATION_FILE_NAME)
    previous_report_pages = previous_report.get("pages")
    verifications_by_page = {
        _page_id(item): item
        for item in (
            previous_report_pages if isinstance(previous_report_pages, list) else []
        )
        if isinstance(item, dict) and _page_id(item)
    }
    for page_id in refreshed_shell_pages:
        entry = entries_by_page[page_id]
        verifications_by_page[page_id] = {
            "pageId": page_id,
            "status": "review_required",
            "staticVerification": entry.get("verification", {}),
            "visualVerification": entry.get("visual_verification", {}),
        }
    for page, page_key in targets:
        page_id = _page_id(page)
        if page_id in entries_by_page:
            continue
        adopted = _adopt_current_generated_page(
            page=page,
            pages=pages,
            shell=shell,
            page_key=page_key,
            project_dir=project_dir,
            existing=existing_by_page.get(page_id, {}),
            reference=reference,
            verification=verifications_by_page.get(page_id, {}),
        )
        if adopted:
            entries_by_page[page_id] = adopted
            verifications_by_page[page_id] = {
                "pageId": page_id,
                "status": "reused",
                "staticVerification": adopted.get("verification", {}),
                "visualVerification": adopted.get("visual_verification", {}),
            }
    reused_count = len(entries_by_page)
    for page_id, entry in entries_by_page.items():
        verifications_by_page.setdefault(
            page_id,
            {
                "pageId": page_id,
                "status": "reused",
                "staticVerification": entry.get("verification", {}),
                "visualVerification": entry.get("visual_verification", {}),
            },
        )
    pending_targets = [
        (page, page_key)
        for page, page_key in targets
        if _page_id(page) not in entries_by_page
    ]
    _emit_generation_progress(
        progress_callback,
        f"正在生成 {len(pending_targets)} 个页面，已复用 {reused_count} 个页面",
        stage="page_generation",
        ready=reused_count,
        reused=reused_count,
        total=len(targets),
    )
    workers = max(
        1,
        min(settings.ui_design_concurrency, len(pending_targets) or 1),
    )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _generate_page_entry,
                page=page,
                pages=pages,
                shell=shell,
                page_key=page_key,
                project_dir=project_dir,
                screenshots=screenshots,
                reference=reference,
                settings=settings,
                initial_code=_load_failed_candidate(
                    existing_by_page.get(_page_id(page), {}),
                    project_dir,
                    page_key,
                ),
                run_visual_audit=True,
                progress_callback=progress_callback,
            ): (_page_id(page), page, page_key)
            for page, page_key in pending_targets
        }
        for future in as_completed(futures):
            page_id, page, page_key = futures[future]
            try:
                entry, verification = future.result()
            except Exception as exc:  # noqa: BLE001 - 线程边界兜底并保留其他页面
                entry = build_ui_page_manifest(
                    page,
                    page_key=page_key,
                    status="generation_failed",
                    error=str(exc),
                )
                entry.update(
                    {
                        "visual_source": "screenshot",
                        "visual_reference_path": REFERENCE_VIRTUAL_PATH,
                        "app_shell_reference_path": APP_SHELL_VIRTUAL_PATH,
                        "screenshot_ui_pipeline_version": SCREENSHOT_UI_PIPELINE_VERSION,
                    }
                )
                verification = {
                    "pageId": page_id,
                    "status": "failed",
                    "error": str(exc)[:500],
                }
            entries_by_page[page_id] = entry
            verifications_by_page[page_id] = verification
            ready = len(entries_by_page)
            _emit_generation_progress(
                progress_callback,
                f"{page.get('name') or page_id}："
                + ("设计稿已就绪" if entry.get("status") == "confirmed" else "生成失败，可单页重试"),
                stage="page_completed",
                pageId=page_id,
                pageStatus=entry.get("status"),
                ready=ready,
                reused=reused_count,
                total=len(targets),
            )
            # 每页完成立即落盘；应用中断后下一次只需补尚未成功的页面。
            partial_manifest = {
                "schema_version": UI_MANIFEST_SCHEMA_VERSION,
                "confirmation_status": "pending_user_confirmation",
                "product_plan_sha256": product_hash,
                "pages": [
                    entries_by_page.get(_page_id(item))
                    or existing_by_page.get(_page_id(item))
                    or build_ui_page_manifest(
                        item,
                        page_key=next(
                            key for target, key in targets if _page_id(target) == _page_id(item)
                        ),
                    )
                    for item in pages
                ],
            }
            write_ui_designs_json(state, partial_manifest)

    manifest = {
        "schema_version": UI_MANIFEST_SCHEMA_VERSION,
        "confirmation_status": "pending_user_confirmation",
        "product_plan_sha256": product_hash,
        "pages": [entries_by_page[_page_id(page)] for page in pages],
    }
    write_ui_designs_json(state, manifest)
    verification_report = {
        "schemaVersion": "screenshot-ui-verification.v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "productPlanSha256": product_hash,
        "method": "confirmed_product_contract_plus_screenshot_visual_review",
        "note": (
            "已确认 ProductPlan 负责完整功能，截图观察与原图负责布局、配色和字体；"
            "页面通过静态契约校验后同步接受视觉审查，再进入人工确认。"
        ),
        "pages": [verifications_by_page[_page_id(page)] for page in pages],
    }
    _write_json_atomically(specs_dir / VERIFICATION_FILE_NAME, verification_report)
    _emit_generation_progress(
        progress_callback,
        "页面设计稿准备完成",
        stage="ready_for_confirmation",
        ready=len(entries_by_page),
        total=len(targets),
        backgroundAudits=0,
    )
    return {
        "ui_designs": manifest,
        "screenshot_ui_prepared_product_plan_sha256": product_hash,
    }


def regenerate_screenshot_ui_page(
    *,
    workspace: str,
    page: dict[str, Any],
    page_key: str,
    project_dir: str,
) -> dict[str, Any] | None:
    """若工作区存在截图视觉规范，则按原截图重新生成一个页面条目。"""

    specs_dir = Path(workspace).expanduser().resolve() / ".xcodeagent" / "specs"
    payload = _read_json_object(specs_dir / REFERENCE_FILE_NAME)
    if not payload:
        return None
    if payload.get("schemaVersion") != "screenshot-ui-reference.v1":
        raise RuntimeError("截图视觉规范版本无效，请从截图重新执行需求与 UI 设计阶段。")
    analysis_error = str(payload.get("analysisError") or "").strip()
    if analysis_error:
        raise RuntimeError(
            "截图视觉分析此前失败，不能降级为普通 UI 生成：" + analysis_error
        )
    source_values = payload.get("sourceScreenshots")
    if not isinstance(source_values, list):
        return None
    references = []
    for source in source_values:
        if not isinstance(source, dict):
            continue
        references.append(
            {
                "relativePath": source.get("relativePath"),
                "name": source.get("name"),
                "mimeType": source.get("mimeType"),
                "size": source.get("size"),
                "sha256": source.get("sha256"),
            }
        )
    requirement_input = RequirementInput.model_validate(
        {"mode": "screenshot", "screenshots": references}
    )
    settings = Settings.from_env()
    screenshots = load_ui_reference_images(
        workspace,
        requirement_input,
        overview_max_side=settings.screenshot_ui_page_image_max_side,
    )
    product_plan = _read_json_object(
        Path(workspace).expanduser().resolve() / ".xcodeagent" / "plans" / "product-plan.json"
    )
    pages = [
        item for item in product_plan.get("pages", [])
        if isinstance(item, dict) and _page_id(item)
    ] or [page]
    product_hash = _product_plan_hash(product_plan)
    reference = _load_cached_reference(
        specs_dir=specs_dir,
        product_plan_hash=product_hash,
        pages=pages,
        screenshots=screenshots,
    )
    page_id = _page_id(page)
    if reference is None or page_id not in reference.mappings:
        # 单页重试应补分析先前未映射的原图，不永久复用失败的页面映射。
        reference = _normalize_reference(
            _analyze_references(pages, screenshots, settings, existing=reference),
            pages,
            screenshots,
        )
        _persist_reference_artifacts(
            specs_dir=specs_dir,
            product_plan_hash=product_hash,
            settings=settings,
            screenshots=screenshots,
            pages=pages,
            reference=reference,
            analysis_error="",
        )
    if page_id not in reference.mappings:
        raise RuntimeError(
            f"页面 {page_id} 仍没有可信的截图映射，请检查页面主内容是否与产品规划对应。"
        )
    cached_code = _load_failed_candidate({}, project_dir, page_key)
    entry, verification = _generate_page_entry(
        page=page,
        pages=pages,
        shell=derive_shared_shell(list(reference.observations), pages, screenshots),
        page_key=page_key,
        project_dir=project_dir,
        screenshots=screenshots,
        reference=reference,
        settings=settings,
        initial_code=cached_code,
        # 旧候选只缺静态契约时可确定性修复；避免为同一视觉稿再耗费一次模型审查。
        run_visual_audit=not bool(cached_code),
    )
    verification_path = specs_dir / VERIFICATION_FILE_NAME
    report = _read_json_object(verification_path)
    if report.get("schemaVersion") == "screenshot-ui-verification.v1":
        existing_pages = report.get("pages")
        existing_pages = existing_pages if isinstance(existing_pages, list) else []
        report["pages"] = [
            verification
            if isinstance(item, dict) and str(item.get("pageId") or "") == page_id
            else item
            for item in existing_pages
        ]
        if not any(
            isinstance(item, dict) and str(item.get("pageId") or "") == page_id
            for item in existing_pages
        ):
            report["pages"].append(verification)
        report["updatedAt"] = datetime.now(UTC).isoformat()
        _write_json_atomically(verification_path, report)
    return entry
