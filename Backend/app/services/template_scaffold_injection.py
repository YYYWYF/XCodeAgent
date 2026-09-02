"""从已确认 TechnicalPlan 确定性推导后端骨架代码并幂等写入模板工程。

在二次修改进入开发阶段前（TechnicalPlan 确认后、签发 revision_continuation 之前），
根据 TechnicalPlan 的 entities 和 api_contracts，把可确定性推导的后端骨架代码
（Entity/PO/Mapper/Repository/DTO/Controller）直接写入模板工程，让开发阶段
Agent 只需补充业务逻辑，不必从零生成这些确定性文件。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

# 后端工程根在工作区中的相对位置
_BACKEND_ROOT = Path("backend")
# Java 基础包路径
_BASE_PACKAGE = "com/cmbchina/backend"
_BASE_JAVA_PACKAGE = "com.cmbchina.backend"


# ---------------------------------------------------------------------------
# 字段类型映射
# ---------------------------------------------------------------------------

_FIELD_TYPE_TO_JAVA: dict[str, str] = {
    "text": "String",
    "string": "String",
    "long_text": "String",
    "richtext": "String",
    "json": "String",
    "integer": "Integer",
    "int": "Integer",
    "number": "Integer",
    "long": "Long",
    "bigint": "Long",
    "decimal": "BigDecimal",
    "double": "BigDecimal",
    "float": "BigDecimal",
    "money": "BigDecimal",
    "boolean": "Boolean",
    "bool": "Boolean",
    "date": "LocalDate",
    "datetime": "LocalDateTime",
    "timestamp": "LocalDateTime",
    "time": "LocalTime",
    "enum": "String",
    "uuid": "String",
}


def _java_type(field_type: str) -> str:
    """把 TechnicalPlan 字段类型映射为 Java 类型，未知类型回退 String。"""

    return _FIELD_TYPE_TO_JAVA.get(str(field_type or "").strip().lower(), "String")


def _needs_bigdecimal(field_type: str) -> bool:
    return _java_type(field_type) == "BigDecimal"


def _needs_localdate(field_type: str) -> bool:
    return _java_type(field_type) == "LocalDate"


def _needs_localtime(field_type: str) -> bool:
    return _java_type(field_type) == "LocalTime"


# ---------------------------------------------------------------------------
# 命名转换
# ---------------------------------------------------------------------------

def _to_pascal_case(name: str) -> str:
    """snake_case → PascalCase：project_member → ProjectMember。"""

    parts = re.split(r"[_\s-]+", str(name or "").strip())
    return "".join(part[:1].upper() + part[1:] for part in parts if part)


def _to_camel_case(name: str) -> str:
    """snake_case → camelCase：project_name → projectName。"""

    pascal = _to_pascal_case(name)
    return pascal[:1].lower() + pascal[1:] if pascal else ""


def _to_snake_case(name: str) -> str:
    """camelCase → snake_case：projectName → project_name。"""

    text = str(name or "").strip()
    result = re.sub(r"([A-Z])", r"_\1", text).lower()
    return result[1:] if result.startswith("_") else result


def _module_name(entity_id: str) -> str:
    """从实体 id 推导模块包名：Project → project，ProjectMember → project。"""

    pascal = _to_pascal_case(entity_id)
    # ProjectMember 归到 project 模块；OrderItem 归到 order 模块
    # 取第一个单词作为模块名，除非实体名就是单个单词
    parts = re.findall(r"[A-Z][a-z]*", pascal)
    if len(parts) <= 1:
        return pascal[:1].lower() + pascal[1:] if pascal else "default"
    return parts[0].lower()


def _table_name(entity_id: str) -> str:
    """从实体 id 推导表名：Project → project，ProjectMember → project_member。"""

    return _to_snake_case(entity_id)


# ---------------------------------------------------------------------------
# 实体字段收集
# ---------------------------------------------------------------------------

def _collect_entity_fields(
    entity: dict[str, Any],
    detail_plan: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """从 entity 和 entity_detail_plan 收集字段，优先用 detail_plan 的数据库绑定。"""

    fields: list[dict[str, str]] = []
    raw_fields = entity.get("fields") if isinstance(entity.get("fields"), list) else []
    # detail_plan 的 database_design.bindings 提供表列映射
    bindings_map: dict[str, str] = {}
    if detail_plan:
        db_design = detail_plan.get("database_design") if isinstance(detail_plan.get("database_design"), dict) else {}
        for binding in db_design.get("bindings") or []:
            if not isinstance(binding, dict):
                continue
            entity_field = str(binding.get("entity_field") or "").strip()
            table_column = str(binding.get("table_column") or "").strip()
            if entity_field and table_column:
                bindings_map[entity_field] = table_column
    for field in raw_fields:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        field_name = str(field.get("name") or "").strip()
        fields.append({
            "name": field_name,
            "java_name": _to_camel_case(field_name),
            "java_type": _java_type(str(field.get("type") or "text")),
            "column": bindings_map.get(field_name, _to_snake_case(field_name)),
            "required": bool(field.get("required")),
            "label": str(field.get("label") or field_name),
        })
    return fields


def _entity_detail_map(project_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """建立 entity_id → entity_detail_plan 的映射。"""

    result: dict[str, dict[str, Any]] = {}
    for detail in project_plan.get("entity_detail_plans") or []:
        if not isinstance(detail, dict):
            continue
        entity_id = str(detail.get("entity_id") or "").strip()
        if entity_id and str(detail.get("status") or "") == "confirmed":
            result[entity_id] = detail
    return result


# ---------------------------------------------------------------------------
# 幂等文件写入
# ---------------------------------------------------------------------------

def _write_file_atomically(path: Path, content: str) -> bool:
    """原子写入文件，返回是否实际写入（文件不存在或内容不同时才写）。"""

    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except (OSError, UnicodeError):
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return True


# ---------------------------------------------------------------------------
# 代码生成：Entity
# ---------------------------------------------------------------------------

def _render_entity(module: str, entity_id: str, fields: list[dict[str, str]]) -> str:
    """生成领域实体 Java 源码。"""

    class_name = _to_pascal_case(entity_id)
    imports = _collect_imports(fields, include_lombok_data=True)
    field_lines = []
    for field in fields:
        field_lines.append(f"    private {field['java_type']} {field['java_name']};")
    field_block = "\n".join(field_lines)
    return f"""package {_BASE_JAVA_PACKAGE}.{module}.domain.entity;

