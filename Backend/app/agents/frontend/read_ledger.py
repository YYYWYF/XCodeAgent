"""记录任务内的版本与实际交付区间，读取正文始终由原工具返回。"""

import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from app.agents.frontend.file_backend import file_revision

_LINE_NUMBER = re.compile(r"^\s*(\d+)(?:\.\d+)?\t")


@dataclass
class ReadCoverage:
    """每个文件仅保留当前版本的有限区间和请求信息。"""

    revision: tuple
    ranges: list[tuple[int, int]] = field(default_factory=list)
    requests: deque = field(default_factory=lambda: deque(maxlen=64))
    eof: int | None = None
    last_step: int = 0
    exact_count: int = 0
    covered_count: int = 0

    def record(self, offset: int, limit: int, content: str, step: int) -> dict:
        """解析原工具的行号，区分精确重复与区间覆盖。"""
        exact = (offset, limit) in self.requests
        wanted_end = min(offset + limit, self.eof) if self.eof is not None else offset + limit
        covered = wanted_end > offset and any(a <= offset and wanted_end <= b for a, b in self.ranges)
        self.exact_count += int(exact)
        self.covered_count += int(covered)
        self.requests.append((offset, limit))
        self.last_step = step
        body, marker, _ = content.partition("\n\n[Output was truncated due to size limits.")
        lines = []
        for row in body.split("\n"):
            match = _LINE_NUMBER.match(row)
            if match:
                lines.append(int(match[1]))
        # 截断可能落在一行中间，保守地舍弃最后一行的覆盖记录。
        last = max(lines, default=offset) - int(bool(lines and marker))
        if lines and min(lines) == offset + 1 and last > offset:
            self._merge(offset, last)
            if not marker and last - offset < limit:
                self.eof = last
        full = self.eof is not None and any(a == 0 and b >= self.eof for a, b in self.ranges)
        return {
            "duplicate_read": exact, "duplicate_read_kind": "exact" if exact else "covered" if covered else "new_range",
            "full_read": full, "frontend_read_repeated_covered_count": int(covered),
        }

    def _merge(self, start: int, end: int) -> None:
        """合并相邻有效区间并限制账本体积。"""
        merged = []
        for left, right in sorted([*self.ranges, (start, end)]):
            if merged and left <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], right))
            else:
                merged.append((left, right))
        self.ranges = merged[-64:]


class FrontendReadLedger:
    """将读取、内部写入与外部文件版本变化关联起来。"""

    def __init__(self):
        """建立任务级的轻量状态。"""
        self.files: dict[str, ReadCoverage] = {}
        self.invalidated: set[str] = set()

    def invalidate(self, path: str) -> None:
        """仅有效写入使旧版本区间失效。"""
        if self.files.pop(path, None) is not None:
            self.invalidated.add(path)

    def clear(self) -> None:
        """关闭任务时释放所有读取记录。"""
        self.files.clear()
        self.invalidated.clear()

    @staticmethod
    def version(physical: Path | None) -> tuple | None:
        """对已获授权的文件采集真实版本。"""
        try:
            return file_revision(physical.stat()) if physical else None
        except OSError:
            return None

    def observe(self, path: str, physical: Path | None, before: tuple | None,
                args: dict, content: str, step: int) -> dict:
        """仅登记版本稳定且真实返回的文本区间。"""
        if before is None or before != self.version(physical):
            return {}
        previous = self.files.get(path)
        new_revision = (previous is not None and previous.revision != before) or path in self.invalidated
        if previous is None or new_revision:
            previous = self.files[path] = ReadCoverage(before)
            self.invalidated.discard(path)
        result = previous.record(args.get("offset", 0), args.get("limit", 2000), content, step)
        if new_revision:
            result["duplicate_read_kind"] = "new_revision"
        return result
