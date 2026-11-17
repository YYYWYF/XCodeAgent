"""在首轮模型请求前有界预读当前 Unit 已授权的必需合同片段。"""

from collections import deque
from collections.abc import Sequence

from app.services.frozen_contract_catalog import ContractCatalogEntry
from app.services.frozen_contract_reader import FrozenContractReader
from app.services.frozen_contract_reader_contracts import (
    FrozenContractFragment,
    FrozenContractReadPolicy,
)


_MAX_PREFETCH_CONTENT_BYTES = 16_384


def prefetch_unit_contracts(
    reader: FrozenContractReader,
    catalog: Sequence[ContractCatalogEntry],
    policy: FrozenContractReadPolicy,
) -> tuple[FrozenContractFragment, ...]:
    """只预读职责清单授权的片段，并为同一 Reader 的模型补读保留至少一半预算。"""

    initial_reads = reader.read_count
    initial_bytes = reader.accumulated_bytes
    read_limit = (policy.max_reads - initial_reads) // 2
    byte_limit = min(
        _MAX_PREFETCH_CONTENT_BYTES,
        (policy.max_total_bytes - initial_bytes) // 2,
    )
    pending: deque[tuple[str, str, str | None]] = deque(
        (entry.ref_id, selector, None)
        for entry in catalog
        for selector in entry.selectors
    )
    fragments: list[FrozenContractFragment] = []
    while pending and reader.read_count - initial_reads < read_limit:
        remaining = byte_limit - (reader.accumulated_bytes - initial_bytes)
        if remaining < 4:
            break
        ref_id, selector, cursor = pending.popleft()
        fragment = reader.read(
            ref_id=ref_id,
            selector=selector,
            cursor=cursor,
            max_bytes=remaining,
        )
        fragments.append(fragment)
        # 先给每个必需 selector 一页，再读取大片段的后续页，避免单份合同独占预读。
        if fragment.next_cursor is not None:
            pending.append((ref_id, selector, fragment.next_cursor))
    return tuple(fragments)