{imports}
/**
 * {class_name} 领域实体。
 */
@Data
public class {class_name} {{
{field_block}
}}
"""


# ---------------------------------------------------------------------------
# 代码生成：PO
# ---------------------------------------------------------------------------

def _render_po(module: str, entity_id: str, fields: list[dict[str, str]]) -> str:
    """生成持久化对象 Java 源码。"""

    class_name = _to_pascal_case(entity_id)
    table_name = _table_name(entity_id)
    imports = _collect_imports(
        fields, include_lombok_data=True, include_mybatis_plus=True
    )
    field_lines = ["    @TableId(value = \"id\", type = IdType.AUTO)", "    private Integer id;"]
    # 实体里可能已含审计字段，跳过避免重复（审计字段由下方统一追加）
    audit_names = {"id", "createdAt", "updatedAt", "createdBy", "updatedBy", "isDeleted", "deletedAt", "deletedBy"}
    for field in fields:
        java_name = field["java_name"]
        if java_name in audit_names:
            continue
        column = field["column"]
        # 字段名与列名一致时不需要 @TableField
        if java_name == column or _to_snake_case(java_name) == column:
            field_lines.append(f"    private {field['java_type']} {java_name};")
        else:
            field_lines.append(f'    @TableField("{column}")')
            field_lines.append(f"    private {field['java_type']} {java_name};")
    # 审计字段（统一追加，确保每个 PO 都有）
    field_lines.extend([
        "    private LocalDateTime createdAt;",
        "    private String createdBy;",
        "    private LocalDateTime updatedAt;",
        "    private String updatedBy;",
        '    @TableField("is_deleted")',
        "    @TableLogic(value = \"0\", delval = \"1\")",
        "    private Boolean isDeleted;",
        "    private LocalDateTime deletedAt;",
        "    private String deletedBy;",
    ])
    field_block = "\n".join(field_lines)
    return f"""package {_BASE_JAVA_PACKAGE}.{module}.infrastructure.po;

