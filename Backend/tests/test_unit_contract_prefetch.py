"""验证首轮合同预读的授权、分页、预算及单轮生成边界。"""

import json
import unittest

from pydantic import ValidationError

from app.services.frozen_contract_catalog import ContractCatalogEntry
from app.services.frozen_contract_reader import FrozenContractReader
from app.services.frozen_contract_reader_contracts import (
    FrozenContractReadError,
    FrozenContractReadInput,
    FrozenContractReadPolicy,
)
from app.services.frozen_contract_store import FrozenContractStore
from app.services.unit_contract_prefetch import prefetch_unit_contracts
from app.services.unit_generation_tool_session import (
    ContractToolSession,
    UnitGenerationPlatformError,
)
from tests.test_frozen_contract_store import _formal_inputs
from tests.test_unit_generation_tool_session import _store_and_job


def _reader(
    *,
    selectors: tuple[str, ...] = ("/",),
    content: str = "订单页面",
    max_reads: int = 6,
    max_total_bytes: int = 10_000,
    max_bytes_per_read: int = 2_000,
) -> tuple[FrozenContractReader, FrozenContractStore, ContractCatalogEntry, FrozenContractReadPolicy]:
    """准备含其他不可见合同的 Store，以及只授权当前页面的 Reader。"""

    inputs = _formal_inputs()
    inputs["page_contracts"][0]["content"]["description"] = content
    store = FrozenContractStore.create(planning_run_id="prefetch-run", formal_inputs=inputs)
    page = next(item for item in store.contracts.values() if item.kind == "page_contract")
    entry = ContractCatalogEntry(ref_id=page.ref_id, kind=page.kind, selectors=selectors)
    policy = FrozenContractReadPolicy(
        max_reads=max_reads, max_total_bytes=max_total_bytes,
        max_bytes_per_read=max_bytes_per_read,
    )
    return FrozenContractReader(
        frozen_contract_store=store, contract_catalog=(entry,), read_policy=policy,
    ), store, entry, policy


