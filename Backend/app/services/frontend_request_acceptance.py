"""用现有 TypeScript AST 核验前端业务请求与展示层的边界。"""

from dataclasses import dataclass
from typing import Any

from app.services.business_acceptance_verifiers.typescript_ast import (
    _imports,
    _node_text,
    _parse_source,
    _walk,
    TypeScriptImport,
    inspect_typescript_sources,
)
from app.services.business_acceptance_verifiers.typescript_inspection import (
    _paths_match,
    _reachable_source_paths,
    _resolve_import_path,
)

_COMPONENTS = {"ProTable", "EditableProTable", "ProFormSelect", "ProFormTreeSelect",
               "ProFormCascader", "ProFormCheckbox", "ProFormRadio", "ProFormRadio.Group",
               "ProFormDependency"}
_HTTP_GLOBALS = {"fetch"}
_STATE_PROPS = {"dataSource", "loading", "options"}


RULE_MESSAGES = {
    "FE-NO-DIRECT-HTTP": "页面或组件直接调用 fetch、axios 或 service。请使用声明的业务 API 模块。",
    "FE-NO-UI-REQUEST-PROP": "组件的 request 属性执行数据请求。请传入 dataSource、loading 或 options。",
    "FE-QUERY-USE-REQUEST": "查询调用未由 ahooks useRequest 管理。请把查询放入其服务回调。",
}


@dataclass(frozen=True)
class RequestFinding:
    """单项规则的判断、定位和可操作原因。"""

    rule_id: str
    status: str
    file_path: str
    line: int | None
    message: str


def query_exports(modules: dict[str, str], endpoints: list[dict]) -> dict[str, set[str]]:
    """通过已完成业务 API 模块的实际导出和唯一 GET 路径绑定查询契约。"""
    exports = {}
    for path, source in modules.items():
        model = inspect_typescript_sources({path: source})
        client_names = {item.local for item in _runtime_imports(_parse_source(path, source))
                        if _is_template_service(path, item.module)}
        for endpoint in endpoints:
            matches = {
                fn.export_symbol for fn in model.functions
                if any(call.object_name in client_names and call.method.lower() == "get"
                       and _paths_match(call.path, endpoint["path"]) for call in fn.calls)
            }
            if len(matches) == 1:
                exports.setdefault(path, set()).update(matches)
    return exports


def _workspace_module(path: str, module: str) -> str:
    """定位相对路径和模板别名指向的模块位置。"""
    import posixpath

    if module.startswith("."):
        module = posixpath.normpath(posixpath.join(posixpath.dirname(path), module))
    if module.startswith("@/"):
        module = "frontend/src/" + module[2:]
    return module


def _is_template_service(path: str, module: str) -> bool:
    """只识别模板 service 的确切模块路径。"""
    return _workspace_module(path, module) in {"frontend/src/apis/service", "frontend/src/apis/service.ts"}


def _aliases(imports, module: str, exported: set[str]) -> set[str]:
    """解析 named alias 和 namespace 两种导入形式。"""
    return {
        f"{item.local}.{name}" if item.namespace else item.local
        for item in imports if item.module == module
        for name in exported if item.namespace or name == item.imported
    }


def _runtime_imports(item) -> list[TypeScriptImport]:
    """补齐当前业务 AST 模型未保留的默认导入，用于识别别名 HTTP 客户端。"""
    result = _imports(item)
    for statement in item.root.named_children:
        if statement.type != "import_statement":
            continue
        clause = next((child for child in statement.named_children
                       if child.type == "import_clause"), None)
        source = statement.child_by_field_name("source")
        if clause is None or source is None:
            continue
        default = next((child for child in clause.named_children
                        if child.type == "identifier"), None)
        if default is not None:
            result.append(TypeScriptImport(
                module=_node_text(source, item.source)[1:-1],
                imported="default", local=_node_text(default, item.source),
            ))
    return result


