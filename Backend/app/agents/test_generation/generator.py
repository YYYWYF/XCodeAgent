"""单元测试生成 Agent 的提示、调用、快照和映射持久化。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.agents.test_generation.capabilities import (
    load_frontend_test_capabilities,
    unavailable_frontend_test_imports,
)
from app.utils.model_output import extract_json_object
from app.workspace.code_changes import capture_workspace_changes


MAX_TEST_FILES = 5
_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".java"}


def generate_or_update_unit_tests_with_agent(
    state: dict[str, Any],
    workspace: str | None,
    *,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> dict[str, Any]:
    """调用测试 Agent 并捕获实际测试文件差异；无目标时返回可放行跳过结果。"""

    context = state.get("unit_test_generation_context")
    context = context if isinstance(context, dict) else {}
    source_files = _string_list(context.get("source_files"), limit=100)
    if not source_files or not workspace:
        return _skipped_result("没有可测试源码变更或工作区不可用。")
    cached = _cached_test_result(workspace, source_files)
    if cached is not None:
        return cached
    try:
        from app.agents import create_agent_bundle

        prompt_context = dict(context)
        if "frontend" in _layers_for_sources(source_files):
            prompt_context["frontend_test_capabilities"] = (
                load_frontend_test_capabilities(workspace)
            )
        prompt = _build_prompt(prompt_context)

        def invoke() -> str:
            """执行一次无命令权限的测试 Agent 调用。"""

            return invoke_agent_with_tool_activity(
                create_agent_bundle(workspace, selected_skill_names).test_generation,
                {"messages": [{"role": "user", "content": prompt}]},
                workspace=workspace,
                on_tool_activity=on_tool_activity,
            )

        security_before = _workspace_security_snapshot(workspace)
        internal_before = _internal_artifact_snapshot(workspace)
        captured = capture_workspace_changes(
            workspace=workspace,
            source_tool="test_generation.deep_agent",
            action=invoke,
            capture_exceptions=True,
        )
        internal_after = _internal_artifact_snapshot(workspace)
        security_after = _workspace_security_snapshot(workspace)
    except Exception as exc:
        summary = (
            f"TestGeneration Agent 初始化异常，按零测试文件放行："
            f"{type(exc).__name__}: {exc}"
        )
        existing = _existing_related_tests(workspace, source_files)
        result = _skipped_result(summary)
        if existing:
            result.update(
                {
                    "status": "completed",
                    "affected_layers": _layers_for_sources(source_files),
                    "test_files": existing,
                    "validation": _validate_test_files(
                        workspace,
                        existing,
                        [],
                        source_files=source_files,
                    ),
                    "mapping_path": str(
                        Path(workspace).expanduser().resolve()
                        / ".xcodeagent"
                        / "cache"
                        / "unit-test-mappings.json"
                    ),
                }
            )
        return result

    change_set = captured.code_change_set
    changed_files = _changed_paths(change_set)
    added_files = _changed_paths(change_set, change_type="added")
    if not added_files:
        added_files = [path for path in changed_files if path not in security_before]
    unauthorized = [path for path in changed_files if not _allowed_test_path(path)]
    unauthorized.extend(
        path
        for path in sorted(set(internal_before) | set(internal_after))
        if internal_before.get(path) != internal_after.get(path)
    )
    unauthorized.extend(
        path
        for path in sorted(set(security_before) | set(security_after))
        if security_before.get(path) != security_after.get(path)
        and not _allowed_test_path(path)
    )
    unauthorized = list(dict.fromkeys(unauthorized))
    if unauthorized:
        return {
            "status": "failed",
            "summary": "检测到测试目录外的实际工作区变化。",
            "affected_layers": _layers_for_sources(source_files),
            "test_files": [],
            "warnings": unauthorized,
            "validation": {"unauthorized_paths": unauthorized},
            "code_change_sets": [change_set] if change_set else [],
            "mapping_path": None,
        }
    payload = extract_json_object(captured.value if isinstance(captured.value, str) else "") or {}
    declared_test_files = [
        str(value).strip().replace("\\", "/").lstrip("/")
        for value in _string_list(payload.get("test_files"), limit=MAX_TEST_FILES)
    ]
    declared_invalid_paths = [
        path for path in declared_test_files if not _allowed_test_path(path)
    ]
    test_files = _normalize_test_files(
        [*declared_test_files, *changed_files]
    )
    source_layers = set(_layers_for_sources(source_files))
    if source_layers:
        # 输出中的跨端路径不作为本轮对应测试；实际写入仍由下面的校验判为失败。
        test_files = [
            path
            for path in test_files
            if _test_path_layer(path) in source_layers
        ]
    validation = _validate_test_files(
        workspace,
        test_files,
        changed_files,
        new_files=added_files,
        source_files=source_files,
    )
    unrelated_layer_paths = [
        path
        for path in changed_files
        if (layer := _test_path_layer(path)) and layer not in source_layers
    ]
    if unrelated_layer_paths:
        validation["unaffected_layer_paths"] = list(dict.fromkeys(unrelated_layer_paths))
    if not test_files and not any(
        validation.get(key)
        for key in (
            "invalid_paths",
            "invalid_contents",
            "missing_files",
            "too_many_files",
            "unaffected_layer_paths",
        )
    ):
        test_files = _existing_related_tests(workspace, source_files)
        if test_files:
            warnings = ["发现已有对应测试文件；本轮未新增文件，仍执行该测试套件。"]
            validation = _validate_test_files(
                workspace,
                test_files,
                changed_files,
                new_files=added_files,
                source_files=source_files,
            )
        else:
            warnings = ["TestGeneration Agent 未生成测试文件，按零测试文件继续。"]
    else:
        warnings = []
    warnings.extend(
        f"忽略不在测试目录内或不符合扩展名约定的 Agent 输出路径：{path}"
        for path in declared_invalid_paths
    )
    if len(
        [path for path in changed_files if _allowed_test_path(path)]
    ) > MAX_TEST_FILES:
        validation["too_many_files"] = [
            path for path in changed_files if _allowed_test_path(path)
        ][MAX_TEST_FILES:]
    if any(
        validation.get(key)
        for key in (
            "invalid_paths",
            "invalid_contents",
            "missing_files",
            "too_many_files",
            "unaffected_layer_paths",
        )
    ):
        status = "failed"
    else:
        status = "completed" if test_files else "skipped"
    mapping_path = _persist_mapping(
        workspace,
        source_files=source_files,
        test_files=test_files,
        behaviors=_string_list(payload.get("behaviors"), limit=20),
    )
    warnings = [*_string_list(payload.get("warnings"), limit=20), *warnings]
    if captured.error:
        warnings.append(f"Agent 异常：{type(captured.error).__name__}: {captured.error}")
    summary = str(
        payload.get("summary")
        or (
            f"TestGeneration Agent 执行异常：{type(captured.error).__name__}"
            if captured.error
            else "已生成或更新测试文件。"
            if test_files
            else "没有生成测试文件。"
        )
    )[:2_000]
    return {
        "status": status,
        "summary": summary,
        "affected_layers": _layers_for_sources(source_files),
        "test_files": test_files[:MAX_TEST_FILES],
        "warnings": warnings,
        "validation": validation,
        "code_change_sets": [change_set] if change_set else [],
        "mapping_path": mapping_path,
    }


def _build_prompt(context: dict[str, Any]) -> str:
    """构造有界测试生成上下文，避免注入完整仓库和会话历史。"""

    references = {
        "source_files": context.get("source_files", [])[:100],
        "code_diff": context.get("code_diff", {}),
        "existing_test_files": context.get("existing_test_files", [])[:5],
        "build_task_plan_path": context.get("build_task_plan_path"),
        "technical_plan_json_path": context.get("technical_plan_json_path"),
        "build_execution_scope": context.get("build_execution_scope", {}),
        "build_execution_slice": context.get("build_execution_slice", {}),
        "frontend_test_capabilities": context.get(
            "frontend_test_capabilities", {}
        ),
    }
    return (
        "Generate or update unit tests for this bounded change. Read inputs in this order: "
        "the frozen Build code diff; changed source files and existing related tests; the "
        "confirmed Build task plan narrowed by the execution scope and slice; the current "
        "TechnicalPlan JSON; then only direct source dependencies needed for the test. "
        "Existing tests must be updated in place when they cover the changed source. Do not "
        "create more than five test files. Frontend test filenames only need to remain flat "
        "under frontend/tests and end in .test.ts or .test.tsx; do not require a separator "
        "such as a hyphen. Before writing a frontend test, use only third-party packages "
        "listed in frontend_test_capabilities.available_packages. Never import a package "
        "that is absent from that list, never modify package.json or a lockfile, and never "
        "request installation of a new npm package. If a preferred interaction helper is "
        "unavailable, use capabilities already declared by the project or a native DOM API.\n\n"
        "Do not generate tests for backend mapping-only classes such as *Assembler, "
        "*Converter or *Mapper, including MapStruct implementations; also exclude DTO, "
        "entity, configuration and getter/setter-only classes. Prefer Service tests and "
        "route-contract tests only when their behavior changed. Every Java unit test, "
        "including a Controller test, may use only test libraries already available from "
        "the current backend build and existing tests. Never add or request a build "
        "dependency. When JUnit 5 and Mockito are available, use them without loading a "
        "Spring application context. Use @ExtendWith(MockitoExtension.class), Mockito @Mock and "
        "@InjectMocks. If a changed HTTP contract requires MockMvc and Spring Test is "
        "already available, use "
        "MockMvcBuilders.standaloneSetup. Never use or import @WebMvcTest, @SpringBootTest, "
        "@MockBean, @Autowired or SpringExtension. If no compatible backend unit-test "
        "capability exists, return a skipped result instead of modifying dependencies.\n\n"
        f"TestGenerationContext:\n{json.dumps(references, ensure_ascii=False, indent=2, default=str)[:28_000]}"
    )


def _validate_test_files(
    workspace: str,
    test_files: list[str],
    changed_files: list[str],
    *,
    new_files: list[str] | None = None,
    source_files: list[str] | None = None,
) -> dict[str, Any]:
    """确定性验证测试路径边界、可执行后缀和最小用例结构。"""

    root = Path(workspace).expanduser().resolve()
    invalid_paths: list[str] = []
    invalid_contents: list[str] = []
    unavailable_imports: dict[str, list[str]] = {}
    source_files = source_files or []
    frontend_capabilities = load_frontend_test_capabilities(workspace)
    for relative in test_files[:MAX_TEST_FILES]:
        path = root / relative
        normalized = relative.casefold()
        if not _is_workspace_relative_path(root, relative):
            invalid_paths.append(relative)
            continue
        if normalized.startswith("frontend/tests/"):
            name = Path(relative).name
            if "/" in relative[len("frontend/tests/") :] or not (
                name.endswith(".test.ts") or name.endswith(".test.tsx")
            ):
                invalid_paths.append(relative)
        elif normalized.startswith("backend/src/test/java/"):
            if not relative.endswith("Test.java"):
                invalid_paths.append(relative)
        else:
            invalid_paths.append(relative)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            invalid_contents.append(relative)
            continue
        if normalized.endswith((".test.ts", ".test.tsx")):
            if not any(
                token in content
                for token in ("test(", "test.each(", "it(", "it.each(")
            ):
                invalid_contents.append(relative)
            if any(
                token in content
                for token in (
                    "test.skip",
                    "test.todo",
                    "test.only",
                    "it.skip",
                    "it.todo",
                    "it.only",
                    "describe.skip",
                    "describe.todo",
                    "describe.only",
                    "toHaveStyle",
                    "getComputedStyle",
                    "toMatchSnapshot",
                    "toMatchInlineSnapshot",
                )
            ):
                invalid_contents.append(relative)
            unavailable = unavailable_frontend_test_imports(
                content,
                frontend_capabilities,
            )
            if unavailable:
                unavailable_imports[relative] = unavailable
                invalid_contents.append(relative)
        elif normalized.endswith("test.java"):
            if "@Test" not in content or "@Disabled" in content or "@Ignore" in content:
                invalid_contents.append(relative)
            package_match = re.search(
                r"\bpackage\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;",
                content,
            )
            package_path = (
                relative.split("/src/test/java/", 1)[-1]
                .rsplit("/", 1)[0]
                .replace("/", ".")
            )
            if not package_match or package_match.group(1) != package_path:
                invalid_contents.append(relative)
        if source_files and not _test_references_source(content, relative, source_files):
            invalid_contents.append(relative)
    missing_files = [
        relative
        for relative in test_files[:MAX_TEST_FILES]
        if not (root / relative).is_file()
    ]
    return {
        "valid": not invalid_paths and not invalid_contents and not missing_files,
        "invalid_paths": list(dict.fromkeys(invalid_paths)),
        "invalid_contents": list(dict.fromkeys(invalid_contents)),
        "unavailable_imports": unavailable_imports,
        "missing_files": missing_files,
        "changed_files": changed_files[:MAX_TEST_FILES],
    }


def _persist_mapping(
    workspace: str,
    *,
    source_files: list[str],
    test_files: list[str],
    behaviors: list[str],
) -> str | None:
    """原子写入可重建的 source→test hash 映射，不保存源码正文。"""

    try:
        root = Path(workspace).expanduser().resolve()
        path = root / ".xcodeagent" / "cache" / "unit-test-mappings.json"
        existing = _read_mapping(path)
        entries = [
            item
            for item in existing.get("entries", [])
            if isinstance(item, dict)
        ]
        for source in source_files:
            source_path = root / source
            if not source_path.is_file() or source_path.suffix.casefold() not in _SOURCE_SUFFIXES:
                continue
            source_hash = _sha256(source_path)
            related = [test for test in test_files if _same_feature(source, test)]
            if not related:
                related = test_files[:1]
            for test in related:
                test_path = root / test
                entries.append(
                    {
                        "layer": "backend" if "/src/test/java/" in test.casefold() else "frontend",
                        "moduleType": _module_type(source, test),
                        "featureId": _feature_id(source, test),
                        "sourcePaths": [source],
                        "sourceHashes": {source: source_hash},
                        "testPath": test,
                        "testHash": _sha256(test_path) if test_path.is_file() else "",
                        "behaviors": behaviors[:20],
                    }
                )
        # 同一 source/test 组合原地更新，保留其他功能的可重建映射。
        deduped: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in entries:
            key = (
                str(entry.get("sourcePaths", [""])[0] if entry.get("sourcePaths") else ""),
                str(entry.get("testPath") or ""),
            )
            if key != ("", ""):
                deduped[key] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(
                {"version": "1.0", "entries": list(deduped.values())},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temp.replace(path)
        return str(path)
    except OSError:
        return None


def _same_feature(source: str, test: str) -> bool:
    """使用规范化 token 判断源码和测试是否属于同一功能。"""

    source_tokens = set(_tokens(Path(source).stem))
    test_tokens = set(_tokens(Path(test).stem.replace(".test", "")))
    return bool(source_tokens.intersection(test_tokens))


def _test_references_source(
    content: str,
    test_path: str,
    source_files: list[str],
) -> bool:
    """确认测试正文或稳定文件名至少关联一个本轮源码目标。"""

    lowered_content = content.casefold()
    for source in source_files:
        source_path = Path(source)
        source_stem = source_path.stem
        tokens = set(_tokens(source_stem))
        if source_stem.casefold() in {"index", "main", "app"}:
            tokens.update(_tokens(source_path.parent.name))
        if any(token and token in lowered_content for token in tokens):
            return True
        if _same_feature(source, test_path):
            # 规范命名本身是稳定映射事实；避免要求测试把实现细节硬编码
            # 到描述文本中，import/FQCN 的更严格关系由 Agent 读取源码确认。
            return True
    return False


def _module_type(source: str, test: str) -> str:
    """从测试命名和源码类名提取稳定模块类型，供映射缓存展示。"""

    if test.casefold().startswith("frontend/tests/"):
        prefix = Path(test).name.split("-", 1)[0].casefold()
        return prefix or "module"
    source_name = Path(source).stem.casefold()
    if source_name.endswith("service"):
        return "service"
    if source_name.endswith(("controller", "resource")):
        return "controller"
    if source_name.endswith(("validator", "mapper")):
        return "utility"
    return "module"


def _feature_id(source: str, test: str) -> str:
    """从稳定文件名提取功能标识，不把源码正文写入缓存。"""

    if test.casefold().startswith("frontend/tests/"):
        stem = Path(test).name.rsplit(".test.", 1)[0]
        return stem.split("-", 1)[-1] or stem
    return Path(source).stem.removesuffix("Test")


def _tokens(value: str) -> list[str]:
    """把 camelCase、下划线和连字符统一为功能 token。"""

    normalized = value.replace("_", "-").replace(".", "-")
    result: list[str] = []
    current = ""
    for char in normalized:
        if char.isupper() and current:
            result.append(current.casefold())
            current = char
        elif char.isalnum():
            current += char
        elif current:
            result.append(current.casefold())
            current = ""
    if current:
        result.append(current.casefold())
    return result


def _sha256(path: Path) -> str:
    """读取文件并返回稳定摘要。"""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _internal_artifact_snapshot(workspace: str) -> dict[str, str]:
    """快照 `.xcodeagent` 正式工件，并忽略 LangGraph 自有 checkpoint 写入。"""

    root = Path(workspace).expanduser().resolve() / ".xcodeagent"
    snapshot: dict[str, str] = {}
    if not root.is_dir():
        return snapshot
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name != "checkpoints")
        for filename in filenames:
            path = Path(dirpath) / filename
            try:
                snapshot[path.relative_to(root.parent).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            except OSError:
                continue
    return snapshot


def _workspace_security_snapshot(workspace: str) -> dict[str, str]:
    """快照普通与敏感配置文件；内部工件由专用快照负责。"""

    root = Path(workspace).expanduser().resolve()
    snapshot: dict[str, str] = {}
    # 构建工具可能与测试生成并行刷新这些目录；它们是可重建产物，
    # 不应被误判为 TestGeneration Agent 写入了生产代码。
    ignored_dirs = {
        ".git",
        ".xcodeagent",
        ".venv",
        "node_modules",
        "target",
        "build",
        "dist",
    }
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in ignored_dirs and not (Path(dirpath) / name).is_symlink()
        )
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.is_symlink() or not path.is_file():
                continue
            try:
                snapshot[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            except OSError:
                continue
    return snapshot


def _cached_test_result(
    workspace: str,
    source_files: list[str],
) -> dict[str, Any] | None:
    """源码摘要和缓存一致时复用已有测试映射，避免重复调用生成 Agent。"""

    root = Path(workspace).expanduser().resolve()
    path = root / ".xcodeagent" / "cache" / "unit-test-mappings.json"
    mapping = _read_mapping(path)
    entries = [item for item in mapping.get("entries", []) if isinstance(item, dict)]
    if not entries:
        return None
    mapped: list[str] = []
    for source in source_files:
        source_path = root / source
        if not source_path.is_file():
            return None
        source_hash = _sha256(source_path)
        matches = [
            entry
            for entry in entries
            if source in _string_list(entry.get("sourcePaths"), limit=20)
            and _source_hash_from_entry(entry, source) == source_hash
        ]
        test_paths = [
            test
            for entry in matches
            for test in _normalize_test_files([str(entry.get("testPath") or "")])
            if (root / test).is_file()
        ]
        if not test_paths:
            return None
        mapped.extend(test_paths)
    mapped = list(dict.fromkeys(mapped))[:MAX_TEST_FILES]
    if not mapped:
        return None
    validation = _validate_test_files(
        workspace,
        mapped,
        [],
        source_files=source_files,
    )
    if not validation.get("valid"):
        # 依赖能力或校验规则变化后，旧映射不能阻止 Agent 重新生成测试。
        return None
    return {
        "status": "completed",
        "summary": "源码摘要未变化，复用已关联单元测试文件。",
        "affected_layers": _layers_for_sources(source_files),
        "test_files": mapped,
        "warnings": [],
        "validation": {"valid": True, "mapping_cache": "hit"},
        "code_change_sets": [],
        "mapping_path": str(path),
    }


def _existing_related_tests(
    workspace: str,
    source_files: list[str],
) -> list[str]:
    """在生成 Agent 无输出时从映射和测试引用中恢复已有对应测试。"""

    root = Path(workspace).expanduser().resolve()
    mapping_path = root / ".xcodeagent" / "cache" / "unit-test-mappings.json"
    mapping = _read_mapping(mapping_path)
    candidates = [
        str(entry.get("testPath") or "")
        for entry in mapping.get("entries", [])
        if isinstance(entry, dict)
        for source in source_files
        if source in _string_list(entry.get("sourcePaths"), limit=20)
    ]
    if not candidates:
        for source in source_files:
            source_path = root / source
            if not source_path.is_file():
                continue
            source_lower = source.casefold()
            source_name = source_path.stem.casefold()
            source_tokens = set(_tokens(source_path.stem))
            if source_path.stem.casefold() in {"index", "main"}:
                source_tokens.update(_tokens(source_path.parent.name))
            for test_path in _normalize_test_files(_workspace_test_files(root)):
                test_file = root / test_path
                content = test_file.read_text(encoding="utf-8", errors="ignore").casefold()
                test_tokens = set(_tokens(Path(test_path).stem.replace(".test", "")))
                if (
                    source_lower.removeprefix("frontend/") in content
                    or (
                        source_name not in {"index", "main", "app"}
                        and source_name in content
                    )
                    or source_path.name.casefold() in content
                    or source_tokens.intersection(test_tokens)
                ):
                    candidates.append(test_path)
    return list(
        dict.fromkeys(
            path
            for path in _normalize_test_files(candidates)
            if (root / path).is_file()
        )
    )[:MAX_TEST_FILES]


def _workspace_test_files(root: Path) -> list[str]:
    """列出两个项目约定目录中的候选单元测试文件。"""

    values = [
        *(path.relative_to(root).as_posix() for path in (root / "frontend" / "tests").glob("*.test.ts") if path.is_file()),
        *(path.relative_to(root).as_posix() for path in (root / "frontend" / "tests").glob("*.test.tsx") if path.is_file()),
        *(path.relative_to(root).as_posix() for path in (root / "Frontend" / "tests").glob("*.test.ts") if path.is_file()),
        *(path.relative_to(root).as_posix() for path in (root / "Frontend" / "tests").glob("*.test.tsx") if path.is_file()),
        *(path.relative_to(root).as_posix() for path in (root / "backend" / "src" / "test" / "java").rglob("*Test.java") if path.is_file()),
        *(path.relative_to(root).as_posix() for path in (root / "Backend" / "src" / "test" / "java").rglob("*Test.java") if path.is_file()),
    ]
    return list(dict.fromkeys(values))


def _read_mapping(path: Path) -> dict[str, Any]:
    """读取可重建映射；缺失或损坏时返回空缓存。"""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _source_hash_from_entry(entry: dict[str, Any], source: str) -> str:
    """安全读取单条映射的源码摘要。"""

    hashes = entry.get("sourceHashes")
    return str(hashes.get(source) or "") if isinstance(hashes, dict) else ""


def _changed_paths(
    change_set: dict[str, Any] | None,
    *,
    change_type: str | None = None,
) -> list[str]:
    """提取快照捕获的全部相对路径，以便检测测试目录外的实际写入。"""

    result: list[str] = []
    for item in (change_set or {}).get("files", []):
        if not isinstance(item, dict):
            continue
        if change_type and str(item.get("changeType") or "") != change_type:
            continue
        path = str(item.get("path") or "").strip().replace("\\", "/").lstrip("/")
        if path and path not in result:
            result.append(path)
    return result


def _normalize_test_files(values: list[str]) -> list[str]:
    """保留测试目录中的相对路径并去重。"""

    result: list[str] = []
    for value in values:
        path = str(value or "").strip().replace("\\", "/").lstrip("/")
        if (
            path
            and ".." not in Path(path).parts
            and path not in result
            and _allowed_test_path(path)
        ):
            result.append(path)
    return result[:MAX_TEST_FILES]


def _allowed_test_path(path: str) -> bool:
    """判断路径是否属于允许的前后端测试目录。"""

    normalized = path.replace("\\", "/").lstrip("/")
    if ".." in Path(normalized).parts:
        return False
    lower = normalized.casefold()
    if lower.startswith("frontend/tests/"):
        return "/" not in normalized[len("frontend/tests/") :] and lower.endswith(
            (".test.ts", ".test.tsx")
        )
    return lower.startswith("backend/src/test/java/") and lower.endswith("test.java")


def _test_path_layer(path: str) -> str | None:
    """根据测试约定目录返回测试文件所属端。"""

    normalized = path.replace("\\", "/").lstrip("/").casefold()
    if normalized.startswith("frontend/tests/") and normalized.endswith(
        (".test.ts", ".test.tsx")
    ):
        return "frontend"
    if normalized.startswith("backend/src/test/java/") and normalized.endswith(
        "test.java"
    ):
        return "backend"
    return None


def _is_workspace_relative_path(root: Path, relative: str) -> bool:
    """确认测试路径解析后仍位于当前工作区，阻止路径穿越。"""

    try:
        (root / relative).resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _layers_for_sources(paths: list[str]) -> list[str]:
    """从源码路径推导受影响端。"""

    layers: list[str] = []
    for path in paths:
        lower = path.casefold()
        layer = "frontend" if lower.startswith("frontend/") else "backend" if lower.startswith("backend/") else ""
        if layer and layer not in layers:
            layers.append(layer)
    return layers


def _string_list(value: Any, *, limit: int) -> list[str]:
    """裁剪不可信 Agent 字符串数组。"""

    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip()[:1_000] for item in value if str(item).strip()))[:limit]


def _skipped_result(summary: str) -> dict[str, Any]:
    """构造零测试文件时的可放行结果。"""

    return {
        "status": "skipped",
        "summary": summary[:2_000],
        "affected_layers": [],
        "test_files": [],
        "warnings": [summary[:2_000]],
        "validation": {},
        "code_change_sets": [],
        "mapping_path": None,
    }