{imports}
/**
 * 数据库 {table_name} 表持久化对象。
 */
@Data
@TableName("{table_name}")
public class {class_name}PO {{
{field_block}
}}
"""


# ---------------------------------------------------------------------------
# 代码生成：Mapper 接口
# ---------------------------------------------------------------------------

def _render_mapper(module: str, entity_id: str) -> str:
    """生成 Mapper 接口 Java 源码。"""

    class_name = _to_pascal_case(entity_id)
    return f"""package {_BASE_JAVA_PACKAGE}.{module}.infrastructure.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import {_BASE_JAVA_PACKAGE}.{module}.infrastructure.po.{class_name}PO;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface {class_name}Mapper extends BaseMapper<{class_name}PO> {{
    // 基础 CRUD 由 BaseMapper 提供，需要自定义 SQL 时在此加方法
}}
"""


# ---------------------------------------------------------------------------
# 代码生成：Mapper XML
# ---------------------------------------------------------------------------

def _render_mapper_xml(module: str, entity_id: str) -> str:
    """生成 Mapper XML，只含 namespace 声明。"""

    class_name = _to_pascal_case(entity_id)
    namespace = f"{_BASE_JAVA_PACKAGE}.{module}.infrastructure.mapper.{class_name}Mapper"
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "https://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="{namespace}">
    <!-- 基础 CRUD 由 MyBatis-Plus 自动实现，需要自定义 SQL 时在此添加 -->
</mapper>
"""


# ---------------------------------------------------------------------------
# 代码生成：Repository 接口
# ---------------------------------------------------------------------------

def _render_repository(module: str, entity_id: str) -> str:
    """生成仓储接口 Java 源码。"""

    class_name = _to_pascal_case(entity_id)
    id_field = _to_camel_case(entity_id) + "Id"
    return f"""package {_BASE_JAVA_PACKAGE}.{module}.domain.repository;

import {_BASE_JAVA_PACKAGE}.{module}.domain.entity.{class_name};
import {_BASE_JAVA_PACKAGE}.common.page.PageResult;

public interface {class_name}Repository {{
    PageResult<{class_name}> page(int current, int pageSize);

    {class_name} findBy{id_field[:1].upper() + id_field[1:]}(String {id_field});

    void save({class_name} entity);

    void update({class_name} entity);

    void softDelete({class_name} entity, String actor);
}}
"""


# ---------------------------------------------------------------------------
# 代码生成：RepositoryImpl
# ---------------------------------------------------------------------------

