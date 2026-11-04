"""在可信模板安装边界记录权限原文件摘要，不从待改写工程自证来源。"""

import hashlib
import json
from pathlib import Path

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.template_reconcile.executor_v3 import WorkingCopyStoreV3
from app.services.template_reconcile.protocol_v3 import TemplateStateV3

SOURCE_RECEIPT = WORKSPACE_ARTIFACT_DIR / "builtin-authorization-sources.json"
PO_ROOT = "backend/src/main/java/com/cmbchina/backend/auth/infrastructure/po/"
XML_ROOT = "backend/src/main/resources/mapper/auth/"
PO_FILES = dict(zip((PO_ROOT + name + "PO.java" for name in ("Role", "Resource", "RoleResource", "RoleMember")), ("role", "resource", "role_resource", "role_member")))
XML_FILES = tuple(XML_ROOT + name + "Mapper.xml" for name in ("Role", "RoleResource", "RoleMember"))


def source_digest(content: bytes) -> str:
    """使用原始字节摘要核对可信安装来源，避免换行规范化掩盖用户编辑。"""

    return "sha256:" + hashlib.sha256(content).hexdigest()


def stage_authorization_sources(store: WorkingCopyStoreV3, template: TemplateStateV3) -> None:
    """仅为完整的受管安装保存摘要；记录与安装文件放在同一提交事务中。"""

    owners = {item.target: item for item in template.installedArtifacts.values() if item.capabilityId == "authorization"}
    paths = (*PO_FILES, *XML_FILES)
    if not all(path in owners for path in paths):
        return
    entries = {path: store.entry(path) for path in paths}
    if any(entry.working is None for entry in entries.values()):
        return
    receipt = store.entry(SOURCE_RECEIPT.as_posix())
    # 增量安装没有改权限文件时沿用已有可信来源，不重新信任用户当前内容。
    if receipt.original is not None and not any(entry.original != entry.working for entry in entries.values()):
        return
    previous = json.loads(receipt.original) if receipt.original else {}
    files = dict(previous.get("files", {}))
    for path, entry in entries.items():
        if entry.original != entry.working:
            files[path] = {"digest": source_digest(entry.working), "installedRevision": owners[path].installedRevision}
        elif path not in files:
            # 没有来自包的新增/替换，就不能把现有工程文件登记为模板原文。
            return
    receipt.working = json.dumps({"files": files}, ensure_ascii=False, sort_keys=True).encode()


def record_materialized_authorization_sources(staging: Path, template: TemplateStateV3) -> None:
    """从已校验 ZIP 的隔离 staging 保存来源，随原物化事务提交或回滚。"""

    store = WorkingCopyStoreV3(staging)
    for path in (*PO_FILES, *XML_FILES):
        entry = store.entry(path)
        if entry.original is not None:
            entry.working, entry.original = entry.original, None
    stage_authorization_sources(store, template)
    receipt = store.entries.get(SOURCE_RECEIPT.as_posix())
    if receipt is not None and receipt.working is not None:
        target = staging / SOURCE_RECEIPT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(receipt.working)
