"""只精简前端 Unit 的模型可见工程摘要，不修改权威工作区快照。"""

from collections.abc import Mapping
from typing import Any

from app.services.planning_frozen import plain_json


def _compact_components(components: object) -> object:
    """按原始路径合并组件名，完整保留符号及无法无损合并的其他记录。"""

    if not isinstance(components, list):
        return components
    result: list[object] = []
    grouped: dict[str, dict[str, Any]] = {}
    for item in components:
        if (
            not isinstance(item, dict) or set(item) != {"path", "name"}
            or not isinstance(item["path"], str) or not isinstance(item["name"], str)
        ):
            result.append(item)
            continue
        path, name = item["path"], item["name"]
        if path not in grouped:
            grouped[path] = {"path": path, "names": []}
            result.append(grouped[path])
        names = grouped[path]["names"]
        if name not in names:
            names.append(name)
    return result


def _belongs_to_frontend(item: object, roots: tuple[str, ...]) -> bool:
    """仅按已有前端工程根或显式入口类型判定归属，不猜测或拼接文件路径。"""

    if not roots:
        # 无法判断归属时保留已有路径事实，不能据缺省元数据声称文件不存在。
        return True
    if not isinstance(item, dict):
        return False
    kind = item.get("kind")
    if isinstance(kind, str) and kind.startswith("frontend"):
        return True
    path = item.get("path")
    return isinstance(path, str) and any(
        path == root or path.startswith(root + "/") for root in roots
    )


def project_frontend_workspace_context(workspace: Mapping[str, Any]) -> dict[str, Any]:
    """移除跨端与运行统计，保留前端文件事实、共享契约及风险和截断信息。"""

    snapshot = plain_json(workspace)
    projected = {
        key: snapshot[key]
        for key in ("tech_stack", "shared_contracts", "risk_notes")
        if key in snapshot
    }
    project_roots = snapshot.get("project_roots")
    frontend_roots = [
        item for item in project_roots
        if isinstance(item, dict) and item.get("kind") == "frontend"
    ] if isinstance(project_roots, list) else []
    # src 下的工程事实及同工程配置文件都属于已有前端根，输出路径仍保留原文。
    roots = tuple(
        item["path"].removesuffix("/src").removesuffix("/")
        for item in frontend_roots
        if isinstance(item.get("path"), str) and item["path"]
    )
    if "project_roots" in snapshot:
        projected["project_roots"] = frontend_roots
    for key in ("entrypoints", "high_value_files"):
        if key in snapshot:
            entries = snapshot[key]
            projected[key] = [
                item for item in entries if _belongs_to_frontend(item, roots)
            ] if isinstance(entries, list) else entries

    if "frontend" in snapshot:
        frontend = snapshot["frontend"]
        if isinstance(frontend, dict) and "components" in frontend:
            frontend["components"] = _compact_components(frontend["components"])
        projected["frontend"] = frontend

    graph = snapshot.get("code_graph")
    if isinstance(graph, dict):
        # Service 等真实文件可能只在符号样例中出现，不能随代码图统计一起丢弃。
        graph_facts = {key: graph[key] for key in ("status", "warnings") if key in graph}
        if "sampleSymbols" in graph:
            symbols = graph["sampleSymbols"]
            graph_facts["sampleSymbols"] = [
                item for item in symbols if _belongs_to_frontend(item, roots)
            ] if isinstance(symbols, list) else symbols
        projected["code_graph"] = graph_facts
    manifest = snapshot.get("file_manifest")
    if isinstance(manifest, dict) and "truncated" in manifest:
        projected["file_manifest"] = {"truncated": manifest["truncated"]}
    return projected
