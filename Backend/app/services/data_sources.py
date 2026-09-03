"""独立数据源目录的校验、持久化与只读检测服务。"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.persistence.data_sources import (
    DataSourceStorageError,
    data_sources_index_file,
    read_sources,
    write_sources,
)
from app.services.database_crypto import DatabaseCryptoError, decrypt_password, is_encrypted_password
from app.services.data_source_json_fields import (
    DataSourceFieldType,
    DataSourceJsonFieldError,
    JsonStructureNode,
    PATH_FIELD_TYPES,
    normalize_operation_fields,
    validate_operation_fields,
)


MAX_SOURCES = 100
MAX_DIRECTORIES = 50
MAX_OPERATIONS = 50
MAX_HEADERS = 50
MAX_SAMPLE_BYTES = 256 * 1024


class DataSourceError(ValueError):
    """表示数据源目录内容或操作不符合当前契约。"""


class DataSourceModel(BaseModel):
    """提供严格、可复用的数据源模型基类。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class HeaderEntry(DataSourceModel):
    """描述一个非敏感 HTTP Header。"""

    name: str = Field(min_length=1, max_length=128)
    value: str = Field(default="", max_length=4096)


class ApiParameter(DataSourceModel):
    """描述外部 API 的路径或查询参数。"""

    name: str = Field(min_length=1, max_length=128)
    type: DataSourceFieldType = "string"
    required: bool = False
    description: str = Field(default="", max_length=1024)


class ApiOperation(DataSourceModel):
    """描述可复用的外部 API 操作模板。"""

    id: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    method: Literal["GET", "POST", "PUT", "DELETE"]
    path: str = Field(min_length=1, max_length=2048)
    path_parameters: list[ApiParameter] = Field(default_factory=list, alias="pathParameters", max_length=50)
    query_parameters: list[ApiParameter] = Field(default_factory=list, alias="queryParameters", max_length=50)
    headers: list[HeaderEntry] = Field(default_factory=list, max_length=50)
    request_sample: Any = Field(default=None, alias="requestSample")
    response_sample: Any = Field(default=None, alias="responseSample")
    request_structure: JsonStructureNode | None = Field(default=None, alias="requestStructure")
    response_structure: JsonStructureNode | None = Field(default=None, alias="responseStructure")


class ApiDirectory(DataSourceModel):
    """描述一个外部 API 域名下的普通接口目录。"""

    id: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    operations: list[ApiOperation] = Field(default_factory=list, max_length=MAX_OPERATIONS)


class DatabaseSourceInput(DataSourceModel):
    """描述数据库数据源的创建或更新输入。"""

    type: Literal["database"]
    mode: Literal["builtin", "dbid", "direct"]
    id: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    domain: str | None = Field(default=None, max_length=512)
    port: int | None = Field(default=None, ge=1, le=65535)
    schema_name: str | None = Field(default=None, alias="schema", max_length=256)
    user_name: str | None = Field(default=None, alias="userName", max_length=256)
    dbid: str | None = Field(default=None, max_length=256)
    password_ciphertext: str | None = Field(default=None, alias="passwordCiphertext", max_length=16384)


class ExternalApiSourceInput(DataSourceModel):
    """描述外部 API 数据源的创建或更新输入。"""

    type: Literal["external_api"]
    id: str | None = Field(default=None, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=2048)
    base_url_config_key: str | None = Field(default=None, alias="baseUrlConfigKey", max_length=256)
    timeout_ms: int = Field(default=10000, alias="timeoutMs", ge=100, le=120000)
    headers: list[HeaderEntry] = Field(default_factory=list, max_length=50)
    directories: list[ApiDirectory] = Field(default_factory=list, max_length=MAX_DIRECTORIES)


DataSourceInput = DatabaseSourceInput | ExternalApiSourceInput


class DatabaseSourcePublic(DataSourceModel):
    """描述脱敏后的数据库数据源。"""

    type: Literal["database"]
    id: str
    name: str
    mode: Literal["builtin", "dbid", "direct"]
    domain: str | None = None
    port: int | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    user_name: str | None = Field(default=None, alias="userName")
    dbid: str | None = None
    has_password: bool = Field(alias="hasPassword")


