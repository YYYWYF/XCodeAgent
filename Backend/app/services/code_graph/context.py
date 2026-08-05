from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.code_graph.manager import CodeGraphManager, get_code_graph_manager
from app.services.code_graph.models import CodeGraphQuery


class CodeGraphContextResolver:
    """把代码图查询结果裁剪为 Planner 和 Agent 的 bounded context。"""

    def __init__(self, manager: CodeGraphManager | None = None) -> None:
        """绑定指定管理器，默认使用后端进程级管理器。"""

        self.manager = manager or get_code_graph_manager()

    def resolve(
        self,
        workspace_root: Path,
        *,
        operation: str,
        query: str = "",
        paths: list[str] | None = None,
        direction: str = "both",
        max_results: int = 20,
        max_depth: int = 2,
    ) -> dict[str, Any]:
        """执行一次受限查询并返回稳定的 JSON 结构。"""

        request = CodeGraphQuery(
            operation=operation,
            query=str(query or "")[:500],
            paths=tuple(str(item)[:500] for item in (paths or [])[:20]),
            direction=direction,
            max_results=max(1, min(max_results, 40)),
            max_depth=max(0, min(max_depth, 2)),
        )
        return self.manager.query(workspace_root, request).as_dict()

    def planning_context(
        self,
        workspace_root: Path,
        task: str,
        *,
        paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """为任务规划生成有限文件、符号、关系和测试上下文。"""

        result = self.resolve(
            workspace_root,
            operation="search_symbols",
            query=task,
            paths=paths,
            max_results=20,
        )
        matches = (
            result.get("matches")
            if isinstance(result.get("matches"), list)
            else []
        )
        # CRG 的多词搜索采用 AND 语义；自然语言任务通常包含“修改、接口、
        # 相关”等描述词，直接把整句交给图查询很容易得到空集。因此先尝试
        # 完整任务，再用少量代码风格 token 补充候选，仍然由后续真实源码读取
        # 做最终确认。
        seen_matches: set[str] = set()
        normalized_matches: list[dict[str, Any]] = []
        for match in matches:
            if not isinstance(match, dict):
                continue
            key = str(
                match.get("qualifiedName")
                or match.get("path")
                or match.get("name")
                or ""
            )
            if key and key not in seen_matches:
                seen_matches.add(key)
                normalized_matches.append(match)
        for search_query in _planning_search_queries(task):
            if len(normalized_matches) >= 20:
                break
            token_result = self.resolve(
                workspace_root,
                operation="search_symbols",
                query=search_query,
                paths=paths,
                max_results=min(8, 20 - len(normalized_matches)),
            )
            token_matches = token_result.get("matches")
            if not isinstance(token_matches, list):
                continue
            for match in token_matches:
                if not isinstance(match, dict):
                    continue
                key = str(
                    match.get("qualifiedName")
                    or match.get("path")
                    or match.get("name")
                    or ""
                )
                if key and key not in seen_matches:
                    seen_matches.add(key)
                    normalized_matches.append(match)
        matches = normalized_matches[:20]
        relevant_files: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        related_tests: list[dict[str, Any]] = []
        seen: set[str] = set()
        seen_relations: set[str] = set()
        seen_tests: set[str] = set()
        for match in matches:
            if not isinstance(match, dict):
                continue
            path = str(match.get("path") or "")
            if not path or path in seen:
                continue
            seen.add(path)
            relevant_files.append(
                {
                    "path": path,
                    "reason": "代码图匹配当前任务描述",
                    "symbols": [
                        {
                            "name": match.get("name"),
                            "kind": match.get("kind"),
                            "lineStart": match.get("lineStart"),
                            "lineEnd": match.get("lineEnd"),
                        }
                    ],
                }
            )
            qualified_name = str(match.get("qualifiedName") or "").strip()
            if qualified_name and len(relevant_files) <= 10:
                reference_result = self.resolve(
                    workspace_root,
                    operation="references",
                    query=qualified_name,
                    max_results=8,
                )
                for relation in reference_result.get("relations", []):
                    if not isinstance(relation, dict):
                        continue
                    relation_key = json.dumps(
                        relation,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if relation_key not in seen_relations:
                        seen_relations.add(relation_key)
                        relations.append(relation)
                test_result = self.resolve(
                    workspace_root,
                    operation="related_tests",
                    query=qualified_name,
                    max_results=4,
                )
                for test in test_result.get("relatedTests", []):
                    if not isinstance(test, dict):
                        continue
                    test_key = str(test.get("qualifiedName") or test.get("path") or "")
                    if test_key and test_key not in seen_tests:
                        seen_tests.add(test_key)
                        related_tests.append(test)
        result_context = {
            "schemaVersion": "xcodeagent.workspace-planning-context.v1",
            "status": result.get("status"),
            "workspaceRevision": result.get("workspaceRevision"),
            "relevantFiles": relevant_files[:20],
            "relations": relations[:80],
            "relatedTests": related_tests[:20],
            "truncated": bool(result.get("truncated")),
            "message": (
                f"找到 {len(matches)} 个相关代码节点。"
                if matches
                else str(result.get("message") or "")[:1_000]
            ),
            "fallback": result.get("fallback", ""),
        }
        # 与单次 Agent 工具查询保持同一上下文预算。
        while len(json.dumps(result_context, ensure_ascii=False)) > 16_384:
            if result_context["relations"]:
                result_context["relations"] = result_context["relations"][:-10]
            elif result_context["relevantFiles"]:
                result_context["relevantFiles"] = result_context["relevantFiles"][:-2]
            elif result_context["relatedTests"]:
                result_context["relatedTests"] = result_context["relatedTests"][:-5]
            else:
                result_context["truncated"] = True
                break
            result_context["truncated"] = True
        return result_context


_PLANNING_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_PLANNING_STOP_WORDS = {
    "and",
    "api",
    "the",
    "with",
    "from",
    "into",
    "modify",
    "update",
    "change",
    "implement",
    "fix",
    "please",
}


def _planning_search_queries(task: str) -> list[str]:
    """从自然语言任务中提取少量可用于符号定位的候选 token。"""

    queries: list[str] = []
    seen: set[str] = set()
    for token in _PLANNING_TOKEN_RE.findall(task):
        normalized = token.strip()
        if normalized.casefold() in _PLANNING_STOP_WORDS or normalized in seen:
            continue
        seen.add(normalized)
        queries.append(normalized)
        if len(queries) >= 8:
            break
    return queries
