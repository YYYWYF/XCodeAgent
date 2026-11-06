"""从正式权限契约确定性编译 Builtin Seed，隔离载荷与可公开摘要。"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from app.services.authorization_manifest import (
    AUTHORIZATION_MANIFEST_SCHEMA_VERSION,
    SYSTEM_ADMIN_ROLE_SEED_KEY,
    SYSTEM_RESOURCE_KEY,
    _with_fingerprint,
)
from app.services.authorization_table_projection import table_mapping
from app.services.builtin_authorization_state import builtin_binding
from app.workspace.endpoint_design_documents import technical_plan_path

PURPOSE = "AUTHORIZATION_SEED_V1"
MAX_BYTES = 256 * 1024
MAX_RESOURCES = 500
MAX_ADMINS = 100
ADMIN = "SYSTEM_ADMIN"
SYSTEM_REF = "system:authorization_management"


class SeedContractError(ValueError):
    """提示已确认输入与当前 SaaS 合同不兼容，不回显原始值。"""


def canonical_json(value: dict) -> bytes:
    """产生固定 UTF-8 JSON，同时用于本地摘要和传输字节上限。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _objects(value: object) -> list[dict]:
    """要求结构化数组完整，拒绝静默忽略非法项。"""

    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("权限 Manifest 数组结构无效。")
    return value


def _text(value: object, limit: int, *, allow_blank: bool = False) -> str:
    """按 Direct Java 字符长度校验文本，并保留已确认的原始值。"""

    if (not isinstance(value, str) or len(value) + sum(ord(char) > 0xFFFF for char in value) > limit
            or not allow_blank and not value.strip()
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF
                   for char in value)):
        raise ValueError("权限 Seed 文本缺失、超长或包含无效字符。")
    return value


def _resource_rows(manifest: dict) -> list[dict]:
    """逐条校验 Direct 使用的资源键与引用，投影 DDL 白名单列。"""

    resources = _objects(manifest.get("resources"))
    if not 1 <= len(resources) <= MAX_RESOURCES:
        raise ValueError("权限资源数量超出 Seed 限制。")
    rows = {}
    for item in resources:
        key = _text(item.get("resourceKey"), 64)
        name = _text(item.get("name"), 32)
        description = item.get("description")
        if description is not None:
            description = _text(description, 128, allow_blank=True)
        origin, kind = item.get("origin"), item.get("type")
        ref = _text(item.get("targetResourceRef"), 255)
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", key) or key in rows:
            raise ValueError("权限资源键非法或重复。")
        if key == SYSTEM_RESOURCE_KEY:
            valid = (origin, kind, ref) == ("system", "system", SYSTEM_REF)
        else:
            valid = origin == "business" and (
                kind == "page" and ref == f"page:{key}"
                or kind == "operation" and re.fullmatch(r"action:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*", ref)
            )
        if not valid:
            raise ValueError("权限资源类型或引用无效。")
        rows[key] = {"key": key, "name": name, "description": description, "origin": origin,
                     "type": kind, "target_resource_ref": ref}
    if SYSTEM_RESOURCE_KEY not in rows:
        raise ValueError("缺少系统权限管理资源。")
    _validate_default_roles(manifest.get("defaultRoleAuthorization"), rows.keys())
    return [rows[key] for key in sorted(rows)]


def _validate_default_roles(defaults: object, resources) -> None:
    """保留 Direct 的业务角色和授权引用校验，Seed 不写入这些行。"""

    if not isinstance(defaults, dict):
        raise ValueError("缺少默认角色授权契约。")
    roles, granted = set(), set()
    for role in _objects(defaults.get("roles")):
        key = _text(role.get("roleSeedKey"), 64)
        _text(role.get("name"), 32)
        if role.get("description") is not None:
            _text(role["description"], 128, allow_blank=True)
        if key in roles or key in {SYSTEM_ADMIN_ROLE_SEED_KEY, ADMIN}:
            raise ValueError("业务默认角色键重复或占用系统角色。")
        roles.add(key)
    for grant in _objects(defaults.get("roleResourceGrants")):
        key = _text(grant.get("roleSeedKey"), 64)
        refs = grant.get("resourceKeys")
        if key not in roles or key in granted or not isinstance(refs, list):
            raise ValueError("业务默认角色授权引用无效。")
        keys = [_text(ref, 64) for ref in refs]
        if len(keys) != len(set(keys)) or any(ref == SYSTEM_RESOURCE_KEY or ref not in resources for ref in keys):
            raise ValueError("业务默认角色授权资源无效。")
        granted.add(key)


