"""复用前端工程规则，以同一证据形态服务步骤与现有任务验收。"""

from collections.abc import Callable
from dataclasses import asdict
from hashlib import sha256

from app.services.business_acceptance_verifiers.typescript_ast import TypeScriptAstError, _parse_source
from app.services.frontend_request_acceptance import (
    RULE_MESSAGES,
    RequestFinding,
    page_request_findings,
    query_exports,
)

_STRUCTURE = "FE-PAGE-STRUCTURE"
_MAX_SOURCE_BYTES = 512_000


def evaluate_frontend_page(
    *, task_id: str, step_id: str, entry: str, targets: list[str],
    queries: list[dict], dependencies: list[dict], read: Callable[[str], bytes],
    structure_only: bool = False,
) -> list[dict]:
    """用有限源码快照独立检查页面，返回规则结果和版本指纹。"""
    versions = {}

    def read_source(path: str) -> str:
        """只接受有界 UTF-8 文件，保留原始字节指纹。"""
        raw = read(path)
        if len(raw) > _MAX_SOURCE_BYTES:
            raise ValueError("源码超出检查大小限制")
        versions[path] = sha256(raw).hexdigest()
        return raw.decode("utf-8")

    def result(finding: RequestFinding) -> dict:
        """附加任务、步骤与文件信息，不保留源码。"""
        return {"task_id": task_id, "step_id": step_id, **asdict(finding),
                "file_version": versions.get(finding.file_path)}

    if structure_only:
        status, detail = "passed", "页面入口存在且可解析。"
        try:
            _parse_source(entry, read_source(entry))
        except FileNotFoundError:
            status, detail = "failed", "请先完成任务声明的页面入口。"
        except TypeScriptAstError:
            status, detail = "failed", "页面入口存在语法错误，请修正后继续。"
        except (OSError, RuntimeError, ValueError):
            status, detail = "not_evaluable", "页面入口无法安全读取或解析。"
        return [result(RequestFinding(_STRUCTURE, status, entry, None, detail))]

    files, findings, symbols, paths = {}, [], {}, {}
    applicable = [rule for rule in RULE_MESSAGES if queries or rule != "FE-QUERY-USE-REQUEST"]
    for path in dict.fromkeys(targets):
        try:
            source = read_source(path)
            _parse_source(path, source)
            relative = path.lstrip("/")
            files[relative] = source
            paths[relative] = path
        except (OSError, RuntimeError, ValueError):
            findings.extend(RequestFinding(rule, "not_evaluable", path, None,
                                           "此文件无法安全读取或解析。") for rule in applicable)

    unresolved = {(item["api_contract_id"], item["endpoint_id"]): item for item in queries}
    for module in dependencies:
        if module["kind"] != "frontend.api_module":
            continue
        pairs = {(item["api_contract_id"], item["endpoint_id"]) for item in module["endpoints"]}
        matches = pairs & unresolved.keys()
        if not matches:
            continue
        path = module["path"]
        try:
            relative = path.lstrip("/")
            source = {relative: read_source(path)}
            for identity in sorted(matches):
                resolved = query_exports(source, [unresolved[identity]])
                if resolved:
                    symbols.setdefault(relative, set()).update(resolved[relative])
                    del unresolved[identity]
        except (OSError, RuntimeError, ValueError):
            continue
    if files:
        try:
            inspected = page_request_findings(
                files, symbols, entry.lstrip("/"), query_expected=bool(queries),
                unresolved_query=bool(unresolved),
            )
        except (RuntimeError, ValueError):
            inspected = [RequestFinding(rule, "not_evaluable", entry, None,
                                        "无法可靠完成此页面的 AST 分析。") for rule in applicable]
        uncertain_rules = {item.rule_id for item in findings}
        findings.extend(RequestFinding(item.rule_id, item.status, paths.get(item.file_path, entry),
                                       item.line, item.message) for item in inspected
                        if item.status != "passed" or item.rule_id not in uncertain_rules)
    return [result(item) for item in findings]
