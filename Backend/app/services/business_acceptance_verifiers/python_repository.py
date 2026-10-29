"""核对 DB-API 参数化访问和正式物理表，不能仅凭方法名推断持久化语义。"""

import ast
import re

from app.services.business_acceptance_verifiers.python_ast import PythonProgram, PythonEvidenceUnavailable, UNKNOWN, result_for
from app.services.business_acceptance_verifiers.python_endpoint import trusted_context_names
from app.services.business_acceptance_verifiers.python_sql import value_matches, verify_query


def _sql_records(program: PythonProgram, targets: set[str]) -> list[tuple]:
    """只读取 execute/executemany 的真实 SQL 实参；动态拼接不尝试猜测。"""

    records = []
    for unit, function in program.functions.values():
        if unit.path not in targets:
            continue
        for call in program.calls(function):
            if not isinstance(call.func, ast.Attribute) or call.func.attr not in {"execute", "executemany"} or not call.args:
                continue
            sql = program.value(unit, call.args[0], function)
            if not isinstance(sql, str):
                raise PythonEvidenceUnavailable(f"{unit.path}:{call.lineno} 的 SQL 不是可证明的固定文本；动态 SQL/ORM 需专用适配。")
            if re.search(r"--|/\*|;\s*\S", sql):
                raise PythonEvidenceUnavailable(f"{unit.path}:{call.lineno} 的注释或多语句 SQL 超出当前验收边界。")
            receiver = program.receiver_type(unit, function, call.func.value)
            if receiver not in {"pymysql.cursors.Cursor", "pymysql.cursors.DictCursor", "pymysql.cursors.SSCursor", "pymysql.cursors.SSDictCursor"}:
                raise PythonEvidenceUnavailable(f"{unit.path}:{call.lineno} 无法确认 execute 的真实 DB-API Cursor 类型。")
            if re.search(r"\b(join|union|having|group\s+by)\b", sql, re.I):
                raise PythonEvidenceUnavailable(f"{unit.path}:{call.lineno} 多表/聚合 SQL 需要专用语义适配。")
            records.append((unit, function, call, sql))
    return records


def _params(program, unit, function, call) -> dict | list | None:
    """展开执行实参容器，保留 AST 表达式以继续核对可信归属和值来源。"""

    node = call.args[1] if len(call.args) > 1 else program.keyword(call, "params")
    node = program.expand(unit, node, function)
    if isinstance(node, (ast.Tuple, ast.List)):
        return node.elts
    if isinstance(node, ast.Dict):
        keys = [program.value(unit, key, function) for key in node.keys]
        if all(isinstance(key, str) for key in keys):
            return dict(zip(keys, node.values))
    return None


def sql_binding(program, unit, function, call, sql: str) -> tuple[dict[str, ast.AST], list[str]]:
    """将具名/位置占位符绑定到实际参数，拒绝 SQL 字符串格式化或缺少参数容器。"""

    params = _params(program, unit, function, call)
    named = re.findall(r"%\(([A-Za-z_]\w*)\)s", sql)
    position_count = len(re.findall(r"(?<!%)%s", sql))
    bindings, errors = {}, []
    if named:
        if not isinstance(params, dict) or set(named) != set(params):
            errors.append("SQL 具名占位符与本次执行参数不一致。")
        else:
            bindings = {name: program.expand(unit, params[name], function) for name in named}
    elif position_count:
        if not isinstance(params, list) or position_count != len(params):
            errors.append("SQL 位置占位符与本次执行参数数量不一致。")
        else:
            bindings = {str(index): program.expand(unit, node, function) for index, node in enumerate(params)}
    elif params:
        errors.append("SQL 没有参数占位符但传入了业务参数。")
    return bindings, errors


def _condition_binding(sql: str, column: str, bindings: dict) -> ast.AST | None:
    """只支持明确的等值归属条件，不能用同方法无关位置出现的 Principal 冒充过滤。"""

    match = re.search(r"\b(?:where|and)\s+(?:\w+\.)?`?" + re.escape(column) + r"`?\s*=\s*(%\((\w+)\)s|%s)", sql, re.I)
    if not match:
        return None
    key = match.group(2)
    if key is None:
        key = str(len(re.findall(r"(?<!%)%s", sql[:match.start(1)])))
    return bindings.get(key)


def _trusted_subject(node, contexts: set[str]) -> bool:
    """只接受 RuntimeContext.principal 的 subjectId/subject_id，不接受客户端 owner 字段。"""

    return (isinstance(node, ast.Attribute) and node.attr in {"subject_id", "subjectId"}
            and isinstance(node.value, ast.Attribute) and node.value.attr == "principal"
            and isinstance(node.value.value, ast.Name) and node.value.value.id in contexts)


