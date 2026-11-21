"""在开发阶段清理本轮已删除接口的代码与契约残留。

**为什么需要它**：删除一个接口只改变了计划层面的目录（变更快照把该接口标为
`deleted`，它随即退出开发产物目录）。基线里那份已生成的接口实现——前端 API
客户端的请求函数、后端的 Controller / Service / DTO——并不会自己消失。
它们本轮"没有变更"，在开发入口被禁用后，用户没有任何路径能清理掉。

**与页面删除的差别**：页面产物是模板级固定目录（`pages/<PageKey>/`），可以按
pageId 确定推出要删什么。接口产物是模型自由生成的：一个 API Contract 对应一个
前后端模块（`apis/<contract>ContractApi.ts`、`backend/.../modules/<module>/`），
单个接口并没有独立的目录或文件边界。因此这里采取"证据化"策略：

- 整个契约的接口都被删除时，契约模块本身失去全部职责，可以整组清理；
- 只删除契约内部分接口时，仅在 TypeScript / Java AST 能证明精确函数或方法边界时
  移除目标实现，保留共享文件和其它接口；解析失败或仍有调用残留时只报告给用户；
- 预演与执行分离，先返回"将要删什么"。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.development_change_snapshot import read_change_snapshot
from app.services.endpoint_partial_deletion import (
    remove_java_function,
    remove_typescript_function,
)

APIS_DIR_RELATIVE = Path("frontend/src/apis")
BACKEND_MODULES_RELATIVE = Path("backend/src/main/java")


@dataclass
class EndpointDeletionPreview:
    """描述一次接口（或整个契约）删除将影响哪些文件，供确认与结果展示。"""

    contract_id: str
    endpoint_id: str
    endpoint_label: str
    files: list[str] = field(default_factory=list)
    contract_removed: bool = False
    backend_module: str = ""
    referenced_by: list[str] = field(default_factory=list)

    @property
    def removable(self) -> bool:
        """至少命中一个可删除目标时才允许执行，避免空操作被当成成功。"""

        return bool(self.files or self.backend_module)

    def to_payload(self) -> dict[str, Any]:
        """转换为 AG-UI 状态的 camelCase 载荷。"""

        return {
            "apiContractId": self.contract_id,
            "endpointId": self.endpoint_id,
            "endpointLabel": self.endpoint_label,
            "files": list(self.files),
            "contractRemoved": self.contract_removed,
            "backendModule": self.backend_module,
            "referencedBy": list(self.referenced_by),
            "removable": self.removable,
        }


def _camel(value: str) -> str:
    """把契约 id 转换成前端 API 文件名使用的驼峰形式。

    契约 id 的命名是 `access_card_contract` 这类下划线风格，前端产物文件名为
    `accessCardContractApi.ts`，需要保持同一条转换规则才能精确定位。
    """

    segments = [segment for segment in str(value or "").split("_") if segment]
    if not segments:
        return ""
    return segments[0] + "".join(segment[0].upper() + segment[1:] for segment in segments[1:])


def _snapshot_endpoints(workspace: str | Path) -> dict[str, dict[str, str]]:
    """读取本轮变更快照里的接口变更类型（contractId → endpointId → status）。"""

    snapshot = read_change_snapshot(workspace) or {}
    raw = snapshot.get("endpoints")
    if not isinstance(raw, dict):
        return {}
    return {
        str(contract_id): {
            str(endpoint_id): str(status)
            for endpoint_id, status in (endpoints or {}).items()
            if isinstance(endpoints, dict)
        }
        for contract_id, endpoints in raw.items()
        if isinstance(endpoints, dict)
    }


def _technical_endpoints(workspace: str | Path) -> dict[str, dict[str, dict[str, Any]]]:
    """读取正式技术规划里的接口定义，用于取展示名称与 HTTP 身份。"""

    path = Path(workspace).expanduser().resolve() / WORKSPACE_ARTIFACT_DIR / "plans/technical-plan.json"
    if not path.is_file():
        return {}
    try:
        import json

        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for contract in plan.get("api_contracts") or []:
        if not isinstance(contract, dict):
            continue
        contract_id = str(contract.get("id") or "").strip()
        if not contract_id:
            continue
        endpoints: dict[str, dict[str, Any]] = {}
        for endpoint in contract.get("endpoints") or []:
            if not isinstance(endpoint, dict):
                continue
            endpoint_id = str(endpoint.get("id") or "").strip()
            if endpoint_id:
                endpoints[endpoint_id] = endpoint
        result[contract_id] = endpoints
    return result


def deleted_endpoint_targets(workspace: str | Path) -> dict[str, str]:
    """列出本轮已删除的接口（以 `contract::endpoint` 为键，值为可读名称）。

    只返回当前技术规划里仍然存在的契约——契约整体消失时已经没有可清理的
    模块归属，交由计划层面的变更处理。
    """

    snapshot = _snapshot_endpoints(workspace)
    technical = _technical_endpoints(workspace)
    targets: dict[str, str] = {}
    for contract_id, endpoints in snapshot.items():
        contract_endpoints = technical.get(contract_id)
        if not contract_endpoints:
            continue
        for endpoint_id, status in endpoints.items():
            if status != "deleted":
                continue
            endpoint = contract_endpoints.get(endpoint_id) or {}
            label = (
                str(endpoint.get("summary") or "").strip()
                or f"{endpoint.get('method') or ''} {endpoint.get('path') or ''}".strip()
                or endpoint_id
            )
            targets[f"{contract_id}::{endpoint_id}"] = label
    return targets


def _contract_endpoint_ids(workspace: str | Path, contract_id: str) -> list[str]:
    """取契约在当前技术规划中的全部接口 id。"""

    return sorted(_technical_endpoints(workspace).get(contract_id, {}).keys())


def _contract_is_fully_deleted(workspace: str | Path, contract_id: str) -> bool:
    """判断契约内所有仍存在的接口是否都已被标记为删除。"""

    endpoints = _snapshot_endpoints(workspace).get(contract_id) or {}
    ids = _contract_endpoint_ids(workspace, contract_id)
    if not ids:
        return False
    return all(endpoints.get(endpoint_id) == "deleted" for endpoint_id in ids)


def _endpoint_function_name(endpoint_id: str) -> str:
    """从接口 id 取出请求函数名，并把 snake_case 统一为生成代码的 lowerCamelCase。"""

    _, _, function = str(endpoint_id or "").rpartition(".")
    return _camel(function.strip())


def _api_client_file(root: Path, contract_id: str) -> Path:
    """按契约 id 推导前端 API 客户端文件路径。

    契约 id 本身已带 `..._contract` 后缀，转驼峰后文件名形如
    `greetingContractApi.ts`，因此这里只追加 `Api.ts`，不能再拼一次 `Contract`。
    """

    return root / APIS_DIR_RELATIVE / f"{_camel(contract_id)}Api.ts"


def _referencing_files(workspace: str | Path, needle: str) -> list[str]:
    """扫描前端源码里仍引用该标识的文件，只报告不修改。"""

    root = Path(workspace).expanduser().resolve()
    frontend = root / "frontend/src"
    if not frontend.is_dir() or not needle:
        return []
    pattern = re.compile(rf"\b{re.escape(needle)}\b")
    hits: list[str] = []
    for path in sorted(frontend.rglob("*.ts*")):
        try:
            if pattern.search(path.read_text(encoding="utf-8")):
                hits.append(path.relative_to(root).as_posix())
        except (OSError, UnicodeError):
            continue
    return hits


def _controller_for_paths(root: Path, paths: set[str]) -> list[Path]:
    """找出声明了这些 HTTP 路径的 Controller 文件。

    后端模块目录名由模型生成（如 `cardadmin`），无法从契约 id 推导，因此只能以
    接口声明过的路径为锚点反查 Controller；命中后由它的包路径确定模块目录。
    """

    backend_root = root / BACKEND_MODULES_RELATIVE
    if not backend_root.is_dir() or not paths:
        return []
    needles = {path.rstrip("/") for path in paths if path}
    # Controller 常把前缀放在类级 @RequestMapping、末段放在方法级 @GetMapping。
    # 同时允许用完整路径与最后一个路径段反查，避免找不到真实模块而放弃部分删除。
    needles.update({
        "/" + path.strip("/").rsplit("/", 1)[-1]
        for path in list(needles)
        if path.strip("/")
    })
    found: list[Path] = []
    for java in sorted(backend_root.rglob("*Controller.java")):
        try:
            content = java.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if any(needle in content for needle in needles):
            found.append(java)
    return found


def _backend_module_dir(root: Path, controller: Path) -> Path | None:
    """由 Controller 的包路径取出所属模块目录（`.../modules/<name>/`）。"""

    try:
        parts = controller.relative_to(root / BACKEND_MODULES_RELATIVE).parts
    except ValueError:
        return None
    if "modules" in parts:
        index = parts.index("modules")
        if index + 1 >= len(parts) - 1:
            return None
        return root / BACKEND_MODULES_RELATIVE / Path(*parts[: index + 2])
    # 当前模板也允许 `<base-package>/<module>/adapter/web`，此时 adapter 的上级
    # 就是业务模块边界；不能只支持旧的 modules/<name> 包结构。
    if "adapter" in parts:
        index = parts.index("adapter")
        if index > 0:
            return root / BACKEND_MODULES_RELATIVE / Path(*parts[:index])
    return None


def _backend_endpoint_files(
    root: Path, technical: dict[str, dict[str, Any]], endpoint_id: str
) -> list[Path]:
    """限定目标接口所属模块的 Java 文件，避免同名函数跨模块误删。"""

    endpoint = technical.get(endpoint_id) or {}
    controllers = _controller_for_paths(root, {str(endpoint.get("path") or "")})
    module_dirs = {
        module_dir
        for controller in controllers
        if (module_dir := _backend_module_dir(root, controller)) is not None
    }
    return sorted({
        path
        for module_dir in module_dirs
        for path in module_dir.rglob("*.java")
        if path.is_file()
    })


def _backend_references(files: list[Path], root: Path, function_name: str) -> list[str]:
    """列出模块内仍声明或调用目标函数的 Java 文件。"""

    pattern = re.compile(rf"\b{re.escape(function_name)}\b")
    references: list[str] = []
    for path in files:
        try:
            if pattern.search(path.read_text(encoding="utf-8")):
                references.append(path.relative_to(root).as_posix())
        except (OSError, UnicodeError):
            continue
    return references


def preview_endpoint_deletion(
    workspace: str | Path, contract_id: str, endpoint_id: str
) -> EndpointDeletionPreview:
    """只读预演：列出该接口删除会触及的文件，不做任何修改。"""

    root = Path(workspace).expanduser().resolve()
    technical = _technical_endpoints(root).get(contract_id) or {}
    endpoint = technical.get(endpoint_id) or {}
    label = (
        str(endpoint.get("summary") or "").strip()
        or f"{endpoint.get('method') or ''} {endpoint.get('path') or ''}".strip()
        or endpoint_id
    )
    preview = EndpointDeletionPreview(
        contract_id=contract_id, endpoint_id=endpoint_id, endpoint_label=label,
    )
    if _contract_is_fully_deleted(root, contract_id):
        # 整个契约没有幸存接口：前后端模块都已失去职责，可以整组清理。
        preview.contract_removed = True
        api_file = _api_client_file(root, contract_id)
        if api_file.is_file():
            preview.files.append(api_file.relative_to(root).as_posix())
        # 只有"整组删光"时才允许动后端模块，且必须能用路径反查到 Controller。
        for controller in _controller_for_paths(
            root, {str(item.get("path") or "") for item in technical.values()}
        ):
            module_dir = _backend_module_dir(root, controller)
            if module_dir is not None and module_dir.is_dir():
                preview.backend_module = module_dir.relative_to(root).as_posix()
                break
    function_name = _endpoint_function_name(endpoint_id)
    java_files = _backend_endpoint_files(root, technical, endpoint_id)
    if not preview.contract_removed:
        # 契约内还有其它接口时，只在能解析出精确函数/方法边界时进行部分删除。
        # 无法安全解析的文件继续留在 referenced_by 中，绝不用模糊替换硬删。
        api_file = _api_client_file(root, contract_id)
        if api_file.is_file():
            try:
                content = api_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                content = ""
            if content and remove_typescript_function(content, function_name) is not None:
                preview.files.append(api_file.relative_to(root).as_posix())
        for java_file in java_files:
            try:
                content = java_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if remove_java_function(content, function_name) is not None:
                preview.files.append(java_file.relative_to(root).as_posix())
    preview.referenced_by = sorted(set([
        *_referencing_files(root, function_name),
        *_backend_references(java_files, root, function_name),
    ]))
    return preview


def delete_endpoint_artifacts(
    workspace: str | Path, contract_id: str, endpoint_id: str
) -> dict[str, Any]:
    """执行接口删除：清理可精确证明属于该接口的文件，返回清理证据。

    整组删除时清理完整文件与模块；部分删除时只移除解析成功的目标函数/方法，
    共享文件及其余接口保持原样。解析失败的文件不会写入。
    """

    root = Path(workspace).expanduser().resolve()
    preview = preview_endpoint_deletion(root, contract_id, endpoint_id)
    removed: list[str] = []
    if preview.contract_removed:
        for relative in preview.files:
            target = root / relative
            if target.is_file():
                target.unlink()
                removed.append(relative)
        if preview.backend_module:
            module_dir = root / preview.backend_module
            if module_dir.is_dir():
                shutil.rmtree(module_dir)
                removed.append(preview.backend_module)
    else:
        function_name = _endpoint_function_name(endpoint_id)
        for relative in preview.files:
            target = root / relative
            try:
                content = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            updated = (
                remove_typescript_function(content, function_name)
                if target.suffix in {".ts", ".tsx"}
                else remove_java_function(content, function_name)
            )
            if updated is not None and updated != content:
                target.write_text(updated, encoding="utf-8")
                removed.append(relative)

    return {
        "apiContractId": contract_id,
        "endpointId": endpoint_id,
        "endpointLabel": preview.endpoint_label,
        "files": removed,
        "contractRemoved": preview.contract_removed,
        "referencedBy": preview.referenced_by,
    }