class ExternalApiSourcePublic(DataSourceModel):
    """描述公开的外部 API 数据源。"""

    type: Literal["external_api"]
    id: str
    name: str
    base_url: str = Field(alias="baseUrl")
    base_url_config_key: str | None = Field(default=None, alias="baseUrlConfigKey")
    # 列表索引只保存外部 API 的展示摘要，未加载详情时使用默认超时时间。
    timeout_ms: int = Field(default=10000, alias="timeoutMs")
    headers: list[HeaderEntry] = Field(default_factory=list)
    directories: list[ApiDirectory] = Field(default_factory=list)


DataSourcePublic = DatabaseSourcePublic | ExternalApiSourcePublic


class DataSourceCatalogPublic(DataSourceModel):
    """描述返回给渲染器的独立数据源目录。"""

    sources: list[DataSourcePublic] = Field(default_factory=list, max_length=MAX_SOURCES)


def _read_catalog(
    workspace_root: str | Path,
    *,
    detail: bool = True,
    source_id: str | None = None,
    operation_id: str | None = None,
) -> list[dict[str, Any]]:
    """读取拆分后的独立目录，列表摘要和详情按需选择。"""

    _ensure_catalog_initialized(workspace_root)
    try:
        sources = read_sources(
            workspace_root,
            detail=detail,
            source_id=source_id,
            operation_id=operation_id,
        )
    except DataSourceStorageError as exc:
        raise DataSourceError(str(exc)) from exc
    if detail and source_id is None:
        _validate_stored_sources(sources)
    elif detail and source_id is not None:
        target = next((item for item in sources if str(item.get("id")) == source_id), None)
        if target is None:
            raise DataSourceError("目标数据源不存在。")
        if operation_id is None:
            _validate_source(target, stored=True)
        else:
            # 按接口读取时，其余接口只是索引摘要，不能执行完整的参数和样例校验。
            target_copy = dict(target)
            target_copy["directories"] = [
                {**directory, "operations": [
                    item for item in directory.get("operations", []) if item.get("id") == operation_id
                ]}
                for directory in target.get("directories", [])
            ]
            _validate_source(target_copy, stored=True)
    return sources


