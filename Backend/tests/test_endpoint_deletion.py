"""开发阶段接口删除的回归：预演只读、整组清理精确、图入口正确分流。

接口产物是模型自由生成的模块（前端 API 客户端 + 后端模块目录），单个接口没有
可切分的文件边界。因此清理只允许在"整个契约已无存活接口"时整组进行；契约内仍有
存活接口时不动文件，只把引用清单交给用户。
"""

import json
import tempfile
import unittest
from pathlib import Path

from app.services.endpoint_deletion import (
    _camel,
    _endpoint_function_name,
    delete_endpoint_artifacts,
    deleted_endpoint_targets,
    preview_endpoint_deletion,
)

_TECHNICAL_PLAN = {
    "confirmation_status": "confirmed",
    "artifact_type": "technical-plan",
    "entities": [],
    "pages": [{"pageId": "card_return"}, {"pageId": "home"}],
    "api_contracts": [
        {
            "id": "greeting_contract",
            "name": "问候",
            "endpoints": [
                {"id": "greeting_contract.getGreeting", "method": "GET", "path": "/api/greetings",
                 "summary": "返回问候语"},
            ],
        },
        {
            "id": "access_card_contract",
            "name": "门禁卡",
            "endpoints": [
                {"id": "access_card_contract.listAccessCards", "method": "GET",
                 "path": "/api/access-cards", "summary": "查询门禁卡列表"},
                {"id": "access_card_contract.applyCancellation", "method": "PATCH",
                 "path": "/api/access-cards/cancellation", "summary": "发起离职注销"},
            ],
        },
    ],
}

_CONTROLLER = """package com.example.backend.modules.greeting.adapter.web;

@RestController
@RequestMapping("/api/greetings")
public class GreetingController {
}
"""


