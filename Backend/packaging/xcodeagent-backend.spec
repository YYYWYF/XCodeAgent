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
grammar_profile = os.environ.get("XCODEAGENT_BACKEND_GRAMMARS", "full")
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
    name="xcodeagent-backend",
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
    name="xcodeagent-backend",
)