def _render_repository_impl(module: str, entity_id: str, fields: list[dict[str, str]]) -> str:
    """生成仓储实现 Java 源码。"""

    class_name = _to_pascal_case(entity_id)
    id_field = _to_camel_case(entity_id) + "Id"
    # update 方法的 .set 行（排除审计字段和 id）
    update_fields = [f for f in fields if f["java_name"] not in ("id", "createdAt", "updatedAt", "createdBy", "updatedBy", "isDeleted", "deletedAt", "deletedBy")]
    set_lines = []
    for field in update_fields:
        set_lines.append(f"                .set({class_name}PO::get{_to_pascal_case(field['java_name'])}, entity.get{field['java_name'][:1].upper() + field['java_name'][1:]}())")
    set_block = "\n".join(set_lines) if set_lines else "                // 无可更新业务字段"
    return f"""package {_BASE_JAVA_PACKAGE}.{module}.infrastructure.repository.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import {_BASE_JAVA_PACKAGE}.{module}.domain.entity.{class_name};
import {_BASE_JAVA_PACKAGE}.{module}.domain.repository.{class_name}Repository;
import {_BASE_JAVA_PACKAGE}.{module}.infrastructure.mapper.{class_name}Mapper;
import {_BASE_JAVA_PACKAGE}.{module}.infrastructure.po.{class_name}PO;
import {_BASE_JAVA_PACKAGE}.{module}.infrastructure.repository.converter.{class_name}Converter;
import {_BASE_JAVA_PACKAGE}.common.page.PageResult;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;

@Repository
@RequiredArgsConstructor
public class {class_name}RepositoryImpl implements {class_name}Repository {{
    private final {class_name}Mapper {class_name[:1].lower() + class_name[1:]}Mapper;
    private final {class_name}Converter converter;

    @Override
    public PageResult<{class_name}> page(int current, int pageSize) {{
        Page<{class_name}PO> page = new Page<>(current, pageSize);
        LambdaQueryWrapper<{class_name}PO> query = new LambdaQueryWrapper<{class_name}PO>()
                .orderByDesc({class_name}PO::getId);
        Page<{class_name}PO> result = {class_name[:1].lower() + class_name[1:]}Mapper.selectPage(page, query);
        return PageResult.of(result.getTotal(), (int) result.getCurrent(), (int) result.getSize(),
                result.getRecords(), converter::toEntity);
    }}

    @Override
    public {class_name} findBy{id_field[:1].upper() + id_field[1:]}(String {id_field}) {{
        {class_name}PO po = {class_name[:1].lower() + class_name[1:]}Mapper.selectOne(new LambdaQueryWrapper<{class_name}PO>()
                .eq({class_name}PO::get{_to_pascal_case(id_field)}, {id_field}));
        return converter.toEntity(po);
    }}

    @Override
    public void save({class_name} entity) {{
        {class_name}PO po = converter.toPO(entity);
        {class_name[:1].lower() + class_name[1:]}Mapper.insert(po);
        entity.setId(po.getId());
    }}

    @Override
    public void update({class_name} entity) {{
        {class_name[:1].lower() + class_name[1:]}Mapper.update(null, new LambdaUpdateWrapper<{class_name}PO>()
                .eq({class_name}PO::get{_to_pascal_case(id_field)}, entity.get{id_field[:1].upper() + id_field[1:]}())
{set_block}
                .set({class_name}PO::getUpdatedBy, entity.getUpdatedBy()));
    }}

    @Override
    public void softDelete({class_name} entity, String actor) {{
        {class_name[:1].lower() + class_name[1:]}Mapper.update(null, new LambdaUpdateWrapper<{class_name}PO>()
                .eq({class_name}PO::get{_to_pascal_case(id_field)}, entity.get{id_field[:1].upper() + id_field[1:]}())
                .set({class_name}PO::getIsDeleted, true)
                .set({class_name}PO::getDeletedAt, LocalDateTime.now())
                .set({class_name}PO::getDeletedBy, actor)
                .set({class_name}PO::getUpdatedBy, actor));
    }}
}}
"""


# ---------------------------------------------------------------------------
# 代码生成：Converter
# ---------------------------------------------------------------------------

def _render_converter(module: str, entity_id: str) -> str:
    """生成 MapStruct Converter 接口。"""

    class_name = _to_pascal_case(entity_id)
    return f"""package {_BASE_JAVA_PACKAGE}.{module}.infrastructure.repository.converter;

import {_BASE_JAVA_PACKAGE}.{module}.domain.entity.{class_name};
import {_BASE_JAVA_PACKAGE}.{module}.infrastructure.po.{class_name}PO;
import org.mapstruct.Mapper;

import java.util.List;

@Mapper(componentModel = "spring")
public interface {class_name}Converter {{
    {class_name} toEntity({class_name}PO source);

    {class_name}PO toPO({class_name} source);

    List<{class_name}> toEntities(List<{class_name}PO> source);
}}
"""


# ---------------------------------------------------------------------------
# 代码生成：DTO
# ---------------------------------------------------------------------------

def _render_dto(module: str, entity_id: str, fields: list[dict[str, str]]) -> str:
    """生成响应 DTO Java 源码。"""

    class_name = _to_pascal_case(entity_id)
    imports = _collect_imports(fields, include_lombok_data=True, include_all_args=True)
    # DTO 只含业务字段，排除审计字段
    dto_fields = [f for f in fields if f["java_name"] not in ("id", "isDeleted", "deletedAt", "deletedBy", "createdBy", "updatedBy")]
    field_lines = [f"    private {f['java_type']} {f['java_name']};" for f in dto_fields]
    field_block = "\n".join(field_lines)
    return f"""package {_BASE_JAVA_PACKAGE}.{module}.application.dto;

{imports}
@Data
@NoArgsConstructor
@AllArgsConstructor
public class {class_name}DTO {{
{field_block}
}}
"""


