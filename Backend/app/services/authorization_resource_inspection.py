"""只读核验完整 resources.ts 投影，并将同次检查证据转为 Planning 外部能力。"""

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.services.authorization_frontend_projection import RESOURCES_RELATIVE_PATH, _render_resources
from app.services.authorization_resource_catalog import ResourceCatalog, resource_catalog_fingerprint
from app.services.build_task_reuse_contracts import ExternalCapability


def inspect_authorization_resource_catalog(
    workspace: str | Path, catalog: ResourceCatalog, *, workspace_revision: str,
) -> dict[str, Any]:
    """将真实文件与当前 catalog 的完整确定性投影比较，不写文件或反推源身份。

    调用方须传入同次 workspace 检查的 revision；结果只是该次只读检查证据，
    不承诺后续文件不变，也不替代 Build 验证。复用既有 renderer，避免两套投影规则。
    """

    if not isinstance(workspace_revision, str) or not workspace_revision or workspace_revision != workspace_revision.strip():
        raise ValueError("权限资源检查必须提供精确 workspace revision。")
    expected = _render_resources(catalog.frontend_resources()).encode("utf-8")
    evidence = {
        "resource_catalog_fingerprint": resource_catalog_fingerprint(catalog),
        "path": RESOURCES_RELATIVE_PATH.as_posix(),
        "workspace_revision": workspace_revision,
        "expected_projection_sha256": sha256(expected).hexdigest(),
    }
    root = Path(workspace).expanduser().resolve()
    path = root / RESOURCES_RELATIVE_PATH
    try:
        # 文件或父目录符号链接不能把检查引向当前工作区之外。
        if not path.resolve().is_relative_to(root):
            return {**evidence, "status": "unsafe_path"}
        if not path.is_file():
            return {**evidence, "status": "missing"}
        actual = path.read_bytes()
    except (OSError, RuntimeError):
        return {**evidence, "status": "unreadable"}
    return {
        **evidence,
        "status": "satisfied" if actual == expected else "mismatch",
        "content_sha256": sha256(actual).hexdigest(),
    }


def verified_auth_resource_capability(
    catalog: ResourceCatalog, workspace_snapshot: Mapping, inspection: Mapping | None,
) -> ExternalCapability | None:
    """验证完整匹配证据的 R、revision、路径及摘要，仅授予当前目录的外部能力。"""

    if inspection is None:
        return None
    if not isinstance(inspection, Mapping):
        raise ValueError("权限资源检查证据必须为对象。")
    if inspection.get("status") != "satisfied":
        return None
    fingerprint = resource_catalog_fingerprint(catalog)
    expected_digest = sha256(_render_resources(catalog.frontend_resources()).encode("utf-8")).hexdigest()
    revision = workspace_snapshot.get("workspace_revision")
    if (
        not isinstance(revision, str) or not revision or revision != revision.strip()
        or inspection.get("workspace_revision") != revision
        or inspection.get("resource_catalog_fingerprint") != fingerprint
        or inspection.get("path") != RESOURCES_RELATIVE_PATH.as_posix()
        or inspection.get("expected_projection_sha256") != expected_digest
        or inspection.get("content_sha256") != expected_digest
    ):
        raise ValueError("权限资源检查证据与当前 catalog、workspace revision 或完整投影摘要不一致。")
    return ExternalCapability(
        unit_id="frontend:auth-guard", capability_id=f"frontend.auth.resources:{fingerprint}",
        source="authorization_resource_catalog", workspace_revision=revision,
        source_refs={
            "path": RESOURCES_RELATIVE_PATH.as_posix(),
            "resource_catalog_fingerprint": fingerprint,
            "content_sha256": expected_digest,
            "expected_projection_sha256": expected_digest,
        },
    )