def verify_python_repository(files: dict, expected: dict) -> dict:
    """验证每个正式数据库操作的真实 SQL、精确表身份、归属过滤和参数化绑定。"""

    program = PythonProgram(files)
    errors, blocked = [], []
    records = _sql_records(program, set(expected["_target_paths"]))
    if not records:
        return result_for([], ["Repository 未提供可静态验证的 DB-API execute；ORM/封装需增加适配，不能按方法名通过。"])
    for unit, function, call, sql in records:
        if re.search(r"\b(create|alter|drop|truncate)\b", sql, re.I):
            errors.append(f"{unit.path}:{call.lineno} 业务 Repository 不允许执行 DDL。")
        _, binding_errors = sql_binding(program, unit, function, call, sql)
        errors.extend(f"{unit.path}:{call.lineno}：{error}" for error in binding_errors)
    operations = {"create": "insert", "read": "select", "update": "update", "delete": "delete"}
    for design in expected.get("endpoint_designs") or []:
        binding = design.get("sourceBinding") or {}
        if binding.get("sourceType") != "database":
            continue
        table, verb = binding.get("table"), operations.get(design.get("databaseOperation"))
        if not table or not verb:
            blocked.append(f"{design.get('endpointId')} 缺少正式数据库表或 CRUD 操作。")
            continue
        table_pattern = r"\b(?:from|into|update)\s+(?:`?\w+`?\.)?`?" + re.escape(table) + r"`?(?=\s|\(|$)"
        matches = [record for record in records if re.match(r"\s*" + verb + r"\b", record[3], re.I)
                   and re.search(table_pattern, record[3], re.I)]
        if not matches:
            errors.append(f"{design['endpointId']} 缺少对正式物理表 {table} 的 {verb.upper()} 操作。")
            continue
        if len(matches) != 1:
            blocked.append(f"{design['endpointId']} 的 SQL 无法唯一绑定，不能把其他方法的实现作为证据。")
            continue
        unit, function, call, sql = matches[0]
        params, _ = sql_binding(program, unit, function, call, sql)
        contexts = trusted_context_names(program, unit, function)
        if not contexts:
            blocked.append(f"{unit.path}:{function.lineno} 无法识别可信 RuntimeContext。")
        elif verb != "insert" and not any(_trusted_subject(_condition_binding(sql, column, params), contexts)
                                           for column in ("created_by", "owner_id", "subject_id")):
            blocked.append(f"{unit.path}:{call.lineno} 无法证明查询/修改以当前可信 Principal 过滤归属。")
        write_bindings = {}
        if verb == "insert":
            match = re.search(r"\binto\s+(?:`?\w+`?\.)?`?\w+`?\s*\(([^)]+)\)\s*values\s*\((.*?)\)\s*$", sql, re.I | re.S)
            # 复杂 INSERT（多行、表达式、别名）不凭字符串包含关系放行。
            if not match:
                blocked.append(f"{unit.path}:{call.lineno} INSERT 列和值不能静态对应。")
            else:
                columns = [column.strip().strip('`') for column in match.group(1).split(',')]
                tokens = [token.strip() for token in match.group(2).split(',')]
                if len(columns) == len(tokens):
                    for index, (column, token) in enumerate(zip(columns, tokens)):
                        named = re.fullmatch(r"%\((\w+)\)s", token)
                        write_bindings[column] = params.get(named.group(1) if named else str(index))
                owners = [index for index, column in enumerate(columns) if column in {"created_by", "owner_id", "subject_id"}]
                if len(columns) != len(tokens) or not owners:
                    blocked.append("INSERT 未提供可证明的可信归属列。")
                else:
                    for index in owners:
                        named = re.fullmatch(r"%\((\w+)\)s", tokens[index])
                        expression = params.get(named.group(1) if named else str(index))
                        if not _trusted_subject(expression, contexts):
                            errors.append(f"INSERT 的 {columns[index]} 不是当前可信 Principal。")
        elif verb == "update":
            assignments = re.search(r"\bset\s+(.*?)\bwhere\b", sql, re.I | re.S)
            if assignments:
                for assignment in assignments.group(1).split(','):
                    match = re.fullmatch(r"\s*`?(\w+)`?\s*=\s*(%\((\w+)\)s|%s)\s*", assignment)
                    if not match:
                        blocked.append("UPDATE SET 表达式无法与正式写入值对应。")
                        continue
                    key = match.group(3)
                    if key is None:
                        offset = assignments.start(1) + assignments.group(1).find(assignment)
                        key = str(len(re.findall(r"(?<!%)%s", sql[:offset])))
                    write_bindings[match.group(1)] = params.get(key)
        for write in design.get("databaseWrites") or []:
            if write.get("table") != table or not re.search(r"\b" + re.escape(write["column"]) + r"\b", sql):
                errors.append(f"正式写入列 {write.get('column')} 未落实在当前表操作。")
            elif write["column"] not in write_bindings:
                blocked.append(f"正式写入列 {write['column']} 无法对应本次绑定参数。")
            elif not value_matches(program, unit, function, write_bindings[write["column"]], write["right"], contexts):
                errors.append(f"正式写入列 {write['column']} 的取值来源不一致。")
        query = design.get("databaseQuery")
        if query:
            errors.extend(verify_query(program, unit, function, sql, params, query, contexts, _trusted_subject))
        elif re.search(r"\bOR\b", sql, re.I):
            blocked.append("SQL 含未声明的 OR 条件，无法证明可信归属过滤。")
        elif verb != "insert":
            where = re.search(r"\bwhere\b(.*?)(?:\border\s+by\b|\blimit\b|$)", sql, re.I | re.S)
            if where and re.search(r"\bAND\b", where.group(1), re.I):
                blocked.append("SQL 含未在正式查询树声明的额外 AND 条件，不能静默改变业务范围。")
    if not expected.get("endpoint_designs"):
        blocked.append("Repository 缺少当前正式 Endpoint Design。")
    return result_for(errors, blocked, facts={"sql_calls": [{"file": unit.path, "line": call.lineno, "function": function.name}
                                                           for unit, function, call, _ in records]})
