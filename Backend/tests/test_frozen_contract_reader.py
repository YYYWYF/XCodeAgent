"""T10.3 受限 Frozen Contract Fragment Reader 测试。"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.frozen_contract_catalog import ContractCatalogEntry
from app.services.frozen_contract_reader import (
    FrozenContractReadError,
    FrozenContractReadPolicy,
    FrozenContractReader,
    read_frozen_contract_fragment,
)
from app.services.frozen_contract_store import FrozenContractStore
from tests.test_frozen_contract_store import _formal_inputs


def _reader(
    *,
    selectors: tuple[str, ...] = ("/endpoints/0", "/id"),
    max_reads: int = 10,
    max_total_bytes: int = 1000,
    max_bytes_per_read: int = 1000,
    payload: dict | None = None,
) -> tuple[FrozenContractReader, FrozenContractStore, str]:
    """创建只授权 orders API 合同的独立 Reader 会话。"""

    store = FrozenContractStore.create(
        planning_run_id="fragment-run",
        formal_inputs=payload or _formal_inputs(),
    )
    contract = next(item for item in store.contracts.values() if item.kind == "api_contract")
    catalog = (ContractCatalogEntry(
        ref_id=contract.ref_id,
        kind=contract.kind,
        selectors=selectors,
    ),)
    reader = FrozenContractReader(
        frozen_contract_store=store,
        contract_catalog=catalog,
        read_policy=FrozenContractReadPolicy(
            max_reads=max_reads,
            max_total_bytes=max_total_bytes,
            max_bytes_per_read=max_bytes_per_read,
        ),
    )
    return reader, store, contract.ref_id


class FrozenContractReaderTests(unittest.TestCase):
    """验证 selector、授权、分页、预算及 Frozen Store 唯一来源。"""

    def test_valid_selector_returns_exact_frozen_fragment(self) -> None:
        """授权 JSON Pointer 返回稳定 JSON 文本和规定的输出字段。"""

        reader, _, ref_id = _reader()
        result = read_frozen_contract_fragment(
            reader,
            ref_id=ref_id,
            selector="/endpoints/0",
        )

        self.assertEqual(json.loads(result.content), {"id": "orders.list"})
        self.assertEqual(result.source_ref, ref_id)
        self.assertIsNone(result.cursor)
        self.assertIsNone(result.next_cursor)
        self.assertTrue(result.complete)
        self.assertEqual(
            set(result.model_dump(mode="json", by_alias=True)),
            {"sourceRef", "selector", "content", "cursor", "nextCursor", "complete"},
        )

    def test_paging_uses_bound_opaque_cursor_and_preserves_utf8(self) -> None:
        """多页可无损拼接，cursor 绑定当前 Reader、ref 和 selector。"""

        payload = _formal_inputs()
        payload["api_contracts"][0]["content"]["id"] = "订单接口-很长的稳定标识"
        reader, _, ref_id = _reader(
            payload=payload,
            selectors=("/id",),
            max_reads=20,
            max_total_bytes=1000,
            max_bytes_per_read=7,
        )
        pages = []
        cursor = None
        while True:
            result = read_frozen_contract_fragment(
                reader,
                ref_id=ref_id,
                selector="/id",
                cursor=cursor,
            )
            pages.append(result.content)
            if result.complete:
                self.assertIsNone(result.next_cursor)
                break
            self.assertRegex(result.next_cursor or "", r"^fragment-cursor-[0-9a-f]{64}$")
            cursor = result.next_cursor

        self.assertEqual(json.loads("".join(pages)), "订单接口-很长的稳定标识")
        self.assertEqual(reader.accumulated_bytes, len("".join(pages).encode("utf-8")))

    def test_unauthorized_ref_is_rejected_without_store_disclosure(self) -> None:
        """Store 中真实存在但不在当前 Unit catalog 的 ref 仍被拒绝。"""

        reader, store, _ = _reader()
        unauthorized = next(
            item.ref_id for item in store.contracts.values() if item.kind == "technical_plan"
        )
        with self.assertRaises(FrozenContractReadError) as caught:
            read_frozen_contract_fragment(
                reader,
                ref_id=unauthorized,
                selector="/architecture",
            )

        self.assertEqual(caught.exception.code, "FROZEN_CONTRACT_REF_UNAUTHORIZED")
        self.assertNotIn("content", caught.exception.as_dict())

    def test_unknown_and_invalid_selectors_return_structured_errors(self) -> None:
        """未授权 selector 与 raw workspace path 都产生稳定结构化错误。"""

        reader, _, ref_id = _reader()
        with self.assertRaises(FrozenContractReadError) as unknown:
            read_frozen_contract_fragment(reader, ref_id=ref_id, selector="/endpoints/1")
        self.assertEqual(unknown.exception.code, "FROZEN_CONTRACT_SELECTOR_UNKNOWN")
        self.assertEqual(unknown.exception.details["selector"], "/endpoints/1")

        reader, _, ref_id = _reader(selectors=("/missing",))
        with self.assertRaises(FrozenContractReadError) as absent:
            read_frozen_contract_fragment(reader, ref_id=ref_id, selector="/missing")
        self.assertEqual(absent.exception.code, "FROZEN_CONTRACT_SELECTOR_UNKNOWN")

        reader, _, ref_id = _reader(selectors=("/Users/example/workspace/plan.json",))
        with self.assertRaises(FrozenContractReadError) as invalid:
            read_frozen_contract_fragment(
                reader,
                ref_id=ref_id,
                selector="/Users/example/workspace/plan.json",
            )
        self.assertEqual(invalid.exception.code, "FROZEN_CONTRACT_SELECTOR_INVALID")
        self.assertIn("raw workspace path", str(invalid.exception))

    def test_read_count_limit_is_enforced_across_requests(self) -> None:
        """成功读取耗尽次数后，后续请求在解析正文前被拒绝。"""

        reader, _, ref_id = _reader(max_reads=1)
        read_frozen_contract_fragment(reader, ref_id=ref_id, selector="/id")
        with self.assertRaises(FrozenContractReadError) as caught:
            read_frozen_contract_fragment(reader, ref_id=ref_id, selector="/id")

        self.assertEqual(caught.exception.code, "FROZEN_CONTRACT_READ_COUNT_EXCEEDED")
        self.assertEqual(reader.read_count, 1)

    def test_accumulated_size_limit_rejects_page_before_return(self) -> None:
        """下一页会突破累计字节上限时拒绝返回且不增加成功字节数。"""

        reader, _, ref_id = _reader(
            selectors=("/id",),
            max_total_bytes=10,
            max_bytes_per_read=8,
        )
        first = read_frozen_contract_fragment(reader, ref_id=ref_id, selector="/id")
        self.assertFalse(first.complete)
        self.assertEqual(reader.accumulated_bytes, 8)

        with self.assertRaises(FrozenContractReadError) as caught:
            read_frozen_contract_fragment(
                reader,
                ref_id=ref_id,
                selector="/id",
                cursor=first.next_cursor,
            )
        self.assertEqual(
            caught.exception.code,
            "FROZEN_CONTRACT_ACCUMULATED_SIZE_EXCEEDED",
        )
        self.assertEqual(reader.accumulated_bytes, 8)

    def test_source_file_change_does_not_drift_reader_content(self) -> None:
        """Store 创建后源文件被替换，Reader 仍只返回原冻结正文。"""

        with TemporaryDirectory() as directory:
            path = Path(directory) / "api-contract.json"
            original = {"id": "orders-api", "endpoints": [{"id": "orders.list"}]}
            path.write_text(json.dumps(original), encoding="utf-8")
            payload = _formal_inputs()
            payload["api_contracts"][0] = {
                "content": json.loads(path.read_text(encoding="utf-8")),
                "source": {"artifact": str(path), "revision": "api-v1"},
            }
            reader, _, ref_id = _reader(payload=payload, selectors=("/id",))

            path.write_text(
                json.dumps({"id": "changed-api", "endpoints": []}),
                encoding="utf-8",
            )
            result = read_frozen_contract_fragment(
                reader,
                ref_id=ref_id,
                selector="/id",
            )

        self.assertEqual(json.loads(result.content), "orders-api")
        self.assertEqual(result.source_ref, ref_id)


if __name__ == "__main__":
    unittest.main()
