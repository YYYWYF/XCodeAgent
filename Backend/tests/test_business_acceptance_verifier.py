"""DAG 业务验收确定性 verifier 的正反例、阻断和重跑测试。"""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.services.business_acceptance import BUSINESS_ACCEPTANCE_KINDS, compile_business_acceptance
from app.services.business_acceptance_verifier import verify_business_acceptance
from app.services.business_acceptance_verifiers.typescript_inspection import (
    verify_api_contract_source,
)
from tests.test_business_acceptance import _formal_context, _task


def _compiled_task(kind: str, path: str, *, unit_id: str = "frontend:api-client") -> tuple[dict, dict]:
    """构造一个带当前正式来源的单任务和 ProjectPlan。"""

    context = _formal_context()
    task = _task(
        kind,
        path=path,
        owner="frontend" if kind.startswith("frontend.") else "backend",
        unit_id=unit_id,
        target_id="orders.list" if kind == "backend.endpoint_controller" else "Order",
    )
    return compile_business_acceptance([task], context)[0], context["project_plan"]


class BusinessAcceptanceVerifierTests(unittest.TestCase):
    """验证 verifier 只接受当前工作区和当前正式来源的确定性证据。"""

    def test_frontend_api_contract_passes_with_cross_platform_target(self) -> None:
        """TypeScript API 模块在 Windows 分隔符目标下应通过。"""

        task, formal = _compiled_task(
            "frontend.api_module",
            "frontend\\src\\apis\\orders.ts",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "frontend" / "src" / "apis" / "orders.ts"
            target.parent.mkdir(parents=True)
            target.write_text(
                """
                type OrderRequest = { status: string }
                type OrderResponse = { id: string }
                export async function listOrders(params: OrderRequest): Promise<OrderResponse> {
                  return service.get('/orders', params)
                }
                """,
                encoding="utf-8",
            )
            result = verify_business_acceptance(task, temp_dir, formal_artifacts=formal)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["business_acceptance_summary"]["failed"], 0)
        self.assertEqual(
            result["business_acceptance_summary"]["by_kind"]["frontend.api_contract"]["passed"],
            1,
        )

    def test_frontend_api_ast_supports_generic_service_calls_and_interface_extends(self) -> None:
        """API AST 必须识别 service.get 泛型调用、interface 继承和整数类型。"""

        source = """
        interface PaginationParams { current?: number; pageSize?: number }
        interface RoleResourceListParams extends PaginationParams { roleId?: number }
        interface RoleListItem { id: number; name: string }
        interface RoleListOutput { total: number; list: RoleListItem[] }
        export function getRoleList(params?: RoleResourceListParams): Promise<RoleListOutput> {
          return service.get<RoleListOutput>('/api/roles', params)
        }
        """
        expected = {
            "endpoints": [
                {
                    "endpoint_id": "role_api.list",
                    "method": "GET",
                    "path": "/api/roles",
                    "request_schema": {
                        "type": "object",
                        "properties": {
                            "current": {"type": "integer"},
                            "pageSize": {"type": "integer"},
                            "roleId": {"type": "integer"},
                        },
                    },
                    "response_schema": {
                        "type": "object",
                        "required": ["total", "list"],
                        "properties": {
                            "total": {"type": "integer"},
                            "list": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["id", "name"],
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "name": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                    "parameters": [],
                }
            ]
        }

        result = verify_api_contract_source({"frontend/src/apis/homeApi.ts": source}, expected)

        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(
            result["facts"]["endpoint_exports"][0]["export_symbol"],
            "getRoleList",
        )

    def test_wrong_endpoint_and_comment_only_evidence_fail(self) -> None:
        """错误路径和只存在于注释中的实现证据都必须失败。"""

        task, formal = _compiled_task(
            "frontend.api_module",
            "frontend/src/apis/orders.ts",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "frontend" / "src" / "apis" / "orders.ts"
            target.parent.mkdir(parents=True)
            target.write_text(
                "// export function listOrders() { return service.get('/orders') }\n",
                encoding="utf-8",
            )
            result = verify_business_acceptance(task, temp_dir, formal_artifacts=formal)
        self.assertEqual(result["status"], "failed")
        self.assertGreater(result["business_acceptance_summary"]["failed"], 0)

    def test_stale_formal_hash_is_blocked(self) -> None:
        """正式 API schema 变化后，旧业务检查必须进入 blocked。"""

        task, formal = _compiled_task(
            "frontend.api_module",
            "frontend/src/apis/orders.ts",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "frontend" / "src" / "apis" / "orders.ts"
            target.parent.mkdir(parents=True)
            target.write_text(
                "export async function listOrders(): Promise<unknown> { return service.get('/orders') }",
                encoding="utf-8",
            )
            formal["api_contracts"][0]["schemas"]["OrderResponse"]["properties"]["newField"] = {
                "type": "string"
            }
            result = verify_business_acceptance(task, temp_dir, formal_artifacts=formal)
        self.assertEqual(result["status"], "blocked")
        self.assertGreater(result["business_acceptance_summary"]["blocked"], 0)

    def test_missing_formal_artifacts_and_verifier_exception_are_blocked(self) -> None:
        """缺少正式产物或 verifier 异常不得被当作通过。"""

        task, _ = _compiled_task(
            "frontend.api_module",
            "frontend/src/apis/orders.ts",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "frontend" / "src" / "apis" / "orders.ts"
            target.parent.mkdir(parents=True)
            target.write_text("export const listOrders = () => service.get('/orders')", encoding="utf-8")
            missing = verify_business_acceptance(task, temp_dir, formal_artifacts=None)
        self.assertEqual(missing["status"], "blocked")

    def test_page_endpoint_usage_consumes_dependency_evidence(self) -> None:
        """页面检查应使用依赖 API 任务的导出证据并拒绝未调用接口的页面。"""

        api_task, formal = _compiled_task(
            "frontend.api_module",
            "frontend/src/apis/orders.ts",
        )
        page_task, _ = _compiled_task(
            "frontend.page",
            "frontend/src/pages/Orders/index.tsx",
            unit_id="page:orders",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            api_file = Path(temp_dir) / "frontend" / "src" / "apis" / "orders.ts"
            page_file = Path(temp_dir) / "frontend" / "src" / "pages" / "Orders" / "index.tsx"
            api_file.parent.mkdir(parents=True)
            page_file.parent.mkdir(parents=True)
            api_file.write_text(
                "type OrderRequest = { status: string }\n"
                "type OrderResponse = { id: string }\n"
                "export async function listOrders(params: OrderRequest): Promise<OrderResponse> { "
                "return service.get('/orders', params) }",
                encoding="utf-8",
            )
            page_file.write_text(
                "import { listOrders } from '../../apis/orders'\nexport default function Orders() { listOrders(); return null }",
                encoding="utf-8",
            )
            api_result = verify_business_acceptance(api_task, temp_dir, formal_artifacts=formal)
            page_result = verify_business_acceptance(
                page_task,
                temp_dir,
                formal_artifacts=formal,
                dependency_evidence=api_result["business_acceptance_evidence"],
            )
        self.assertEqual(api_result["status"], "passed")
        self.assertEqual(page_result["status"], "passed", page_result)

    def test_page_endpoint_usage_tracks_import_alias_and_rejects_unbound_same_name(self) -> None:
        """页面 AST 应接受 import 别名，并拒绝没有 API import 的本地同名调用。"""

        api_task, formal = _compiled_task(
            "frontend.api_module",
            "frontend/src/apis/orders.ts",
        )
        page_task, _ = _compiled_task(
            "frontend.page",
            "frontend/src/pages/Orders/index.tsx",
            unit_id="page:orders",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            api_file = Path(temp_dir) / "frontend/src/apis/orders.ts"
            page_file = Path(temp_dir) / "frontend/src/pages/Orders/index.tsx"
            api_file.parent.mkdir(parents=True)
            page_file.parent.mkdir(parents=True)
            api_file.write_text(
                "type OrderRequest = { status: string }\n"
                "type OrderResponse = { id: string }\n"
                "export function listOrders(params: OrderRequest): Promise<OrderResponse> { "
                "return service.get<OrderResponse>('/orders', params) }",
                encoding="utf-8",
            )
            api_result = verify_business_acceptance(api_task, temp_dir, formal_artifacts=formal)
            page_file.write_text(
                "import { listOrders as loadOrders } from '../../apis/orders'\n"
                "export default function Orders() { loadOrders(); return null }",
                encoding="utf-8",
            )
            alias_result = verify_business_acceptance(
                page_task,
                temp_dir,
                formal_artifacts=formal,
                dependency_evidence=api_result["business_acceptance_evidence"],
            )
            page_file.write_text(
                "function listOrders() { return 1 }\n"
                "export default function Orders() { listOrders(); return null }",
                encoding="utf-8",
            )
            unbound_result = verify_business_acceptance(
                page_task,
                temp_dir,
                formal_artifacts=formal,
                dependency_evidence=api_result["business_acceptance_evidence"],
            )
        self.assertEqual(alias_result["status"], "passed", alias_result)
        self.assertEqual(unbound_result["status"], "failed", unbound_result)

    def test_typescript_ast_parse_error_is_blocked(self) -> None:
        """无法安全解析的 TypeScript 必须 blocked，不能伪装成实现缺失。"""

        task, formal = _compiled_task(
            "frontend.api_module",
            "frontend/src/apis/orders.ts",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "frontend/src/apis/orders.ts"
            target.parent.mkdir(parents=True)
            target.write_text(
                "export function listOrders( { return service.get('/orders')",
                encoding="utf-8",
            )
            result = verify_business_acceptance(task, temp_dir, formal_artifacts=formal)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["business_acceptance_evidence"][0]["facts"]["reason_code"],
            "verifier_unsupported_syntax",
        )

    def test_controller_ast_combines_class_path_and_binds_service_call(self) -> None:
        """Controller AST 必须组合类/方法路径，并把 Service 调用绑定到该 handler。"""

        task, formal = _compiled_task(
            "backend.endpoint_controller",
            "backend/src/controller/OrderController.java",
            unit_id="backend:orders.list",
        )
        valid_source = """
        @RequestMapping("/orders")
        class OrderController {
          private OrderService service;
          @GetMapping
          ResponseEntity<OrderResponse> list(@RequestParam String status) {
            return service.list(status);
          }
        }
        """
        unrelated_mapping = """
        class OrderController { private OrderService service; }
        class OtherController {
          @GetMapping("/orders")
          ResponseEntity<OrderResponse> list(@RequestParam String status) { return null; }
        }
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "backend/src/controller/OrderController.java"
            target.parent.mkdir(parents=True)
            target.write_text(valid_source, encoding="utf-8")
            passed = verify_business_acceptance(task, temp_dir, formal_artifacts=formal)
            target.write_text(unrelated_mapping, encoding="utf-8")
            failed = verify_business_acceptance(task, temp_dir, formal_artifacts=formal)
        self.assertEqual(passed["status"], "passed", passed)
        self.assertEqual(failed["status"], "failed", failed)

    def test_domain_mapping_verifies_entity_po_dto_and_converter_as_one_delivery(self) -> None:
        """领域映射检查应联合读取兄弟交付物，不能要求每个文件独立包含完整映射。"""

        paths = {
            "entity": "backend/src/domain/Order.java",
            "po": "backend/src/po/OrderPO.java",
            "dto": "backend/src/dto/OrderDTO.java",
            "converter": "backend/src/converter/OrderConverter.java",
        }
        task = _task(
            "backend.domain_mapping",
            path=paths["entity"],
            owner="backend",
            unit_id="backend:orders",
            target_id="Order",
        )
        for role in ("po", "dto", "converter"):
            path = paths[role]
            task["deliverables"].append(
                {
                    "id": f"order-{role}",
                    "kind": "backend.domain_mapping",
                    "target_id": "Order",
                    "paths": [path],
                    "provides": [f"order.{role}"],
                }
            )
            task["allowed_paths"].append(path)
            task["target_files"].append(path)
            task["change_scope"].append({"operation": "modify", "path": path})
        compiled = compile_business_acceptance([task], _formal_context())[0]
        sources = {
            paths["entity"]: "class Order { private String id; private String status; }",
            paths["po"]: (
                "class OrderPO { @TableField(\"order_id\") private String id; "
                "private String status; }"
            ),
            paths["dto"]: "class OrderDTO { private String id; private String status; }",
            paths["converter"]: (
                "class OrderConverter { Order toEntity(OrderPO po) { "
                "return Order.builder().id(po.getId()).status(po.getStatus()).build(); } }"
            ),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            for path, source in sources.items():
                target = Path(temp_dir) / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            result = verify_business_acceptance(
                compiled,
                temp_dir,
                formal_artifacts=_formal_context()["project_plan"],
            )

        self.assertEqual(len(compiled["business_acceptance_checks"]), 1)
        self.assertEqual(result["status"], "passed", result)

    def test_application_service_ast_requires_real_repository_delegation(self) -> None:
        """ApplicationService 仅声明 Repository 字段但不调用时不能通过。"""

        task, formal = _compiled_task(
            "backend.application_service",
            "backend/src/service/OrderService.java",
            unit_id="backend:orders",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "backend/src/service/OrderService.java"
            target.parent.mkdir(parents=True)
            target.write_text(
                "class OrderService { private OrderRepository repository; "
                "List<Order> list(String status) { return List.of(); } }",
                encoding="utf-8",
            )
            result = verify_business_acceptance(task, temp_dir, formal_artifacts=formal)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("未调用 Repository/Mapper", result["business_acceptance_evidence"][0]["evidence"])

    def test_repository_xml_is_read_through_xml_ast(self) -> None:
        """MyBatis XML 的 mapper、select id、selector 和列名应由 XML AST 提取。"""

        task, formal = _compiled_task(
            "backend.repository",
            "backend/src/repository/OrderMapper.xml",
            unit_id="backend:orders",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "backend/src/repository/OrderMapper.xml"
            target.parent.mkdir(parents=True)
            target.write_text(
                '<mapper namespace="OrderMapper">'
                '<select id="listOrders">select order_id, status from orders where status = #{status}</select>'
                "</mapper>",
                encoding="utf-8",
            )
            result = verify_business_acceptance(task, temp_dir, formal_artifacts=formal)
        self.assertEqual(result["status"], "passed", result)

    def test_java_ast_parse_error_is_blocked(self) -> None:
        """无法安全解析的 Java 必须 blocked，不能被判为缺少 Controller。"""

        task, formal = _compiled_task(
            "backend.endpoint_controller",
            "backend/src/controller/OrderController.java",
            unit_id="backend:orders.list",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "backend/src/controller/OrderController.java"
            target.parent.mkdir(parents=True)
            target.write_text("class OrderController { @GetMapping( ", encoding="utf-8")
            result = verify_business_acceptance(task, temp_dir, formal_artifacts=formal)
        self.assertEqual(result["status"], "blocked", result)
        self.assertEqual(
            result["business_acceptance_evidence"][0]["facts"]["reason_code"],
            "verifier_unsupported_syntax",
        )

    def test_page_endpoint_usage_does_not_count_api_function_definition_as_page_call(self) -> None:
        """页面与 API 模块同任务时，API 函数定义本身不能伪造页面调用证据。"""

        api_task, formal = _compiled_task(
            "frontend.api_module",
            "frontend/src/apis/orders.ts",
        )
        page_model = _task(
            "frontend.page",
            path="frontend/src/pages/Orders/index.tsx",
            owner="frontend",
            unit_id="page:orders",
        )
        api_path = "frontend/src/apis/orders.ts"
        page_model["allowed_paths"].append(api_path)
        page_model["target_files"].append(api_path)
        page_model["change_scope"].append({"operation": "modify", "path": api_path})
        page_model["deliverables"][0]["paths"].append(api_path)
        page_task = compile_business_acceptance([page_model], _formal_context())[0]
        with tempfile.TemporaryDirectory() as temp_dir:
            api_file = Path(temp_dir) / api_path
            page_file = Path(temp_dir) / "frontend/src/pages/Orders/index.tsx"
            api_file.parent.mkdir(parents=True)
            page_file.parent.mkdir(parents=True)
            api_file.write_text(
                "type OrderRequest = { status: string }\n"
                "type OrderResponse = { id: string }\n"
                "export async function listOrders(params: OrderRequest): Promise<OrderResponse> { "
                "return service.get('/orders', params) }",
                encoding="utf-8",
            )
            page_file.write_text(
                "import { listOrders } from '../../apis/orders'\n"
                "export default function Orders() { return null }",
                encoding="utf-8",
            )
            api_result = verify_business_acceptance(api_task, temp_dir, formal_artifacts=formal)
            page_result = verify_business_acceptance(
                page_task,
                temp_dir,
                formal_artifacts=formal,
                dependency_evidence=api_result["business_acceptance_evidence"],
            )
        self.assertEqual(api_result["status"], "passed")
        self.assertEqual(page_result["status"], "failed")

    def test_already_satisfied_reruns_business_verifier(self) -> None:
        """already_satisfied 任务也必须重新执行当前业务检查而不是复用旧证据。"""

        task, formal = _compiled_task(
            "frontend.api_module",
            "frontend/src/apis/orders.ts",
        )
        task["status"] = "already_satisfied"
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "frontend" / "src" / "apis" / "orders.ts"
            target.parent.mkdir(parents=True)
            target.write_text(
                "export async function listOrders(): Promise<unknown> { return service.get('/wrong') }",
                encoding="utf-8",
            )
            result = verify_business_acceptance(task, temp_dir, formal_artifacts=formal)
        self.assertEqual(result["status"], "failed")

    def test_all_phase_two_verifiers_have_positive_and_non_false_positive_paths(self) -> None:
        """九种业务检查均应有可通过的结构样本，并拒绝只存在于注释中的伪实现。"""

        positive_sources = {
            "frontend.api_contract": (
                "type OrderRequest = { status: string }\n"
                "type OrderResponse = { id: string }\n"
                "export async function listOrders(params: OrderRequest): Promise<OrderResponse> { "
                "return service.get('/orders', params) }"
            ),
            "frontend.page_endpoint_usage": (
                "import { listOrders } from '../../apis/orders'\n"
                "export default function Orders() { listOrders(); return null }"
            ),
            "frontend.static_data_contract": (
                "type OrderRequest = { status: string }\n"
                "type OrderResponse = { id: string }\n"
                "const orders = [{ id: '1', status: 'open' }]\n"
                "export function listOrders(params: OrderRequest): Promise<OrderResponse> { "
                "return Promise.resolve(orders[0]) }"
            ),
            "backend.domain_mapping": (
                "class OrderEntity { String id; String status; }\n"
                "class OrderPo { String order_id; }\n"
                "class OrderConverter { OrderEntity convert(String id, String status, String order_id) { return null; } }"
            ),
            "backend.repository_contract": (
                "interface OrderRepository { List<Order> findByStatus(String status); }\n"
                "class OrderMapper { String order_id; }"
            ),
            "backend.application_service_contract": (
                "class OrderService {\n"
                "  private final OrderRepository repository;\n"
                "  List<Order> list(String status) { return repository.findByStatus(status); }\n"
                "}"
            ),
            "backend.endpoint_contract": (
                "class OrderController {\n"
                "  private OrderService service;\n"
                "  @GetMapping(\"/orders\") ResponseEntity<OrderResponse> list(@RequestParam String status) { "
                "return service.list(status); }\n"
                "}"
            ),
            "backend.external_api_client_contract": (
                "class OrderClient {\n"
                "  private RestTemplate restTemplate;\n"
                "  OrderResponse get() { return restTemplate.getForObject(\"/upstream/orders\", OrderResponse.class); }\n"
                "}"
            ),
            "backend.external_api_mapping_contract": (
                "class OrderMapper {\n"
                "  Order map(Upstream value) { String id = value.id; String state = value.state.value; "
                "String status = state; return null; }\n"
                "}"
            ),
        }
        paths = {
            "frontend.api_contract": "frontend/src/apis/orders.ts",
            "frontend.page_endpoint_usage": "frontend/src/pages/Orders/index.tsx",
            "frontend.static_data_contract": "frontend/src/apis/ordersMock.ts",
            "backend.domain_mapping": "backend/src/domain/Order.java",
            "backend.repository_contract": "backend/src/repository/OrderRepository.java",
            "backend.application_service_contract": "backend/src/service/OrderService.java",
            "backend.endpoint_contract": "backend/src/controller/OrderController.java",
            "backend.external_api_client_contract": "backend/src/client/OrderClient.java",
            "backend.external_api_mapping_contract": "backend/src/mapper/OrderMapper.java",
        }
        task_inputs = {
            "frontend.api_contract": ("frontend.api_module", "frontend", "frontend:api-client"),
            "frontend.page_endpoint_usage": ("frontend.page", "frontend", "page:orders"),
            "frontend.static_data_contract": ("frontend.static_data_module", "frontend", "frontend:data:orders"),
            "backend.domain_mapping": ("backend.domain_mapping", "backend", "backend:orders"),
            "backend.repository_contract": ("backend.repository", "backend", "backend:orders"),
            "backend.application_service_contract": ("backend.application_service", "backend", "backend:orders"),
            "backend.endpoint_contract": ("backend.endpoint_controller", "backend", "backend:orders.list"),
            "backend.external_api_client_contract": ("backend.external_api_client", "backend", "backend:orders"),
            "backend.external_api_mapping_contract": ("backend.external_api_mapping", "backend", "backend:orders"),
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            positive_evidence: dict[str, list[dict]] = {}
            for check_kind in BUSINESS_ACCEPTANCE_KINDS:
                deliverable_kind, _owner, unit_id = task_inputs[check_kind]
                task, formal = _compiled_task(
                    deliverable_kind,
                    paths[check_kind],
                    unit_id=unit_id,
                )
                target = Path(temp_dir) / paths[check_kind]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(positive_sources[check_kind], encoding="utf-8")
                dependency_evidence = positive_evidence.get("frontend.api_contract", [])
                result = verify_business_acceptance(
                    task,
                    temp_dir,
                    formal_artifacts=formal,
                    dependency_evidence=dependency_evidence,
                )
                self.assertEqual(result["status"], "passed", check_kind)
                positive_evidence[check_kind] = result["business_acceptance_evidence"]

                target.write_text(
                    "\n".join(
                        f"// {line}" for line in positive_sources[check_kind].splitlines()
                    ),
                    encoding="utf-8",
                )
                false_positive = verify_business_acceptance(
                    task,
                    temp_dir,
                    formal_artifacts=formal,
                    dependency_evidence=dependency_evidence,
                )
                self.assertNotEqual(false_positive["status"], "passed", check_kind)
                blocked = verify_business_acceptance(
                    task,
                    temp_dir,
                    formal_artifacts=None,
                    dependency_evidence=dependency_evidence,
                )
                self.assertEqual(blocked["status"], "blocked", check_kind)

    def test_typescript_schema_comparison_covers_nested_optional_array_and_enum(self) -> None:
        """TypeScript API 检查必须比较嵌套对象、可选字段、数组和枚举，而非只查字段名。"""

        context = _formal_context()
        context["project_plan"]["api_contracts"][0]["schemas"] = {
            "OrderRequest": {
                "type": "object",
                "required": ["filters"],
                "properties": {
                    "filters": {
                        "type": "object",
                        "required": ["status"],
                        "properties": {
                            "status": {"type": "string", "enum": ["open", "closed"]},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "note": {"type": "string"},
                        },
                    }
                },
            },
            "OrderResponse": {
                "type": "object",
                "required": ["items"],
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"$ref": "#/schemas/OrderItem"},
                    },
                    "cursor": {"type": "string"},
                },
            },
            "OrderItem": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"type": "string"}},
            },
        }
        base_task, _ = _compiled_task(
            "frontend.api_module",
            "frontend/src/apis/orders.ts",
        )
        task = compile_business_acceptance([base_task], context)[0]
        source = (
            "type OrderRequest = { filters: { status: 'open' | 'closed'; "
            "tags?: string[]; note?: string } }\n"
            "type OrderItem = { id: string }\n"
            "type OrderResponse = { items: OrderItem[]; cursor?: string }\n"
            "export async function listOrders(params: OrderRequest): Promise<OrderResponse> { "
            "return service.get('/orders', params) }"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "frontend/src/apis/orders.ts"
            target.parent.mkdir(parents=True)
            target.write_text(source, encoding="utf-8")
            passed = verify_business_acceptance(
                task,
                temp_dir,
                formal_artifacts=context["project_plan"],
            )
            target.write_text(source.replace("id: string", "id?: string"), encoding="utf-8")
            failed = verify_business_acceptance(
                task,
                temp_dir,
                formal_artifacts=context["project_plan"],
            )
        self.assertEqual(passed["status"], "passed")
        self.assertEqual(failed["status"], "failed")


if __name__ == "__main__":
    unittest.main()
