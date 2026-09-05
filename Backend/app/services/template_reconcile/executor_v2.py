"""执行 V2 Strategy 的进程内 Working Copy、ADD_FILE 和原子 Apply 内核。"""

from __future__ import annotations

import os
import tempfile
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services.template_reconcile.protocol_v2 import StrategyDescriptorV2
from app.services.template_reconcile.strategy_ast_v2 import (
    StrategyAstV2Error,
    insert_after_last_import,
    insert_at_selector,
)


class StrategyExecutionV2Error(ValueError):
    """表示 Strategy 无法在当前 Workspace 安全收敛。"""


@dataclass
class WorkingCopyEntryV2:
    """保存单个 touched 文件的初始状态与最终内存内容。"""

    path: str
    original_exists: bool
    original_content: str | None
    working_content: str | None
    changed: bool = False
    applied: bool = False


class WorkingCopyStoreV2:
    """保证同一路径的多个 Strategy 只读取一次并共享内存副本。"""

    def __init__(self, workspace: str | Path) -> None:
        """绑定一个已由 Run Gate 独占的 Workspace 根目录。"""

        self._root = Path(workspace).expanduser().resolve()
        self._entries: dict[str, WorkingCopyEntryV2] = {}

    def entry(self, raw_path: str) -> WorkingCopyEntryV2:
        """按需读取普通文本文件，或为缺失路径创建空 before-image。"""

        if raw_path not in self._entries:
            target = _target(self._root, raw_path)
            if target.exists():
                if not target.is_file() or target.is_symlink():
                    raise StrategyExecutionV2Error("Strategy 目标必须是普通文件。")
                content = target.read_text(encoding="utf-8")
                self._entries[raw_path] = WorkingCopyEntryV2(raw_path, True, content, content)
            else:
                self._entries[raw_path] = WorkingCopyEntryV2(raw_path, False, None, None)
        return self._entries[raw_path]

    def changed_entries(self) -> list[WorkingCopyEntryV2]:
        """按路径稳定排序返回需要最终写盘的 entry。"""

        return sorted((entry for entry in self._entries.values() if entry.changed), key=lambda entry: entry.path)


