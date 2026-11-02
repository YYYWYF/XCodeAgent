"""为目标页面创建独立验收视图，不覆盖正式路由或占用普通 Preview。"""
from pathlib import Path
import json
import shutil

from app.services.page_identity import canonical_page_entry_path
from app.services.agent_runtime_health import allocate_runtime_port


def prepare_acceptance_frontend(root: Path, attempt_root: Path, page_id: str, identity: dict) -> Path:
    """有界复制当前前端源码并只替换临时入口，实际页面和正式 Runtime Adapter 保持原样。"""
    source, target = root / "frontend", attempt_root / "frontend"
    entry = canonical_page_entry_path(page_id).removeprefix("frontend/")
    if not (source / entry).is_file() or target.exists():
        raise ValueError("目标页面入口缺失或验收临时目录已存在，不能覆盖或重放。")
    count, size = 0, 0

    def copy_directory(directory: Path, destination: Path):
        """只复制普通开发资产，排除依赖、构建、凭据文件并限制整体复制规模。"""
        nonlocal count, size
        destination.mkdir(parents=True, exist_ok=False)
        for item in sorted(directory.iterdir()):
            if item.name in {"node_modules", ".git", "dist", "build", ".rsbuild", ".devagentstudio"} or item.name.startswith(".env"):
                continue
            if item.is_symlink():
                raise ValueError("页面验收源码不能包含符号链接。")
            count += 1
            if count > 6000:
                raise ValueError("页面验收源码超过文件数量上限。")
            if item.is_dir():
                copy_directory(item, destination / item.name)
            elif item.is_file():
                size += item.stat().st_size
                if size > 64 * 1024 * 1024:
                    raise ValueError("页面验收源码超过总字节上限。")
                shutil.copyfile(item, destination / item.name)
            else:
                raise ValueError("页面验收只接受普通源码文件。")

    copy_directory(source, target)
    dependencies = source / "node_modules"
    if not dependencies.is_dir():
        raise ValueError("页面验收需要已安装的前端依赖，不自动安装。")
    (target / "node_modules").symlink_to(dependencies.resolve(), target_is_directory=True)
    # 保留真实 bootstrap、AppProviders、明暗主题与样式，仅在副本中替换路由视图。
    # 直接裸渲染 Page 会丢失 Context，不能作为实际页面联调证据。
    entry_path, routes = target / "src/index.tsx", target / "src/routes/index.tsx"
    if not entry_path.is_file() or not routes.is_file():
        raise ValueError("临时验收视图缺少当前模板入口或 routes/index.tsx，不能猜测其他路由。")
    module = "@/" + entry.removeprefix("src/").removesuffix(".tsx")
    original = entry_path.read_text(encoding="utf-8")
    entry_path.write_text(f"Object.defineProperty(window, '__dasRuntimeAcceptance', {{value: Object.freeze({json.dumps(identity)})}});\n" + original,
        encoding="utf-8")
    routes.write_text("import React from 'react';\n" + f"import Page from {json.dumps(module)};\n"
        + "/** 在验收工程副本中显示唯一实际目标，保留原 App 的 Provider 和主题。 */\n"
        + "export const Routes: React.FC = () => <Page />;\n", encoding="utf-8")
    package = json.loads((target / "package.json").read_text())
    port = allocate_runtime_port()
    if "rsbuild" not in (package.get("scripts") or {}).get("dev", ""):
        raise ValueError("页面验收只接受当前 rsbuild 开发模板，不猜测其他启动方式。")
    package["scripts"]["dev"] = f"rsbuild dev --host 127.0.0.1 --port {port}"
    (target / "package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