class EndpointDeletionTests(unittest.TestCase):
    """以真实目录结构验证预演与删除，不触碰其它接口的产物。"""

    def setUp(self) -> None:
        """准备前后端接口产物、技术规划与变更快照。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "frontend/src/apis").mkdir(parents=True)
        (self.root / "frontend/src/pages/CardReturn").mkdir(parents=True)
        (self.root / ".devagentstudio/plans").mkdir(parents=True)
        (self.root / "frontend/src/apis/greetingContractApi.ts").write_text(
            "export function getGreeting() { return service.get('/api/greetings') }\n",
            encoding="utf-8",
        )
        (self.root / "frontend/src/apis/accessCardContractApi.ts").write_text(
            "export function listAccessCards() {}\nexport function applyCancellation() {}\n",
            encoding="utf-8",
        )
        (self.root / "frontend/src/pages/CardReturn/index.tsx").write_text(
            "import { getGreeting } from '@/apis/greetingContractApi'\n", encoding="utf-8",
        )
        module = self.root / "backend/src/main/java/com/example/backend/modules/greeting"
        (module / "adapter/web").mkdir(parents=True)
        (module / "adapter/web/GreetingController.java").write_text(_CONTROLLER, encoding="utf-8")
        (self.root / ".devagentstudio/plans/technical-plan.json").write_text(
            json.dumps(_TECHNICAL_PLAN), encoding="utf-8",
        )

    def _snapshot(self, endpoints: dict[str, dict[str, str]]) -> None:
        """写入本轮接口变更快照。"""

        (self.root / ".devagentstudio/development-change-snapshot.json").write_text(
            json.dumps({"pages": {}, "endpoints": endpoints}), encoding="utf-8",
        )

    def test_contract_id_maps_to_api_file_name(self) -> None:
        """契约 id 转驼峰与前端产物文件名一致。"""

        self.assertEqual(_camel("access_card_contract"), "accessCardContract")
        self.assertEqual(_camel("greeting_contract"), "greetingContract")

    def test_endpoint_function_name_converts_snake_case_to_camel_case(self) -> None:
        """snake_case Endpoint ID 必须能定位生成代码里的 lowerCamelCase 函数。"""

        self.assertEqual(
            _endpoint_function_name("greeting_message_contract.get_good_evening"),
            "getGoodEvening",
        )

    def test_only_deleted_endpoints_are_listed(self) -> None:
        """待删除接口只包含快照里显式删除的项。"""

        self._snapshot({"access_card_contract": {
            "access_card_contract.listAccessCards": "deleted",
            "access_card_contract.applyCancellation": "unchanged",
        }})
        targets = deleted_endpoint_targets(self.root)
        self.assertEqual(list(targets), ["access_card_contract::access_card_contract.listAccessCards"])

    def test_preview_is_read_only_while_contract_has_survivors(self) -> None:
        """契约内仍有存活接口时预演不动文件，且报告仍在引用它的地方。"""

        self._snapshot({"access_card_contract": {
            "access_card_contract.listAccessCards": "deleted",
            "access_card_contract.applyCancellation": "unchanged",
        }})
        preview = preview_endpoint_deletion(
            self.root, "access_card_contract", "access_card_contract.listAccessCards"
        )
        self.assertFalse(preview.contract_removed)
        self.assertEqual(preview.files, ["frontend/src/apis/accessCardContractApi.ts"])
        self.assertTrue(preview.removable)
        # 预演只读：即使列出了可精确部分删除的文件，也不能在执行前修改它。
        self.assertTrue((self.root / "frontend/src/apis/accessCardContractApi.ts").is_file())

    def test_fully_deleted_contract_removes_client_and_module(self) -> None:
        """契约内全部接口都删除后，前端客户端与后端模块整组清理。"""

        self._snapshot({"greeting_contract": {"greeting_contract.getGreeting": "deleted"}})
        preview = preview_endpoint_deletion(
            self.root, "greeting_contract", "greeting_contract.getGreeting"
        )
        self.assertTrue(preview.contract_removed)
        self.assertIn("frontend/src/apis/greetingContractApi.ts", preview.files)
        self.assertEqual(
            preview.backend_module, "backend/src/main/java/com/example/backend/modules/greeting"
        )
        # 预演只读。
        self.assertTrue((self.root / "frontend/src/apis/greetingContractApi.ts").is_file())

        result = delete_endpoint_artifacts(
            self.root, "greeting_contract", "greeting_contract.getGreeting"
        )
        self.assertTrue(result["contractRemoved"])
        self.assertFalse((self.root / "frontend/src/apis/greetingContractApi.ts").exists())
        self.assertFalse(
            (self.root / "backend/src/main/java/com/example/backend/modules/greeting").exists()
        )
        # 其它契约的产物必须完好。
        self.assertTrue((self.root / "frontend/src/apis/accessCardContractApi.ts").is_file())

    def test_partial_deletion_removes_target_java_methods_only(self) -> None:
        """Controller 与 Service 只移除目标方法，保留同模块其它接口。"""

        plan_path = self.root / ".devagentstudio/plans/technical-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["api_contracts"][0]["endpoints"].append({
            "id": "greeting_contract.getOtherGreeting",
            "method": "GET",
            "path": "/api/greetings/other",
            "summary": "其它存活接口",
        })
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        self._snapshot({
            "greeting_contract": {
                "greeting_contract.getGreeting": "deleted",
                "greeting_contract.getOtherGreeting": "unchanged",
            }
        })
        (self.root / "frontend/src/apis/greetingContractApi.ts").write_text(
            "export function getGreeting() {}\nexport function getOtherGreeting() {}\n",
            encoding="utf-8",
        )
        controller = self.root / "backend/src/main/java/com/example/backend/modules/greeting/adapter/web/GreetingController.java"
        controller.write_text(
            "package com.example.backend.modules.greeting.adapter.web;\n"
            "public class GreetingController {\n"
            "  /** 目标接口。 */\n"
            "  @GetMapping(\"/api/greetings\")\n"
            "  public String getGreeting() { return greetingApplicationService.getGreeting(); }\n"
            "  /** 保留接口。 */\n"
            "  @GetMapping(\"/api/greetings/other\")\n"
            "  public String getOtherGreeting() { return greetingApplicationService.getOtherGreeting(); }\n"
            "}\n",
            encoding="utf-8",
        )
        service = self.root / "backend/src/main/java/com/example/backend/modules/greeting/application/service/GreetingApplicationService.java"
        service.parent.mkdir(parents=True, exist_ok=True)
        service.write_text(
            "package com.example.backend.modules.greeting.application.service;\n"
            "public class GreetingApplicationService {\n"
            "  /** 目标方法。 */\n"
            "  public String getGreeting() { return \"hello\"; }\n"
            "  /** 保留方法。 */\n"
            "  public String getOtherGreeting() { return \"other\"; }\n"
            "}\n",
            encoding="utf-8",
        )

        result = delete_endpoint_artifacts(
            self.root, "greeting_contract", "greeting_contract.getGreeting"
        )
        (self.root / "frontend/src/pages/CardReturn/index.tsx").write_text(
            "import { getOtherGreeting } from '@/apis/greetingContractApi'\n",
            encoding="utf-8",
        )

        self.assertIn("frontend/src/apis/greetingContractApi.ts", result["files"])
        for path in (controller, service):
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("getGreeting()", content)
            self.assertIn("getOtherGreeting()", content)
        remaining = preview_endpoint_deletion(
            self.root, "greeting_contract", "greeting_contract.getGreeting"
        )
        self.assertEqual(remaining.referenced_by, [])

    def test_repeat_delete_is_idempotent(self) -> None:
        """已清理过的接口再次预演时不可删，避免空操作被当成成功。"""

        self._snapshot({"greeting_contract": {"greeting_contract.getGreeting": "deleted"}})
        delete_endpoint_artifacts(self.root, "greeting_contract", "greeting_contract.getGreeting")
        preview = preview_endpoint_deletion(
            self.root, "greeting_contract", "greeting_contract.getGreeting"
        )
        self.assertFalse(preview.removable)


class DeleteEndpointNodeTests(unittest.TestCase):
    """已删除接口应转入删除节点，而不是常规开发门禁。"""

    def setUp(self) -> None:
        """准备最小工作区与生命周期快照。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "frontend/src/apis").mkdir(parents=True)
        (self.root / ".devagentstudio/plans").mkdir(parents=True)
        (self.root / "frontend/src/apis/greetingContractApi.ts").write_text("x", encoding="utf-8")
        (self.root / ".devagentstudio/plans/product-plan.json").write_text(
            json.dumps({"confirmation_status": "confirmed", "pages": [{"pageId": "home", "name": "欢迎页"}]}),
            encoding="utf-8",
        )
        (self.root / ".devagentstudio/plans/technical-plan.json").write_text(
            json.dumps({
                **_TECHNICAL_PLAN,
                "api_contracts": [_TECHNICAL_PLAN["api_contracts"][0]],
            }),
            encoding="utf-8",
        )
        (self.root / ".devagentstudio/development-change-snapshot.json").write_text(
            json.dumps({
                "pages": {"home": "unchanged"},
                "endpoints": {"greeting_contract": {"greeting_contract.getGreeting": "deleted"}},
            }),
            encoding="utf-8",
        )
        from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
        from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle

        lifecycle = create_application_lifecycle(application_id="test", application_name="测试")
        lifecycle.initialization.stage = ApplicationLifecycleStage.READY_FOR_WORKBENCH
        lifecycle.initialization.status = ApplicationLifecycleStatus.COMPLETED
        write_application_lifecycle(self.root, lifecycle)

    def _state(self, endpoint_id: str) -> dict:
        """构造一次接口开发请求的最小状态。"""

        return {
            "workspace": str(self.root),
            "selected_api_contract_id": "greeting_contract",
            "selected_endpoint_id": endpoint_id,
            "build_execution_scope": {
                "type": "endpoint", "targetId": endpoint_id, "apiContractId": "greeting_contract",
            },
        }

    def test_route_sends_deleted_endpoint_to_delete_node(self) -> None:
        """已删除接口走删除节点。"""

        from app.graph.workflow import route_workflow_start

        self.assertEqual(
            route_workflow_start(self._state("greeting_contract.getGreeting")),
            "delete_development_endpoint",
        )

    def test_delete_node_reports_cleanup(self) -> None:
        """删除节点清理产物，并在门禁未通过时结束本轮开发。"""

        from app.graph.nodes.development_readiness import delete_development_endpoint

        update = delete_development_endpoint(self._state("greeting_contract.getGreeting"))

        self.assertEqual(update["status"], "completed")
        self.assertTrue(update["build_summary"]["status"] == "completed")
        self.assertFalse((self.root / "frontend/src/apis/greetingContractApi.ts").exists())


    def test_partial_deletion_removes_only_target_typescript_function(self) -> None:
        """共享 API 文件只删除目标函数，保留其它接口并判定清理完成。"""

        plan_path = self.root / ".devagentstudio/plans/technical-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["api_contracts"][0]["endpoints"].append({
            "id": "greeting_contract.getOtherGreeting",
            "method": "GET",
            "path": "/api/greetings/other",
            "summary": "其它存活接口",
        })
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        api_path = self.root / "frontend/src/apis/greetingContractApi.ts"
        api_path.write_text(
            "export function getGreeting() {}\nexport function getOtherGreeting() {}\n",
            encoding="utf-8",
        )
        (self.root / ".devagentstudio/development-change-snapshot.json").write_text(
            json.dumps({
                "pages": {"home": "unchanged"},
                "endpoints": {
                    "greeting_contract": {
                        "greeting_contract.getGreeting": "deleted",
                        "greeting_contract.getOtherGreeting": "unchanged",
                    }
                },
            }),
            encoding="utf-8",
        )
        from app.graph.nodes.development_readiness import delete_development_endpoint

        update = delete_development_endpoint(self._state("greeting_contract.getGreeting"))

        self.assertTrue(update["deleted_endpoint_result"]["completed"])
        self.assertIn("frontend/src/apis/greetingContractApi.ts", update["deleted_endpoint_result"]["files"])
        remaining = api_path.read_text(encoding="utf-8")
        self.assertNotIn("function getGreeting", remaining)
        self.assertIn("function getOtherGreeting", remaining)
        self.assertEqual(update["deleted_endpoint_result"]["referencedBy"], [])

    def test_incomplete_deletion_returns_continue_development_selection(self) -> None:
        """残留引用阻断删除时结束本轮并展示继续开发，而不是卡在通用确认页。"""

        plan_path = self.root / ".devagentstudio/plans/technical-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["api_contracts"][0]["endpoints"].append({
            "id": "greeting_contract.getOtherGreeting",
            "method": "GET",
            "path": "/api/greetings/other",
            "summary": "其它存活接口",
        })
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        (self.root / "frontend/src/apis/greetingContractApi.ts").write_text(
            "export function getGreeting() {}\nexport function getOtherGreeting() {}\n",
            encoding="utf-8",
        )
        (self.root / ".devagentstudio/development-change-snapshot.json").write_text(
            json.dumps({
                "pages": {"home": "unchanged"},
                "endpoints": {
                    "greeting_contract": {
                        "greeting_contract.getGreeting": "deleted",
                        "greeting_contract.getOtherGreeting": "unchanged",
                    }
                },
            }),
            encoding="utf-8",
        )
        from app.graph.nodes.development_readiness import delete_development_endpoint

        # 即使共享 API 文件中的函数已精确移除，页面里的调用残留仍会阻止删除完成。
        (self.root / "frontend/src/pages/CardReturn").mkdir(parents=True, exist_ok=True)
        (self.root / "frontend/src/pages/CardReturn/index.tsx").write_text(
            "import { getGreeting } from '@/apis/greetingContractApi'\n",
            encoding="utf-8",
        )
        update = delete_development_endpoint(self._state("greeting_contract.getGreeting"))

        self.assertEqual(update["status"], "completed")
        self.assertEqual(update["integration_next_action"], "end")
        self.assertEqual(update["clarification"]["mode"], "development_target_selection")
        self.assertNotEqual(update["clarification"]["mode"], "test_phase_confirmation")
        self.assertFalse(update["deleted_endpoint_result"]["completed"])
        self.assertTrue(update["deleted_endpoint_result"]["referencedBy"])
        blockers = update["clarification"]["testEntryGate"]["blockers"]
        self.assertTrue(any(
            target.get("endpointId") == "greeting_contract.getGreeting"
            for target in blockers
        ))


if __name__ == "__main__":
    unittest.main()
