"""应用模板页面/菜单增量初始化、manifest 持久化和完成门禁。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from app.domain.application_lifecycle import utc_now
from app.services.frontend_scaffold import (
    collect_template_pages,
    ensure_frontend_menu_entries,
    ensure_frontend_page_placeholders,
    inspect_frontend_menu_entries,
)


TEMPLATE_GENERATION_MANIFEST_RELATIVE_PATH = Path(
    ".xcodeagent/template-generation-manifest.json"
)
_TEMPLATE_LOCKS: dict[str, threading.RLock] = {}
_TEMPLATE_LOCKS_GUARD = threading.Lock()


class ApplicationTemplateGenerationError(ValueError):
    """表示模板下载、增量初始化或完成门禁没有满足要求。"""


def template_generation_manifest_path(workspace: str | Path) -> Path:
    """返回工作区模板生成 manifest 的权威路径。"""

    return Path(workspace).expanduser().resolve() / TEMPLATE_GENERATION_MANIFEST_RELATIVE_PATH


def load_template_generation_manifest(workspace: str | Path) -> dict[str, Any]:
    """读取并校验 manifest 根结构，缺失或损坏时显式失败。"""

    path = template_generation_manifest_path(workspace)
    if not path.is_file():
        raise ApplicationTemplateGenerationError(f"模板生成 manifest 不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApplicationTemplateGenerationError("模板生成 manifest 损坏或无法读取。") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("steps"), dict):
        raise ApplicationTemplateGenerationError("模板生成 manifest 结构无效。")
    return payload


def prepare_application_template_generation(
    workspace: str | Path,
    download_result: dict[str, Any],
) -> dict[str, Any]:
    """读取最新正式产物，并行补齐页面和菜单后统一写 manifest。"""

    workspace_path = Path(workspace).expanduser().resolve()
    with _template_lock(workspace_path):
        download_step = _normalize_download_step(workspace_path, download_result)
        manifest = _base_manifest(workspace_path, download_step)
        if download_step["status"] != "succeeded":
            failure_details = "; ".join(
                f"{name}(attempt={target['attempt']}): {target.get('error') or '未完成'}"
                for name, target in download_step["targets"].items()
                if target.get("status") != "succeeded"
            )
            manifest["overall"].update(
                status="failed",
                error=f"模板下载未完成，不能执行模板初始化：{failure_details}",
                updatedAt=utc_now().isoformat(),
            )
            _write_manifest_atomically(workspace_path, manifest)
            raise ApplicationTemplateGenerationError(manifest["overall"]["error"])

        try:
            product_plan, ui_designs = _load_template_planning_artifacts(workspace_path)
            pages = collect_template_pages(product_plan, ui_designs)
            frontend_dir = workspace_path / "frontend"
            jobs: dict[str, Callable[[], dict[str, Any]]] = {
                "templateFiles": lambda: ensure_frontend_page_placeholders(
                    frontend_dir, pages
                ),
                "menus": lambda: ensure_frontend_menu_entries(frontend_dir, pages),
            }
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {
                    name: executor.submit(_run_template_step, job)
                    for name, job in jobs.items()
                }
                results = {name: future.result() for name, future in futures.items()}
        except Exception as exc:
            manifest["overall"].update(
                status="failed",
                error=str(exc),
                updatedAt=utc_now().isoformat(),
            )
            _write_manifest_atomically(workspace_path, manifest)
            raise ApplicationTemplateGenerationError(str(exc)) from exc

        manifest["steps"].update(results)
        failed_steps = [
            name for name, result in results.items() if result.get("status") != "succeeded"
        ]
        manifest["overall"].update(
            status="failed" if failed_steps else "running",
            lastCompletedStep="menus" if not failed_steps else None,
            error=(
                "模板初始化步骤失败：" + "、".join(failed_steps)
                if failed_steps
                else None
            ),
            updatedAt=utc_now().isoformat(),
        )
        _write_manifest_atomically(workspace_path, manifest)
        if failed_steps:
            details = "; ".join(
                f"{name}: {results[name].get('error') or 'unknown error'}"
                for name in failed_steps
            )
            raise ApplicationTemplateGenerationError(details)
        return manifest


def validate_application_template_generation(workspace: str | Path) -> dict[str, Any]:
    """重读正式产物、manifest 和真实文件，执行只读完成门禁。"""

    workspace_path = Path(workspace).expanduser().resolve()
    with _template_lock(workspace_path):
        manifest = load_template_generation_manifest(workspace_path)
        product_plan, ui_designs = _load_template_planning_artifacts(workspace_path)
        pages = collect_template_pages(product_plan, ui_designs)
        errors: list[str] = []
        steps = manifest.get("steps") if isinstance(manifest.get("steps"), dict) else {}
        for step_name in ("download", "templateFiles", "menus"):
            step = steps.get(step_name) if isinstance(steps, dict) else None
            if not isinstance(step, dict) or step.get("status") != "succeeded":
                errors.append(f"manifest 步骤 {step_name} 未完成")

        for target in ("frontend", "backend"):
            target_error = _template_target_error(workspace_path, target)
            if target_error:
                errors.append(target_error)
        expected_pages = [
            f"frontend/src/pages/{page['key']}/index.tsx" for page in pages
        ]
        missing_pages = [
            relative_path
            for relative_path in expected_pages
            if not (workspace_path / relative_path).is_file()
        ]
        if missing_pages:
            errors.append("页面占位缺失：" + "、".join(missing_pages))
        menu_check = inspect_frontend_menu_entries(workspace_path / "frontend", pages)
        if menu_check.get("error"):
            errors.append(f"菜单文件无效：{menu_check['error']}")
        elif menu_check["missingKeys"]:
            errors.append("菜单项缺失：" + "、".join(menu_check["missingKeys"]))

        checked_at = utc_now().isoformat()
        steps["gate"] = {
            "status": "failed" if errors else "succeeded",
            "checkedAt": checked_at,
            "error": "；".join(errors) if errors else None,
        }
        manifest["overall"].update(
            status="failed" if errors else "succeeded",
            lastCompletedStep="menus" if errors else "gate",
            error="；".join(errors) if errors else None,
            updatedAt=checked_at,
        )
        _write_manifest_atomically(workspace_path, manifest)
        if errors:
            raise ApplicationTemplateGenerationError("；".join(errors))
        return manifest


def _base_manifest(workspace: Path, download_step: dict[str, Any]) -> dict[str, Any]:
    """创建不携带计划版本和 API 骨架字段的本轮 manifest。"""

    now = utc_now().isoformat()
    pending = {"status": "pending", "error": None}
    return {
        "generationId": f"generation-{uuid.uuid4().hex}",
        "workspaceRoot": str(workspace),
        "planningArtifacts": {
            "productPlanJsonPath": ".xcodeagent/plans/product-plan.json",
            "uiDesignsJsonPath": ".xcodeagent/specs/ui-designs.json",
        },
        "steps": {
            "download": download_step,
            "templateFiles": dict(pending),
            "menus": dict(pending),
            "gate": {**pending, "checkedAt": None},
        },
        "overall": {
            "status": "running",
            "lastCompletedStep": "download" if download_step["status"] == "succeeded" else None,
            "error": None,
            "startedAt": now,
            "updatedAt": now,
        },
    }


def _normalize_download_step(workspace: Path, value: dict[str, Any]) -> dict[str, Any]:
    """规范化 Renderer 下载结果，并用真实模板目录修正虚假成功。"""

    raw_targets = value.get("targets") if isinstance(value.get("targets"), dict) else {}
    targets: dict[str, Any] = {}
    failed_targets: list[str] = []
    incomplete_targets: list[str] = []
    for target_name in ("frontend", "backend"):
        raw = raw_targets.get(target_name) if isinstance(raw_targets, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        status = str(raw.get("status") or "failed")
        error = str(raw.get("error") or "").strip() or None
        directory_error = _template_target_error(workspace, target_name)
        if status not in {"pending", "succeeded", "failed"}:
            status = "failed"
            error = error or f"{target_name} 模板下载返回了非法状态。"
        if status == "failed":
            error = error or directory_error or f"{target_name} 模板下载失败。"
            failed_targets.append(target_name)
            incomplete_targets.append(target_name)
        elif status == "pending":
            error = error or f"{target_name} 模板下载尚未开始。"
            incomplete_targets.append(target_name)
        elif directory_error:
            status = "failed"
            error = directory_error
            failed_targets.append(target_name)
            incomplete_targets.append(target_name)
        try:
            attempt = int(raw.get("attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        targets[target_name] = {
            "status": status,
            "path": target_name,
            "attempt": max(0, min(attempt, 3)),
            "error": error,
        }
    return {
        "status": "failed" if incomplete_targets else "succeeded",
        "attempt": max(target["attempt"] for target in targets.values()),
        "failedTargets": failed_targets,
        "targets": targets,
    }


def _template_target_error(workspace: Path, target_name: str) -> str | None:
    """检查前后端模板目录是否包含可识别的工程入口。"""

    directory = workspace / target_name
    if not directory.is_dir():
        return f"{target_name} 模板目录不存在"
    markers = (
        (directory / "package.json",)
        if target_name == "frontend"
        else (
            directory / "pom.xml",
            directory / "build.gradle",
            directory / "build.gradle.kts",
        )
    )
    return None if any(marker.is_file() for marker in markers) else f"{target_name} 模板入口文件缺失"


def _load_template_planning_artifacts(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """读取模板页面和菜单所需的最新 ProductPlan 与 UiDesign Manifest。"""

    product_plan = _load_json_object(
        workspace / ".xcodeagent/plans/product-plan.json",
        "正式 ProductPlan",
    )
    if product_plan.get("confirmation_status") != "confirmed":
        raise ApplicationTemplateGenerationError("正式 ProductPlan 尚未确认。")
    if product_plan.get("schema_version") != "product-plan.v4":
        raise ApplicationTemplateGenerationError("正式 ProductPlan 不是 product-plan.v4。")
    ui_designs = _load_json_object(
        workspace / ".xcodeagent/specs/ui-designs.json",
        "正式 UiDesign Manifest",
    )
    if ui_designs.get("schema_version") != "ui-manifest.v3":
        raise ApplicationTemplateGenerationError("正式 UiDesign 不是 ui-manifest.v3。")
    if ui_designs.get("confirmation_status") not in {"confirmed", "skipped"}:
        raise ApplicationTemplateGenerationError("正式 UiDesign 尚未确认或明确跳过。")
    return product_plan, ui_designs


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    """严格读取指定正式 JSON 对象并保留明确错误。"""

    if not path.is_file():
        raise ApplicationTemplateGenerationError(f"{label}不存在：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApplicationTemplateGenerationError(f"{label}无法读取或格式错误。") from exc
    if not isinstance(value, dict):
        raise ApplicationTemplateGenerationError(f"{label}根节点必须是对象。")
    return value


def _run_template_step(job: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """执行单个并行步骤并把异常转换成结构化失败结果。"""

    try:
        return job()
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def _template_lock(workspace: Path) -> threading.RLock:
    """返回进程内按工作区隔离的模板初始化互斥锁。"""

    key = os.path.normcase(str(workspace))
    with _TEMPLATE_LOCKS_GUARD:
        return _TEMPLATE_LOCKS.setdefault(key, threading.RLock())


def _write_manifest_atomically(workspace: Path, manifest: dict[str, Any]) -> None:
    """通过同目录临时文件和原子替换写入 manifest。"""

    path = template_generation_manifest_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
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
