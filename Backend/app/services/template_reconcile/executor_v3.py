"""在 Workspace 中执行 V3 Update Operations。"""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from app.services.template_reconcile.protocol_v3 import AddFileOperationV3, EnsureJavaAnnotationOperationV3, EnsureMavenDependencyOperationV3, ExtensionUpdatePackageV3, RemoveJavaAnnotationOperationV3, RemoveMavenDependencyOperationV3, ReplaceManagedFileOperationV3

class UpdateExecutionV3Error(ValueError):
    """表示当前 Workspace 不满足 V3 Operation 前置条件。"""

@dataclass
class WorkingCopyEntryV3:
    """记录一个目标文件的原始和待提交字节。"""
    path: str
    original: bytes | None
    working: bytes | None

class WorkingCopyStoreV3:
    """让全部 Operation 在内存副本中执行，避免半包写入。"""
    def __init__(self, workspace: str | Path) -> None:
        """绑定已取得写锁的 Workspace。"""
        self.root = Path(workspace).expanduser().resolve()
        self.entries: dict[str, WorkingCopyEntryV3] = {}

    def entry(self, path: str) -> WorkingCopyEntryV3:
        """读取普通文件或创建缺失文件的 before-image。"""
        if path not in self.entries:
            target = _target(self.root, path)
            if target.is_symlink() or (target.exists() and not target.is_file()):
                raise UpdateExecutionV3Error("UNSAFE_TARGET_PATH：目标必须是普通文件。")
            original = target.read_bytes() if target.exists() else None
            self.entries[path] = WorkingCopyEntryV3(path, original, original)
        return self.entries[path]

    def changed(self) -> list[WorkingCopyEntryV3]:
        """稳定返回待提交文件。"""
        return sorted((item for item in self.entries.values() if item.original != item.working), key=lambda item: item.path)

