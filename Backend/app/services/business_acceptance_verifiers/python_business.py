"""Direct Python 业务交付物的轻量级源码结构检查。"""

from __future__ import annotations

import ast
from typing import Any

from app.services.business_acceptance_verifiers.common import verification_result


def _python_trees(files: dict[str, str]) -> tuple[list[ast.Module], list[str]]:
    """解析任务拥有的 Python 文件，拒绝空文件和语法错误。"""

    trees: list[ast.Module] = []
    errors: list[str] = []
    for path, source in files.items():
        if not path.endswith(".py") or not source.strip():
            errors.append(f"Python 业务交付文件无效：{path}。")
            continue
        try:
            trees.append(ast.parse(source, filename=path))
        except SyntaxError as exc:
            errors.append(f"Python 业务文件语法错误：{path}:{exc.lineno}。")
    return trees, errors


def _defined_names(trees: list[ast.Module]) -> set[str]:
    """收集真实 AST 定义，避免注释文本冒充业务实现。"""

    return {
        node.name
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def verify_python_entity_source(files: dict[str, str], expected: dict[str, Any]) -> dict[str, Any]:
    """检查正式 Entity 的字段至少落实在可解析的 Python 领域模型中。"""

    trees, errors = _python_trees(files)
    field_names = {
        node.target.id
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    for entity in expected.get("entities", []):
        if not isinstance(entity, dict):
            continue
        for field in entity.get("fields", []):
            name = str(field.get("name") or "") if isinstance(field, dict) else ""
            if name and name not in field_names:
                errors.append(f"Entity 字段 {name} 未在 Python 模型中定义。")
    if not _defined_names(trees):
        errors.append("Python Entity 文件未定义可调用或模型对象。")
    return verification_result(
        "failed" if errors else "passed",
        "；".join(errors) if errors else "Python Entity 字段结构与正式契约一致。",
    )


def verify_python_repository_source(files: dict[str, str], expected: dict[str, Any]) -> dict[str, Any]:
    """检查 Repository 具有真实方法及显式 Principal/Context 范围输入。"""

    trees, errors = _python_trees(files)
    methods = [
        node for tree in trees for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not methods:
        errors.append("Python Repository 缺少可执行读写方法。")
    scoped = any(
        any(arg.arg in {"principal", "context", "owner_id", "subject_id"} for arg in method.args.args)
        for method in methods
    )
    if not scoped:
        errors.append("Python Repository 缺少显式 Principal/Context 范围输入。")
    if not expected.get("entities"):
        errors.append("Repository 缺少正式 Entity 来源。")
    return verification_result(
        "failed" if errors else "passed",
        "；".join(errors) if errors else "Python Repository 具备 Principal-scoped 方法。",
    )


def verify_python_migration_source(files: dict[str, str], expected: dict[str, Any]) -> dict[str, Any]:
    """核对版本化 SQL 覆盖正式 Entity 字段并具有建表语句。"""

    errors: list[str] = []
    sql = "\n".join(source for path, source in files.items() if path.endswith(".sql"))
    if "create table" not in sql.lower():
        errors.append("业务迁移缺少 CREATE TABLE 语句。")
    for entity in expected.get("entities", []):
        if not isinstance(entity, dict):
            continue
        for field in entity.get("fields", []):
            name = str(field.get("name") or "") if isinstance(field, dict) else ""
            if name and name.lower() not in sql.lower():
                errors.append(f"业务迁移缺少正式字段 {name}。")
    return verification_result(
        "failed" if errors else "passed",
        "；".join(errors) if errors else "业务迁移覆盖正式 Entity 字段。",
    )


def verify_python_application_service_source(
    files: dict[str, str], expected: dict[str, Any]
) -> dict[str, Any]:
    """检查业务服务实现了至少一个可调用操作并绑定正式 API Contract。"""

    trees, errors = _python_trees(files)
    if not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for tree in trees for node in ast.walk(tree)
    ):
        errors.append("Python Application Service 缺少可调用业务操作。")
    if not expected.get("endpoints"):
        errors.append("Application Service 缺少正式 API Contract 来源。")
    return verification_result(
        "failed" if errors else "passed",
        "；".join(errors) if errors else "Python Application Service 结构已落实。",
    )


def verify_python_endpoint_source(files: dict[str, str], expected: dict[str, Any]) -> dict[str, Any]:
    """检查 FastAPI 路由真实声明正式 method/path，而非只在注释中出现。"""

    trees, errors = _python_trees(files)
    decorators = [
        decorator
        for tree in trees
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
    ]
    literals = {
        node.value
        for tree in trees for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for endpoint in expected.get("endpoints", []):
        if not isinstance(endpoint, dict):
            continue
        method = str(endpoint.get("method") or "").lower()
        path = str(endpoint.get("path") or "")
        if not any(decorator.func.attr == method for decorator in decorators):
            errors.append(f"FastAPI Endpoint 未声明 {method.upper()} 路由。")
        if path and path not in literals:
            errors.append(f"FastAPI Endpoint 未声明正式路径 {path}。")
    if not decorators:
        errors.append("Python Endpoint 缺少 FastAPI 路由装饰器。")
    return verification_result(
        "failed" if errors else "passed",
        "；".join(errors) if errors else "FastAPI Endpoint method/path 与正式契约一致。",
    )
