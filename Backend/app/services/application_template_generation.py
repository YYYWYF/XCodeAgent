"""应用模板契约检查、manifest 持久化和完成门禁。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
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
_TEMPLATE_ACTIVITY = threading.Condition(threading.Lock())
_ACTIVE_TEMPLATE_OPERATIONS: dict[str, int] = {}
_DELETING_TEMPLATE_WORKSPACES: set[str] = set()


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


def inspect_template_generation_readiness(workspace: str | Path) -> dict[str, Any]:
    """按 manifest 模板变体只读检查前端代码生成前置条件。"""

    workspace_path = Path(workspace).expanduser().resolve()
    errors: list[str] = []
    try:
        manifest = load_template_generation_manifest(workspace_path)
    except ApplicationTemplateGenerationError as exc:
        return {"ready": False, "errors": [str(exc)], "manifest": {}}

    variant, variant_error = _template_variant(manifest)
    if variant_error:
        errors.append(variant_error)
    steps = manifest.get("steps") if isinstance(manifest.get("steps"), dict) else {}
    for step_name in _required_step_names(variant, include_gate=True):
        step = steps.get(step_name) if isinstance(steps, dict) else None
        if not isinstance(step, dict) or step.get("status") != "succeeded":
            errors.append(f"模板 manifest 步骤 {step_name} 未完成")
    overall = manifest.get("overall") if isinstance(manifest.get("overall"), dict) else {}
    if overall.get("status") != "succeeded":
        errors.append(f"模板 manifest 完成门禁状态为 {overall.get('status') or 'unknown'}")

    for target in ("frontend", "backend"):
        target_error = _template_target_error(workspace_path, target)
        if target_error:
            errors.append(target_error)

    result = {
        "ready": not errors,
        "errors": errors,
        "manifest": manifest,
        "templateVariant": variant,
    }
    if variant == "auth":
        contract_errors = _frontend_template_contract_errors(workspace_path / "frontend")
        errors.extend(contract_errors)
        result["templateContract"] = {"valid": not contract_errors}
    elif variant == "main":
        try:
            pages = _load_template_pages(workspace_path)
            missing = [f"frontend/src/pages/{page['key']}/index.tsx" for page in pages if not (workspace_path / f"frontend/src/pages/{page['key']}/index.tsx").is_file()]
            menu = inspect_frontend_menu_entries(workspace_path / "frontend", pages)
            if missing:
                errors.append("页面入口缺失：" + "、".join(missing))
            if menu.get("error"):
                errors.append(f"菜单文件无效：{menu['error']}")
            elif menu.get("missingKeys"):
                errors.append("菜单项缺失：" + "、".join(menu["missingKeys"]))
            result.update({"pages": pages, "menu": menu})
        except (ApplicationTemplateGenerationError, ValueError) as exc:
            errors.append(str(exc))
    result["ready"] = not errors
    return result


def _track_template_operation(operation: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """跟踪可能写入模板文件的同步操作，并拒绝删除栅栏后的新调用。"""

    @wraps(operation)
    def wrapped(workspace: str | Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """在真实同步操作前后登记工作区活跃计数。"""

        key = _template_workspace_key(workspace)
        with _TEMPLATE_ACTIVITY:
            if key in _DELETING_TEMPLATE_WORKSPACES:
                raise ApplicationTemplateGenerationError("应用正在删除，已拒绝新的模板生成写入。")
            _ACTIVE_TEMPLATE_OPERATIONS[key] = _ACTIVE_TEMPLATE_OPERATIONS.get(key, 0) + 1
        try:
            return operation(workspace, *args, **kwargs)
        finally:
            with _TEMPLATE_ACTIVITY:
                remaining = _ACTIVE_TEMPLATE_OPERATIONS.get(key, 1) - 1
                if remaining > 0:
                    _ACTIVE_TEMPLATE_OPERATIONS[key] = remaining
                else:
                    _ACTIVE_TEMPLATE_OPERATIONS.pop(key, None)
                _TEMPLATE_ACTIVITY.notify_all()

    return wrapped


@_track_template_operation
def prepare_application_template_generation(
    workspace: str | Path,
    download_result: dict[str, Any],
) -> dict[str, Any]:
    """按下载分支执行 main 初始化或 auth 模板契约检查。"""

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

        variant, variant_error = _template_variant(manifest)
        if variant_error:
            manifest["overall"].update(status="failed", error=variant_error, updatedAt=utc_now().isoformat())
            _write_manifest_atomically(workspace_path, manifest)
            raise ApplicationTemplateGenerationError(variant_error)
        if variant == "auth":
            errors = _frontend_template_contract_errors(workspace_path / "frontend")
            manifest["steps"]["templateContract"] = {"status": "failed" if errors else "succeeded", "errors": errors, "resourcesPath": "frontend/src/constants/resources.ts", "routesPath": "frontend/src/constants/routes.tsx"}
            last_step = "templateContract"
        else:
            try:
                pages = _load_template_pages(workspace_path)
                jobs: dict[str, Callable[[], dict[str, Any]]] = {
                    "templateFiles": lambda: ensure_frontend_page_placeholders(workspace_path / "frontend", pages),
                    "menus": lambda: ensure_frontend_menu_entries(workspace_path / "frontend", pages),
                }
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = {name: future.result() for name, future in {name: executor.submit(job) for name, job in jobs.items()}.items()}
                manifest["steps"].update(results)
                errors = [f"模板初始化步骤失败：{name}" for name, result in results.items() if result.get("status") != "succeeded"]
            except Exception as exc:
                errors = [str(exc)]
            last_step = "menus"
        manifest["overall"].update(status="failed" if errors else "running", lastCompletedStep=last_step if not errors else None, error="；".join(errors) if errors else None, updatedAt=utc_now().isoformat())
        _write_manifest_atomically(workspace_path, manifest)
        if errors:
            raise ApplicationTemplateGenerationError("；".join(errors))
        return manifest


@_track_template_operation
def validate_application_template_generation(workspace: str | Path) -> dict[str, Any]:
    """重读正式产物、manifest 和真实文件，执行只读完成门禁。"""

    workspace_path = Path(workspace).expanduser().resolve()
    with _template_lock(workspace_path):
        manifest = load_template_generation_manifest(workspace_path)
        errors: list[str] = []
        variant, variant_error = _template_variant(manifest)
        if variant_error:
            errors.append(variant_error)
        steps = manifest.get("steps") if isinstance(manifest.get("steps"), dict) else {}
        for step_name in _required_step_names(variant, include_gate=False):
            step = steps.get(step_name) if isinstance(steps, dict) else None
            if not isinstance(step, dict) or step.get("status") != "succeeded":
                errors.append(f"manifest 步骤 {step_name} 未完成")

        for target in ("frontend", "backend"):
            target_error = _template_target_error(workspace_path, target)
            if target_error:
                errors.append(target_error)
        if variant == "auth":
            errors.extend(_frontend_template_contract_errors(workspace_path / "frontend"))
        elif variant == "main":
            try:
                pages = _load_template_pages(workspace_path)
                missing = [f"frontend/src/pages/{page['key']}/index.tsx" for page in pages if not (workspace_path / f"frontend/src/pages/{page['key']}/index.tsx").is_file()]
                if missing:
                    errors.append("页面占位缺失：" + "、".join(missing))
                menu = inspect_frontend_menu_entries(workspace_path / "frontend", pages)
                if menu.get("error"):
                    errors.append(f"菜单文件无效：{menu['error']}")
                elif menu.get("missingKeys"):
                    errors.append("菜单项缺失：" + "、".join(menu["missingKeys"]))
            except (ApplicationTemplateGenerationError, ValueError) as exc:
                errors.append(str(exc))

        checked_at = utc_now().isoformat()
        steps["gate"] = {
            "status": "failed" if errors else "succeeded",
            "checkedAt": checked_at,
            "error": "；".join(errors) if errors else None,
        }
        manifest["overall"].update(
            status="failed" if errors else "succeeded",
            lastCompletedStep=(_required_step_names(variant, include_gate=False)[-1] if errors else "gate"),
            error="；".join(errors) if errors else None,
            updatedAt=checked_at,
        )
        _write_manifest_atomically(workspace_path, manifest)
        if errors:
            raise ApplicationTemplateGenerationError("；".join(errors))
        return manifest


def _base_manifest(workspace: Path, download_step: dict[str, Any]) -> dict[str, Any]:
    """创建按下载分支隔离的本轮 manifest。"""

    now = utc_now().isoformat()
    pending = {"status": "pending", "error": None}
    variant, _ = _template_variant_from_download(download_step)
    steps = {"download": download_step, "gate": {**pending, "checkedAt": None}}
    if variant == "main":
        steps.update({"templateFiles": dict(pending), "menus": dict(pending)})
    else:
        steps["templateContract"] = dict(pending)
    return {
        "generationId": f"generation-{uuid.uuid4().hex}",
        "workspaceRoot": str(workspace),
        "templateVariant": variant,
        "steps": steps,
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
            "repositoryUrl": str(raw.get("repositoryUrl") or "").strip() or None,
            "branch": str(raw.get("branch") or "").strip() or None,
            "commitSha": str(raw.get("commitSha") or "").strip() or None,
        }
    return {
        "status": "failed" if incomplete_targets else "succeeded",
        "attempt": max(target["attempt"] for target in targets.values()),
        "failedTargets": failed_targets,
        "targets": targets,
    }


def _template_variant(manifest: dict[str, Any]) -> tuple[str, str | None]:
    """读取 manifest 模板变体，并以下载记录复核其一致性。"""

    download = manifest.get("steps", {}).get("download") if isinstance(manifest.get("steps"), dict) else {}
    derived, error = _template_variant_from_download(download if isinstance(download, dict) else {})
    stored = str(manifest.get("templateVariant") or "").strip()
    if stored and stored != derived:
        return derived, "模板 manifest 的 templateVariant 与下载分支不一致。"
    return derived, error


def _template_variant_from_download(download: dict[str, Any]) -> tuple[str, str | None]:
    """根据双端下载分支确定模板变体，拒绝混合分支。"""

    targets = download.get("targets") if isinstance(download.get("targets"), dict) else {}
    frontend = targets.get("frontend") if isinstance(targets.get("frontend"), dict) else {}
    backend = targets.get("backend") if isinstance(targets.get("backend"), dict) else {}
    frontend_branch = str(frontend.get("branch") or "").strip()
    backend_branch = str(backend.get("branch") or "").strip()
    if frontend_branch not in {"main", "auth"} or backend_branch not in {"main", "auth"}:
        return "invalid", "模板下载结果缺少有效的 frontend/backend 分支。"
    if frontend_branch != backend_branch:
        return "invalid", f"前后端模板分支不一致：frontend={frontend_branch}，backend={backend_branch}。"
    return frontend_branch, None


def _required_step_names(variant: str, *, include_gate: bool) -> tuple[str, ...]:
    """返回指定模板变体必须完成的 manifest 步骤。"""

    names = ("download", "templateFiles", "menus") if variant == "main" else ("download", "templateContract")
    return (*names, "gate") if include_gate else names


def _load_template_pages(workspace: Path) -> list[dict[str, Any]]:
    """读取 main 模板初始化依赖的已确认正式规划产物。"""

    product_plan = _load_json_object(workspace / ".xcodeagent/plans/product-plan.json", "正式 ProductPlan")
    if product_plan.get("confirmation_status") != "confirmed":
        raise ApplicationTemplateGenerationError("正式 ProductPlan 尚未确认。")
    if product_plan.get("schema_version") != "product-plan.v5":
        raise ApplicationTemplateGenerationError("正式 ProductPlan 不是 product-plan.v5。")
    ui_designs = _load_json_object(workspace / ".xcodeagent/specs/ui-designs.json", "正式 UiDesign Manifest")
    if ui_designs.get("schema_version") != "ui-manifest.v3":
        raise ApplicationTemplateGenerationError("正式 UiDesign Manifest 不是 ui-manifest.v3。")
    return collect_template_pages(product_plan, ui_designs)


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    """读取单个 JSON 对象，避免 main 初始化在损坏输入上继续执行。"""

    if not path.is_file():
        raise ApplicationTemplateGenerationError(f"{label} 不存在。")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApplicationTemplateGenerationError(f"{label} 损坏或无法读取。") from exc
    if not isinstance(value, dict):
        raise ApplicationTemplateGenerationError(f"{label} 必须是 JSON 对象。")
    return value


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


def _frontend_template_contract_errors(frontend: Path) -> list[str]:
    """验证 auth 前端模板的资源和路由插槽契约。"""

    resources = frontend / "src/constants/resources.ts"
    routes = frontend / "src/constants/routes.tsx"
    errors: list[str] = []
    if not resources.is_file():
        errors.append("auth 前端模板缺少 src/constants/resources.ts")
    if not routes.is_file():
        return [*errors, "auth 前端模板缺少 src/constants/routes.tsx"]
    source = routes.read_text(encoding="utf-8")
    for marker in (
        "// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START",
        "// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END",
        "// XCODEAGENT_BUSINESS_ROUTES_START",
        "// XCODEAGENT_BUSINESS_ROUTES_END",
    ):
        if marker not in source:
            errors.append(f"auth 前端模板 routes.tsx 缺少托管标记：{marker}")
    return errors


def _template_lock(workspace: Path) -> threading.RLock:
    """返回进程内按工作区隔离的模板初始化互斥锁。"""

    key = os.path.normcase(str(workspace))
    with _TEMPLATE_LOCKS_GUARD:
        return _TEMPLATE_LOCKS.setdefault(key, threading.RLock())


def _template_workspace_key(workspace: str | Path) -> str:
    """返回模板任务登记使用的规范化工作区键。"""

    return os.path.normcase(str(Path(workspace).expanduser().resolve(strict=False)))


def begin_application_template_deletion(workspace: str | Path) -> None:
    """封锁目标工作区后续模板生成和完成门禁写入。"""

    with _TEMPLATE_ACTIVITY:
        _DELETING_TEMPLATE_WORKSPACES.add(_template_workspace_key(workspace))


def wait_for_application_template_idle(
    workspace: str | Path,
    *,
    timeout_seconds: float = 30.0,
) -> bool:
    """等待已经进入同步线程的模板操作退出，避免删除后继续落盘。"""

    key = _template_workspace_key(workspace)
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    with _TEMPLATE_ACTIVITY:
        while _ACTIVE_TEMPLATE_OPERATIONS.get(key, 0) > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _TEMPLATE_ACTIVITY.wait(timeout=remaining)
        return True


def clear_application_template_lock(workspace: str | Path) -> bool:
    """在模板任务结束后移除目标工作区的进程内互斥锁缓存。"""

    key = os.path.normcase(str(Path(workspace).expanduser().resolve(strict=False)))
    with _TEMPLATE_LOCKS_GUARD:
        return _TEMPLATE_LOCKS.pop(key, None) is not None


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