def _render_upsert_dto(module: str, entity_id: str, fields: list[dict[str, str]]) -> str:
    """生成请求 DTO（新增/更新）Java 源码。"""

    class_name = _to_pascal_case(entity_id)
    # 请求 DTO 排除 id、审计字段、自动生成字段
    excluded = {"id", "isDeleted", "deletedAt", "deletedBy", "createdBy", "updatedBy", "createdAt", "updatedAt"}
    upsert_fields = [f for f in fields if f["java_name"] not in excluded]
    field_lines = []
    for f in upsert_fields:
        annotations = []
        if f["required"]:
            annotations.append("    @NotBlank" if f["java_type"] == "String" else "    @NotNull")
        annotations.append(f"    @Size(max = 128)")
        field_lines.append("\n".join(annotations) + f"\n    private {f['java_type']} {f['java_name']};")
    field_block = "\n\n".join(field_lines) if field_lines else "    // 无业务字段"
    return f"""package {_BASE_JAVA_PACKAGE}.{module}.application.dto;

import lombok.Data;

import javax.validation.constraints.NotBlank;
import javax.validation.constraints.NotNull;
import javax.validation.constraints.Size;

@Data
public class {class_name}UpsertDTO {{
{field_block}
}}
"""


# ---------------------------------------------------------------------------
# 代码生成：Controller 骨架
# ---------------------------------------------------------------------------

def _render_controller(
    module: str,
    entity_id: str,
    endpoints: list[dict[str, Any]],
) -> str:
    """从 API Contract endpoints 生成 Controller 骨架。"""

    class_name = _to_pascal_case(entity_id)
    var_name = class_name[:1].lower() + class_name[1:]
    # 按方法分组端点
    has_list = any(str(ep.get("method") or "").upper() == "GET" and "{" not in str(ep.get("path") or "") for ep in endpoints)
    has_get = any("{projectId}" in str(ep.get("path") or "") or "{id}" in str(ep.get("path") or "") for ep in endpoints)
    has_create = any(str(ep.get("method") or "").upper() == "POST" for ep in endpoints)
    has_update = any(str(ep.get("method") or "").upper() == "PUT" and "{projectId}" in str(ep.get("path") or "") for ep in endpoints)
    has_delete = any(str(ep.get("method") or "").upper() == "DELETE" for ep in endpoints)
    id_field = _to_camel_case(entity_id) + "Id"
    id_path_param = "{" + id_field + "}"
    base_path = f"/api/{_to_snake_case(entity_id)}s".replace("_", "-")

    methods = []
    if has_list:
        methods.append(f"""    @GetMapping
    public ResponseEntity<PageResult<{class_name}DTO>> list(@ModelAttribute PageParam query) {{
        return ResponseEntity.success({var_name}Service.list{class_name}s(query));
    }}""")
    if has_get:
        methods.append(f"""    @GetMapping("/{id_path_param}")
    public ResponseEntity<{class_name}DTO> get(@PathVariable String {id_field}) {{
        return ResponseEntity.success({var_name}Service.get{class_name}({id_field}));
    }}""")
    if has_create:
        methods.append(f"""    @PostMapping
    public ResponseEntity<{class_name}DTO> create(@Valid @RequestBody {class_name}UpsertDTO request) {{
        return ResponseEntity.success({var_name}Service.create{class_name}(request));
    }}""")
    if has_update:
        methods.append(f"""    @PutMapping("/{id_path_param}")
    public ResponseEntity<{class_name}DTO> update(@PathVariable String {id_field},
                                              @Valid @RequestBody {class_name}UpsertDTO request) {{
        return ResponseEntity.success({var_name}Service.update{class_name}({id_field}, request));
    }}""")
    if has_delete:
        methods.append(f"""    @DeleteMapping("/{id_path_param}")
    public ResponseEntity<Void> delete(@PathVariable String {id_field}) {{
        {var_name}Service.delete{class_name}({id_field});
        return ResponseEntity.success();
    }}""")
    methods_block = "\n\n".join(methods) if methods else "    // 无端点"
    return f"""package {_BASE_JAVA_PACKAGE}.{module}.adapter.web;

import {_BASE_JAVA_PACKAGE}.{module}.application.dto.{class_name}DTO;
import {_BASE_JAVA_PACKAGE}.{module}.application.dto.{class_name}UpsertDTO;
import {_BASE_JAVA_PACKAGE}.{module}.application.service.{class_name}ApplicationService;
import {_BASE_JAVA_PACKAGE}.common.page.PageParam;
import {_BASE_JAVA_PACKAGE}.common.page.PageResult;
import {_BASE_JAVA_PACKAGE}.common.response.ResponseEntity;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import javax.validation.Valid;

@RestController
@RequiredArgsConstructor
@RequestMapping("{base_path}")
public class {class_name}Controller {{

    private final {class_name}ApplicationService {var_name}Service;

{methods_block}
}}
"""