def _ensure_catalog_initialized(workspace_root: str | Path) -> None:
    """在独立目录首次读取时导入当前应用数据库，并用目录文件作为初始化哨兵。"""

    try:
        target = data_sources_index_file(workspace_root, create=False)
    except DataSourceStorageError as exc:
        raise DataSourceError(str(exc)) from exc
    if target.exists() or target.is_symlink():
        return

    root = Path(workspace_root).expanduser().resolve()
    application_file = root / ".xcodeagent" / "application.json"
    if application_file.is_symlink():
        raise DataSourceError("应用配置文件不允许使用符号链接。")
    if application_file.is_file():
        try:
            application = json.loads(application_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataSourceError("应用配置文件无法读取或格式无效，无法初始化数据源目录。") from exc
        if not isinstance(application, dict):
            raise DataSourceError("应用配置文件必须是 JSON 对象，无法初始化数据源目录。")
    else:
        application = None

    imported_source = _database_source_from_application(application)
    # 再次确认目标不存在，避免初始化期间覆盖另一个请求刚写入的独立目录。
    if target.exists() or target.is_symlink():
        return
    _write_catalog(root, [imported_source] if imported_source else [])


def _database_source_from_application(application: dict[str, Any] | None) -> dict[str, Any] | None:
    """把当前 application.json 的应用级数据库映射成独立目录资源。"""

    if application is None:
        return None
    if "datasource" not in application:
        raise DataSourceError("应用配置缺少 datasource，无法初始化数据源目录。")
    datasource = application.get("datasource")
    if not isinstance(datasource, dict):
        raise DataSourceError("应用配置 datasource 必须是对象，无法初始化数据源目录。")
    source_type = str(datasource.get("type") or "").strip().lower()
    if source_type not in {"database", "static", "external_api"}:
        raise DataSourceError("应用配置 datasource.type 无效，无法初始化数据源目录。")
    if source_type != "database":
        return None

    database = datasource.get("db")
    if database is None:
        return None
    if not isinstance(database, dict):
        raise DataSourceError("应用配置 datasource.db 必须是对象，无法初始化数据源目录。")

    use_builtin = database.get("useBuiltin")
    if use_builtin is not None and not isinstance(use_builtin, bool):
        raise DataSourceError("应用配置 datasource.db.useBuiltin 必须是布尔值。")
    dbid_mode = database.get("dbidMode")
    plant_mode = database.get("plantMode")
    if dbid_mode is not None and not isinstance(dbid_mode, dict):
        raise DataSourceError("应用配置 datasource.db.dbidMode 必须是对象。")
    if plant_mode is not None and not isinstance(plant_mode, dict):
        raise DataSourceError("应用配置 datasource.db.plantMode 必须是对象。")
    if dbid_mode is not None and plant_mode is not None:
        raise DataSourceError("应用配置同时包含 DBID 和直连数据库配置，无法初始化数据源目录。")

    app_name = str(application.get("appName") or "").strip()
    source_name = f"{app_name}数据库" if app_name else "应用数据库"
    source_base: dict[str, Any] = {
        "id": "application-database",
        "type": "database",
        "name": source_name[:256],
    }
    if use_builtin is True:
        if dbid_mode is not None or plant_mode is not None:
            raise DataSourceError("平台内置数据库不应同时包含外部连接配置。")
        source_base["mode"] = "builtin"
        return _normalize_source(source_base)
    if use_builtin is not False:
        if dbid_mode is None and plant_mode is None:
            return None
        raise DataSourceError("包含外部数据库连接配置时必须明确 useBuiltin=false。")

    if dbid_mode is not None:
        source = {
            **source_base,
            "mode": "dbid",
            "dbid": _required_application_text(dbid_mode.get("dbid"), "DBID"),
            "domain": _required_application_text(dbid_mode.get("domain"), "数据库地址"),
            "port": _required_application_port(dbid_mode.get("port")),
            "schema": _required_application_text(dbid_mode.get("schema"), "Schema"),
            "userName": _required_application_text(dbid_mode.get("userName"), "用户名"),
        }
        return _normalize_source(source)

    if plant_mode is None:
        raise DataSourceError("应用配置缺少 DBID 或直连数据库配置，无法初始化数据源目录。")
    password_ciphertext = plant_mode.get("pwd")
    if not isinstance(password_ciphertext, str) or not is_encrypted_password(password_ciphertext):
        raise DataSourceError("应用数据库密码不是当前平台加密密文，无法安全导入独立数据源目录。")
    source = {
        **source_base,
        "mode": "direct",
        "domain": _required_application_text(plant_mode.get("domain"), "数据库地址"),
        "port": _required_application_port(plant_mode.get("port")),
        "schema": _required_application_text(plant_mode.get("schema"), "Schema"),
        "userName": _required_application_text(plant_mode.get("userName"), "用户名"),
        "passwordCiphertext": password_ciphertext,
    }
    return _normalize_source(source)


def _required_application_text(value: Any, label: str) -> str:
    """读取应用数据库导入所需的非空文本字段。"""

    if not isinstance(value, str) or not value.strip():
        raise DataSourceError(f"应用数据库的{label}不能为空，无法初始化数据源目录。")
    return value.strip()


def _required_application_port(value: Any) -> int:
    """读取并校验应用数据库导入所需的端口字段。"""

    if isinstance(value, bool):
        raise DataSourceError("应用数据库端口必须是 1 到 65535 的整数。")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise DataSourceError("应用数据库端口必须是 1 到 65535 的整数。") from exc
    if not 1 <= port <= 65535:
        raise DataSourceError("应用数据库端口必须是 1 到 65535 的整数。")
    return port


def _write_catalog(workspace_root: str | Path, sources: list[dict[str, Any]]) -> None:
    """将完整数据源目录委托给分文件存储层写入。"""

    try:
        write_sources(workspace_root, sources)
    except DataSourceStorageError as exc:
        raise DataSourceError(str(exc)) from exc


def _validate_header_entries(headers: list[HeaderEntry]) -> None:
    """校验 Header 名称唯一且不包含敏感凭据。"""

    sensitive = {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
    }
    names: set[str] = set()
    for header in headers:
        normalized = header.name.strip().lower()
        if not normalized or normalized in names:
            raise DataSourceError("Header 名称不能为空且不能重复。")
        if normalized in sensitive:
            raise DataSourceError("外部 API 暂不支持保存鉴权或敏感 Header。")
        names.add(normalized)


def _validate_operation(operation: ApiOperation, *, stored: bool = False) -> None:
    """校验一个外部 API 操作模板的结构与安全边界。"""

    if not operation.path.startswith("/"):
        raise DataSourceError("外部 API 操作路径必须以 / 开头。")
    placeholders = set(re.findall(r"\{([^{}\/]+)\}", operation.path))
    remainder = re.sub(r"\{([^{}\/]+)\}", "", operation.path)
    path_names = {parameter.name.strip() for parameter in operation.path_parameters}
    if "{" in remainder or "}" in remainder or placeholders != path_names:
        raise DataSourceError("API 路径占位符必须与 Path 参数一一对应。")
    if len(operation.path_parameters) + len(operation.query_parameters) > 50:
        raise DataSourceError("同一 API 操作的 Path 和 Query 参数合计不能超过 50 个。")
    for location, parameters in (("path", operation.path_parameters), ("query", operation.query_parameters)):
        parameter_names: set[str] = set()
        for parameter in parameters:
            if not parameter.name.strip():
                raise DataSourceError("API 参数名称不能为空。")
            if location == "path":
                if not parameter.required:
                    raise DataSourceError("Path 参数必须为必填。")
                if parameter.type not in PATH_FIELD_TYPES:
                    raise DataSourceError("Path 参数类型只能为 string、integer、number 或 boolean。")
            key = parameter.name.strip().lower()
            if key in parameter_names:
                raise DataSourceError("同一位置的 API 参数不能重复。")
            parameter_names.add(key)
    _validate_header_entries(operation.headers)
    for sample in (operation.request_sample, operation.response_sample):
        if sample is not None:
            try:
                encoded = json.dumps(sample, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise DataSourceError("API 请求/响应样例必须是可序列化 JSON。") from exc
            if len(encoded) > MAX_SAMPLE_BYTES:
                raise DataSourceError("API 请求/响应样例不能超过 256 KiB。")
    try:
        validate_operation_fields(operation.model_dump(by_alias=True, exclude_none=True), stored=stored)
    except DataSourceJsonFieldError as exc:
        raise DataSourceError(str(exc)) from exc


def _validate_source(source: dict[str, Any], *, stored: bool = False) -> None:
    """校验并规范单个内部数据源对象。"""

    source_type = str(source.get("type") or "")
    if source_type == "database":
        request = DatabaseSourceInput.model_validate(source)
        if request.mode == "direct":
            if not request.domain or request.port is None or not request.schema_name or not request.user_name:
                raise DataSourceError("直连数据库必须填写地址、端口、Schema 和用户名。")
            if not request.password_ciphertext or not is_encrypted_password(request.password_ciphertext):
                raise DataSourceError("直连数据库密码必须使用平台加密密文。")
        elif request.mode == "dbid":
            if not request.dbid or not request.domain or request.port is None or not request.schema_name or not request.user_name:
                raise DataSourceError("DBID 数据库必须填写 DBID、地址、端口、Schema 和用户名。")
            if request.password_ciphertext:
                raise DataSourceError("DBID 数据库不应保存直连密码。")
        elif request.password_ciphertext or any(
            value not in (None, "") for value in (request.domain, request.port, request.schema_name, request.user_name, request.dbid)
        ):
            raise DataSourceError("内置数据库不应包含外部连接字段。")
        return
    if source_type == "external_api":
        request = ExternalApiSourceInput.model_validate(source)
        if not request.base_url.strip():
            raise DataSourceError("外部 API Base URL 或域名不能为空。")
        _validate_header_entries(request.headers)
        directory_ids: set[str] = set()
        operation_ids: set[str] = set()
        operation_count = 0
        for directory in request.directories:
            if directory.id and directory.id in directory_ids:
                raise DataSourceError("外部 API 目录 ID 不能重复。")
            if directory.id:
                directory_ids.add(directory.id)
            if not directory.name.strip():
                raise DataSourceError("外部 API 目录名称不能为空。")
            for operation in directory.operations:
                operation_count += 1
                if operation.id and operation.id in operation_ids:
                    raise DataSourceError("外部 API 操作 ID 不能重复。")
                if operation.id:
                    operation_ids.add(operation.id)
                _validate_operation(operation, stored=stored)
        if operation_count > MAX_OPERATIONS:
            raise DataSourceError(f"单个外部 API 域名下的接口不能超过 {MAX_OPERATIONS} 个。")
        return
    raise DataSourceError("数据源类型必须是 database 或 external_api。")


def _ensure_operation_ids(source: dict[str, Any]) -> dict[str, Any]:
    """为外部 API 目录和接口补齐稳定 ID，便于前端编辑和后续引用。"""

    if source.get("type") != "external_api":
        return source
    normalized = dict(source)
    directories = []
    operation_index = 1
    for directory_index, directory in enumerate(source.get("directories") or [], start=1):
        directory_item = dict(directory)
        directory_item["id"] = str(directory_item.get("id") or f"directory-{directory_index}")
        operations = []
        for operation in directory_item.get("operations") or []:
            item = dict(operation)
            item["id"] = str(item.get("id") or f"operation-{operation_index}")
            operation_index += 1
            operations.append(item)
        directory_item["operations"] = operations
        directories.append(directory_item)
    normalized["directories"] = directories
    return normalized


def _add_default_directory_on_create(source: dict[str, Any]) -> dict[str, Any]:
    """为新建外部 API 域名补充一个可普通编辑的默认目录。"""

    if source.get("type") != "external_api":
        return source
    directories = [dict(item) for item in source.get("directories") or []]
    if not any(str(item.get("name") or "").strip().lower() == "默认目录" for item in directories):
        directories.insert(
            0,
            {
                "id": f"directory-{uuid4().hex[:16]}",
                "name": "默认目录",
                "operations": [],
            },
        )
    return {**source, "directories": directories}


def _normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    """按当前别名和默认值规范化待保存的数据源对象。"""

    candidate = _ensure_operation_ids(dict(source))
    if candidate.get("type") == "database":
        normalized = DatabaseSourceInput.model_validate(candidate).model_dump(
            by_alias=True, exclude_none=True
        )
    elif candidate.get("type") == "external_api":
        candidate["name"] = str(candidate.get("name") or "").strip()
        candidate["baseUrl"] = str(candidate.get("baseUrl") or "").strip()
        candidate["directories"] = [
            {**directory, "name": str(directory.get("name") or "").strip()}
            for directory in candidate.get("directories") or []
        ]
        normalized = ExternalApiSourceInput.model_validate(candidate).model_dump(
            by_alias=True, exclude_none=True
        )
        for directory in normalized.get("directories") or []:
            try:
                directory["operations"] = [
                    normalize_operation_fields(operation) for operation in directory.get("operations", [])
                ]
            except DataSourceJsonFieldError as exc:
                raise DataSourceError(str(exc)) from exc
    else:
        return candidate
    if source.get("id"):
        normalized["id"] = str(source["id"])
    return normalized


def _validate_stored_sources(sources: list[dict[str, Any]]) -> None:
    """校验目录中的资源数量、名称和每个资源的当前契约。"""

    if len(sources) > MAX_SOURCES:
        raise DataSourceError(f"数据源数量不能超过 {MAX_SOURCES} 个。")
    ids: set[str] = set()
    names: set[tuple[str, str]] = set()
    database_count = 0
    for source in sources:
        source_id = str(source.get("id") or "").strip()
        if not source_id or source_id in ids:
            raise DataSourceError("数据源 ID 必须存在且唯一。")
        ids.add(source_id)
        source_type = str(source.get("type") or "")
        name_key = (source_type, str(source.get("name") or "").strip().lower())
        if not name_key[1] or name_key in names:
            raise DataSourceError("同类数据源名称必须存在且唯一。")
        names.add(name_key)
        if source_type == "database":
            database_count += 1
        if source_type == "external_api":
            directory_names: set[str] = set()
            for directory in source.get("directories") or []:
                directory_name = str(directory.get("name") or "").strip().lower()
                if directory_name in directory_names:
                    raise DataSourceError("同一外部 API 域名下的目录名称不能重复。")
                directory_names.add(directory_name)
        _validate_source(source, stored=True)
    if database_count > 1:
        raise DataSourceError("当前应用最多配置一个数据库数据源。")


def _public_source(source: dict[str, Any]) -> DataSourcePublic:
    """把内部资源转换成不泄露密码的公开模型。"""

    if source.get("type") == "database":
        return DatabaseSourcePublic(
            type="database",
            id=str(source["id"]),
            name=str(source["name"]),
            mode=str(source.get("mode") or "direct"),
            domain=source.get("domain"),
            port=source.get("port"),
            schema=source.get("schema"),
            userName=source.get("userName"),
            dbid=source.get("dbid"),
            hasPassword=bool(source.get("passwordCiphertext") or source.get("hasPassword")),
        )
    return ExternalApiSourcePublic.model_validate(source)


def public_catalog(
    workspace_root: str | Path,
    *,
    source_id: str | None = None,
    operation_id: str | None = None,
) -> DataSourceCatalogPublic:
    """读取列表摘要，或按指定数据源和接口读取详情。"""

    detail = source_id is not None
    sources = _read_catalog(
        workspace_root,
        detail=detail,
        source_id=source_id,
        operation_id=operation_id,
    )
    if operation_id is not None:
        target = next((item for item in sources if str(item.get("id")) == source_id), None)
        if not target or target.get("type") != "external_api":
            raise DataSourceError("目标接口不存在。")
        if not any(
            str(operation.get("id")) == operation_id
            for directory in target.get("directories") or []
            for operation in directory.get("operations") or []
        ):
            raise DataSourceError("目标接口不存在。")
    return DataSourceCatalogPublic(sources=[_public_source(source) for source in sources])


def mutate_catalog(
    workspace_root: str | Path,
    *,
    action: Literal["create", "update", "delete"],
    source: dict[str, Any] | None = None,
    source_id: str | None = None,
) -> DataSourceCatalogPublic:
    """执行一次独立数据源目录变更，不启用目录版本或并发冲突保护。"""

    sources = _read_catalog(workspace_root)
    next_sources = [dict(item) for item in sources]
    if action == "create":
        if source is None:
            raise DataSourceError("创建数据源必须提供 source。")
        candidate = _normalize_source(dict(source))
        candidate["id"] = str(candidate.get("id") or f"ds-{uuid4().hex[:16]}")
        candidate = _add_default_directory_on_create(candidate)
        candidate = _normalize_source(candidate)
        _validate_source(candidate)
        next_sources.append(candidate)
    elif action == "update":
        if source is None or not source.get("id"):
            raise DataSourceError("更新数据源必须提供 source.id。")
        source_key = str(source["id"])
        if not any(str(item.get("id")) == source_key for item in next_sources):
            raise DataSourceError("目标数据源不存在。")
        candidate = _normalize_source(dict(source))
        current_sources = next_sources
        next_sources = []
        for item in current_sources:
            if str(item.get("id")) != source_key:
                next_sources.append(item)
                continue
            merged = dict(candidate)
            if (
                candidate.get("type") == "database"
                and candidate.get("mode") == "direct"
                and not candidate.get("passwordCiphertext")
                and item.get("passwordCiphertext")
            ):
                merged["passwordCiphertext"] = item["passwordCiphertext"]
            next_sources.append(merged)
    elif action == "delete":
        if not source_id:
            raise DataSourceError("删除数据源必须提供 sourceId。")
        if not any(str(item.get("id")) == source_id for item in next_sources):
            raise DataSourceError("目标数据源不存在。")
        next_sources = [item for item in next_sources if str(item.get("id")) != source_id]
    else:
        raise DataSourceError("不支持的数据源目录动作。")
    _validate_stored_sources(next_sources)
    _write_catalog(workspace_root, next_sources)
    return DataSourceCatalogPublic(sources=[_public_source(item) for item in next_sources])


def validate_saved_source(workspace_root: str | Path, source_id: str) -> dict[str, Any]:
    """读取目录内的单个资源并执行校验，避免向前端返回密码密文。"""

    sources = _read_catalog(workspace_root)
    source = next((item for item in sources if str(item.get("id")) == source_id), None)
    if source is None:
        raise DataSourceError("目标数据源不存在。")
    return validate_source(source, workspace_root)


def validate_source(source: dict[str, Any], workspace_root: str | Path | None = None) -> dict[str, Any]:
    """校验单个数据源，并对直连数据库执行只读连接检测。"""

    candidate = _source_with_stored_password(source, workspace_root)
    _validate_source(candidate)
    if candidate.get("type") != "database" or candidate.get("mode") != "direct":
        return {"valid": True, "connection": "not_tested"}
    password_ciphertext = str(candidate.get("passwordCiphertext") or "")
    try:
        password = decrypt_password(password_ciphertext)
    except DatabaseCryptoError as exc:
        raise DataSourceError(
            "数据库密码已加密，但当前环境无法解密；请确认使用当前后端的加密公钥，或重新保存密码。"
        ) from exc
    try:
        import pymysql

        connection = pymysql.connect(
            host=str(candidate["domain"]),
            port=int(candidate["port"]),
            user=str(candidate["userName"]),
            password=password,
            database=str(candidate["schema"]),
            connect_timeout=5,
            read_timeout=5,
        )
        connection.close()
    except ImportError as exc:
        raise DataSourceError("数据库连接检测不可用：后端未安装 PyMySQL 驱动。") from exc
    except Exception as exc:
        raise DataSourceError(_database_connection_error_message(exc)) from exc
    return {"valid": True, "connection": "ok"}


def _source_with_stored_password(
    source: dict[str, Any], workspace_root: str | Path | None
) -> dict[str, Any]:
    """编辑直连数据库且密码留空时，从独立目录恢复已保存的密文。"""

    if (
        source.get("type") != "database"
        or source.get("mode") != "direct"
        or source.get("passwordCiphertext")
        or not source.get("id")
        or workspace_root is None
    ):
        return source
    sources = _read_catalog(workspace_root)
    source_id = str(source["id"])
    stored = next(
        (
            item
            for item in sources
            if str(item.get("id")) == source_id and item.get("type") == "database"
        ),
        None,
    )
    stored_password = stored.get("passwordCiphertext") if stored else None
    if not stored_password:
        return source
    return {**source, "passwordCiphertext": stored_password}


def _database_connection_error_message(error: Exception) -> str:
    """将 MySQL 驱动异常转换为不暴露底层类名或密码的明确提示。"""

    error_code = _mysql_error_code(error)
    if error_code in {1045, 1698}:
        return "数据库连接失败：用户名或密码错误，或当前用户没有登录权限。"
    if error_code == 1044:
        return "数据库连接失败：当前用户没有访问该 Schema 的权限。"
    if error_code == 1049:
        return "数据库连接失败：目标 Schema 不存在，请检查 Schema 名称。"
    if error_code in {2002, 2003, 2005, 2006, 2013, 2055}:
        return "数据库连接失败：无法连接到数据库服务器，请检查地址、端口以及 MySQL 服务是否启动。"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "数据库连接失败：连接超时，请检查地址、端口和网络是否可达。"
    if isinstance(error, (ConnectionError, OSError)):
        return "数据库连接失败：无法解析或连接数据库地址，请检查地址、端口和网络是否可达。"
    return "数据库连接失败：数据库服务器拒绝了连接，请检查地址、端口、Schema、用户名和密码。"


def _mysql_error_code(error: Exception) -> int | None:
    """读取 PyMySQL 异常中的数字错误码，不读取或返回敏感连接内容。"""

    arguments = getattr(error, "args", ())
    if not arguments:
        return None
    value = arguments[0]
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
