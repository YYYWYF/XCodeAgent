"""在验收完成时输出版本数据库脚本，升级基线只来自本地已交付结构。"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from threading import RLock

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.database_delivery import DatabaseDeliveries, DeliveredDatabase, DeliveryTable
from app.domain.managed_database import ManagedDatabaseRegistration
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.database_delivery_sql import full_schema_sql, incremental_schema_sql
from app.services.entity_table_binding import bind_plan_entity_tables
from app.services.entity_table_sql import compile_entity_table
from app.services.git_branch import validate_branch_name
from app.workspace.endpoint_design_documents import technical_plan_path

_lock = RLock()
_HISTORY_NAME = "database-deliveries.json"


def _safe_path(root: Path, *parts: str) -> Path:
    """约束全部输出和基线都在当前工作区内，拒绝通过软链或相对段越界。"""

    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError("数据库交付路径超出当前工作区。")
    for path in (target, *target.parents):
        if path == root:
            break
        if path.is_symlink():
            raise ValueError("数据库交付路径不能使用软链接。")
    return target


def _version_directory(root: Path, branch_name: str) -> Path:
    """以真实分支名组织版本目录，带斜杠的合法分支保留层级，不静默改名。"""

    if validate_branch_name(branch_name) != branch_name or any(part in {"", ".", ".."} for part in branch_name.split("/")):
        raise ValueError("数据库交付版本名不能包含首尾空白。")
    return _safe_path(root, "backend", "db", branch_name)


def _digest(content: bytes) -> str:
    """计算已交付文件摘要，识别手工修改，避免重试时覆盖用户脚本。"""

    return hashlib.sha256(content).hexdigest()


def _verify_files(root: Path, version: DeliveredDatabase) -> None:
    """核对基线结构和两个交付文件，缺失或被修改时明确停止。"""

    directory = _version_directory(root, version.branchName)
    for name, expected in (("create.sql", version.createHash), ("alter.sql", version.alterHash)):
        path = _safe_path(root, str(directory.relative_to(root)), name)
        if not path.is_file() or _digest(path.read_bytes()) != expected:
            raise ValueError(f"数据库交付文件 {path.relative_to(root)} 缺失或已被修改，未覆盖文件。")
    if _digest(full_schema_sql(version.tables).encode("utf-8")) != version.createHash:
        raise ValueError("数据库交付结构快照与完整建表脚本不一致。")


def _stage_file(path: Path, content: bytes) -> Path:
    """在目标同目录暂存并落盘，替换时不会留下半个 SQL 或 JSON 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".database-delivery-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _write_files(contents: dict[Path, bytes]) -> None:
    """先暂存全部文件，失败时恢复本次已替换内容；快照最后提交。"""

    before = {path: path.read_bytes() if path.exists() else None for path in contents}
    staged: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for path, content in contents.items():
            if before[path] != content:
                staged[path] = _stage_file(path, content)
        for path, temporary in staged.items():
            os.replace(temporary, path)
            committed.append(path)
    except OSError:
        for path in reversed(committed):
            if before[path] is None:
                path.unlink(missing_ok=True)
            else:
                rollback = _stage_file(path, before[path])
                try:
                    os.replace(rollback, path)
                finally:
                    rollback.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _current_tables(application: dict, plan: dict) -> list[DeliveryTable]:
    """只编译已确认且固定表名的完整实体清单，不读取 SaaS 或真实数据库。"""

    if not isinstance(plan, dict) or plan.get("artifact_type") != "technical-plan" \
            or plan.get("confirmation_status") != "confirmed":
        raise ValueError("请先确认当前技术规划，再交付数据库脚本。")
    entities = plan.get("entities")
    if not isinstance(entities, list) or any(not isinstance(entity, dict) for entity in entities):
        raise ValueError("数据库交付需要完整的当前实体清单。")
    if bind_plan_entity_tables(plan, application).get("entities") != entities:
        raise ValueError("实体尚未固定完整表名，请重新确认技术规划。")
    tables = []
    for entity in entities:
        table = compile_entity_table(entity)
        tables.append(DeliveryTable(name=table["name"], comment=table["comment"], columns=table["columns"]))
    if len({table.name for table in tables}) != len(tables):
        raise ValueError("当前实体包含重复表名，不能生成交付脚本。")
    return sorted(tables, key=lambda table: table.name)


