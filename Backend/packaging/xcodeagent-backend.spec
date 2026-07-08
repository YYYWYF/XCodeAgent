# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


backend_root = Path(SPECPATH).parent.resolve()

datas = [
    (
        str(backend_root / "app" / "builtin_skills" / "react-antd-v4-codegen"),
        "app/builtin_skills/react-antd-v4-codegen",
    ),
    (str(backend_root / "resources" / "docs" / "antd-v4"), "resources/docs/antd-v4"),
]

for package_name in ("ag_ui", "langchain_core", "langgraph"):
    datas += collect_data_files(package_name)

for distribution_name in (
    "ag-ui-protocol",
    "fastapi",
    "langchain-core",
    "langgraph",
    "langchain-openai",
    "openai",
    "pydantic",
    "uvicorn",
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
):
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
    binaries=[],
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
