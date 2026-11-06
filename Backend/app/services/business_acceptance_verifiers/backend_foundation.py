"""模块基础骨架验收只确认结构，不把空 Repository 或 Client 当成业务实现。"""

from app.services.business_acceptance_verifiers.common import verification_result
from app.services.business_acceptance_verifiers.backend_domain_support import annotation_value, find_bound_po_field
from app.services.business_acceptance_verifiers.java_inspection_support import _inspect_or_block


def verify_foundation_source(
    files: dict[str, str], expected: dict, *, new_paths: frozenset[str] = frozenset(),
) -> dict:
    """检查数据库或外部 API 骨架；允许复用已经包含其他业务方法的有效类型。"""
    if not expected.get("source_types"):
        return verification_result("blocked", "缺少已确认的骨架来源。")
    model = _inspect_or_block({path: text for path, text in files.items() if path.endswith(".java")})
    if isinstance(model, dict):
        return model
    errors = []
    types = expected.get("source_types", [])
    if "database" in types:
        for table in expected.get("tables", []):
            table_name = table.get("name")
            types_for_table = [item for item in model.types
                               if annotation_value(item.annotations, "TableName") == table_name]
            if not types_for_table:
                errors.append(f"缺少绑定 {table_name} 的 PO。")
                continue
            for column in table.get("columns", []):
                if find_bound_po_field(types_for_table, column, column) is None:
                    errors.append(f"PO 缺少 {table_name}.{column}。")
        if "BaseMapper" not in model.identifiers:
            errors.append("缺少 BaseMapper 基础接口。")
        repositories = [item for item in model.types if item.name.endswith(("Repository", "RepositoryImpl"))]
        if not repositories or not any(field.type_name.endswith("Mapper") for item in repositories for field in item.fields):
            errors.append("缺少具有 Mapper 注入的 Repository 骨架。")
    if "external_api" in types:
        annotations = [annotation for item in model.types for annotation in item.annotations
                       if annotation.name == "FeignClient"]
        if not annotations:
            errors.append("缺少 FeignClient 基础框架。")
        for key in expected.get("config_keys", []):
            reference = "${" + key + "}"
            if not any(reference in annotation.strings for annotation in annotations):
                errors.append(f"FeignClient 缺少地址配置引用 {key}。")
    # 仅限制本阶段新建的骨架，已有业务方法应原样保留。
    for item in model.types:
        skeleton = item.name.endswith(("Repository", "RepositoryImpl", "Mapper")) or any(
            annotation.name == "FeignClient" for annotation in item.annotations
        )
        if item.source_path in new_paths and skeleton and any(method.return_type for method in item.methods):
            errors.append(f"新建骨架 {item.name} 不能包含业务方法。")
    return verification_result("failed" if errors else "passed",
        "；".join(errors) if errors else "基础类型与依赖结构已就绪，具体业务由后续任务验收。")