def _bindings(item, nodes: list):
    """仅建立唯一的本地回调绑定，保守处理重名与解构变量。"""
    callbacks = {}
    repeated = set()
    for node in nodes:
        if node.type == "function_declaration":
            name, value = node.child_by_field_name("name"), node
        elif node.type == "variable_declarator":
            name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        else:
            continue
        if name is None or value is None:
            continue
        symbol = _node_text(name, item.source)
        if symbol in callbacks:
            repeated.add(symbol)
        callbacks[symbol] = value
    for name in repeated:
        callbacks.pop(name, None)

    def related(node: Any, symbols: set[str], visited: frozenset[str] = frozenset()) -> set:
        """沿函数值、调用目标和唯一局部绑定追踪具体业务引用。"""
        hits = set()
        for child in _walk(node):
            if child.type == "call_expression":
                target = child.child_by_field_name("function")
            elif child.type in {"identifier", "member_expression"} and child == node:
                target = child
            else:
                continue
            if target is None:
                continue
            symbol = _node_text(target, item.source)
            if (symbol in symbols or f"{symbol.split('.')[0]}.*" in symbols):
                hits.add(child)
            elif symbol in callbacks and symbol not in visited:
                callback = callbacks[symbol]
                nested = related(callback, symbols, visited | {symbol})
                if nested:
                    hits.add(child)
                    hits.update(nested)
        return hits

    return related


def _shadowed_names(item, nodes: list) -> set[str]:
    """识别可能遮蔽全局或导入绑定的局部声明，避免把同名局部函数当成 API。"""
    names = set()
    for node in nodes:
        if node.type in {"function_declaration", "variable_declarator", "class_declaration"}:
            bound = node.child_by_field_name("name")
        elif node.type in {"required_parameter", "optional_parameter"}:
            bound = node.child_by_field_name("pattern")
        elif node.type == "arrow_function":
            bound = node.child_by_field_name("parameter")
        else:
            continue
        if bound is not None and bound.type == "identifier":
            names.add(_node_text(bound, item.source))
    return names


def _rendered_state(item, nodes: list) -> set[str]:
    """收集显示数据与加载态的 JSX 属性中显式引用的变量。"""
    return {
        _node_text(child, item.source)
        for node in nodes if node.type == "jsx_attribute" and len(node.named_children) >= 2
        if _node_text(node.named_children[0], item.source) in _STATE_PROPS
        for child in _walk(node.named_children[1]) if child.type == "identifier"
    }


def _hook_state_names(item, call) -> set[str]:
    """从请求调用的直接赋值中取得结果对象或解构量。"""
    parent = call.parent
    if parent is None or parent.type != "variable_declarator" or parent.child_by_field_name("value") != call:
        return set()
    binding = parent.child_by_field_name("name")
    return {
        _node_text(node, item.source) for node in _walk(binding)
        if node.type in {"identifier", "shorthand_property_identifier_pattern"}
    } if binding is not None else set()


