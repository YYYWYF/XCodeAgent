"""使用真实片段校验和原子存储验证跨分组联合修正及断线恢复。"""

from copy import deepcopy
import json
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.agents.main.technical_plan_catalog_conflict import CatalogContractConflict
from app.agents.main.technical_plan_catalog_parts import generate_catalog
from app.agents.main.technical_plan_catalog_parts_validation import validate_registry
from app.agents.main.technical_plan_contract_budget import fragment_digest
from app.agents.main.technical_plan_fragment_retry import PlanningFragmentFailed, generate_fragment
from app.services.run_workers import bind_run_workers
from app.services.technical_plan_fragments import TechnicalPlanFragmentStore
from app.services.workspace_process_registry import workspace_process_registry
from tests.technical_plan_catalog_fixture import catalog_response, catalog_responses
from tests.test_technical_plan_modular_generation import modular_fixture


class TechnicalPlanCatalogJointRepairTests(TestCase):
    """只替换远端响应，保留生成编排、范围约束、重试与持久化边界。"""

    def setUp(self):
        """准备互不相同的业务能力和当前分段模型响应。"""

        self.requirement, self.catalog, _, _ = modular_fixture()
        self.cache, self.calls, self.publications = {}, [], []
        self.joint = None
        self.store = None
        self.disconnect_bindings = False
        self.fail_publication = False
        self.fingerprint = "a" * 64

    def invoke(self, prompt):
        """联合片段由用例明确提供，其他响应按真实阶段选择当前业务对象。"""

        self.calls.append(prompt)
        stage, context = prompt.splitlines()[0], json.loads(prompt.splitlines()[-1])
        if "catalog_joint_repair" in stage:
            self.assertIsNotNone(self.joint)
            return json.dumps(self.joint(context), ensure_ascii=False)
        if self.disconnect_bindings and "catalog_bindings" in stage:
            raise ConnectionError("联合结果已保存，动作映射前断线")
        return json.dumps(catalog_response(prompt, self.catalog), ensure_ascii=False)

    def save(self, key, value):
        """在每个成功片段返回前同步保存正文和引用。"""

        if self.store is not None:
            self.store.save(self.fingerprint, key, value)

    def publish(self, value):
        """模拟生产目录原子替换，失败时不改变内存或磁盘中的有效目录。"""

        if self.fail_publication and any(":joint:" in key for key in self.cache):
            raise OSError("原子替换失败")
        if self.store is not None:
            self.store.replace(self.fingerprint, value)
        self.publications.append(deepcopy(value))
        self.cache.clear()
        self.cache.update(deepcopy(value))

    def fragment(self, key, prompt, validate):
        """调用真实三次预算片段执行器，不替换任何校验器。"""

        return generate_fragment(self.cache, key, prompt, self.invoke, validate, self.save)

    def generate(self):
        """执行真实目录生成及确认前的候选发布，不进入正式产物或用户确认。"""

        return generate_catalog(self.requirement, cache=self.cache, fragment=self.fragment, publish=self.publish)

    def registry_key(self):
        """使用生产输入计算当前实体关系对应的分组片段键。"""

        relationships = catalog_responses(self.catalog)[0]
        return "catalog-part:" + fragment_digest({"initial": True}) + ":registry:" + fragment_digest(relationships["entities"])

    def duplicate_groups(self):
        """复现同一业务主体按管理页面和展示页面被错误拆成两组。"""

        original = self.catalog["contracts"][0]
        duplicate = deepcopy(original)
        original["page_ids"] = ["orders"]
        duplicate.update(id="orders_display", name="订单展示", page_ids=["order_detail"])
        self.catalog["contracts"].insert(1, duplicate)

    def cross_routes(self):
        """构造两个不同实体的路由冲突，并在它们之间保留一个无关已完成分组。"""

        first = self.catalog["contracts"][0]
        first["base_path"] = "/api/shared"
        first["endpoints"][0]["path"] = "/api/shared/{first}"
        extra = deepcopy(self.catalog["contracts"][1])
        extra.update(id="extra_api", name="附加查询", page_ids=["extra"], base_path="/api/shared")
        extra["endpoints"][0].update(id="extra_api.read", path="/api/shared/{second}")
        self.catalog["contracts"].append(extra)
        page = deepcopy(self.requirement["confirmed_product_plan"]["pages"][0])
        page["pageId"] = "extra"
        self.requirement["confirmed_product_plan"]["pages"].append(page)
        self.catalog["action_bindings"].append({"pageId": "extra", "actionId": "refresh", "stepId": None, "endpointId": "extra_api.read"})

    def fixed_pair(self, context):
        """保留两个不同业务分组，同时联合调整双方接口以消除路由重叠。"""

        contracts = deepcopy(context["contracts"])
        for contract in contracts:
            suffix = "orders" if contract["id"] == "orders_api" else "extra"
            contract["endpoints"][0]["path"] = "/api/shared/" + suffix
        return {"contracts": contracts}

    def test_group_validation_identifies_both_owners_but_allows_distinct_subjects(self):
        """只拒绝实体集合及路径边界都相同的分组，不拒绝合法共享路径范围。"""

        self.duplicate_groups()
        registry = catalog_responses(self.catalog)[1]
        with self.assertRaises(CatalogContractConflict) as caught:
            validate_registry(registry, self.requirement["confirmed_product_plan"], entities=self.catalog["entities"])
        self.assertEqual(caught.exception.contract_ids, ("orders_api", "orders_display"))
        self.assertIn("订单展示", str(caught.exception))
        registry["contracts"][1]["entity_ids"] = ["Customer"]
        validate_registry(registry, self.requirement["confirmed_product_plan"], entities=self.catalog["entities"])

    def test_fresh_and_cached_group_conflicts_are_jointly_merged(self):
        """新输出和当前缓存中的同主体拆分均联合修正，其他业务分组保持原样。"""

        self.duplicate_groups()
        original = deepcopy(self.catalog)
        unrelated = deepcopy(self.catalog["contracts"][-1])

        def merge(context):
            """在模型响应中合并两个同主体分组，同时汇总页面归属。"""

            self.assertEqual({item["id"] for item in context["contracts"]}, {"orders_api", "orders_display"})
            self.assertEqual([item["id"] for item in context["readonly_contracts"]], ["customers_api"])
            merged = deepcopy(context["contracts"][0])
            merged["page_ids"] = ["orders", "order_detail"]
            self.catalog["contracts"] = [{**merged, "endpoints": original["contracts"][0]["endpoints"]}, unrelated]
            return {"contracts": [merged]}

        for cached in (False, True):
            with self.subTest(cached=cached):
                self.catalog, self.cache, self.calls = deepcopy(original), {}, []
                if cached:
                    self.cache[self.registry_key()] = catalog_responses(self.catalog)[1]
                self.joint = merge
                result = self.generate()
                self.assertEqual(len(result["contracts"]), 2)
                self.assertEqual(result["contracts"][1], unrelated)
                self.assertEqual(sum("catalog_joint_repair" in call.splitlines()[0] for call in self.calls), 1)
                self.assertEqual(sum("catalog_endpoints" in call.splitlines()[0] for call in self.calls), 2)

    def test_route_conflict_jointly_rewrites_both_sides_and_reuses_unrelated_group(self):
        """冲突发生后立即联合修正，不浪费两次当前组重试，也不重跑无关组。"""

        self.cross_routes()
        unrelated = deepcopy(self.catalog["contracts"][1])
        self.joint = self.fixed_pair
        result = self.generate()
        joint = [call for call in self.calls if "catalog_joint_repair" in call.splitlines()[0]]
        self.assertEqual(len(joint), 1)
        context = json.loads(joint[0].splitlines()[-1])
        self.assertEqual({item["id"] for item in context["contracts"]}, {"orders_api", "extra_api"})
        self.assertEqual({page["pageId"] for page in context["pages"]}, {"orders", "order_detail", "extra"})
        self.assertEqual(result["contracts"][1], unrelated)
        self.assertEqual(sum("catalog_endpoints" in call.splitlines()[0] for call in self.calls), 3)
        self.assertEqual(result["contracts"][0]["endpoints"][0]["path"], "/api/shared/orders")
        self.assertEqual(result["contracts"][2]["endpoints"][0]["path"], "/api/shared/extra")

    def test_joint_repair_can_merge_groups_and_reassign_shared_pages(self):
        """接口阶段发现边界重叠时允许模型合并，并由共享归属承接原有业务动作。"""

        self.cross_routes()

        def merge(context):
            """保留一个原 ID，将两侧实体、页面及原有业务接口一起纳入同一分组。"""

            first, second = deepcopy(context["contracts"])
            first["entity_ids"] = list(dict.fromkeys(first["entity_ids"] + second["entity_ids"]))
            first["page_ids"] += second["page_ids"]
            second["endpoints"][0].update(id="orders_api.extra_read", path="/api/shared/extra")
            first["endpoints"] += second["endpoints"]
            for binding in self.catalog["action_bindings"]:
                if binding["pageId"] == "extra":
                    binding["endpointId"] = "orders_api.extra_read"
            return {"contracts": [first]}

        self.joint = merge
        result = self.generate()
        self.assertEqual([item["id"] for item in result["contracts"]], ["orders_api", "customers_api"])
        self.assertEqual(result["contracts"][0]["page_ids"], ["orders", "order_detail", "extra"])
        self.assertEqual(len(result["action_bindings"]), 4)

    def test_duplicate_endpoint_id_with_foreign_namespace_also_joins_both_owners(self):
        """复制已生成接口 ID 时先识别跨组冲突，不在单组命名空间错误中空转。"""

        self.cross_routes()
        self.catalog["contracts"][2]["endpoints"][0].update(id="orders_api.read", path="/api/shared/extra")

        def fix(context):
            """联合处理双方候选，为各自保留唯一接口身份及路径。"""

            result = self.fixed_pair(context)
            for contract in result["contracts"]:
                contract["endpoints"][0]["id"] = contract["id"] + ".read"
            return result

        self.joint = fix
        result = self.generate()
        self.assertEqual(len(result["contracts"]), 3)
        self.assertEqual(sum("catalog_joint_repair" in call.splitlines()[0] for call in self.calls), 1)
        self.assertEqual(sum("catalog_endpoints" in call.splitlines()[0] for call in self.calls), 3)

    def test_repair_cannot_steal_an_unrelated_completed_groups_route_boundary(self):
        """联合调整路径边界后仍复验只读接口，不能通过迫使无关分组重生成来收敛。"""

        self.cross_routes()
        self.catalog["contracts"][1]["endpoints"][0]["path"] = "/api/customers/stats/read"

        def fix(context):
            """首轮侵占只读分组的更具体路径，第二轮仅修正授权范围内的分组。"""

            result = self.fixed_pair(context)
            if sum("catalog_joint_repair" in call.splitlines()[0] for call in self.calls) == 1:
                result["contracts"][0]["base_path"] = "/api/customers/stats"
                result["contracts"][0]["endpoints"][0]["path"] = "/api/customers/stats/list"
            return result

        self.joint = fix
        result = self.generate()
        self.assertEqual(result["contracts"][1], self.catalog["contracts"][1])
        joint = [call for call in self.calls if "catalog_joint_repair" in call.splitlines()[0]]
        self.assertEqual(len(joint), 2)
        self.assertIn("customers_api 接口目录越界", joint[1])
        self.assertEqual(sum("catalog_endpoints" in call.splitlines()[0] for call in self.calls), 3)

    def test_invalid_joint_scope_exhausts_three_attempts_without_partial_publication(self):
        """联合结果不得改动无关身份、遗漏业务页面或继续占用重复路由。"""

        self.cross_routes()

        def invalid(context):
            """前两次扩大或缩小范围，第三次保留冲突，确保诊断来自实际校验。"""

            attempt = sum("catalog_joint_repair" in call.splitlines()[0] for call in self.calls)
            result = self.fixed_pair(context)
            if attempt == 1:
                result["contracts"][0]["id"] = "unrelated_api"
            elif attempt == 2:
                result["contracts"][0]["page_ids"] = []
            else:
                result = {"contracts": context["contracts"]}
            return result

        self.joint = invalid
        with self.assertRaisesRegex(PlanningFragmentFailed, "已尝试 3 次.*method/path"):
            self.generate()
        self.assertEqual(len(self.publications), 0)
        self.assertNotIn("catalog", self.cache)
        self.assertEqual(len([key for key in self.cache if ":endpoints:" in key]), 2)
        joint = [call for call in self.calls if "catalog_joint_repair" in call.splitlines()[0]]
        self.assertIn("不得新增或重复分组", joint[1])
        self.assertIn("page_ids", joint[2])

    def test_durable_repair_survives_disconnect_without_replaying_any_directory(self):
        """联合结果原子发布后断线，重新读取存储仅继续动作映射。"""

        self.cross_routes()
        self.joint = self.fixed_pair
        with TemporaryDirectory() as workspace:
            self.store = TechnicalPlanFragmentStore(workspace)
            self.store.load(self.fingerprint)
            self.disconnect_bindings = True
            with self.assertRaises(ConnectionError):
                self.generate()
            self.cache = self.store.load(self.fingerprint)
            self.assertEqual(self.cache[self.registry_key()]["contracts"][0]["id"], "orders_api")
            self.assertEqual(len([key for key in self.cache if ":endpoints:" in key]), 3)
            self.calls, self.disconnect_bindings = [], False
            self.generate()
            self.assertTrue(all("catalog_bindings" in call.splitlines()[0] for call in self.calls))
            self.assertEqual(set(self.store.load(self.fingerprint)), {"catalog"})

    def test_atomic_replacement_failure_keeps_old_pair_and_reuses_saved_joint_response(self):
        """目录替换失败不提交单侧修正，重试可复用已保存的完整联合响应。"""

        self.cross_routes()
        self.joint = self.fixed_pair
        with TemporaryDirectory() as workspace:
            self.store = TechnicalPlanFragmentStore(workspace)
            self.store.load(self.fingerprint)
            self.fail_publication = True
            with self.assertRaisesRegex(OSError, "原子替换失败"):
                self.generate()
            self.cache = self.store.load(self.fingerprint)
            self.assertTrue(any(":joint:" in key for key in self.cache))
            self.assertEqual(len([key for key in self.cache if ":endpoints:" in key]), 2)
            self.calls, self.fail_publication = [], False
            self.generate()
            self.assertFalse(any("catalog_joint_repair" in call.splitlines()[0] for call in self.calls))

    def test_stop_during_joint_model_call_does_not_publish_or_retry(self):
        """联合调用收到停止后不保存新结果、不继续调用或绕过原取消机制。"""

        self.cross_routes()
        run_id = "catalog-joint-stop"

        def stop(context):
            """模型返回时模拟用户停止，保存边界必须拒绝发布结果。"""

            workspace_process_registry.cancel_run(run_id)
            return self.fixed_pair(context)

        self.joint = stop
        try:
            with bind_run_workers(run_id, ""), self.assertRaisesRegex(RuntimeError, "当前运行已停止"):
                self.generate()
        finally:
            workspace_process_registry.allow_run(run_id)
        self.assertEqual(self.publications, [])
        self.assertFalse(any(":joint:" in key for key in self.cache))
