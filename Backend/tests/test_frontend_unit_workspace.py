"""验证前端规划摘要精简后的事实保留、工程归属及冻结边界。"""

from copy import deepcopy
import unittest

from app.agents.main.frontend_unit_workspace import project_frontend_workspace_context
from app.agents.main.unit_task_prompt import _prompt_context, build_unit_generation_prompt
from app.services.unit_generation_contracts import UnitGenerationContext
from tests.test_unit_generation_contracts import _context_payload


def _snapshot() -> dict:
    """构造含前后端、重复符号及仅在代码图出现的 HTTP Service 的当前快照。"""

    return {
        "schema_version": "1.2.0", "workspace_revision": "revision", "generated_at": "time",
        "project_roots": [
            {"path": "frontend/src", "kind": "frontend", "description": "前端源码"},
            {"path": "backend", "kind": "backend"},
        ],
        "tech_stack": ["React", "Ant Design", "Spring Boot"],
        "entrypoints": [
            {"path": "frontend/src/App.tsx", "kind": "frontend_app"},
            {"path": "backend/Main.java", "kind": "backend_main"},
            {"path": "frontend-other/main.ts", "kind": "other"},
        ],
        "build_commands": [{"command": "pnpm build"}],
        "test_commands": [{"command": "pnpm test"}],
        "backend": {"dir_structure": "backend/Main.java", "api_routes": ["/backend-route"]},
        "frontend": {
            "components": [
                {"path": "frontend/src/icons.tsx", "name": "ICON_A"},
                {"path": "frontend/src/icons.tsx", "name": "ICON_B"},
                {"path": "frontend/src/icons.tsx", "name": "ICON_A"},
                {"path": "frontend/src/pages/Orders/index.tsx", "name": "Orders"},
            ],
            "pages": [{"path": "frontend/src/pages/Orders/index.tsx"}],
            "api_clients": [{"path": "frontend/src/apis/requests.ts", "kind": "http_client"}],
            "ipc_calls": [{"path": "frontend/src/preload.ts", "channel": "read-only"}],
            "ag_ui_usage": [{"path": "frontend/src/workflow.ts", "endpoint": "/workflow/run"}],
        },
        "shared_contracts": [
            {"path": "frontend/src/types/order.ts"}, {"path": "shared/contracts/order.ts"},
        ],
        "high_value_files": [
            {"path": "frontend/package.json"}, {"path": "frontend/vite.config.ts"},
            {"path": "backend/pom.xml"}, {"path": "frontend-other/package.json"},
        ],
        "code_graph": {
            "status": "partial", "warnings": ["部分符号未索引"], "filesIndexed": 100,
            "cacheHit": True, "durationMs": 200, "progress": {"current": 100},
            "sampleSymbols": [
                {"path": "frontend/src/apis/service.ts", "name": "Service", "kind": "class",
                 "language": "typescript", "lineStart": 53, "lineEnd": 90},
                {"path": "backend/Repository.java", "name": "Repository", "kind": "class"},
            ],
        },
        "risk_notes": ["清单可能被截断", "不得覆盖托管文件"],
        "file_manifest": {"truncated": True, "total_files_indexed": 4000, "source_files_indexed": 900},
    }


def _context(unit_id: str = "page:orders", kind: str = "page") -> UnitGenerationContext:
    """提供精确职责、合同清单、已有依赖和写入限制，检查只修改模型工程摘要。"""

    value = _context_payload()
    value["unit_id"], value["unit_kind"] = unit_id, kind
    value["workspace_context"] = _snapshot()
    if unit_id.startswith("frontend:"):
        deliverable = "frontend.api_module" if unit_id == "frontend:api-client" else "frontend.static_data_module"
        value["generation_requirements"] = [{
            "requirement_id": deliverable + ":orders-api:orders.list",
            "description": "实现订单访问",
            "source_refs": {"kind": deliverable, "api_contract_id": "orders-api", "endpoint_id": "orders.list"},
        }]
    return UnitGenerationContext.model_validate(value)


