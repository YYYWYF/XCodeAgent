"""按 Unit allowlist 分页读取 Frozen Contract fragment 的受限会话。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
import re
from threading import Lock
from typing import Any

from langchain_core.tools import tool

from app.services.frozen_contract_catalog import ContractCatalogEntry
from app.services.frozen_contract_reader_contracts import (
    FrozenContractFragment,
    FrozenContractReadInput,
    FrozenContractReadError,
    FrozenContractReadPolicy,
    read_error as _read_error,
)
from app.services.frozen_contract_store import FrozenContract, FrozenContractStore
from app.services.planning_frozen import plain_json


_RAW_PATH_PREFIXES = (
    "/Users/",
    "/home/",
    "/private/",
    "/tmp/",
    "/var/",
    "/workspace/",
)
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
FROZEN_CONTRACT_READER_TOOL_NAME = "read_frozen_contract_fragment"


def _decode_pointer_segment(segment: str) -> str:
    """严格解码 JSON Pointer 转义，拒绝未定义的波浪号序列。"""

    result = []
    index = 0
    while index < len(segment):
        character = segment[index]
        if character != "~":
            result.append(character)
            index += 1
            continue
        if index + 1 >= len(segment) or segment[index + 1] not in {"0", "1"}:
            raise _read_error(
                "FROZEN_CONTRACT_SELECTOR_INVALID",
                "selector 包含无效 JSON Pointer 转义。",
                selector_segment=segment,
            )
        result.append("~" if segment[index + 1] == "0" else "/")
        index += 2
    return "".join(result)


def _selector_segments(selector: str) -> tuple[str, ...]:
    """验证受限 JSON Pointer，并显式拒绝常见宿主绝对路径。"""

    if not isinstance(selector, str) or not selector or selector != selector.strip():
        raise _read_error(
            "FROZEN_CONTRACT_SELECTOR_INVALID",
            "selector 必须是精确非空字符串。",
        )
    if (
        selector.startswith(_RAW_PATH_PREFIXES)
        or selector.startswith(("file://", "~/"))
        or _WINDOWS_PATH.match(selector)
        or "\\" in selector
    ):
        raise _read_error(
            "FROZEN_CONTRACT_SELECTOR_INVALID",
            "selector 不能是 raw workspace path。",
            selector=selector,
        )
    if selector == "/":
        return ()
    if not selector.startswith("/") or "//" in selector:
        raise _read_error(
            "FROZEN_CONTRACT_SELECTOR_INVALID",
            "selector 必须使用受限 JSON Pointer 语法。",
            selector=selector,
        )
    segments = tuple(_decode_pointer_segment(item) for item in selector[1:].split("/"))
    if any(not item or item in {".", ".."} for item in segments):
        raise _read_error(
            "FROZEN_CONTRACT_SELECTOR_INVALID",
            "selector 不能包含空段或路径导航段。",
            selector=selector,
        )
    return segments


def _resolve_selector(contract: FrozenContract, selector: str) -> Any:
    """只在冻结合同正文内解析 selector，不访问文件系统或其他 Store 条目。"""

    current: Any = contract.content
    for segment in _selector_segments(selector):
        if isinstance(current, Mapping):
            if segment not in current:
                raise _read_error(
                    "FROZEN_CONTRACT_SELECTOR_UNKNOWN",
                    "selector 在冻结合同中不存在。",
                    ref_id=contract.ref_id,
                    selector=selector,
                )
            current = current[segment]
            continue
        if isinstance(current, (list, tuple)):
            if not segment.isdigit() or (len(segment) > 1 and segment.startswith("0")):
                raise _read_error(
                    "FROZEN_CONTRACT_SELECTOR_UNKNOWN",
                    "selector 的数组索引无效。",
                    ref_id=contract.ref_id,
                    selector=selector,
                )
            index = int(segment)
            if index >= len(current):
                raise _read_error(
                    "FROZEN_CONTRACT_SELECTOR_UNKNOWN",
                    "selector 的数组索引超出冻结合同范围。",
                    ref_id=contract.ref_id,
                    selector=selector,
                )
            current = current[index]
            continue
        raise _read_error(
            "FROZEN_CONTRACT_SELECTOR_UNKNOWN",
            "selector 不能继续遍历冻结合同标量。",
            ref_id=contract.ref_id,
            selector=selector,
        )
    return current


def _canonical_content(value: Any) -> str:
    """把选中 fragment 编码为稳定、紧凑且可分页拼接的 JSON 文本。"""

    return json.dumps(
        plain_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _page_end(text: str, offset: int, byte_limit: int) -> int:
    """在 UTF-8 字符边界内选择不超过单页字节预算的最大结束位置。"""

    used = 0
    end = offset
    while end < len(text):
        size = len(text[end].encode("utf-8"))
        if used + size > byte_limit:
            break
        used += size
        end += 1
    return end


class FrozenContractReader:
    """维护一次受限读取会话的 allowlist、cursor 与累计预算状态。"""

    def __init__(
        self,
        *,
        frozen_contract_store: FrozenContractStore,
        contract_catalog: Sequence[ContractCatalogEntry | Mapping[str, Any]],
        read_policy: FrozenContractReadPolicy | Mapping[str, Any],
    ) -> None:
        """绑定已验证 Store、Unit catalog 和显式策略，不读取任何实时来源。"""

        if not isinstance(frozen_contract_store, FrozenContractStore):
            raise _read_error(
                "FROZEN_CONTRACT_READER_INPUT_INVALID",
                "Fragment Reader 只接受已验证的 FrozenContractStore instance。",
            )
        try:
            catalog = tuple(ContractCatalogEntry.model_validate(item) for item in contract_catalog)
            policy = FrozenContractReadPolicy.model_validate(plain_json(read_policy))
        except (TypeError, ValueError) as exc:
            if isinstance(exc, FrozenContractReadError):
                raise
            raise _read_error(
                "FROZEN_CONTRACT_READER_INPUT_INVALID",
                "Fragment Reader catalog 或 read policy 无效。",
                reason=str(exc),
            ) from exc
        if len({entry.ref_id for entry in catalog}) != len(catalog):
            raise _read_error(
                "FROZEN_CONTRACT_READER_INPUT_INVALID",
                "Unit contract catalog 包含重复 ref_id。",
            )
        allowed: dict[str, ContractCatalogEntry] = {}
        for entry in catalog:
            contract = frozen_contract_store.get(entry.ref_id)
            if contract is None or contract.kind != entry.kind:
                raise _read_error(
                    "FROZEN_CONTRACT_READER_INPUT_INVALID",
                    "Unit contract catalog 未精确指向当前 Frozen Store。",
                    ref_id=entry.ref_id,
                )
            allowed[entry.ref_id] = entry
        self._store = frozen_contract_store
        self._allowed = allowed
        self._policy = policy
        self._read_count = 0
        self._accumulated_bytes = 0
        self._cursors: dict[str, tuple[str, str, int]] = {}
        self._lock = Lock()

    @property
    def read_count(self) -> int:
        """返回本会话已消耗的读取次数，包括内容返回前的失败请求。"""

        return self._read_count

    @property
    def accumulated_bytes(self) -> int:
        """返回本会话已经成功返回的 UTF-8 内容字节数。"""

        return self._accumulated_bytes

    def _charge_read(self) -> None:
        """在解析授权前原子消耗一次读取额度，阻止无限探测。"""

        if self._read_count >= self._policy.max_reads:
            raise _read_error(
                "FROZEN_CONTRACT_READ_COUNT_EXCEEDED",
                "Frozen contract read count 已达到策略上限。",
                max_reads=self._policy.max_reads,
                read_count=self._read_count,
            )
        self._read_count += 1

    def _offset(self, ref_id: str, selector: str, cursor: str | None) -> int:
        """解析仅由当前 Reader 签发且绑定 ref/selector 的 opaque cursor。"""

        if cursor is None:
            return 0
        if not isinstance(cursor, str) or cursor not in self._cursors:
            raise _read_error(
                "FROZEN_CONTRACT_CURSOR_INVALID",
                "cursor 不属于当前 Fragment Reader 会话。",
                ref_id=ref_id,
                selector=selector,
            )
        bound_ref, bound_selector, offset = self._cursors[cursor]
        if (bound_ref, bound_selector) != (ref_id, selector):
            raise _read_error(
                "FROZEN_CONTRACT_CURSOR_INVALID",
                "cursor 与当前 ref_id/selector 不匹配。",
                ref_id=ref_id,
                selector=selector,
            )
        return offset

    def _next_cursor(self, ref_id: str, selector: str, offset: int) -> str:
        """签发当前会话内可验证的稳定 opaque cursor。"""

        digest = sha256(
            json.dumps(
                {"ref_id": ref_id, "selector": selector, "offset": offset},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        cursor = f"fragment-cursor-{digest}"
        self._cursors[cursor] = (ref_id, selector, offset)
        return cursor

    def read(
        self,
        *,
        ref_id: str,
        selector: str,
        cursor: str | None = None,
    ) -> FrozenContractFragment:
        """执行一次授权、selector、分页和累计预算均受控的冻结读取。"""

        with self._lock:
            self._charge_read()
            entry = self._allowed.get(ref_id)
            if entry is None:
                raise _read_error(
                    "FROZEN_CONTRACT_REF_UNAUTHORIZED",
                    "ref_id 不在当前 Unit contract catalog 中。",
                    ref_id=ref_id,
                )
            _selector_segments(selector)
            if selector not in entry.selectors:
                raise _read_error(
                    "FROZEN_CONTRACT_SELECTOR_UNKNOWN",
                    "selector 不在当前 Unit 的授权清单中。",
                    ref_id=ref_id,
                    selector=selector,
                )
            contract = self._store.get(ref_id)
            if contract is None:
                raise _read_error(
                    "FROZEN_CONTRACT_REF_UNAUTHORIZED",
                    "ref_id 不属于当前 Frozen Store。",
                    ref_id=ref_id,
                )
            content = _canonical_content(_resolve_selector(contract, selector))
            offset = self._offset(ref_id, selector, cursor)
            if offset < 0 or offset >= len(content):
                raise _read_error(
                    "FROZEN_CONTRACT_CURSOR_INVALID",
                    "cursor 已超出当前冻结 fragment。",
                    ref_id=ref_id,
                    selector=selector,
                )
            end = _page_end(content, offset, self._policy.max_bytes_per_read)
            if end == offset:
                raise _read_error(
                    "FROZEN_CONTRACT_ACCUMULATED_SIZE_EXCEEDED",
                    "单页字节预算不足以返回下一个 UTF-8 字符。",
                    max_bytes_per_read=self._policy.max_bytes_per_read,
                )
            page = content[offset:end]
            page_bytes = len(page.encode("utf-8"))
            if self._accumulated_bytes + page_bytes > self._policy.max_total_bytes:
                raise _read_error(
                    "FROZEN_CONTRACT_ACCUMULATED_SIZE_EXCEEDED",
                    "Frozen contract accumulated size 将超过策略上限。",
                    max_total_bytes=self._policy.max_total_bytes,
                    accumulated_bytes=self._accumulated_bytes,
                    requested_bytes=page_bytes,
                )
            self._accumulated_bytes += page_bytes
            complete = end == len(content)
            next_cursor = None if complete else self._next_cursor(ref_id, selector, end)
            return FrozenContractFragment(
                source_ref=ref_id,
                selector=selector,
                content=page,
                cursor=cursor,
                next_cursor=next_cursor,
                complete=complete,
            )


def read_frozen_contract_fragment(
    reader: FrozenContractReader,
    *,
    ref_id: str,
    selector: str,
    cursor: str | None = None,
) -> FrozenContractFragment:
    """通过显式 Reader 会话读取一页，拒绝直接传 Store、路径或任意文件参数。"""

    if not isinstance(reader, FrozenContractReader):
        raise _read_error(
            "FROZEN_CONTRACT_READER_INPUT_INVALID",
            "read_frozen_contract_fragment 必须接收 FrozenContractReader instance。",
        )
    return reader.read(ref_id=ref_id, selector=selector, cursor=cursor)


def create_frozen_contract_reader_tool(reader: FrozenContractReader):
    """创建只绑定一个 Attempt Reader 的唯一模型工具，不暴露 Store 或工作区参数。"""

    if not isinstance(reader, FrozenContractReader):
        raise _read_error(
            "FROZEN_CONTRACT_READER_INPUT_INVALID",
            "Frozen contract tool 必须绑定 FrozenContractReader instance。",
        )

    @tool(FROZEN_CONTRACT_READER_TOOL_NAME, args_schema=FrozenContractReadInput)
    def frozen_contract_fragment_tool(
        ref_id: str,
        selector: str,
        cursor: str | None = None,
    ) -> str:
        """读取当前 Unit catalog 授权的一页冻结合同 JSON；分页时仅复用返回的 nextCursor。"""

        fragment = read_frozen_contract_fragment(
            reader,
            ref_id=ref_id,
            selector=selector,
            cursor=cursor,
        )
        return json.dumps(
            fragment.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    return frozen_contract_fragment_tool
