"""验证前端 API 契约使用真实 AST 参数绑定而非源码文本。"""

import unittest

from app.services.business_acceptance_verifiers.typescript_inspection import verify_api_contract_source
from app.services.business_acceptance_verifiers.typescript_ast import inspect_typescript_sources


PATH = "/api/mentor-employee-relations/{id}"
BODY = {"type": "object", "properties": {"name": {"type": "string", "required": True}}}


class FrontendApiContractBindingsTests(unittest.TestCase):
    """覆盖路径编码、多形参和请求位置的正反例。"""

    def verify(self, source, *, method="PUT", body=True, parameters=None):
        """通过实际默认 service 导入和完整正式契约运行验收。"""
        endpoint = {
            "endpoint_id": "mentor_employee_relation_contract." + method.lower(),
            "method": method, "path": PATH,
            "parameters": parameters if parameters is not None else [
                {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
            ],
        }
        if body:
            endpoint["request_schema"] = BODY
        return verify_api_contract_source(
            {"frontend/src/apis/relations.ts": "import service from '../service';\n"
             "interface UpdateRequest { name: string }\n" + source},
            {"required_endpoints": [endpoint]}, service_path="frontend/src/service.ts",
        )

    def test_full_put_and_delete_contract(self):
        """原始编码实现及不编码实现均应直接通过正式契约。"""
        for expression in ("id", "encodeURIComponent(id)"):
            with self.subTest(expression=expression):
                result = self.verify(
                    'export function update(id: string, data: UpdateRequest): Promise<void> {'
                    'return service.put(`/api/mentor-employee-relations/${' + expression + '}`, data); }'
                )
                self.assertEqual(result["status"], "passed", result)
                self.assertEqual(result["facts"]["endpoint_exports"][0]["export_symbol"], "update")
        result = self.verify(
            'export function remove(id: string): Promise<void> {'
            'return service.delete(`/api/mentor-employee-relations/${encodeURIComponent(id)}`); }',
            method="DELETE", body=False,
        )
        self.assertEqual(result["status"], "passed", result)

    def test_wrong_path_or_body_fails(self):
        """错误路径绑定、缺失请求体和错误类型仍须失败。"""
        for expression, params, argument in (
            ("encodeURIComponent(otherId)", "id: string, otherId: string, data: UpdateRequest", ", data"),
            ("id", "id?: string", ""),
            ("id", "id: string, data: UpdateRequest", ""),
            ("id", "id: string, data: UpdateRequest", ", id"),
            ("id", "id: string, data: { name: number }", ", data"),
        ):
            with self.subTest(expression=expression, params=params, argument=argument):
                result = self.verify('export function update(' + params + ') {'
                    'return service.put(`/api/mentor-employee-relations/${' + expression + '}`' + argument + '); }')
                self.assertEqual(result["status"], "failed", result)

    def test_unknown_dynamic_path_blocks(self):
        """任意函数和计算表达式不能冒充合法路径。"""
        for expression in ("format(id)", "id + suffix", "encodeURIComponent(id, otherId)"):
            with self.subTest(expression=expression):
                result = self.verify('export function update(id: string, data: UpdateRequest) {'
                    'return service.put(`/api/mentor-employee-relations/${' + expression + '}`, data); }')
                self.assertEqual(result["status"], "blocked", result)

    def test_query_binding_and_types(self):
        """查询字段必须实际进入 params，且必填性、字段类型均须正确。"""
        parameters = [
            {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": "page", "in": "query", "required": True, "schema": {"type": "integer"}},
        ]
        for declaration, config, status in (
            ("params: { page: number }", "{ params }", "passed"),
            ("params: { page: number }", "{ params: { page: params.page } }", "passed"),
            ("params: { page: number }", "{}", "failed"),
            ("params: { page: number }", "params", "failed"),
            ("params: { page?: number }", "{ params }", "failed"),
            ("params?: { page: number }", "{ params }", "failed"),
            ("params: { page: string }", "{ params }", "failed"),
        ):
            with self.subTest(declaration=declaration, config=config):
                result = self.verify('export function get(id: string, ' + declaration + ') {'
                    'return service.get(`/api/mentor-employee-relations/${id}`, ' + config + '); }',
                    method="GET", body=False, parameters=parameters)
                self.assertEqual(result["status"], status, result)

    def test_post_and_delete_body_positions(self):
        """POST 第二参数和 DELETE config.data 均按实际绑定核验请求体。"""
        for method, argument, status in (
            ("POST", "data", "passed"),
            ("POST", "{ name: data.name }", "passed"),
            ("POST", "{ params: data }", "failed"),
            ("DELETE", "{ data }", "passed"),
            ("DELETE", "{ data: { name: data.name } }", "passed"),
            ("DELETE", "{ params: data }", "failed"),
        ):
            with self.subTest(method=method, argument=argument):
                result = self.verify('export function change(id: string, data: UpdateRequest) {'
                    'return service.' + method.lower() + '(`/api/mentor-employee-relations/${id}`, '
                    + argument + '); }', method=method)
                self.assertEqual(result["status"], status, result)

    def test_nested_parameter_fields(self):
        """显式对象字段可同时绑定路径和 Body，不依赖形参位置或固定名称。"""
        for declaration, status in (
            ("input: { id: string; data: UpdateRequest }", "passed"),
            ("input: { id?: string; data: UpdateRequest }", "failed"),
        ):
            with self.subTest(declaration=declaration):
                result = self.verify('export function change(' + declaration + ') {'
                    'return service.put(`/api/mentor-employee-relations/${encodeURIComponent(input.id)}`, input.data); }')
                self.assertEqual(result["status"], status, result)

    def test_wrong_static_path_method_and_extra_call(self):
        """错误静态段、方法、固定占位文本及额外调用不能被合法主调用掩盖。"""
        valid = ('export function update(id: string, data: UpdateRequest) {'
                 'return service.put(`/api/mentor-employee-relations/${id}`, data); }')
        for source in (
            valid.replace("mentor-employee-relations", "wrong"),
            valid.replace("service.put", "service.post"),
            valid.replace("${id}", "{id}"),
            valid + 'export function extra() { return service.get("/extra"); }',
            valid + 'export function extra(id: string) { return service.get(`/api/mentor-employee-relations/${id}`); }',
        ):
            with self.subTest(source=source):
                self.assertEqual(self.verify(source)["status"], "failed")

    def test_encoder_shadowing_and_unbound_path(self):
        """自定义同名编码函数不可视为内置编码，未声明的路径变量也不能通过。"""
        result = self.verify('function encodeURIComponent(value: string) { return "wrong"; }'
            'export function update(id: string, data: UpdateRequest) {'
            'return service.put(`/api/mentor-employee-relations/${encodeURIComponent(id)}`, data); }')
        self.assertEqual(result["status"], "blocked", result)
        result = self.verify('export function update(data: UpdateRequest) {'
            'return service.put(`/api/mentor-employee-relations/${id}`, data); }')
        self.assertEqual(result["status"], "failed", result)

    def test_default_service_binding_and_direct_http(self):
        """默认值导入须来自真实 service；axios/fetch 和伪导入保持拒绝。"""
        source = ('export function update(id: string, data: UpdateRequest) {'
                  'return service.put(`/api/mentor-employee-relations/${id}`, data); }')
        for imported in ("import { service } from '../service';", "import service from '../other';",
                         "import type service from '../service';"):
            with self.subTest(imported=imported):
                result = verify_api_contract_source(
                    {"frontend/src/apis/a.ts": imported + source},
                    {"required_endpoints": [{"method": "PUT", "path": PATH}]},
                    service_path="frontend/src/service.ts")
                self.assertEqual(result["status"], "failed", result)
        for invocation in ('axios.get("/extra")', 'fetch("/extra")'):
            result = self.verify(source + 'export function extra() { return ' + invocation + '; }')
            self.assertEqual(result["status"], "failed", result)
        result = self.verify(source.replace("id: string, data: UpdateRequest", "id: string, data: UpdateRequest, service: any"))
        self.assertEqual(result["status"], "failed", result)

    def test_ast_retains_first_parameter_projection(self):
        """新增全部形参事实时保持旧消费者的首参数与原始调用文本字段。"""
        source = 'export function update(id: string, data?: { name: string }) { return service.put(`/a/${id}`, data) }'
        function = inspect_typescript_sources({"a.ts": source}).functions[0]
        self.assertEqual((function.parameter_name, function.request_type, function.request_shape),
                         ("id", "string", {"type": "string"}))
        self.assertEqual([item["position"] for item in function.parameters], [0, 1])
        self.assertFalse(function.parameters[1]["required"])
        self.assertEqual(function.calls[0].arguments, ['`/a/${id}`', 'data'])

    def test_static_url_constants_are_scoped(self):
        """静态 const 路径可验证，但另一函数中的同名常量不能成为路径证据。"""
        expected = {"required_endpoints": [{"method": "GET", "path": "/items"}]}
        for source, status in (
            ('const url = "/items"; export function list() { return service.get(url) }', "passed"),
            ('export function list() { const url = "/items"; return service.get(url) }', "passed"),
            ('function other() { const url = "/items"; } export function list() { return service.get(url) }', "blocked"),
            ('const url = "/items"; export function list(url: string) { return service.get(url) }', "blocked"),
        ):
            with self.subTest(source=source):
                result = verify_api_contract_source({"a.ts": source}, expected)
                self.assertEqual(result["status"], status, result)

    def test_single_post_body_and_response(self):
        """单请求体 POST 保持通过，响应结构错误仍须失败。"""
        endpoint = {"method": "POST", "path": "/items", "request_schema": BODY,
                    "response_schema": {"type": "object", "properties": {"id": {"type": "string"}}}}
        for response, status in (("string", "passed"), ("number", "failed")):
            with self.subTest(response=response):
                source = ('export function create(data: { name: string }): Promise<{ id: '
                          + response + ' }> { return service.post("/items", data) }')
                result = verify_api_contract_source({"a.ts": source}, {"required_endpoints": [endpoint]})
                self.assertEqual(result["status"], status, result)


if __name__ == "__main__":
    unittest.main()
