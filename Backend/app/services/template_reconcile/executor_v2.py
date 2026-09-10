"""执行 V2 Strategy 的进程内 Working Copy、ADD_FILE 和原子 Apply 内核。"""

from __future__ import annotations

import os
import tempfile
import json
import xml.etree.ElementTree as element_tree
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.services.template_reconcile.protocol_v2 import StrategyDescriptorV2


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
                self._ensure_marked_structure(strategy, store, payloads)
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
        """在唯一锚点前后插入不可变内容，并用完整内容判断幂等。"""

        entry = _existing_text_entry(strategy, store)
        insertion = _strategy_content(strategy, payloads)
        if insertion in str(entry.working_content):
            return
        anchor = _required_string(strategy.parameters, "anchor")
        occurrences = str(entry.working_content).count(anchor)
        if occurrences != 1:
            raise StrategyExecutionV2Error("TEXT_ANCHOR_INSERT 的 anchor 必须在目标中唯一。")
        position = str(strategy.parameters.get("position", "before"))
        if position not in {"before", "after"}:
            raise StrategyExecutionV2Error("TEXT_ANCHOR_INSERT 的 position 必须是 before 或 after。")
        content = str(entry.working_content)
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
        lines = content.splitlines(keepends=True)
        import_indexes = [index for index, line in enumerate(lines) if line.lstrip().startswith("import ")]
        if not import_indexes:
            raise StrategyExecutionV2Error("ENSURE_IMPORT 找不到既有 import 区，拒绝猜测插入位置。")
        insertion = import_statement + ("" if import_statement.endswith(";") else ";") + "\n"
        insert_at = import_indexes[-1] + 1
        lines.insert(insert_at, insertion)
        entry.working_content = "".join(lines)
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
        try:
            root = element_tree.fromstring(str(entry.working_content))
        except element_tree.ParseError as exc:
            raise StrategyExecutionV2Error("ENSURE_MAVEN_DEPENDENCY 目标不是合法 XML。") from exc
        namespace = _xml_namespace(root.tag)
        dependencies = root.find(f"{namespace}dependencies")
        if dependencies is None:
            dependencies = element_tree.SubElement(root, f"{namespace}dependencies")
        for dependency in dependencies.findall(f"{namespace}dependency"):
            found_group = dependency.findtext(f"{namespace}groupId")
            found_artifact = dependency.findtext(f"{namespace}artifactId")
            if found_group == group_id and found_artifact == artifact_id:
                found_version = dependency.findtext(f"{namespace}version")
                if found_version != version:
                    raise StrategyExecutionV2Error("MAVEN_DEPENDENCY_CONFLICT：已存在不同依赖版本。")
                return
        dependency = element_tree.SubElement(dependencies, f"{namespace}dependency")
        element_tree.SubElement(dependency, f"{namespace}groupId").text = group_id
        element_tree.SubElement(dependency, f"{namespace}artifactId").text = artifact_id
        element_tree.SubElement(dependency, f"{namespace}version").text = version
        element_tree.indent(root, space="  ")
        entry.working_content = element_tree.tostring(root, encoding="unicode") + "\n"
        entry.changed = True

    def _ensure_marked_structure(self, strategy: StrategyDescriptorV2, store: WorkingCopyStoreV2, payloads: dict[str, str]) -> None:
        """用 Capability 专属标记和唯一锚点维护 Provider、路由、菜单及 Spring 结构。"""

        entry = _existing_text_entry(strategy, store)
        marker = _required_string(strategy.parameters, "managedMarker")
        content = str(entry.working_content)
        if marker in content:
            return
        insertion = _strategy_content(strategy, payloads)
        if marker not in insertion:
            raise StrategyExecutionV2Error("结构化 Strategy 的 payload 必须包含 managedMarker。")
        anchor = _required_string(strategy.parameters, "anchor")
        if content.count(anchor) != 1:
            raise StrategyExecutionV2Error("结构化 Strategy 的 anchor 必须在目标中唯一。")
        position = str(strategy.parameters.get("position", "before"))
        if position not in {"before", "after"}:
            raise StrategyExecutionV2Error("结构化 Strategy 的 position 必须是 before 或 after。")
        offset = content.index(anchor) + (len(anchor) if position == "after" else 0)
        entry.working_content = content[:offset] + insertion + content[offset:]
        entry.changed = True


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


def _xml_namespace(tag: str) -> str:
    """从 XML 根标签提取 ElementTree 查询所需的命名空间前缀。"""

    return tag[: tag.index("}") + 1] if tag.startswith("{") and "}" in tag else ""


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