class FrontendUnitWorkspaceTests(unittest.TestCase):
    """精简提示词时保留事实，不把投影结果变成第二份权威输入。"""

    def test_drops_cross_backend_and_run_statistics(self) -> None:
        """删除无关后端及执行统计，同时保留风险提示和真实截断状态。"""

        result = project_frontend_workspace_context(_snapshot())
        for key in ("backend", "build_commands", "test_commands", "workspace_revision",
                    "generated_at", "schema_version"):
            self.assertNotIn(key, result)
        self.assertEqual(set(result["code_graph"]), {"sampleSymbols", "status", "warnings"})
        self.assertEqual(result["file_manifest"], {"truncated": True})
        self.assertEqual(result["risk_notes"], _snapshot()["risk_notes"])
        self.assertEqual(result["shared_contracts"], _snapshot()["shared_contracts"])
        self.assertEqual(result["tech_stack"], _snapshot()["tech_stack"])

    def test_component_grouping_retains_every_name_and_exact_path(self) -> None:
        """重复路径只传一次，已有名称保持顺序且不按数量裁剪。"""

        source = _snapshot()
        names = [f"ICON_{i}" for i in range(200)]
        source["frontend"]["components"] = [
            {"path": "frontend/src/icons.tsx", "name": name} for name in names
        ]
        source["frontend"]["components"].append({"path": "frontend/src/icons.tsx", "name": names[0]})
        result = project_frontend_workspace_context(source)
        self.assertEqual(result["frontend"]["components"], [{
            "path": "frontend/src/icons.tsx", "names": names,
        }])

    def test_extra_component_facts_are_preserved_without_reinterpretation(self) -> None:
        """有额外字段的记录不能因路径分组丢失行号或其他已有事实。"""

        source = _snapshot()
        record = {"path": "frontend/src/Widget.tsx", "name": "Widget", "lineStart": 10}
        source["frontend"]["components"].append(record)
        result = project_frontend_workspace_context(source)
        self.assertIn(record, result["frontend"]["components"])

    def test_service_and_symbol_ranges_survive_graph_statistics_removal(self) -> None:
        """只出现在代码图里的 HTTP Service 路径、类型及行号仍完整可见。"""

        source = _snapshot()
        result = project_frontend_workspace_context(source)
        self.assertEqual(result["code_graph"]["sampleSymbols"], [source["code_graph"]["sampleSymbols"][0]])
        for key in ("pages", "api_clients", "ipc_calls", "ag_ui_usage"):
            self.assertEqual(result["frontend"][key], source["frontend"][key])

    def test_frontend_ownership_has_directory_boundaries_and_keeps_configuration(self) -> None:
        """同名前缀不能误判归属，源码根之外的同工程 package 和 Vite 文件应保留。"""

        result = project_frontend_workspace_context(_snapshot())
        self.assertEqual(result["project_roots"], [_snapshot()["project_roots"][0]])
        self.assertEqual(result["entrypoints"], [_snapshot()["entrypoints"][0]])
        self.assertEqual(result["high_value_files"], [
            {"path": "frontend/package.json"}, {"path": "frontend/vite.config.ts"},
        ])

    def test_declared_roots_work_without_hard_coded_frontend_directory(self) -> None:
        """按当前快照声明的工程根判断，不把目录名当成版本或兼容别名。"""

        source = _snapshot()
        source["project_roots"] = [{"path": "apps/web/src", "kind": "frontend"}]
        source["high_value_files"] = [
            {"path": "apps/web/package.json"}, {"path": "apps/web-other/package.json"},
        ]
        result = project_frontend_workspace_context(source)
        self.assertEqual(result["high_value_files"], [{"path": "apps/web/package.json"}])

    def test_unknown_ownership_keeps_existing_path_evidence(self) -> None:
        """缺少根目录信息时不猜归属，也不把已有路径事实当作不存在。"""

        source = _snapshot()
        del source["project_roots"]
        result = project_frontend_workspace_context(source)
        for key in ("entrypoints", "high_value_files"):
            self.assertEqual(result[key], source[key])
        self.assertEqual(result["code_graph"]["sampleSymbols"], source["code_graph"]["sampleSymbols"])
        self.assertNotIn("project_roots", result)

    def test_projection_neither_mutates_nor_aliases_original_snapshot(self) -> None:
        """分组、后续模型投影修改均不能改变调用方或冻结快照。"""

        source = _snapshot()
        before = deepcopy(source)
        result = project_frontend_workspace_context(source)
        result["frontend"]["components"][0]["names"].append("NEW")
        result["code_graph"]["sampleSymbols"][0]["lineStart"] = 0
        result["risk_notes"].clear()
        self.assertEqual(source, before)

    def test_all_frontend_unit_kinds_keep_authoritative_business_context(self) -> None:
        """页面、API 和静态数据 Unit 都只改变工程投影，合同、约束与依赖逐字段不变。"""

        for unit, kind in (("page:orders", "page"), ("frontend:api-client", "frontend"),
                           ("frontend:data:static", "frontend")):
            with self.subTest(unit=unit):
                context = _context(unit, kind)
                before = context.model_dump(mode="json")
                projected = _prompt_context(context)
                for key, value in before.items():
                    if key != "workspace_context":
                        self.assertEqual(projected[key], value)
                prompt = build_unit_generation_prompt(context)
                self.assertIn("frontend/src/apis/service.ts", prompt)
                self.assertIn("frozen-contract-orders", prompt)
                self.assertIn("input-digest", prompt)
                self.assertIn("托管文件", prompt)
                self.assertNotIn("filesIndexed", prompt)
                self.assertEqual(context.model_dump(mode="json"), before)

    def test_backend_directory_projection_stays_unchanged(self) -> None:
        """后端仍只投影原目录树，不套用新的前端分组或元数据处理。"""

        value = _context_payload()
        value["unit_id"], value["unit_kind"] = "backend:bootstrap", "backend"
        value["workspace_context"] = _snapshot()
        context = UnitGenerationContext.model_validate(value)
        self.assertEqual(_prompt_context(context)["workspace_context"], {
            "backend": {"dir_structure": "backend/Main.java"},
        })

    def test_empty_snapshot_does_not_invent_file_or_truncation_facts(self) -> None:
        """快照缺失字段时不捏造文件、组件、工程根或完整性声明。"""

        self.assertEqual(project_frontend_workspace_context({}), {})