# ---------------------------------------------------------------------------
# import 收集
# ---------------------------------------------------------------------------

def _collect_imports(
    fields: list[dict[str, str]],
    *,
    include_lombok_data: bool = False,
    include_mybatis_plus: bool = False,
    include_all_args: bool = False,
) -> str:
    """根据字段类型和需要的注解收集 import 语句。"""

    imports: list[str] = []
    if include_lombok_data:
        imports.append("import lombok.Data;")
    if include_all_args:
        imports.append("import lombok.AllArgsConstructor;")
        imports.append("import lombok.NoArgsConstructor;")
    if include_mybatis_plus:
        imports.append("import com.baomidou.mybatisplus.annotation.IdType;")
        imports.append("import com.baomidou.mybatisplus.annotation.TableField;")
        imports.append("import com.baomidou.mybatisplus.annotation.TableId;")
        imports.append("import com.baomidou.mybatisplus.annotation.TableLogic;")
        imports.append("import com.baomidou.mybatisplus.annotation.TableName;")
    type_imports: set[str] = set()
    for field in fields:
        java_type = field["java_type"]
        if java_type == "BigDecimal":
            type_imports.add("import java.math.BigDecimal;")
        elif java_type == "LocalDateTime":
            type_imports.add("import java.time.LocalDateTime;")
        elif java_type == "LocalDate":
            type_imports.add("import java.time.LocalDate;")
        elif java_type == "LocalTime":
            type_imports.add("import java.time.LocalTime;")
        elif java_type == "Long":
            pass  # java.lang 不需要 import
    imports.extend(sorted(type_imports))
    return "\n".join(imports)


# ---------------------------------------------------------------------------
# 端点分组
# ---------------------------------------------------------------------------

def _endpoints_for_entity(
    project_plan: dict[str, Any],
    entity_id: str,
) -> list[dict[str, Any]]:
    """从 api_contracts 找到与实体关联的端点。"""

    result: list[dict[str, Any]] = []
    for contract in project_plan.get("api_contracts") or []:
        if not isinstance(contract, dict):
            continue
        contract_entity_ids = [str(eid).strip() for eid in contract.get("entity_ids") or [] if eid]
        if entity_id not in contract_entity_ids:
            continue
        for endpoint in contract.get("endpoints") or []:
            if isinstance(endpoint, dict):
                result.append(endpoint)
    return result


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def inject_deterministic_backend_skeleton(
    workspace: str | Path,
    technical_plan: dict[str, Any],
) -> dict[str, Any]:
    """从已确认 TechnicalPlan 推导后端骨架代码并幂等写入模板工程。

    只写入不删除：已存在的文件若内容一致则跳过，内容不同则覆盖（平台预置优先）。
    返回写入文件清单和统计。
    """

    workspace_path = Path(workspace).expanduser().resolve()
    backend_root = workspace_path / _BACKEND_ROOT
    if not backend_root.is_dir():
        return {"status": "skipped", "reason": "backend_template_not_found", "files": []}

    entities = technical_plan.get("entities") or []
    if not isinstance(entities, list) or not entities:
        return {"status": "skipped", "reason": "no_entities", "files": []}

    detail_map = _entity_detail_map(technical_plan)
    written_files: list[str] = []
    skipped_files: list[str] = []
    modules: dict[str, list[str]] = {}

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("id") or entity.get("name") or "").strip()
        if not entity_id:
            continue
        module = _module_name(entity_id)
        detail_plan = detail_map.get(entity_id)
        fields = _collect_entity_fields(entity, detail_plan)
        if not fields:
            continue
        endpoints = _endpoints_for_entity(technical_plan, entity_id)
        module_files = _write_entity_skeleton(
            backend_root, module, entity_id, fields, endpoints
        )
        written_files.extend(module_files["written"])
        skipped_files.extend(module_files["skipped"])
        modules.setdefault(module, []).extend(module_files["written"])

    return {
        "status": "succeeded",
        "files": written_files,
        "skipped": skipped_files,
        "moduleCount": len(modules),
        "fileCount": len(written_files),
    }