class ModificationStrategyExecutorV2:
    """严格按 Package 顺序把已实现 Strategy 写入 WorkingCopyStore。"""

    def execute(self, strategies: list[StrategyDescriptorV2], store: WorkingCopyStoreV2, payloads: dict[str, str]) -> None:
        """按既定顺序分派全部冻结的 V2 Strategy，未知类型必须失败关闭。"""

        for strategy in strategies:
            if strategy.type == "ADD_FILE":
                self._add_file(strategy, store, payloads)
            elif strategy.type == "TEXT_ANCHOR_INSERT":
                self._text_anchor_insert(strategy, store, payloads)
            elif strategy.type == "ENSURE_IMPORT":
                self._ensure_import(strategy, store)
            elif strategy.type == "ENSURE_NPM_DEPENDENCY":
                self._ensure_npm_dependency(strategy, store)
            elif strategy.type == "ENSURE_MAVEN_DEPENDENCY":
                self._ensure_maven_dependency(strategy, store)
            elif strategy.type in {
                "ENSURE_REACT_PROVIDER",
                "ENSURE_ROUTE",
                "ENSURE_MENU_ITEM",
                "ENSURE_SPRING_BEAN",
                "ENSURE_INTERCEPTOR",
            }:
                self._ensure_ast_structure(strategy, store, payloads)
            else:
                raise StrategyExecutionV2Error(f"Strategy 尚未在 V2 内核实现：{strategy.type}。")

    def _add_file(self, strategy: StrategyDescriptorV2, store: WorkingCopyStoreV2, payloads: dict[str, str]) -> None:
        """以不存在/同内容/冲突三分支实现 ADD_FILE 的 retry-safe 语义。"""

        if strategy.payloadRef is None or strategy.payloadRef not in payloads:
            raise StrategyExecutionV2Error("ADD_FILE 缺少已验证的 payload。")
        entry = store.entry(strategy.target)
        expected = payloads[strategy.payloadRef]
        if entry.working_content is None:
            entry.working_content = expected
            entry.changed = True
        elif entry.working_content != expected:
            raise StrategyExecutionV2Error("ADDITION_TARGET_CONFLICT：ADD_FILE 目标已有不同内容。")

    def _text_anchor_insert(self, strategy: StrategyDescriptorV2, store: WorkingCopyStoreV2, payloads: dict[str, str]) -> None:
        """以成对 managed marker 收敛文本块；已有块只原位替换，不完整标记立即失败关闭。"""

        entry = _existing_text_entry(strategy, store)
        insertion = _strategy_content(strategy, payloads)
        marker = _required_string(strategy.parameters, "managedMarker")
        content = str(entry.working_content)
        existing_block = _managed_marker_block(content, marker)
        expected_block = _managed_marker_block(insertion, marker)
        if existing_block is not None:
            if expected_block is None:
                raise StrategyExecutionV2Error("TEXT_ANCHOR_INSERT 的 content 必须包含一对 managedMarker begin/end。")
            if content[existing_block[0]:existing_block[1]] == insertion:
                return
            entry.working_content = content[:existing_block[0]] + insertion + content[existing_block[1]:]
            entry.changed = True
            return
        if expected_block is None:
            raise StrategyExecutionV2Error("TEXT_ANCHOR_INSERT 的 content 必须包含一对 managedMarker begin/end。")
        anchor = _required_string(strategy.parameters, "anchor")
        occurrences = content.count(anchor)
        if occurrences != 1:
            raise StrategyExecutionV2Error("TEXT_ANCHOR_INSERT 的 anchor 必须在目标中唯一。")
        position = str(strategy.parameters.get("position", "before"))
        if position not in {"before", "after"}:
            raise StrategyExecutionV2Error("TEXT_ANCHOR_INSERT 的 position 必须是 before 或 after。")
        offset = content.index(anchor) + (len(anchor) if position == "after" else 0)
        entry.working_content = content[:offset] + insertion + content[offset:]
        entry.changed = True

    def _ensure_import(self, strategy: StrategyDescriptorV2, store: WorkingCopyStoreV2) -> None:
        """确保 TypeScript 或 Java import 仅出现一次，缺失时插入 import 区末尾。"""

        entry = _existing_text_entry(strategy, store)
        import_statement = _required_string(strategy.parameters, "importStatement").strip()
        content = str(entry.working_content)
        normalized_lines = [line.strip().rstrip(";") for line in content.splitlines()]
        if import_statement.rstrip(";") in normalized_lines:
            return
        insertion = import_statement + ("" if import_statement.endswith(";") else ";") + "\n"
        try:
            entry.working_content = insert_after_last_import(strategy.target, content, insertion)
        except StrategyAstV2Error as exc:
            raise StrategyExecutionV2Error(str(exc)) from exc
        entry.changed = True

    def _ensure_npm_dependency(self, strategy: StrategyDescriptorV2, store: WorkingCopyStoreV2) -> None:
        """以 JSON 语义确保 package.json 中的单个依赖版本，不覆盖冲突版本。"""

        entry = _existing_text_entry(strategy, store)
        dependency_name = _required_string(strategy.parameters, "name")
        dependency_version = _required_string(strategy.parameters, "version")
        section = str(strategy.parameters.get("section", "dependencies"))
        if section not in {"dependencies", "devDependencies"}:
            raise StrategyExecutionV2Error("ENSURE_NPM_DEPENDENCY 仅支持 dependencies 或 devDependencies。")
        try:
            document = json.loads(str(entry.working_content))
        except json.JSONDecodeError as exc:
            raise StrategyExecutionV2Error("ENSURE_NPM_DEPENDENCY 目标不是合法 JSON。") from exc
        if not isinstance(document, dict):
            raise StrategyExecutionV2Error("ENSURE_NPM_DEPENDENCY 的 package.json 根节点必须是对象。")
        for candidate in ("dependencies", "devDependencies"):
            existing = document.get(candidate, {})
            if isinstance(existing, dict) and dependency_name in existing:
                if existing[dependency_name] != dependency_version:
                    raise StrategyExecutionV2Error("NPM_DEPENDENCY_CONFLICT：已存在不同依赖版本。")
                return
        dependencies = document.setdefault(section, {})
        if not isinstance(dependencies, dict):
            raise StrategyExecutionV2Error("ENSURE_NPM_DEPENDENCY 的依赖区必须是对象。")
        dependencies[dependency_name] = dependency_version
        entry.working_content = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        entry.changed = True

    def _ensure_maven_dependency(self, strategy: StrategyDescriptorV2, store: WorkingCopyStoreV2) -> None:
        """以 Maven XML 语义确保唯一 groupId/artifactId 依赖，版本冲突时失败关闭。"""

        entry = _existing_text_entry(strategy, store)
        group_id = _required_string(strategy.parameters, "groupId")
        artifact_id = _required_string(strategy.parameters, "artifactId")
        version = _required_string(strategy.parameters, "version")
        content = str(entry.working_content)
        matches = re.findall(r"<dependency\b[^>]*>(.*?)</dependency>", content, flags=re.DOTALL)
        for dependency in matches:
            if _xml_tag_text(dependency, "groupId") == group_id and _xml_tag_text(dependency, "artifactId") == artifact_id:
                if _xml_tag_text(dependency, "version") != version:
                    raise StrategyExecutionV2Error("MAVEN_DEPENDENCY_CONFLICT：已存在不同依赖版本。")
                return
        closing = re.search(r"</dependencies\s*>", content)
        if closing is None:
            raise StrategyExecutionV2Error("MAVEN_DEPENDENCIES_TARGET_MISSING：pom.xml 缺少 dependencies 节点。")
        indent = "  "
        insertion = f"\n{indent}<dependency>\n{indent}  <groupId>{group_id}</groupId>\n{indent}  <artifactId>{artifact_id}</artifactId>\n{indent}  <version>{version}</version>\n{indent}</dependency>"
        entry.working_content = content[:closing.start()] + insertion + content[closing.start():]
        entry.changed = True

    def _ensure_ast_structure(self, strategy: StrategyDescriptorV2, store: WorkingCopyStoreV2, payloads: dict[str, str]) -> None:
        """用唯一 AST selector 维护 Provider、路由、菜单及 Spring 结构。"""

        entry = _existing_text_entry(strategy, store)
        marker = _required_string(strategy.parameters, "managedMarker")
        content = str(entry.working_content)
        if marker in content:
            return
        insertion = _strategy_content(strategy, payloads)
        if marker not in insertion:
            raise StrategyExecutionV2Error("结构化 Strategy 的 payload 必须包含 managedMarker。")
        selector = strategy.parameters.get("astSelector")
        if not isinstance(selector, dict):
            raise StrategyExecutionV2Error("结构化 Strategy 必须提供 astSelector。")
        try:
            entry.working_content = insert_at_selector(strategy.target, content, selector, insertion)
        except StrategyAstV2Error as exc:
            raise StrategyExecutionV2Error(str(exc)) from exc
        entry.changed = True