def export_database_scripts(workspace: str | Path) -> list[str]:
    """输出当前内置数据库版本的两个脚本；其他应用不改变既有交付流程。"""

    root = Path(workspace).expanduser().resolve()
    application_path = _safe_path(root, str(WORKSPACE_ARTIFACT_DIR), "application.json")
    if not application_path.is_file():
        return []
    with _lock:
        original_application = application_path.read_bytes()
        application = ApplicationConfigRepository(root).load()
        datasource = application.get("datasource") or {}
        if datasource.get("type") != "database" or (datasource.get("db") or {}).get("useBuiltin") is not True:
            return []
        if not _safe_path(root, "backend").is_dir():
            raise ValueError("当前工程缺少 backend 目录，不能交付数据库脚本。")
        binding = ManagedDatabaseRegistration.model_validate(application.get("managedDatabase") or {})
        if binding.status != "REGISTERED":
            raise ValueError("内置数据库尚未注册成功，不能交付数据库脚本。")
        branch = application.get("branchName")
        if not isinstance(branch, str):
            raise ValueError("当前应用缺少 branchName，不能确定脚本版本目录。")
        directory = _version_directory(root, branch)
        plan_path = _safe_path(root, str(technical_plan_path(root).relative_to(root)))
        original_plan = plan_path.read_bytes()
        tables = _current_tables(application, json.loads(original_plan))
        history_path = _safe_path(root, str(WORKSPACE_ARTIFACT_DIR), _HISTORY_NAME)
        original_history = history_path.read_bytes() if history_path.exists() else None
        history = DatabaseDeliveries.model_validate_json(original_history) if original_history is not None else DatabaseDeliveries()
        # 版本由交付顺序决定，不按目录字典序或分支名大小猜测上一版。
        if not history.versions and _safe_path(root, "backend", "db").exists() \
                and any((root / "backend/db").rglob("create.sql")):
            raise ValueError("已有数据库交付脚本但缺少结构基线，不能按首版覆盖或生成空增量。")
        if any(version.datasourceKey != binding.datasource_key for version in history.versions):
            raise ValueError("数据库资源绑定与已交付版本不同，不能自动生成跨资源升级脚本。")
        existing = next((version for version in history.versions if version.branchName == branch), None)
        if existing is not None:
            _verify_files(root, existing)
            if existing != history.versions[-1]:
                if tables == existing.tables:
                    return [str((directory / name).relative_to(root)) for name in ("create.sql", "alter.sql")]
                raise ValueError("不能覆盖后续版本已依赖的旧版脚本，请在新版本中交付变更。")
            previous = history.versions[-2] if len(history.versions) > 1 else None
        else:
            previous = history.versions[-1] if history.versions else None
        if previous is not None:
            _verify_files(root, previous)
        create = full_schema_sql(tables)
        alter = incremental_schema_sql(previous.tables, tables) if previous else ""
        version = DeliveredDatabase(branchName=branch, baseBranchName=previous.branchName if previous else None,
                                    datasourceKey=binding.datasource_key, tables=tables,
                                    createHash=_digest(create.encode("utf-8")), alterHash=_digest(alter.encode("utf-8")))
        if existing is not None:
            history.versions[-1] = version
        else:
            history.versions.append(version)
        DatabaseDeliveries.model_validate(history.model_dump())
        files = {_safe_path(root, str(directory.relative_to(root)), name): content.encode("utf-8")
                 for name, content in (("create.sql", create), ("alter.sql", alter))}
        if existing is None and any(path.exists() for path in files):
            raise ValueError("当前版本目录已有未登记的脚本，未覆盖用户文件。")
        if application_path.read_bytes() != original_application or plan_path.read_bytes() != original_plan \
                or (history_path.read_bytes() if history_path.exists() else None) != original_history:
            raise ValueError("交付期间应用配置、实体设计或基线发生变化，请重新交付。")
        files[history_path] = (history.model_dump_json(indent=2) + "\n").encode("utf-8")
        _write_files(files)
        return [str(path.relative_to(root)) for path in files if path != history_path]
