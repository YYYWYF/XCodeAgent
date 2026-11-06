"""仓库外 G4 成品交接验收；正式文档为隔离夹具，不批准任何用户应用。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from app.services.agent_ui_build_contract import current_agent_ui_build_contract
from app.services.agent_ui_build_evidence import load_build_evidence
from app.services.agent_ui_dependency_install import execute_dependency_plan
from app.services.agent_ui_dependency_plan import REPOSITORY, prepare_dependency_plan, sha256, target_snapshot
from app.services.agent_ui_design_confirmation import verify_agent_design_page
from app.services.agent_ui_design_generator import _host_files
from app.services.agent_ui_design_project import command_environment, source_digest, tree_digest
from app.services.agent_ui_design_transfer import execute_transfer
from app.services.agent_ui_design_transfer_plan import prepare_transfer_plan
from app.services.business_acceptance_verifiers.frontend_agent_ui import verify_agent_ui_runtime_source
from app.services.page_implementation_contract import _artifact_hash
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from app.services.ui_design_manifest import persisted_ui_manifest
from app.workspace.plan_documents import render_project_plan_markdown
from app.workspace.product_plan_documents import render_product_plan_markdown
from app.workspace.spec_documents import render_requirement_spec_markdown, write_ui_designs_json
from tests.test_agent_ui_design_confirmation import confirmation_fixture
from tests.test_runtime_endpoint_contracts import agent, plan as runtime_plan


def write(root: Path, name: str, content: str) -> None:
    """只向本次创建的隔离目录写入样例文件。"""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run(root: Path, cwd: Path, label: str, argv: list[str]) -> None:
    """保存实际命令的完整输出，失败时保留全部现场。"""
    print(label, flush=True)
    result = subprocess.run(argv, cwd=cwd, env=command_environment(), capture_output=True,
                            text=True, timeout=240, check=False)
    log = root / (label + ".log")
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{label} failed ({result.returncode}): {log}")


def prepare_source(root: Path, pnpm: list[str], node: str) -> tuple[dict, Path]:
    """真实独立安装、公开初始化和构建设计源，不复用 node_modules 或假 SDK。"""
    state, manifest, source = confirmation_fixture(str(root))
    seed = REPOSITORY / "tools/agent-ui-consumer"
    for name in ("package.json", "pnpm-lock.yaml", ".pnpmfile.cjs", "tsconfig.json",
                 "vite.config.ts", "eslint.config.mjs", "index.html"):
        shutil.copy2(seed / name, source / name)
    shutil.copytree(REPOSITORY / "vendor/agent-ui", source.parents[1] / "vendor/agent-ui")
    for name in ("App.tsx", "main.tsx", "styles.css"):
        shutil.copy2(seed / "src" / name, source / "src" / name)
    run(root, source, "source-install", [*pnpm, "install", "--frozen-lockfile", "--ignore-scripts",
                                          "--registry=https://registry.npmjs.org"])
    run(root, source, "source-initialize", [node, str(REPOSITORY / "tools/agent-ui/prepare-consumer.mjs"), str(source)])
    plugin = source / "src/agent-ui/plugins/conversation-thread-list/index.tsx"
    content = plugin.read_text()
    changed = content.replace(" {...props} headerActions=", " {...props} showSearch={false} headerActions=")
    if content == changed:
        raise AssertionError("公开生成的定制位置已变化，请审阅当前应用层。")
    plugin.write_text(changed)
    presentations = {page: {"props": {"locale": "zh-CN", "welcome": {"title": title}}, "styles": []}
                     for page, title in (("home", "首页欢迎"), ("chat", "独立页欢迎"))}
    for name, content in _host_files(project_agent_design_pages(state["requirement_spec"], state["product_plan"]),
                                     presentations=presentations).items():
        write(source, name, content)
    write(source, "src/pages/page-0.tsx", "/** 显示业务首页。 */\nexport default function Page(){return <main>业务首页</main>}\n")
    for stage in ("lint", "typecheck", "build"):
        run(root, source, "source-" + stage, [*pnpm, "run", stage])
    asset = manifest["pages"][0]["design_asset"]
    asset.update(sourceDigest=source_digest(source), buildDigest=tree_digest(source / "dist"))
    asset["revision"] = hashlib.sha256((asset["factsDigest"] + asset["environmentIdentity"] + asset["sourceDigest"]).encode()).hexdigest()
    generation = source.parents[1] / "generation.json"
    generation.write_text(json.dumps({**json.loads(generation.read_text()), **asset}))
    # 仅在测试夹具中构造正式确认；不调用或批准任何现有用户工作区。
    manifest["pages"] = [{**verify_agent_design_page(state, {**page, "design_asset": asset}),
                          "status": "confirmed"} for page in manifest["pages"]]
    manifest["confirmation_status"] = "confirmed"
    write_ui_designs_json(state, manifest)
    technical = runtime_plan()
    technical.update(confirmation_status="confirmed", artifact_type="technical-plan",
                     product_plan_sha256=_artifact_hash(state["product_plan"]),
                     ui_designs_sha256=_artifact_hash(persisted_ui_manifest(manifest)))
    technical["pages"] = [{"pageId": page} for page in ("home", "chat")]
    technical["agent_contracts"] = [agent("assistant", "home")]
    technical["agent_contracts"][0]["pageIds"] = ["home", "chat"]
    for name, value, renderer in (("specs/requirement-spec", state["requirement_spec"], render_requirement_spec_markdown),
            ("plans/product-plan", state["product_plan"], render_product_plan_markdown),
            ("plans/technical-plan", technical, render_project_plan_markdown)):
        write(root, ".devagentstudio/" + name + ".json", json.dumps(value, ensure_ascii=False))
        write(root, ".devagentstudio/" + name + ".md", renderer(value))
    write(root, ".devagentstudio/application.json", json.dumps({"name": "G4 隔离验收夹具", "auth": {"enable": True}}))
    return state, source


def prepare_target(root: Path, template: Path) -> tuple[Path, dict[str, str]]:
    """使用当前模板的真实扩展、身份和路由 owner，仅装配独立验收 Shell。"""
    target = root / "frontend"
    target.mkdir()
    seed = REPOSITORY / "tools/agent-ui-consumer"
    inputs = {}
    for name in ("src/providers/AppProviders.tsx", "src/platform/identity/IdentityContext.tsx",
                 "src/utils/pageRouteTree.ts", "src/utils/pageIdentity.ts", "src/typings/routes.ts"):
        content = (template / name).read_text()
        write(target, name, content)
        inputs[name] = sha256(content.encode())
    package = json.loads((seed / "package.json").read_text())
    package["name"] = "agent-ui-g4-isolated-target"
    package["dependencies"] = {name: value for name, value in package["dependencies"].items() if not name.startswith("@za38.03/")}
    package["dependencies"]["react-router-dom"] = "6.30.6"
    package["scripts"] = {"lint": "eslint src/App.tsx src/main.tsx src/extensions/*.tsx src/pages/*.tsx", "typecheck": "tsc --noEmit", "build": "vite build"}
    # T21 前扩展目录保留独立占位，不预占将由受控转入生成的 Provider。
    write(target, "src/extensions/Existing.tsx", "export {}\n")
    write(target, "package.json", json.dumps(package, indent=2))
    write(target, ".npmrc", "registry=https://registry.npmjs.org\n")
    config = json.loads((seed / "tsconfig.json").read_text())
    config["compilerOptions"].update(baseUrl=".", paths={"@/*": ["src/*"]})
    write(target, "tsconfig.json", json.dumps(config, indent=2))
    for name in ("index.html", "eslint.config.mjs"):
        shutil.copy2(seed / name, target / name)
    write(target, "src/extensions/providers.ts", "import type {ComponentType,PropsWithChildren} from 'react'\nexport const extensionProviders: ComponentType<PropsWithChildren>[]=[]\n")
    write(target, "src/routes/pageRegistry.ts", "import {resolvePageRouteTree} from '@/utils/pageRouteTree'\nexport const APP_PAGE_ROUTE_TREE=resolvePageRouteTree([{path:'home',label:'业务首页'},{path:'chat',label:'独立助手'}],'/page')\n")
    # App 和 Shell 必须在交接前固定；交接后只允许普通页面继续开发。
    for name in ("App.tsx", "g4.css"):
        shutil.copy2(REPOSITORY / "tools/agent-ui-consumer/g4" / name, target / "src" / name)
    for page in ("Home", "Chat"):
        write(target, "src/pages/" + page + ".tsx", "/** 安装阶段保留业务页面。 */\nexport default function Page(){return <main>G4 安装准备</main>}\n")
    write(target, "src/main.tsx", "import {createRoot} from 'react-dom/client'\nimport {App} from './App'\ncreateRoot(document.getElementById('root')!).render(<App />)\n")
    vite = "import {fileURLToPath} from 'node:url'\n" + (seed / "vite.config.ts").read_text()
    vite = vite.replace("plugins: [", "resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },\n  plugins: [")
    vite = vite.replace("port: 5188", "port: 5194")
    write(target, "vite.config.ts", vite)
    return target, inputs


def main() -> None:
    """串行执行真实安装、重新审阅、转入、构建和来源复核，保留可审查证据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template-root", required=True, type=Path)
    parser.add_argument("--pnpm-cli", required=True, type=Path)
    parser.add_argument("--node", default=shutil.which("node"))
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="Agent UI G4 中文 ")).resolve()
    print("workspace=" + str(root), flush=True)
    pnpm = [args.node, str(args.pnpm_cli.resolve(strict=True))]
    _, source = prepare_source(root, pnpm, args.node)
    target, inputs = prepare_target(root, args.template_root.resolve(strict=True))
    plan = prepare_dependency_plan(root, source="local-vendor")
    installation = execute_dependency_plan(root, plan_id=plan["planId"], source="local-vendor", entry="src/main.tsx",
        thread_id="g4-fixture", run_id="g4-install", progress=lambda step: print("dependency=" + step, flush=True))
    if installation["state"] != "verified":
        raise AssertionError(installation.get("message"))
    for action in ("initialize", "confirm"):
        transfer = prepare_transfer_plan(root, installation_plan_id=plan["planId"], target_source_root="src/正式助手")
        write(root, "review-" + action + ".json", json.dumps(transfer, ensure_ascii=False, indent=2))
        if transfer["status"] != "reviewable":
            raise AssertionError(transfer.get("blockers"))
        record = execute_transfer(root, installation_plan_id=plan["planId"], plan_id=transfer["planId"],
            target_source_root="src/正式助手", action=action, thread_id="g4-fixture", run_id="g4-" + action,
            progress=lambda step: print("transfer=" + step, flush=True))
        if record["state"] not in {"initialized", "verified"}:
            raise AssertionError(record.get("message"))
    snapshot = target_snapshot(target)
    duplicate = execute_transfer(root, installation_plan_id=plan["planId"], plan_id=transfer["planId"],
        target_source_root="src/正式助手", action="confirm", thread_id="g4-fixture", run_id="g4-duplicate")
    if duplicate["state"] != "verified" or target_snapshot(target) != snapshot:
        raise AssertionError("重复确认改变了目标或未返回原验证结果。")
    before = load_build_evidence(root)
    for name in ("Home.tsx", "Chat.tsx"):
        shutil.copy2(REPOSITORY / "tools/agent-ui-consumer/g4" / name, target / "src/pages" / name)
    for stage in ("lint", "typecheck", "build"):
        run(root, target, "target-" + stage, [*pnpm, "run", stage])
    if load_build_evidence(root) != before:
        raise AssertionError("普通业务页面装配改变了共享来源。")
    contracts = {page: current_agent_ui_build_contract(root, page) for page in ("home", "chat")}
    checks = {}
    for page, contract in contracts.items():
        relative = "frontend/src/pages/" + page.title() + ".tsx"
        checks[page] = verify_agent_ui_runtime_source({relative: (root / relative).read_text()},
            {"agent_ui": contract}, workspace_root=root)
        if checks[page]["status"] != "passed":
            raise AssertionError(checks[page])
    if "showSearch={false}" not in (target / "src/正式助手/plugins/conversation-thread-list/index.tsx").read_text():
        raise AssertionError("共享定制在转入中丢失。")
    write(root, "g4-result.json", json.dumps({"workspace": str(root), "source": str(source), "target": str(target),
        "fixtureConfirmationOnly": True, "templateInputs": inputs, "installationPlanId": plan["planId"],
        "transferPlanId": transfer["planId"], "static": "passed", "contracts": contracts, "pageChecks": checks,
        "realConnection": "blocked_T27", "electron": "not_run", "registry": "not_run"}, ensure_ascii=False, indent=2))
    print("G4 fixture static passed: " + str(root / "g4-result.json"), flush=True)


if __name__ == "__main__":
    main()
