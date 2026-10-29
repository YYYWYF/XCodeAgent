"""受限 SQL 查询树与参数值对应；不执行 SQL，不用列名出现代替条件语义。"""

import ast
import re

from app.services.business_acceptance_verifiers.python_ast import PythonEvidenceUnavailable, UNKNOWN
from app.services.business_acceptance_verifiers.python_service import expression_path

_TOKEN = re.compile(r"\s*(%\(\w+\)s|%s|(?:`[^`]+`|[A-Za-z_]\w*)(?:\.(?:`[^`]+`|[A-Za-z_]\w*))*|>=|<=|<>|!=|=|>|<|\(|\)|,)")
_OPERATORS = {"=": "eq", "!=": "ne", "<>": "ne", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}


def value_matches(program, unit, function, expression, right: dict, contexts: set[str]) -> bool:
    """核对单一参数表达式来源；自然语言业务规则明确不作猜测。"""

    expression = program.expand(unit, expression, function)
    if right.get("kind") == "fixed":
        actual = program.value(unit, expression, function)
        return actual is not UNKNOWN and type(actual) is type(right.get("value")) and actual == right.get("value")
    if right.get("kind") == "endpoint":
        chain = expression_path(expression)
        path = tuple(right["endpointField"]["path"].split('.'))
        arguments = {argument.arg for argument in (*function.args.args, *function.args.kwonlyargs)} - {"self", *contexts}
        return bool(chain and chain[0] in arguments and chain[-len(path):] == path)
    raise PythonEvidenceUnavailable("数据库条件/写入含自然语言业务取值，需行为证据或专用适配。")


def _normalize(node):
    """扁平化同类关联但保留 AND/OR 关系，排序只消除条件顺序差异。"""

    if node[0] == "condition":
        return node
    children = []
    for child in node[1]:
        child = _normalize(child)
        children.extend(child[1] if child[0] == node[0] else [child])
    return (node[0], tuple(sorted(children, key=repr))) if len(children) != 1 else children[0]


def verify_query(program, unit, function, sql: str, bindings: dict, expected: dict,
                 contexts: set[str], trusted_subject) -> list[str]:
    """把 WHERE 解析成有界逻辑树，与正式 AND/OR、运算符和实际绑定右值逐项比较。"""

    where = re.search(r"\bwhere\b(.*?)(?:\border\s+by\b|\blimit\b|\bfor\s+update\b|$)", sql, re.I | re.S)
    if where is None:
        return ["正式数据库查询树没有落实 WHERE 条件。"]
    text = where.group(1).strip()
    tokens, position = [], 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise PythonEvidenceUnavailable("WHERE 含当前不支持的 SQL 表达式，不能省略条件后通过。")
        tokens.append(match.group(1))
        position = match.end()
    cursor, parameter_index = 0, len(re.findall(r"(?<!%)%s", sql[:where.start(1)]))
    leaves = []

    def take():
        """消费一个 SQL token，越界时返回明确的不支持诊断。"""
        nonlocal cursor
        if cursor >= len(tokens):
            raise PythonEvidenceUnavailable("WHERE 语句不完整。")
        token = tokens[cursor]
        cursor += 1
        return token

    def atom():
        """解析括号或单列标量比较，其他 SQL 运算保持 blocked。"""
        nonlocal parameter_index
        column = take()
        if column == '(':
            node = parse_or()
            if take() != ')':
                raise PythonEvidenceUnavailable("WHERE 括号不匹配。")
            return node
        column = column.split('.')[-1].strip('`')
        operator = take().upper()
        if operator == "IS":
            null_token = take().upper()
            not_null = null_token == "NOT"
            if (take().upper() if not_null else null_token) != "NULL":
                raise PythonEvidenceUnavailable("当前只支持 IS NULL/IS NOT NULL。")
            value, operation = None, "is_not_null" if not_null else "is_null"
        else:
            operation = _OPERATORS.get(operator)
            if operation is None:
                raise PythonEvidenceUnavailable(f"当前 SQL 条件运算符 {operator} 尚无语义适配。")
            token = take()
            named = re.fullmatch(r"%\((\w+)\)s", token)
            if named:
                value = bindings.get(named.group(1))
            elif token == "%s":
                value = bindings.get(str(parameter_index))
                parameter_index += 1
            else:
                raise PythonEvidenceUnavailable("条件右值必须使用参数化占位符。")
        if operation == "eq" and column in {"created_by", "owner_id", "subject_id"} and trusted_subject(value, contexts):
            return ("trusted_owner",)
        index = len(leaves)
        leaves.append((column, operation, value))
        return ("condition", index)

    def parse_and():
        """先结合 AND，保持 SQL 运算符优先级。"""
        nodes = [atom()]
        while cursor < len(tokens) and tokens[cursor].upper() == "AND":
            take()
            nodes.append(atom())
        return ("and", tuple(nodes)) if len(nodes) > 1 else nodes[0]

    def parse_or():
        """在 AND 之后结合 OR，不能把 OR 归属条件当成 AND 隔离。"""
        nodes = [parse_and()]
        while cursor < len(tokens) and tokens[cursor].upper() == "OR":
            take()
            nodes.append(parse_and())
        return ("or", tuple(nodes)) if len(nodes) > 1 else nodes[0]

    actual = parse_or()
    if cursor != len(tokens):
        raise PythonEvidenceUnavailable("WHERE 尾部存在尚未解释的语义。")

    def remove_owner(node, under_or=False):
        """仅移除顶层 AND 的已验证归属条件；OR 里的归属条件不能证明数据隔离。"""
        if node[0] == "trusted_owner":
            if under_or:
                raise PythonEvidenceUnavailable("可信归属条件位于 OR 内，无法证明隔离。")
            return None
        if node[0] == "condition":
            return node
        children = [value for child in node[1] if (value := remove_owner(child, under_or or node[0] == "or")) is not None]
        return (node[0], tuple(children)) if children else None

    used = set()

    def formal_tree(query):
        """每个正式条件唯一匹配到同列、同操作及同右值的本次执行参数。"""
        children = []
        for item in query.get("items") or []:
            if item.get("kind") == "condition":
                candidates = [index for index, (column, operation, value) in enumerate(leaves)
                              if index not in used and column == item["column"] and operation == item["operator"]
                              and (value is None if item["operator"] in {"is_null", "is_not_null"} else
                                   value_matches(program, unit, function, value, item.get("right") or {}, contexts))]
                if len(candidates) != 1:
                    raise PythonEvidenceUnavailable(f"条件 {item['column']}/{item['operator']} 无法唯一对应实际参数值。")
                index = candidates[0]
                used.add(index)
                children.append(("condition", index))
            else:
                children.append(formal_tree(item))
        return (query.get("join", "and"), tuple(children))

    remaining = remove_owner(actual)
    wanted = formal_tree(expected)
    return [] if remaining is not None and _normalize(remaining) == _normalize(wanted) else ["实际 WHERE 的条件或 AND/OR 关系与正式查询树不一致。"]