def page_request_findings(
    files: dict[str, str], query_symbols: dict[str, set[str]], entry_path: str = "",
    *, query_expected: bool = False, unresolved_query: bool = False,
) -> list[RequestFinding]:
    """确认真实导入、JSX request 属性和 ahooks 服务回调之间的关系。"""
    model = inspect_typescript_sources(files)
    reach_inputs = ({entry_path: files[entry_path], **files} if entry_path in files else files)
    reachable = _reachable_source_paths(reach_inputs, model)
    findings = []
    seen_managed = set()
    seen_rendered = set()
    expected_symbols = {(path, name) for path, names in query_symbols.items() for name in names}
    for path, source in files.items():
        item = _parse_source(path, source)
        imports = _runtime_imports(item)
        nodes = list(_walk(item.root))
        shadowed = _shadowed_names(item, nodes)
        rendered = _rendered_state(item, nodes)
        business_calls = set()
        identities: dict[str, tuple[str, str]] = {}
        queries = set()
        clients = {"window.fetch", "globalThis.fetch"}
        clients.update(_HTTP_GLOBALS - {binding.local for binding in imports})
        for binding in imports:
            module = binding.module
            if module == "axios" or _is_template_service(path, module):
                clients.add(binding.local)
            if _workspace_module(path, module).startswith("frontend/src/apis/") and (
                not _is_template_service(path, module)
            ):
                business_calls.add(binding.local + ".*" if binding.namespace else binding.local)
            target = _resolve_import_path(path, module, query_symbols)
            if target:
                for export in query_symbols[target]:
                    for alias in _aliases([binding], module, {export}):
                        queries.add(alias)
                        identities[alias] = (target, export)

        request_hooks = _aliases(imports, "ahooks", {"useRequest"})
        components = _aliases(imports, "@ant-design/pro-components", _COMPONENTS)
        # ProFormRadio.Group 等成员保留导入别名的实际前缀。
        components.update(f"{binding.local}.Group" for binding in imports
                          if binding.module == "@ant-design/pro-components"
                          and binding.imported == "ProFormRadio")
        # 无法唯一解析的符号不参与阻断性判断。
        clients = {name for name in clients if name.partition(".")[0] not in shadowed}
        business_calls = {name for name in business_calls if name.partition(".")[0] not in shadowed}
        queries = {name for name in queries if name.partition(".")[0] not in shadowed}
        components = {name for name in components if name.partition(".")[0] not in shadowed}
        if {name.partition(".")[0] for name in request_hooks} & shadowed:
            queries.clear()
        related = _bindings(item, nodes)

        def finding(rule: str, node: Any, uncertain: str = "") -> None:
            """记录可以定位的违规或需要人工确认的调用。"""
            findings.append(RequestFinding(rule, "not_evaluable" if uncertain else "failed",
                                           path, node.start_point.row + 1, uncertain or RULE_MESSAGES[rule]))

        managed = set()
        for node in nodes:
            if node.type == "call_expression":
                target = node.child_by_field_name("function")
                if target is None:
                    continue
                callee = _node_text(target, item.source)
                if callee in clients or callee.split(".", 1)[0] in clients:
                    finding("FE-NO-DIRECT-HTTP", node)
                args = node.child_by_field_name("arguments")
                if callee in request_hooks and args is not None and args.named_children:
                    matched = related(args.named_children[0], queries)
                    managed.update(matched)
                    if path in reachable:
                        state_is_rendered = bool(_hook_state_names(item, node) & rendered)
                        for hit in matched:
                            target = hit.child_by_field_name("function") if hit.type == "call_expression" else hit
                            symbol = _node_text(target, item.source) if target is not None else ""
                            if symbol in identities:
                                seen_managed.add(identities[symbol])
                                if state_is_rendered:
                                    seen_rendered.add(identities[symbol])
            elif node.type in {"jsx_opening_element", "jsx_self_closing_element"}:
                name = node.child_by_field_name("name")
                if name is None or _node_text(name, item.source) not in components:
                    continue
                for attr in node.named_children:
                    if attr.type == "jsx_expression" and any(
                        child.type == "spread_element" for child in attr.named_children
                    ):
                        finding("FE-NO-UI-REQUEST-PROP", attr, "展开的组件属性需要核对是否包含 request。")
                        continue
                    if attr.type != "jsx_attribute" or len(attr.named_children) < 2:
                        continue
                    key, value = attr.named_children[:2]
                    if _node_text(key, item.source) != "request" or value.type != "jsx_expression":
                        continue
                    if not value.named_children or value.named_children[0].type in {"null", "undefined"}:
                        continue
                    tables = _aliases(imports, "@ant-design/pro-components", {"ProTable", "EditableProTable"})
                    if (_node_text(name, item.source) in tables
                        or related(value.named_children[0], business_calls | clients)):
                        finding("FE-NO-UI-REQUEST-PROP", attr)
                    else:
                        finding("FE-NO-UI-REQUEST-PROP", attr, "无法确认 request 回调的数据来源。")
        if path in reachable:
            for node in nodes:
                if node.type != "call_expression" or node in managed:
                    continue
                target = node.child_by_field_name("function")
                if (target is not None and _node_text(target, item.source) not in request_hooks
                    and related(target, queries)):
                    finding("FE-QUERY-USE-REQUEST", node)
    for rule in RULE_MESSAGES:
        if any(item.rule_id == rule and item.status == "failed" for item in findings):
            continue
        if rule == "FE-QUERY-USE-REQUEST":
            if not (query_expected or expected_symbols):
                continue
            if unresolved_query or not expected_symbols or expected_symbols - seen_managed:
                findings.append(RequestFinding(rule, "not_evaluable", entry_path, None,
                                               "无法可靠确认所有声明查询到 ahooks 的调用关系。"))
                continue
            if expected_symbols - seen_rendered:
                findings.append(RequestFinding(rule, "not_evaluable", entry_path, None,
                                               "已确认查询使用 ahooks，展示层状态流需要人工确认。"))
                continue
        if not any(item.rule_id == rule for item in findings):
            findings.append(RequestFinding(rule, "passed", entry_path, None, "规则检查通过。"))
    return list(dict.fromkeys(findings))


def page_request_errors(
    files: dict[str, str], query_symbols: dict[str, set[str]], entry_path: str = "",
) -> list[str]:
    """使用原有错误文本形态展示可确定的违规。"""
    return [f"{item.file_path}:{item.line} {item.rule_id}"
            for item in page_request_findings(files, query_symbols, entry_path)
            if item.status == "failed"]