def _managed_marker_block(content: str, marker: str) -> tuple[int, int] | None:
    """返回唯一 managed block 的整行范围；缺失可插入，不完整、重复或倒序标记必须失败关闭。"""

    begin = f"{marker}:begin"
    end = f"{marker}:end"
    begin_positions = _all_positions(content, begin)
    end_positions = _all_positions(content, end)
    if not begin_positions and not end_positions:
        return None
    if len(begin_positions) != 1 or len(end_positions) != 1 or begin_positions[0] >= end_positions[0]:
        raise StrategyExecutionV2Error("TEXT_ANCHOR_INSERT 的 managedMarker begin/end 必须各唯一且顺序完整。")
    return _line_start(content, begin_positions[0]), _line_end(content, end_positions[0])


def _all_positions(content: str, token: str) -> list[int]:
    """返回 token 的全部非重叠位置，供 managed marker 的重复检测使用。"""

    positions: list[int] = []
    offset = 0
    while (position := content.find(token, offset)) >= 0:
        positions.append(position)
        offset = position + len(token)
    return positions


def _line_start(content: str, position: int) -> int:
    """定位 marker 所在行的起始位置，使替换不会遗留注释前缀。"""

    return content.rfind("\n", 0, position) + 1


def _line_end(content: str, position: int) -> int:
    """定位 marker 所在行的结尾并包含换行，使整块替换保持原位边界。"""

    line_end = content.find("\n", position)
    return len(content) if line_end < 0 else line_end + 1


