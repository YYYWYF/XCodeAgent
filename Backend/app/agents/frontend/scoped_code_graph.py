"""在任务或步骤精确授权的 TypeScript 文件上构建符号视图。"""

import json

from langchain_core.tools import tool

from app.agents.frontend.task_packet import SERVICE
from app.services.business_acceptance_verifiers.typescript_ast import (
    TypeScriptAstError,
    inspect_typescript_sources,
)


def create_scoped_code_graph_tool(scope):
    """建立无需扫描全局图、只返回授权文件导出的代码图工具。"""

    @tool("code_graph_context")
    def code_graph_context(paths: list[str]) -> str:
        """在授权范围内核对指定虚拟文件的导出和相关业务模块依赖。"""
        with scope.lock:
            query = tuple(sorted(set(paths)))
            try:
                if (not scope.task_level and scope.step != 1) or not query or len(query) > 20:
                    raise ValueError("仅活动 API 任务或页面 Step 1 可查询明确的任务文件")
                fresh = set(query) - scope.graph_queries
                if not fresh:
                    raise ValueError("该范围已经查询，请复用已有代码图结果")
                if any(not path.startswith("/frontend/") or not path.endswith((".ts", ".tsx"))
                       or path == SERVICE for path in query):
                    raise ValueError("代码图只接收任务授权的 TypeScript 文件")
                physical = {path: scope.authorize("read_file", path)[1] for path in query}
            except (ValueError, PermissionError) as exc:
                scope.record("frontend_tool_permission", tool_name="code_graph_context",
                             operation="graph", permission_decision="denied",
                             permission_denial_reason=str(exc))
                return str(exc)
            scope.graph_queries.update(fresh)
            scope.record("frontend_tool_permission", tool_name="code_graph_context",
                         operation="graph", permission_decision="allowed",
                         code_graph_query_scope=query)
            results = []
            for path, target in physical.items():
                if path not in fresh:
                    results.append({"path": path, "status": "previously_reported"})
                    continue
                try:
                    with target.open(encoding="utf-8") as stream:
                        source = stream.read(256_001)
                    if len(source) > 256_000:
                        raise ValueError("代码图输入超过长度限制")
                    model = inspect_typescript_sources({path: source})
                    results.append({"path": path,
                                    "exports": [fn.export_symbol for fn in model.functions][:40]})
                except (OSError, TypeScriptAstError, ValueError):
                    results.append({"path": path, "status": "unavailable"})
            return json.dumps(results, ensure_ascii=False)

    return code_graph_context