def _write_entity_skeleton(
    backend_root: Path,
    module: str,
    entity_id: str,
    fields: list[dict[str, str]],
    endpoints: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """为单个实体写入全部骨架文件，返回写入/跳过清单。"""

    class_name = _to_pascal_case(entity_id)
    java_base = backend_root / "src" / "main" / "java" / _BASE_PACKAGE / module
    resources_mapper = backend_root / "src" / "main" / "resources" / "mapper" / module

    files: list[tuple[Path, str]] = [
        (java_base / "domain" / "entity" / f"{class_name}.java", _render_entity(module, entity_id, fields)),
        (java_base / "infrastructure" / "po" / f"{class_name}PO.java", _render_po(module, entity_id, fields)),
        (java_base / "infrastructure" / "mapper" / f"{class_name}Mapper.java", _render_mapper(module, entity_id)),
        (resources_mapper / f"{class_name}Mapper.xml", _render_mapper_xml(module, entity_id)),
        (java_base / "domain" / "repository" / f"{class_name}Repository.java", _render_repository(module, entity_id)),
        (java_base / "infrastructure" / "repository" / "impl" / f"{class_name}RepositoryImpl.java",
         _render_repository_impl(module, entity_id, fields)),
        (java_base / "infrastructure" / "repository" / "converter" / f"{class_name}Converter.java",
         _render_converter(module, entity_id)),
        (java_base / "application" / "dto" / f"{class_name}DTO.java", _render_dto(module, entity_id, fields)),
        (java_base / "application" / "dto" / f"{class_name}UpsertDTO.java", _render_upsert_dto(module, entity_id, fields)),
        (java_base / "adapter" / "web" / f"{class_name}Controller.java", _render_controller(module, entity_id, endpoints)),
    ]

    written: list[str] = []
    skipped: list[str] = []
    for path, content in files:
        relative = str(path.relative_to(backend_root.parent))
        if _write_file_atomically(path, content):
            written.append(relative)
        else:
            skipped.append(relative)
    return {"written": written, "skipped": skipped}


def prebuilt_files_for_plan(technical_plan: dict[str, Any]) -> list[str]:
    """返回给定 TechnicalPlan 将注入的后端文件相对路径清单（不实际写入）。

    供 build 阶段 implementation_contract.prebuilt_files 使用，让 Agent 知道
    哪些文件已由平台预置。
    """

    entities = technical_plan.get("entities") or []
    if not isinstance(entities, list):
        return []
    detail_map = _entity_detail_map(technical_plan)
    result: list[str] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_id = str(entity.get("id") or entity.get("name") or "").strip()
        if not entity_id:
            continue
        module = _module_name(entity_id)
        detail_plan = detail_map.get(entity_id)
        fields = _collect_entity_fields(entity, detail_plan)
        if not fields:
            continue
        class_name = _to_pascal_case(entity_id)
        base = f"backend/src/main/java/{_BASE_PACKAGE}/{module}"
        result.extend([
            f"{base}/domain/entity/{class_name}.java",
            f"{base}/infrastructure/po/{class_name}PO.java",
            f"{base}/infrastructure/mapper/{class_name}Mapper.java",
            f"backend/src/main/resources/mapper/{module}/{class_name}Mapper.xml",
            f"{base}/domain/repository/{class_name}Repository.java",
            f"{base}/infrastructure/repository/impl/{class_name}RepositoryImpl.java",
            f"{base}/infrastructure/repository/converter/{class_name}Converter.java",
            f"{base}/application/dto/{class_name}DTO.java",
            f"{base}/application/dto/{class_name}UpsertDTO.java",
            f"{base}/adapter/web/{class_name}Controller.java",
        ])
    return result