def apply_working_copy_v2(workspace: str | Path, store: WorkingCopyStoreV2) -> None:
    """以确定路径顺序和同目录原子替换写入所有 changed 文件。"""

    root = Path(workspace).expanduser().resolve()
    for entry in store.changed_entries():
        target = _target(root, entry.path)
        if entry.working_content is None:
            raise StrategyExecutionV2Error("Working Copy 缺少待写内容。")
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, entry.working_content)
        entry.applied = True


def restore_working_copy_v2(workspace: str | Path, store: WorkingCopyStoreV2) -> None:
    """仅在进程仍存活时恢复本次已 Apply 的 touched 文件。"""

    root = Path(workspace).expanduser().resolve()
    for entry in reversed(store.changed_entries()):
        if not entry.applied:
            continue
        target = _target(root, entry.path)
        if entry.original_exists:
            _atomic_write(target, str(entry.original_content))
        else:
            target.unlink(missing_ok=True)


def _target(root: Path, raw_path: str) -> Path:
    """解析受限相对路径，禁止 Strategy 写入控制目录或越界位置。"""

    path = PurePosixPath(raw_path)
    if not raw_path or "\\" in raw_path or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.parts[0] in {".git", ".xcodeagent"}:
        raise StrategyExecutionV2Error("Strategy target 路径无效。")
    return root.joinpath(*path.parts)


def _existing_text_entry(strategy: StrategyDescriptorV2, store: WorkingCopyStoreV2) -> WorkingCopyEntryV2:
    """取得必须已存在的文本目标，防止结构化 Strategy 隐式创建未知文件。"""

    entry = store.entry(strategy.target)
    if entry.working_content is None:
        raise StrategyExecutionV2Error(f"{strategy.type} 的目标文件不存在。")
    return entry


def _strategy_content(strategy: StrategyDescriptorV2, payloads: dict[str, str]) -> str:
    """从已校验 payload 或内联参数获取待插入文本，拒绝二义来源。"""

    inline = strategy.parameters.get("content")
    has_inline = isinstance(inline, str) and bool(inline)
    has_payload = strategy.payloadRef is not None
    if has_inline == has_payload:
        raise StrategyExecutionV2Error("Strategy 必须且只能提供一个 content 或 payloadRef。")
    if has_inline:
        return str(inline)
    if strategy.payloadRef not in payloads:
        raise StrategyExecutionV2Error("Strategy 缺少已验证的 payload。")
    return payloads[str(strategy.payloadRef)]


def _required_string(parameters: dict[str, object], key: str) -> str:
    """读取非空字符串参数，避免 Handler 对 Service 输出做隐式类型转换。"""

    value = parameters.get(key)
    if not isinstance(value, str) or not value:
        raise StrategyExecutionV2Error(f"Strategy 参数 {key} 必须是非空字符串。")
    return value


def _xml_tag_text(content: str, tag: str) -> str | None:
    """从单个 Maven dependency 片段读取简单 XML 标签文本。"""

    match = re.search(rf"<{tag}\b[^>]*>\s*([^<]+?)\s*</{tag}\s*>", content)
    return match.group(1).strip() if match else None


def _atomic_write(path: Path, content: str) -> None:
    """以同目录临时文件、fsync 和 replace 避免留下半写文本文件。"""

    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
