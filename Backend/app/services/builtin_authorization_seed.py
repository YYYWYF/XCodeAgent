"""从正式权限契约确定性编译 Builtin Seed，隔离载荷与可公开摘要。"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from app.services.authorization_manifest import (
    AUTHORIZATION_MANIFEST_SCHEMA_VERSION,
    RESOURCE_KEY_MAX_LENGTH,
    SYSTEM_ADMIN_ROLE_SEED_KEY,
    SYSTEM_RESOURCE_KEY,
    _with_fingerprint,
)
from app.services.authorization_table_projection import table_mapping
from app.services.application_config.initial_administrators import parse_admins_for_initialization
from app.services.builtin_authorization_state import builtin_binding
from app.workspace.endpoint_design_documents import technical_plan_path

PURPOSE = "AUTHORIZATION_SEED_V1"
MAX_BYTES = 256 * 1024
MAX_RESOURCES = 500
MAX_ADMINS = 100
ADMIN = "SYSTEM_ADMIN"
SYSTEM_REF = "system:authorization_management"
SEED_COLUMNS = {
    "role": {"role_id": 32, "role_name": 32, "description": 128, "enable": 0,
             "is_system": 0, "is_deleted": 0, "created_by": 32},
    "resource": {"key": RESOURCE_KEY_MAX_LENGTH, "name": 32, "description": 128, "origin": 8,
                 "type": 9, "target_resource_ref": 255},
    "role_resource": {"role_id": 32, "resource_key": RESOURCE_KEY_MAX_LENGTH, "created_by": 32},
    "role_member": {"role_id": 32, "member_id": 32, "member_name": 32, "created_by": 32},
}
SEED_ROW_LIMITS = {"role": 1, "resource": MAX_RESOURCES, "role_resource": 1, "role_member": MAX_ADMINS}


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


def _text(value: object, limit: int, *, allow_blank: bool = False, code_points: bool = False) -> str:
    """复用文本校验，正式编译保留 Direct 长度约束，SaaS 传输按码点计数。"""

    if (not isinstance(value, str) or len(value) > limit
            or not code_points and len(value) + sum(ord(char) > 0xFFFF for char in value) > limit
            or not allow_blank and not value or value != value.strip()
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF
                   for char in value)):
        raise ValueError("权限 Seed 文本缺失、超长或包含无效字符。")
    return value


def _validate_resource_identity(key: str, origin: str, kind: str, ref: str) -> None:
    """复用正式资源键及系统、页面、操作引用的组合约束。"""

    if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", key):
        raise ValueError("权限资源键非法。")
    if key == SYSTEM_RESOURCE_KEY:
        valid = (origin, kind, ref) == ("system", "system", SYSTEM_REF)
    else:
        valid = origin == "business" and (
            kind == "page" and ref == f"page:{key}"
            or kind == "operation" and re.fullmatch(r"action:[a-z][a-z0-9_]*:[a-z][a-z0-9_]*", ref)
        )
    if not valid:
        raise ValueError("权限资源类型或引用无效。")


def _resource_rows(manifest: dict) -> list[dict]:
    """逐条校验 Direct 使用的资源键与引用，投影 DDL 白名单列。"""

    resources = _objects(manifest.get("resources"))
    if not 1 <= len(resources) <= MAX_RESOURCES:
        raise ValueError("权限资源数量超出 Seed 限制。")
    rows = {}
    for item in resources:
        key = _text(item.get("resourceKey"), RESOURCE_KEY_MAX_LENGTH)
        name = _text(item.get("name"), 32)
        description = _text(item.get("description"), 128, allow_blank=True)
        origin, kind = item.get("origin"), item.get("type")
        ref = _text(item.get("targetResourceRef"), 255)
        if key in rows:
            raise ValueError("权限资源键重复。")
        _validate_resource_identity(key, origin, kind, ref)
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
        keys = [_text(ref, RESOURCE_KEY_MAX_LENGTH) for ref in refs]
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
    admins = parse_admins_for_initialization(subjects)
    for admin in admins:
        # 旧值继续使用原文本约束，新姓名按 Unicode 码点匹配数据库列长度。
        if admin.legacy:
            _text(admin.member_id, 32)
            if admin.member_id != admin.member_id.strip():
                raise ValueError("权限 Seed 初始管理员存在首尾空白。")

    role = {"role_id": ADMIN, "created_by": "SYSTEM"}
    rows = {
        "role": [{**role, "role_name": "系统管理员", "description": "内置权限系统管理员",
                  "enable": 1, "is_system": 1, "is_deleted": 0}],
        "resource": resources,
        "role_resource": [{**role, "resource_key": SYSTEM_RESOURCE_KEY}],
        "role_member": [{**role, "member_id": admin.member_id, "member_name": admin.member_name}
                        for admin in sorted(admins, key=lambda item: item.member_id)],
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


def _validate_seed_rows(logical: str, rows: list[dict]) -> None:
    """校验单表白名单、固定写入身份和自然键唯一性。"""

    columns = SEED_COLUMNS[logical]
    if not 1 <= len(rows) <= SEED_ROW_LIMITS[logical]:
        raise ValueError("权限 Seed 行数无效。")
    identities = set()
    for row in rows:
        if row.keys() != columns.keys():
            raise ValueError("权限 Seed 字段集合无效。")
        for column, limit in columns.items():
            if limit:
                _text(row[column], limit, allow_blank=logical == "resource" and column == "description",
                      code_points=True)
            elif type(row[column]) is not int:
                raise ValueError("权限 Seed 标志字段必须是整数。")
        if logical == "resource":
            identity = row["key"]
            _validate_resource_identity(identity, row["origin"], row["type"], row["target_resource_ref"])
        else:
            if (row["role_id"], row["created_by"]) != (ADMIN, "SYSTEM"):
                raise ValueError("权限 Seed 角色或审计身份无效。")
            if logical == "role" and (row["enable"], row["is_system"], row["is_deleted"]) != (1, 1, 0):
                raise ValueError("权限 Seed 系统角色标志无效。")
            if logical == "role_resource" and row["resource_key"] != SYSTEM_RESOURCE_KEY:
                raise ValueError("权限 Seed 系统授权引用无效。")
            identity = row["member_id"] if logical == "role_member" else row["role_id"]
        if identity in identities:
            raise ValueError("权限 Seed 自然键重复。")
        identities.add(identity)
    if logical == "resource" and SYSTEM_RESOURCE_KEY not in identities:
        raise ValueError("缺少系统权限管理资源。")


def validate_seed_transport(request: dict, *, datasource_key: str) -> None:
    """发起远端请求前复核四表顺序及数据，错误只包含固定提示。"""

    try:
        mapping = table_mapping(datasource_key)
        if not isinstance(request, dict) or set(request) != {"purpose", "idempotencyKey", "tables"}:
            raise ValueError("权限 Seed 请求结构无效。")
        key = request["idempotencyKey"]
        if request["purpose"] != PURPOSE or not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("权限 Seed 用途或幂等键无效。")
        tables = _objects(request["tables"])
        if len(tables) != len(mapping):
            raise ValueError("权限 Seed 必须包含固定四表。")
        for (logical, name), table in zip(mapping.items(), tables, strict=True):
            if set(table) != {"tableName", "rows"} or table["tableName"] != name:
                raise ValueError("权限 Seed 表名或顺序无效。")
            _validate_seed_rows(logical, _objects(table["rows"]))
        if len(canonical_json(request)) > MAX_BYTES:
            raise ValueError("权限 Seed 超过 SaaS 256 KiB 请求上限。")
    except ValueError as exc:
        raise SeedContractError(str(exc)) from None


def read_confirmed_seed_plan(root: Path) -> dict:
    """从正式工作区路径读取当前技术规划，不接受外部路径或旧运行快照。"""

    path = technical_plan_path(root)
    if path.resolve() != path or not path.is_file():
        raise ValueError("当前技术规划路径无效。")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("当前技术规划无效。")
    return plan