class UpdatePackageExecutorV3:
    """实现 V3 固定 Operation 集合。"""
    def execute(self, package: ExtensionUpdatePackageV3, store: WorkingCopyStoreV3, payloads: dict[str, bytes]) -> None:
        """严格按 package index 将全部操作应用到内存副本。"""
        for operation in package.operations:
            if isinstance(operation, AddFileOperationV3):
                entry = store.entry(operation.target)
                if entry.working is not None:
                    raise UpdateExecutionV3Error("ADD_FILE_TARGET_EXISTS：ADD_FILE 目标已存在。")
                entry.working = payloads[operation.payloadRef]
            elif isinstance(operation, ReplaceManagedFileOperationV3):
                store.entry(operation.target).working = payloads[operation.payloadRef]
            elif isinstance(operation, (EnsureJavaAnnotationOperationV3, RemoveJavaAnnotationOperationV3)):
                self._java(operation, store, isinstance(operation, RemoveJavaAnnotationOperationV3))
            elif isinstance(operation, (EnsureMavenDependencyOperationV3, RemoveMavenDependencyOperationV3)):
                self._maven(operation, store, isinstance(operation, RemoveMavenDependencyOperationV3))
            else:
                raise UpdateExecutionV3Error("OPERATION_UNSUPPORTED：未知 V3 Operation。")

    def _java(self, operation: EnsureJavaAnnotationOperationV3 | RemoveJavaAnnotationOperationV3, store: WorkingCopyStoreV3, remove: bool) -> None:
        """安全确保或删除 import 与 Application annotation 成对出现。"""
        entry = store.entry(operation.target)
        if entry.working is None:
            raise UpdateExecutionV3Error("MANAGED_SURFACE_CONFLICT：Application.java 不存在。")
        content = entry.working.decode("utf-8")
        fqcn = operation.parameters.annotationClass
        simple = fqcn.rsplit(".", 1)[-1]
        imports = list(re.finditer(rf"(?m)^\s*import\s+{re.escape(fqcn)}\s*;\s*\n?", content))
        annotations = list(re.finditer(rf"(?m)^\s*@{re.escape(simple)}\s*\n?", content))
        if len(imports) > 1 or len(annotations) > 1 or bool(imports) != bool(annotations):
            raise UpdateExecutionV3Error("MANAGED_SURFACE_CONFLICT：Java import 或 annotation 缺失、重复。")
        if remove:
            if imports:
                content = re.sub(rf"(?m)^\s*@{re.escape(simple)}\s*\n?", "", content)
                content = re.sub(rf"(?m)^\s*import\s+{re.escape(fqcn)}\s*;\s*\n?", "", content)
        elif not imports:
            import_end = list(re.finditer(r"(?m)^\s*import\s+.+;\s*$", content))
            offset = import_end[-1].end() if import_end else 0
            content = content[:offset] + ("\n" if offset else "") + f"import {fqcn};\n" + content[offset:]
            marker = "@SpringBootApplication"
            if marker not in content:
                raise UpdateExecutionV3Error("MANAGED_SURFACE_CONFLICT：缺少 @SpringBootApplication。")
            content = content.replace(marker, f"@{simple}\n{marker}", 1)
        entry.working = content.encode("utf-8")

    def _maven(self, operation: EnsureMavenDependencyOperationV3 | RemoveMavenDependencyOperationV3, store: WorkingCopyStoreV3, remove: bool) -> None:
        """按完整坐标、version 与 scope 处理 Maven Dependency。"""
        entry = store.entry(operation.target)
        if entry.working is None:
            raise UpdateExecutionV3Error("MANAGED_SURFACE_CONFLICT：pom.xml 不存在。")
        content = entry.working.decode("utf-8")
        spec = operation.parameters
        blocks = list(re.finditer(r"<dependency\b[^>]*>(?P<body>.*?)</dependency>", content, re.S))
        matches = [item for item in blocks if _tag(item.group("body"), "groupId") == spec.groupId and _tag(item.group("body"), "artifactId") == spec.artifactId]
        if len(matches) > 1:
            raise UpdateExecutionV3Error("MANAGED_SURFACE_CONFLICT：pom.xml 中存在重复 Maven 坐标。")
        if matches:
            match = matches[0]
            if (_tag(match.group("body"), "version"), _tag(match.group("body"), "scope")) != (spec.version, spec.scope):
                raise UpdateExecutionV3Error("MANAGED_SURFACE_CONFLICT：Maven 依赖版本或 scope 已被修改。")
            if remove:
                content = content[:match.start()] + content[match.end():]
        elif not remove:
            closing = content.find("</dependencies>")
            if closing < 0:
                raise UpdateExecutionV3Error("MANAGED_SURFACE_CONFLICT：pom.xml 缺少 dependencies。")
            optional = (f"\n      <version>{spec.version}</version>" if spec.version is not None else "") + (f"\n      <scope>{spec.scope}</scope>" if spec.scope is not None else "")
            block = f"\n    <dependency>\n      <groupId>{spec.groupId}</groupId>\n      <artifactId>{spec.artifactId}</artifactId>{optional}\n    </dependency>\n    "
            content = content[:closing] + block + content[closing:]
        entry.working = content.encode("utf-8")

def apply_working_copy_v3(workspace: str | Path, store: WorkingCopyStoreV3) -> None:
    """仅在全部 Operation 成功后提交；任一落盘失败则恢复 before-image。"""
    root = Path(workspace).expanduser().resolve()
    committed: list[WorkingCopyEntryV3] = []
    try:
        for entry in store.changed():
            path = _target(root, entry.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.v3.tmp")
            temporary.write_bytes(entry.working or b"")
            temporary.replace(path)
            committed.append(entry)
    except Exception:
        for entry in reversed(committed):
            path = _target(root, entry.path)
            if entry.original is None:
                path.unlink(missing_ok=True)
            else:
                temporary = path.with_name(f".{path.name}.v3.rollback")
                temporary.write_bytes(entry.original)
                temporary.replace(path)
        raise

def _target(root: Path, raw: str) -> Path:
    """解析并验证操作目标仍在 Workspace 内。"""
    parsed = PurePosixPath(raw)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise UpdateExecutionV3Error("UNSAFE_TARGET_PATH：Operation target 非法。")
    target = (root / Path(*parsed.parts)).resolve()
    if root not in target.parents:
        raise UpdateExecutionV3Error("UNSAFE_TARGET_PATH：Operation target 越界。")
    return target

def _tag(body: str, name: str) -> str | None:
    """读取一个 Maven dependency 子元素的文本值。"""
    match = re.search(rf"<\s*{name}\s*>(.*?)</\s*{name}\s*>", body, re.S)
    return match.group(1).strip() if match else None