def _manifest(plan: dict) -> dict:
    """复用正式 Manifest 指纹算法并拒绝缺失或被篡改的结构。"""

    manifest = plan.get("authorization_manifest")
    if (plan.get("artifact_type") != "technical-plan" or plan.get("confirmation_status") != "confirmed"
            or not isinstance(manifest, dict) or manifest.get("schema_version") != AUTHORIZATION_MANIFEST_SCHEMA_VERSION
            or manifest.get("systemAuthorization") != {"adminRoleSeedKey": SYSTEM_ADMIN_ROLE_SEED_KEY,
                                                        "managementResourceKey": SYSTEM_RESOURCE_KEY}):
        raise ValueError("缺少已确认的权限 Manifest 或系统授权。")
    bindings = manifest.get("bindings")
    if not isinstance(bindings, dict) or set(bindings) != {"pages", "actions", "endpoints"}:
        raise ValueError("权限 Manifest 绑定结构无效。")
    for value in bindings.values():
        _objects(value)
    return manifest


def compile_builtin_authorization_seed(application: dict, technical_plan: dict, *, datasource_key: str) -> dict:
    """编译完整四表实际行，返回载荷、本地指纹和独立的幂等键。"""

    authorization = application.get("authorization")
    if (builtin_binding(application)["datasourceKey"] != datasource_key
            or not isinstance(authorization, dict) or authorization.get("enabled") is not True):
        raise ValueError("Builtin 注册绑定或权限开关无效。")
    manifest = _manifest(technical_plan)
    resources = _resource_rows(manifest)
    if _with_fingerprint(deepcopy(manifest))["fingerprint"] != manifest.get("fingerprint"):
        raise ValueError("权限 Manifest 指纹与已确认内容不符。")

    subjects = authorization.get("initialAdministratorSubjects")
    if not isinstance(subjects, list) or not subjects:
        raise ValueError("权限 Seed 需要至少一个真实初始管理员。")
    admins = {_text(subject, 32) for subject in subjects}
    if len(admins) > MAX_ADMINS or any(subject != subject.strip() or subject == "current-user" for subject in admins):
        raise ValueError("权限 Seed 初始管理员无效或超过数量上限。")

    role = {"role_id": ADMIN, "created_by": "SYSTEM"}
    rows = {
        "role": [{**role, "role_name": "系统管理员", "description": "内置权限系统管理员",
                  "enable": 1, "is_system": 1, "is_deleted": 0}],
        "resource": resources,
        "role_resource": [{**role, "resource_key": SYSTEM_RESOURCE_KEY}],
        "role_member": [{**role, "member_id": subject, "member_name": subject} for subject in sorted(admins)],
    }
    payload = {"purpose": PURPOSE, "tables": [
        {"tableName": name, "rows": rows[logical]} for logical, name in table_mapping(datasource_key).items()
    ]}
    fingerprint = "sha256:" + hashlib.sha256(canonical_json(payload)).hexdigest()
    key = hashlib.sha256((datasource_key + PURPOSE + fingerprint).encode("ascii")).hexdigest()
    request = {**payload, "idempotencyKey": key}
    if len(canonical_json(request)) > MAX_BYTES:
        raise ValueError("权限 Seed 超过 SaaS 256 KiB 请求上限。")
    return {"request": request, "seedFingerprint": fingerprint, "idempotencyKey": key}


def seed_request_fingerprint(datasource_key: str, request: dict) -> str:
    """按 SaaS Java 合同计算带长度前缀的请求指纹，排除幂等键。"""

    def encode(value: str | int) -> bytes:
        """将字符串或十进制整数编码为大端长度和 UTF-8 字节。"""

        encoded = str(value).encode("utf-8")
        return len(encoded).to_bytes(4, "big") + encoded

    digest = hashlib.sha256(encode(request["purpose"]) + encode(datasource_key))
    for table in request["tables"]:
        digest.update(encode(table["tableName"]))
        digest.update(len(table["rows"]).to_bytes(4, "big"))
        for row in table["rows"]:
            for column, value in sorted(row.items()):
                digest.update(encode(column) + encode(value))
    return "sha256:" + digest.hexdigest()


def validate_seed_transport(request: dict) -> None:
    """在受理前识别 SaaS 无法接收的当前正式数据，保留 Direct 行语义。"""

    resources = request["tables"][1]["rows"]
    if any(row["type"] == "operation" for row in resources):
        raise SeedContractError("当前 SaaS Seed 接口尚不接受已确认操作资源的 action: 引用，请核对 SaaS 合同。")
    if any(value is None or isinstance(value, str) and (not value.strip() or value != value.strip())
           for table in request["tables"] for row in table["rows"] for value in row.values()):
        raise SeedContractError("当前 SaaS Seed 接口要求非空且无首尾空白的文本，请核对权限资源描述及 SaaS 合同。")


def read_confirmed_seed_plan(root: Path) -> dict:
    """从正式工作区路径读取当前技术规划，不接受外部路径或旧运行快照。"""

    path = technical_plan_path(root)
    if path.resolve() != path or not path.is_file():
        raise ValueError("当前技术规划路径无效。")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("当前技术规划无效。")
    return plan
