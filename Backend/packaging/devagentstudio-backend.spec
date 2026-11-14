# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from code_review_graph.parser import EXTENSION_TO_LANGUAGE

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


backend_root = Path(SPECPATH).parent.resolve()
grammar_profile = os.environ.get("DEVAGENTSTUDIO_BACKEND_GRAMMARS", "full")
if grammar_profile not in {"full", "builtin"}:
    raise ValueError(f"Unsupported backend grammar profile: {grammar_profile}")

# 精简包保留代码图默认语言，以及后端 AST 校验直接使用的 Java/TypeScript/TSX。
builtin_grammars = frozenset(EXTENSION_TO_LANGUAGE.values()) | {
    "java",
    "typescript",
    "tsx",
}


def include_grammar_module(module_name: str) -> bool:
    """只过滤语言包的语法绑定模块，保留包入口和默认语法。"""

    prefix = "tree_sitter_language_pack.bindings."
    return not module_name.startswith(prefix) or module_name[len(prefix):] in builtin_grammars


def include_grammar_binary(binary: tuple[str, str]) -> bool:
    """防止动态库收集路径重新带入精简包排除的语法。"""

    source, destination = binary
    return "bindings" not in Path(destination).parts or Path(source).name.split(".")[0] in builtin_grammars

datas = [
    # 标准开发预览进程托管官方 Mock，资源不进入目标应用生产构建。
    (str(backend_root / "app/services/agent_ui_managed_preview.mjs"), "app/services"),
    (str(backend_root / "app/services/agent_ui_preview_endpoint.cjs"), "app/services"),
    (str(backend_root / "app/services/agent_ui_preview_observer.cjs"), "app/services"),
    (str(backend_root / "app/services/agent_ui_preview_observer.tsx"), "app/services"),
    # 转入分析和目标 Rsbuild 资源适配共用同一受审阅编译边界。
    (
        str(backend_root / "app" / "services" / "agent_ui_rsbuild_loader.cjs"),
        "app/services",
    ),
    # T17 只解析模型源码的 AST 校验器，不执行候选或暴露新产品端点。
    (
        str(backend_root / "app" / "services" / "agent_ui_design_validate.mjs"),
        "app/services",
    ),
    # 设计预览只服务已构建静态快照，不依赖工具源码目录或 Vite 开发文件服务。
    (
        str(backend_root / "app" / "services" / "agent_ui_design_static.mjs"),
        "app/services",
    ),
    (
        str(backend_root / "app" / "services" / "agent_ui_design_mock.mjs"),
        "app/services",
    ),
    # 公开 Creator SDK 的 Node 控制包装不作为 Python 模块收集，显式随后端分发。
    (
        str(backend_root / "app" / "services" / "agent_ui_tool_bridge.mjs"),
        "app/services",
    ),
    (
        str(backend_root / "app" / "services" / "agent_ui_design_creator.mjs"),
        "app/services",
    ),
    (
        str(backend_root / "app" / "services" / "agent_ui_design_creator_source.mjs"),
        "app/services",
    ),
    # 埋点产品版本与 Electron 共用 package.json，避免维护第二份版本号。
    (
        str(backend_root.parent / "Frontend" / "package.json"),
        "app/product_metadata",
    ),
    (
        str(backend_root / "app" / "builtin_skills"),
        "app/builtin_skills",
    ),
    # 前端模板目录是唯一源码；后端选模板时仍需读取原始 TSX。
    (
        str(backend_root.parent / "Frontend" / "src" / "renderer" / "src" / "templates"),
        "app/page_templates",
    ),
]
binaries = []

for package_name in (
    "ag_ui",
    "langchain_core",
    "langgraph",
    "code_review_graph",
    "tree_sitter",
    "tree_sitter_language_pack",
):
    datas += collect_data_files(package_name)
    package_binaries = collect_dynamic_libs(package_name)
    if grammar_profile == "builtin" and package_name == "tree_sitter_language_pack":
        package_binaries = [binary for binary in package_binaries if include_grammar_binary(binary)]
    binaries += package_binaries

for distribution_name in (
    "ag-ui-protocol",
    "fastapi",
    "langchain-core",
    "langgraph",
    "langchain-openai",
    "openai",
    "pydantic",
    "PyYAML",
    "uvicorn",
    "code-review-graph",
    "tree-sitter",
    "tree-sitter-language-pack",
):
    datas += copy_metadata(distribution_name)

hiddenimports = []
for package_name in (
    "app",
    "ag_ui",
    "fastapi",
    "langchain_core",
    "langchain_openai",
    "langgraph",
    "openai",
    "pydantic",
    "starlette",
    "uvicorn",
    "yaml",
    "code_review_graph",
    "tree_sitter",
    "tree_sitter_language_pack",
):
    if grammar_profile == "builtin" and package_name == "tree_sitter_language_pack":
        hiddenimports += collect_submodules(package_name, filter=include_grammar_module)
    else:
        hiddenimports += collect_submodules(package_name)

hiddenimports += [
    "uvicorn.lifespan.on",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
]

a = Analysis(
    [str(backend_root / "packaging" / "backend_server.py")],
    pathex=[str(backend_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="devagentstudio-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="devagentstudio-backend",
)