class UnitContractPrefetchTests(unittest.TestCase):
    """预读不绕过原 Reader，也不改变模型补读的授权和预算。"""

    def test_complete_fragments_are_scoped_and_frozen(self) -> None:
        """完整预读只包含当前授权页面，不注入全局 ProductPlan 或其他合同。"""

        reader, store, entry, policy = _reader(selectors=("/pageId", "/requiredEndpointIds"))
        before = store.model_dump(mode="json")
        fragments = prefetch_unit_contracts(reader, (entry,), policy)
        self.assertEqual([json.loads(item.content) for item in fragments], ["orders", ["orders.list"]])
        self.assertTrue(all(item.complete for item in fragments))
        self.assertEqual({item.source_ref for item in fragments}, {entry.ref_id})
        self.assertEqual(reader.read_count, 2)
        self.assertEqual(store.model_dump(mode="json"), before)

    def test_large_fragment_leaves_budget_and_same_reader_cursor_for_supplement(self) -> None:
        """大合同预读在一半次数内停止，模型可用同一 Reader 的游标继续。"""

        reader, _, entry, policy = _reader(
            selectors=("/description", "/pageId"), content="订单查询" * 300,
            max_reads=6, max_total_bytes=1_000, max_bytes_per_read=64,
        )
        fragments = prefetch_unit_contracts(reader, (entry,), policy)
        self.assertEqual(reader.read_count, 3)
        self.assertLessEqual(reader.accumulated_bytes, policy.max_total_bytes // 2)
        self.assertEqual(json.loads(fragments[1].content), "orders")
        last = fragments[-1]
        self.assertFalse(last.complete)
        following = reader.read(ref_id=entry.ref_id, selector=last.selector, cursor=last.next_cursor)
        self.assertEqual(following.cursor, last.next_cursor)
        self.assertEqual(reader.read_count, 4)
        self.assertEqual(reader.accumulated_bytes, sum(
            len(item.content.encode("utf-8")) for item in (*fragments, following)
        ))

    def test_large_production_page_cannot_overrun_prefetch_byte_cap(self) -> None:
        """单页策略很大时，平台仍把预读正文限制在 16 KiB 并保留补读能力。"""

        reader, _, entry, policy = _reader(
            content="中文" * 10_000, max_reads=24,
            max_total_bytes=100_000, max_bytes_per_read=100_000,
        )
        fragments = prefetch_unit_contracts(reader, (entry,), policy)
        self.assertEqual(len(fragments), 1)
        self.assertLessEqual(reader.accumulated_bytes, 16_384)
        self.assertFalse(fragments[0].complete)
        following = reader.read(
            ref_id=entry.ref_id, selector="/", cursor=fragments[0].next_cursor,
        )
        self.assertTrue(following.complete)
        self.assertEqual(json.loads(fragments[0].content + following.content)["pageId"], "orders")

    def test_small_read_budget_is_preserved_for_model(self) -> None:
        """只有一次读取额度时不提前消耗，原有模型读取仍可完成。"""

        reader, _, entry, policy = _reader(max_reads=1)
        self.assertEqual(prefetch_unit_contracts(reader, (entry,), policy), ())
        self.assertEqual(reader.read_count, 0)
        self.assertTrue(reader.read(ref_id=entry.ref_id, selector="/").complete)

    def test_small_byte_budget_is_shared_exactly(self) -> None:
        """预读按 UTF-8 边界占用一半字节，续读不能获得新的累计预算。"""

        reader, _, entry, policy = _reader(
            selectors=("/pageId",), max_total_bytes=8, max_bytes_per_read=8,
        )
        fragments = prefetch_unit_contracts(reader, (entry,), policy)
        self.assertEqual(reader.accumulated_bytes, 4)
        last = fragments[-1]
        following = reader.read(ref_id=entry.ref_id, selector=last.selector, cursor=last.next_cursor)
        self.assertEqual(reader.accumulated_bytes, 8)
        self.assertEqual(json.loads(last.content + following.content), "orders")
        with self.assertRaises(FrozenContractReadError) as caught:
            reader.read(ref_id=entry.ref_id, selector="/pageId")
        self.assertEqual(caught.exception.code, "FROZEN_CONTRACT_ACCUMULATED_SIZE_EXCEEDED")

    def test_prefetch_cannot_expand_reader_allowlist(self) -> None:
        """向预读器传入另一份真实合同也不能突破 Reader 的 Unit allowlist。"""

        reader, store, _, policy = _reader()
        other = next(item for item in store.contracts.values() if item.kind == "technical_plan")
        entry = ContractCatalogEntry(ref_id=other.ref_id, kind=other.kind, selectors=("/architecture",))
        with self.assertRaises(FrozenContractReadError) as caught:
            prefetch_unit_contracts(reader, (entry,), policy)
        self.assertEqual(caught.exception.code, "FROZEN_CONTRACT_REF_UNAUTHORIZED")
        self.assertEqual(reader.accumulated_bytes, 0)

    def test_platform_page_limit_cannot_enlarge_reader_policy_or_tool_schema(self) -> None:
        """平台只能缩小页大小，模型工具参数中仍不接受此选项。"""

        reader, _, entry, _ = _reader(content="内容" * 100, max_bytes_per_read=32)
        page = reader.read(ref_id=entry.ref_id, selector="/", max_bytes=1_000)
        self.assertLessEqual(len(page.content.encode("utf-8")), 32)
        with self.assertRaises(ValidationError):
            FrozenContractReadInput(ref_id=entry.ref_id, selector="/", max_bytes=1_000)
        for invalid in (True, 0, 3, 4.5):
            with self.subTest(value=invalid), self.assertRaises(FrozenContractReadError):
                reader.read(ref_id=entry.ref_id, selector="/", max_bytes=invalid)

    def test_invalid_platform_selector_is_fatal_before_model_dispatch(self) -> None:
        """平台清单指向不存在的片段时明确报告平台损坏，不让模型补造合同。"""

        store, job, ref_id = _store_and_job()
        context = job.context.model_copy(update={"contract_catalog": (
            ContractCatalogEntry(ref_id=ref_id, kind="page_contract", selectors=("/missing",)),
        )})
        with self.assertRaises(UnitGenerationPlatformError) as caught:
            ContractToolSession(job.model_copy(update={"context": context}),
                                frozen_contract_store=store, prompt="生成当前任务")
        self.assertEqual(caught.exception.stage, "contract_read")
