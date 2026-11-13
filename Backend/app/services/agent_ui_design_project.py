"""T16 隔离设计生产者；只准备草稿工程，不确认设计或创建正式业务工程。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.agent_ui_tool_host import AgentUiToolHostInspection, inspect_agent_ui_tool_host
from app.services.agent_ui_tool_environment import resolve_node_executable
from app.services.workspace_process_registry import workspace_process_registry


REPOSITORY = Path(__file__).resolve().parents[3]
SEED_FILES = ("package.json", "pnpm-lock.yaml", ".pnpmfile.cjs", "tsconfig.json", "eslint.config.mjs")
_PREPARE_LOCK = threading.Lock()
_PLACEHOLDER = '''import type { ReactElement } from 'react'

/** 仅表示隔离环境就绪，实际产品设计由后续生成器写入。 */
export function App(): ReactElement {
  return <main><p>设计环境已就绪，等待生成智能体 UI 设计稿。</p></main>
}
'''


@dataclass(frozen=True)
class AgentUiDesignProject:
    """绑定当前工作区、实际源码及构建摘要，不是应用配置或确认记录。"""

    workspace: Path
    root: Path
    project_dir: Path
    identity: str
    source_digest: str
    build_digest: str


def design_project_root(workspace: Path, draft_root: Path) -> Path:
    """沿现有工作区资产路径接受草稿根，拒绝越界和符号链接。"""
    workspace = workspace.absolute()
    if not workspace.is_dir() or not draft_root.is_absolute():
        raise ValueError("工作区和草稿必须使用有效的原生绝对路径。")
    assets = workspace / WORKSPACE_ARTIFACT_DIR
    draft = draft_root.absolute()
    try:
        relative = draft.relative_to(assets)
    except ValueError:
        raise ValueError("设计草稿必须位于当前工作区资产目录。") from None
    current = assets
    for part in (*relative.parts, "agent-ui-project"):
        if current.is_symlink() or part in {"..", "."}:
            raise ValueError("设计草稿路径不能包含符号链接或路径穿越。")
        current = current / part
    if current.is_symlink() or not current.resolve().is_relative_to(assets.resolve()):
        raise ValueError("设计草稿路径越界。")
    return current


def tree_digest(root: Path) -> str:
    """摘要绑定目录中全部当前文件和相对路径，拒绝符号链接。"""
    if not root.is_dir() or root.is_symlink():
        raise ValueError("设计资产目录不存在或为符号链接。")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().encode()):
        if path.is_symlink():
            raise ValueError("设计资产不能包含符号链接。")
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def source_digest(project: Path) -> str:
    """将运行源码与构建配置、实际锁文件一起纳入缓存身份。"""
    digest = hashlib.sha256(tree_digest(project / "src").encode())
    for name in (*SEED_FILES, "vite.config.ts", "index.html"):
        path = project / name
        if path.is_symlink():
            raise ValueError("设计配置不能链接到外部文件。")
        digest.update(name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def command_environment() -> dict[str, str]:
    """构建和预览使用统一 Node 的 PATH，仅继承必要系统环境，不注入模型凭据。"""
    allowed = {"PATH", "HOME", "USERPROFILE", "SystemRoot", "SYSTEMROOT", "WINDIR",
               "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL"}
    environment = {**{key: value for key, value in os.environ.items() if key in allowed}, "CI": "true"}
    # 包管理器及生成脚本的子进程也必须使用同一个 Node，不能沿旧 PATH 切回其他版本。
    node_directory = str(resolve_node_executable().parent)
    environment["PATH"] = os.pathsep.join(filter(None, (node_directory, environment.get("PATH", ""))))
    return environment


def _run(argv: list[str], workspace: Path, cwd: Path, log: Path, run_id: str) -> None:
    """通过工作区进程 owner 执行无 shell 命令，失败保留诊断而不回显输出。"""
    with log.open("w", encoding="utf-8") as output:
        completed = workspace_process_registry.run(
            argv, workspace=workspace, run_id=run_id, cwd=cwd, env=command_environment(),
            stdout=output, stderr=output, timeout=180, check=False,
        )
    if completed.returncode != 0:
        raise ValueError(f"隔离设计工程命令失败，请查看 {log.name}。")


def validate_design_project(project: AgentUiDesignProject) -> None:
    """启动前复核当前源码与产物，拒绝缺文件或修改后沿用旧构建。"""
    if (design_project_root(project.workspace, project.root.parent) != project.root
            or project.project_dir != project.root / "tools/agent-ui-consumer"):
        raise ValueError("设计工程与当前工作区身份不一致。")
    for path in (project.root / "tools", project.project_dir):
        if path.is_symlink():
            raise ValueError("设计工程不能链接到其他目录。")
    if source_digest(project.project_dir) != project.source_digest:
        raise ValueError("当前设计源码已变化，需要重新构建后再预览。")
    if tree_digest(project.project_dir / "dist") != project.build_digest:
        raise ValueError("当前设计构建文件缺失或已变化。")
    generation = project.root / "generation.json"
    if generation.exists():
        if generation.is_symlink():
            raise ValueError("设计生成记录不能为符号链接。")
        evidence = json.loads(generation.read_text(encoding="utf-8"))
        if (evidence.get("status") != "built"
                or evidence.get("sourceDigest") != project.source_digest
                or evidence.get("buildDigest") != project.build_digest):
            raise ValueError("本轮设计未构建成功，不能预览旧稿。")


def build_agent_ui_design_project(
    project: AgentUiDesignProject, *, node_executable: Path, pnpm_cli: Path, run_id: str = "",
    check_cancel: Callable[[], None] | None = None,
) -> AgentUiDesignProject:
    """验证生成后的完整源码；不更新缓存或确认状态，由调用方提交本轮生成结果。"""
    before = source_digest(project.project_dir)
    for action in ("lint", "typecheck", "build"):
        if check_cancel:
            check_cancel()
        # 初始化 SDK 的源码保持原样；本轮全部业务文件和 Host 均必须经过 lint。
        arguments = ["exec", "eslint", "src", "vite.config.ts"] if action == "lint" else [action]
        _run([str(node_executable), str(pnpm_cli), *arguments], project.workspace, project.project_dir,
             project.root / "logs" / f"generated-{action}.log", run_id)
    if check_cancel:
        check_cancel()
    if source_digest(project.project_dir) != before:
        raise ValueError("验证期间设计源码发生变化，不能登记本次构建。")
    return replace(project, source_digest=before, build_digest=tree_digest(project.project_dir / "dist"))


def _check_installed_packages(project: Path, manifest: dict[str, object]) -> None:
    """复验四个目标运行包的公开身份和安装边界，不把目录存在当安装成功。"""
    modules = project / "node_modules"
    if modules.is_symlink() or not modules.is_dir():
        raise ValueError("设计工程必须使用独立 node_modules。")
    records = manifest["packages"]
    if not isinstance(records, list):
        raise ValueError("成品包清单无效。")
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("成品包清单无效。")
        name = str(item["name"])
        if name.rsplit("/", 1)[-1] in {"agent-ui-creator", "agent-ui-web-component"}:
            continue
        metadata_path = (modules / name / "package.json").resolve(strict=True)
        if not metadata_path.is_relative_to(modules):
            raise ValueError("运行包不能链接到独立设计工程之外。")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("name") != name or metadata.get("version") != item["version"]:
            raise ValueError("实际安装的运行包身份不匹配。")
    if (modules / "@za38.03/agent-ui-creator").exists():
        raise ValueError("Creator 工具不能安装到设计目标工程。")


def prepare_agent_ui_design_project(
    inspection: AgentUiToolHostInspection, *, draft_root: Path, pnpm_cli: Path,
    offline: bool = False, run_id: str = "",
) -> AgentUiDesignProject:
    """从已授权本地成品包准备独立工程；已有源码只在完整摘要一致时复用。"""
    # 只串行工程准备；不把安装锁扩展到普通会话、产品执行或预览生命周期。
    with _PREPARE_LOCK:
        return _prepare(inspection, draft_root, pnpm_cli, offline, run_id)


def _prepare(
    inspection: AgentUiToolHostInspection, draft_root: Path, pnpm_cli: Path,
    offline: bool, run_id: str,
) -> AgentUiDesignProject:
    """重新检查 Node/工具身份，复制白名单输入并执行真实安装、SDK 初始化与构建。"""
    current = inspect_agent_ui_tool_host(
        workspace=inspection.workspace, installation_root=inspection.installation_root,
        node_executable=inspection.node_executable,
        creator_version=inspection.creator_version, run_id=run_id,
    )
    if current != inspection:
        raise ValueError("工具身份已变化，请重新检查设计环境。")
    cli = pnpm_cli.resolve(strict=True)
    if cli.suffix not in {".js", ".cjs", ".mjs"}:
        raise ValueError("请提供 pnpm JavaScript CLI 原生路径，不能传入 shell 或 cmd 包装。")
    root = design_project_root(current.workspace, draft_root)
    seed = REPOSITORY / "tools/agent-ui-consumer"
    vendor = REPOSITORY / "vendor/agent-ui"
    manifest = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["packages"]:
        artifact = vendor / item["file"]
        if artifact.name != item["file"] or artifact.is_symlink():
            raise ValueError("本地成品包路径无效。")
        data = artifact.read_bytes()
        if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("本地成品包摘要不匹配。")
    fingerprint = hashlib.sha256(current.package_json_sha256.encode())
    fingerprint.update(current.node_version.encode())
    fingerprint.update(cli.read_bytes())
    fingerprint.update(tree_digest(vendor).encode())
    initializer = (REPOSITORY / "tools/agent-ui/prepare-consumer.mjs").read_text(encoding="utf-8")
    fingerprint.update(initializer.encode())
    for name in (*SEED_FILES, "vite.config.ts", "src/main.tsx", "src/styles.css"):
        fingerprint.update((seed / name).read_bytes())
    # 本生产者代码改变时旧缓存也不再被当作已验证环境。
    fingerprint.update(Path(__file__).read_bytes())
    identity = fingerprint.hexdigest()
    project = root / "tools/agent-ui-consumer"
    record = root / "environment.json"
    if root.exists():
        try:
            evidence = json.loads(record.read_text(encoding="utf-8"))
            result = AgentUiDesignProject(current.workspace, root, project, identity,
                                         evidence["sourceDigest"], evidence["buildDigest"])
            if evidence["identity"] != identity:
                raise ValueError("设计环境身份或依赖已变化。")
            _check_installed_packages(project, manifest)
            validate_design_project(result)
            return result
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("已有设计工程未通过当前身份/摘要检查；保留文件，请使用新的草稿目录。") from exc
    project.mkdir(parents=True)
    (root / "logs").mkdir()
    shutil.copytree(vendor, root / "vendor/agent-ui")
    for name in SEED_FILES:
        shutil.copyfile(seed / name, project / name)
    (project / "src").mkdir()
    for name in ("main.tsx", "styles.css"):
        shutil.copyfile(seed / "src" / name, project / "src" / name)
    (project / "src/App.tsx").write_text(_PLACEHOLDER, encoding="utf-8")
    (project / "index.html").write_text(
        '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8" />'
        '<meta name="viewport" content="width=device-width,initial-scale=1" />'
        '<title>Agent UI 设计预览</title></head><body><div id="root"></div>'
        '<script type="module" src="/src/main.tsx"></script></body></html>', encoding="utf-8",
    )
    # 相对资源路径使预览可绑定独立 launch URL，不携带消费者固定端口或 Mock 地址。
    config = (seed / "vite.config.ts").read_text(encoding="utf-8")
    config = config[:config.index("  server: {")] + "})\n"
    config = config.replace("plugins: [", "base: './',\n  plugins: [", 1)
    (project / "vite.config.ts").write_text(config, encoding="utf-8")
    node = str(current.node_executable)
    command = [node, str(cli)]
    _run([*command, "--version"], current.workspace, project, root / "logs/pnpm-version.log", run_id)
    manager_version = (root / "logs/pnpm-version.log").read_text(encoding="utf-8").strip()
    expected_manager = json.loads((project / "package.json").read_text(encoding="utf-8"))["packageManager"]
    if f"pnpm@{manager_version}" != expected_manager:
        raise ValueError("实际 pnpm 版本与设计工程 packageManager 不一致。")
    install = [*command, "install", "--frozen-lockfile", "--ignore-scripts", "--registry=https://registry.npmjs.org"]
    if offline:
        install.append("--offline")
    _run(install, current.workspace, project, root / "logs/install.log", run_id)
    _check_installed_packages(project, manifest)
    # 固定适配脚本在已核对工具安装 cwd 中解析公开 SDK，不能误用另一安装的包。
    script = initializer + "\nconsole.log(JSON.stringify(await initializeConsumer(process.argv[1])))\n"
    _run([node, "--input-type=module", "--eval", script, str(project)],
         current.workspace, current.installation_root, root / "logs/initialize.log", run_id)
    for action in ("lint", "typecheck", "build"):
        _run([*command, action], current.workspace, project, root / f"logs/{action}.log", run_id)
    result = AgentUiDesignProject(current.workspace, root, project, identity,
                                 source_digest(project), tree_digest(project / "dist"))
    record.write_text(json.dumps({"identity": identity, "sourceDigest": result.source_digest,
                                  "buildDigest": result.build_digest}, indent=2), encoding="utf-8")
    validate_design_project(result)
    return result
