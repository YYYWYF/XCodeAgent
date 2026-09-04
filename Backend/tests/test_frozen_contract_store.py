"""T10.1 PlanningRun Frozen Contract Store 行为测试。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError

from app.services.frozen_contract_store import FrozenContractStore, PlanningFormalInputs


def _formal_inputs() -> dict:
    """构造覆盖六类正式输入且来源身份互不重复的最小测试数据。"""

    return {
        "product_plan": {
            "content": {"artifact_type": "product-plan", "pages": [{"pageId": "orders"}]},
            "source": {"artifact": "product-plan.json", "revision": "product-v1"},
        },
        "technical_plan": {
            "content": {"artifact_type": "technical-plan", "architecture": {"frontend": "React"}},
            "source": {"artifact": "project-plan.json", "revision": "technical-v1"},
        },
        "page_contracts": [{
            "content": {"pageId": "orders", "requiredEndpointIds": ["orders.list"]},
            "source": {"artifact": "runtime-page-contracts", "pointer": "/orders"},
        }],
        "api_contracts": [{
            "content": {"id": "orders-api", "endpoints": [{"id": "orders.list"}]},
            "source": {"artifact": "project-plan.json", "pointer": "/api_contracts/orders-api"},
        }],
        "entity_bindings": [{
            "content": {"entity_id": "Order", "source_type": "database"},
            "source": {"artifact": "entities/Order.json", "revision": "entity-v1"},
        }],
        "authorization_slices": [{
            "content": {"pageId": "orders", "resourceKeys": ["orders.read"]},
            "source": {"artifact": "authorization-manifest", "pointer": "/bindings/pages/orders"},
        }],
    }


class FrozenContractStoreTests(unittest.TestCase):
    """验证正式合同一次冻结、稳定寻址和严格输入边界。"""

    def test_freeze_covers_only_declared_formal_contract_groups(self) -> None:
        """Store 覆盖六类正式输入，递归内容只读且不接受 Candidate 分组。"""

        store = FrozenContractStore.create(
            planning_run_id="planning-run-1",
            formal_inputs=PlanningFormalInputs.model_validate(_formal_inputs()),
        )
        self.assertEqual(
            {contract.kind for contract in store.contracts.values()},
            {
                "product_plan", "technical_plan", "page_contract", "api_contract",
                "entity_binding", "authorization_slice",
            },
        )
        page = next(contract for contract in store.contracts.values() if contract.kind == "page_contract")
        with self.assertRaises(TypeError):
            page.content["pageId"] = "changed"
        self.assertIsInstance(page.content["requiredEndpointIds"], tuple)
        with self.assertRaises(ValidationError):
            PlanningFormalInputs.model_validate({**_formal_inputs(), "candidates": [{"id": "candidate-1"}]})

    def test_source_later_changes_do_not_change_frozen_contract(self) -> None:
        """正式源文件随后变化时，Store 不重读磁盘并保留 Run 创建时版本。"""

        with TemporaryDirectory() as directory:
            path = Path(directory) / "page-contract.json"
            original = {"pageId": "orders", "requiredEndpointIds": ["orders.list"]}
            path.write_text(json.dumps(original), encoding="utf-8")
            payload = _formal_inputs()
            payload["page_contracts"][0] = {
                "content": json.loads(path.read_text(encoding="utf-8")),
                "source": {"artifact": str(path), "revision": "page-v1"},
            }
            store = FrozenContractStore.create(planning_run_id="planning-run-1", formal_inputs=payload)
            page = next(contract for contract in store.contracts.values() if contract.kind == "page_contract")

            changed = {"pageId": "customers", "requiredEndpointIds": ["customers.list"]}
            path.write_text(json.dumps(changed), encoding="utf-8")

            self.assertEqual(store[page.ref_id].content["pageId"], "orders")
            self.assertEqual(store[page.ref_id].content["requiredEndpointIds"], ("orders.list",))
            self.assertEqual(store[page.ref_id].source["revision"], "page-v1")

    def test_stored_version_is_unchanged_by_serialized_copy_mutation(self) -> None:
        """序列化结果是独立副本，修改副本不能反向污染已冻结版本。"""

        store = FrozenContractStore.create(planning_run_id="planning-run-1", formal_inputs=_formal_inputs())
        before = store.model_dump(mode="json")
        dumped = store.model_dump(mode="json")
        dumped["contracts"].clear()
        dumped["planning_run_id"] = "changed"

        self.assertEqual(store.model_dump(mode="json"), before)
        self.assertIsNone(store.get("frozen-contract-missing"))
        with self.assertRaises(KeyError):
            _ = store["frozen-contract-missing"]

    def test_missing_formal_input_is_rejected(self) -> None:
        """六个正式输入分组都必须显式提供，核心计划也不能用空正文代替。"""

        payload = _formal_inputs()
        for field in payload:
            with self.subTest(missing=field), self.assertRaises(ValidationError):
                PlanningFormalInputs.model_validate({key: value for key, value in payload.items() if key != field})
        with self.assertRaises(ValidationError):
            PlanningFormalInputs.model_validate({
                **payload,
                "product_plan": {**payload["product_plan"], "content": {}},
            })
        with self.assertRaises(ValidationError):
            PlanningFormalInputs.model_validate({
                **payload,
                "technical_plan": {**payload["technical_plan"], "source": {}},
            })

    def test_ref_id_is_stable_for_same_run_source_and_scoped_to_run(self) -> None:
        """引用不受映射和列表顺序影响，但不同 PlanningRun 不共享引用身份。"""

        payload = _formal_inputs()
        reordered = deepcopy(payload)
        reordered["api_contracts"].append({
            "content": {"id": "customers-api", "endpoints": [{"id": "customers.list"}]},
            "source": {"pointer": "/api_contracts/customers-api", "artifact": "project-plan.json"},
        })
        first = FrozenContractStore.create(planning_run_id="planning-run-1", formal_inputs=reordered)
        reordered["api_contracts"].reverse()
        reordered["product_plan"]["source"] = {
            "revision": "product-v1", "artifact": "product-plan.json",
        }
        second = FrozenContractStore.create(planning_run_id="planning-run-1", formal_inputs=reordered)
        other_run = FrozenContractStore.create(planning_run_id="planning-run-2", formal_inputs=reordered)

        self.assertEqual(first.ref_ids, second.ref_ids)
        self.assertNotEqual(set(first.ref_ids), set(other_run.ref_ids))
        for ref_id in first.ref_ids:
            self.assertEqual(first[ref_id].source, second[ref_id].source)
        forged = first.model_dump(mode="json")
        original_ref = next(iter(forged["contracts"]))
        forged_contract = forged["contracts"].pop(original_ref)
        forged_contract["ref_id"] = "frozen-contract-forged"
        forged["contracts"]["frozen-contract-forged"] = forged_contract
        with self.assertRaises(ValidationError):
            FrozenContractStore.model_validate(forged)


if __name__ == "__main__":
    unittest.main()
