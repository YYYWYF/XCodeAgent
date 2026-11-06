"""T22 使用当前确认与安装合同的独立磁盘夹具，不调用模型或联网安装。"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys
import tarfile
from unittest.mock import patch

from app.services.agent_ui_dependency_install import execute_dependency_plan
from app.services.agent_ui_dependency_plan import REPOSITORY, prepare_dependency_plan
from app.services.agent_ui_design_confirmation import verify_agent_design_page
from app.services.agent_ui_design_project import source_digest
from app.services.page_implementation_contract import _artifact_hash
from app.services.ui_design_manifest import persisted_ui_manifest
from app.workspace.spec_documents import write_ui_designs_json
from app.workspace.spec_documents import render_requirement_spec_markdown
from app.workspace.product_plan_documents import render_product_plan_markdown
from app.workspace.plan_documents import render_project_plan_markdown
from tests.test_agent_ui_design_confirmation import confirmation_fixture
from tests.test_agent_ui_dependency_install import AgentUiDependencyPlanTests


def transfer_fixture(directory: str) -> tuple[Path, Path, str]:
    """准备已确认多文件设计和真实安装记录；仅替代昂贵安装/构建命令。"""
    state, manifest, source = confirmation_fixture(directory)
    workspace = Path(directory)
    target = workspace / "frontend"
    (target / "src").mkdir(parents=True)
    (target / "package.json").write_text(json.dumps({"name": "target", "type": "module",
        "packageManager": "pnpm@9.15.9", "dependencies": {"react": "19.1.1", "react-dom": "19.1.1"},
        "scripts": {"build": "rsbuild build", "typecheck": "tsc --noEmit"}}))
    (target / "src/main.tsx").write_text("export {}\n")
    (target / "rsbuild.config.ts").write_text("export default {source: {entry: {index: './src/main.tsx'}}}\n")
    files = {"src/agent-ui/app-ui.json": '{"root":{"type":"slot","plugins":[]}}',
        "src/agent-ui/application/project-config.tsx": "import type { AgentProps } from '@za38.03/agent-ui-runtime/ui'\nimport model from '../app-ui.json'\nexport const agentUIProject = model\n",
        "src/agent-ui/styles.css": ".aui { color: inherit; }\n",
        "src/agent-ui/application/Agent.tsx": "// generated entry, never copy\n",
        "src/agent-ui/application/runtime-config.generated.ts": "// target tool regenerates\n",
        "src/agent-ui/plugins/registry.generated.ts": "// generated registry\n",
        "src/agent-ui/plugins/composer/index.tsx": "// package implementation\n",
        "src/agent-ui/services/conversations.ts": "// runtime service\n",
        "src/design/Agent.tsx": "export { agentUIProject } from '../agent-ui/application/project-config'\n",
        "src/design/presentation.json": "{}",
        ".agent-ui/project.json": '{"sourceRoot":"src/agent-ui"}',
        ".agent-ui/source-lock.json": '{"sourceRoot":"src/agent-ui","items":{}}'}
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    # 假实现也明确登记 BASE，测试不能把无 owner 的组件入口误当可丢弃包实现。
    lock_path = source / ".agent-ui/source-lock.json"
    lock_path.write_text(json.dumps({"sourceRoot": "src/agent-ui", "items": {"fixture": {"files": {
        "plugins/composer/index.tsx": {"sha256": hashlib.sha256(files["src/agent-ui/plugins/composer/index.tsx"].encode()).hexdigest()}
    }}}}))
    # AST 分析使用消费者已有 TypeScript；不执行被分析的应用代码。
    (source / "node_modules/typescript").symlink_to(REPOSITORY / "Frontend/node_modules/typescript", target_is_directory=True)
    packages = json.loads((REPOSITORY / "vendor/agent-ui/manifest.json").read_text())["packages"]
    for item in packages:
        if item["name"].endswith(("creator", "web-component")):
            continue
        with tarfile.open(REPOSITORY / "vendor/agent-ui" / item["file"]) as archive:
            metadata = json.load(archive.extractfile("package/package.json"))
            selected = {"package/package.json"} | {"package/" + value["types"].removeprefix("./")
                for name, value in metadata.get("exports", {}).items()
                if isinstance(value, dict) and isinstance(value.get("types"), str) and "*" not in name}
            selected.update("package/" + path.removeprefix("./") for name, value in metadata.get("exports", {}).items()
                for path in ([value] if isinstance(value, str) else [value.get("import"), value.get("default")])
                if "*" not in name and isinstance(path, str))
            if "./catalog.json" in metadata.get("exports", {}):
                selected.add("package/" + metadata["exports"]["./catalog.json"].removeprefix("./"))
            # 只从已发布成品中取资源，不读取 A 工程源码。
            for member in archive.getmembers():
                if not member.isfile() or member.name not in selected:
                    continue
                for project in (source, target):
                    path = project / "node_modules" / item["name"] / member.name.removeprefix("package/")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.extractfile(member).read())
    asset = manifest["pages"][0]["design_asset"]
    asset["sourceDigest"] = source_digest(source)
    asset["revision"] = hashlib.sha256((asset["factsDigest"] + asset["environmentIdentity"] + asset["sourceDigest"]).encode()).hexdigest()
    record = source.parents[1] / "generation.json"
    generation = json.loads(record.read_text())
    generation.update(asset)
    record.write_text(json.dumps(generation))
    manifest["pages"] = [{**verify_agent_design_page(state, {**page, "design_asset": asset}), "status": "confirmed"} for page in manifest["pages"]]
    manifest["confirmation_status"] = "confirmed"
    write_ui_designs_json(state, manifest)
    documents = {"specs/requirement-spec": state["requirement_spec"], "plans/product-plan": state["product_plan"],
        "plans/technical-plan": {"confirmation_status": "confirmed", "artifact_type": "technical-plan",
            "product_plan_sha256": _artifact_hash(state["product_plan"]),
            "ui_designs_sha256": _artifact_hash(persisted_ui_manifest(manifest))}}
    for name, value in documents.items():
        path = workspace / ".devagentstudio" / (name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False))
        renderer = {"specs/requirement-spec": render_requirement_spec_markdown,
                    "plans/product-plan": render_product_plan_markdown, "plans/technical-plan": render_project_plan_markdown}[name]
        path.with_suffix(".md").write_text(renderer(value), encoding="utf-8")
    (workspace / ".devagentstudio/application.json").write_text('{"name":"T22 fixture"}')
    return source, target, verify_target(workspace)


def verify_target(workspace: Path) -> str:
    """通过当前 T21 owner 重新生成并验证夹具，不伪造 verified 记录。"""
    target = workspace / "frontend"
    plan = prepare_dependency_plan(workspace, source="local-vendor")
    helper = AgentUiDependencyPlanTests()
    helper.target = target
    with patch("app.services.agent_ui_dependency_install._tools", return_value=[sys.executable, __file__]), \
         patch("app.services.agent_ui_dependency_install._command", side_effect=helper.fake_command):
        record = execute_dependency_plan(workspace, plan_id=plan["planId"], source="local-vendor",
            entry="src/main.tsx", thread_id="fixture", run_id="fixture")
    if record["state"] != "verified":
        raise AssertionError(record.get("message"))
    return plan["planId"]
