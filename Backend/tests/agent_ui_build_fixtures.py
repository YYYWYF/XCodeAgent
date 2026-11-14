"""通过现有确认、依赖和转入 owner 构造隔离共享目标；不调用模型或联网安装。"""

import json
from pathlib import Path
from unittest.mock import patch

from app.services.agent_ui_dependency_plan import sha256
from app.services.agent_ui_design_confirmation import verify_agent_design_page
from app.services.agent_ui_design_generator import _host_files
from app.services.agent_ui_design_project import source_digest, tree_digest
from app.services.agent_ui_design_transfer import execute_transfer
from app.services.agent_ui_design_transfer_plan import _formal_state, prepare_transfer_plan
from app.services.page_implementation_contract import _artifact_hash
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from app.services.ui_design_manifest import persisted_ui_manifest
from app.workspace.plan_documents import render_project_plan_markdown
from app.workspace.spec_documents import write_ui_designs_json
from tests.agent_ui_transfer_fixtures import transfer_fixture, verify_target
from tests.test_runtime_endpoint_contracts import agent, plan as runtime_plan


def shared_build_target(root: Path, *, transfer: bool = True) -> dict:
    """准备完整正式来源，可停在转入前以验证主Workflow自动准备。"""
    source, target, _ = transfer_fixture(str(root))
    state = _formal_state(root)
    pages = project_agent_design_pages(state['requirement_spec'], state['product_plan'])
    presentations = {page['pageId']: {'props': {'locale': 'zh-CN', 'welcome': {'title': '定制欢迎'}},
                                      'styles': []} for page in pages}
    for name, text in _host_files(pages, presentations=presentations).items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    manifest = state['ui_designs']
    asset = manifest['pages'][0]['design_asset']
    asset.update(sourceDigest=source_digest(source), buildDigest=tree_digest(source / 'dist'))
    asset['revision'] = sha256((asset['factsDigest'] + asset['environmentIdentity'] + asset['sourceDigest']).encode())
    generation_path = source.parents[1] / 'generation.json'
    generation_path.write_text(json.dumps({**json.loads(generation_path.read_text()), **asset}))
    manifest['pages'] = [{**verify_agent_design_page(state, {**page, 'design_asset': asset}),
                         'status': 'confirmed'} for page in manifest['pages']]
    write_ui_designs_json(state, manifest)
    technical_path = root / '.devagentstudio/plans/technical-plan.json'
    technical = json.loads(technical_path.read_text())
    runtime = runtime_plan()
    runtime['pages'] = [{'pageId': page['pageId']} for page in pages]
    runtime['agent_contracts'] = [agent('assistant', pages[0]['pageId'])]
    runtime['agent_contracts'][0]['pageIds'] = [page['pageId'] for page in pages]
    technical.update(runtime)
    technical['ui_designs_sha256'] = _artifact_hash(persisted_ui_manifest(manifest))
    technical_path.write_text(json.dumps(technical))
    technical_path.with_suffix('.md').write_text(render_project_plan_markdown(technical))
    (root / '.devagentstudio/application.json').write_text(
        '{"name":"T25 fixture","auth":{"enable":false},"authorization":{"enabled":false}}')
    app = ("import { extensionProviders } from '@/extensions/providers'\n"
           "import { IdentityProvider } from '@/platform/identity/IdentityContext'\n"
           "export function AppProviders({children}) {\n"
           " const content=extensionProviders.reduceRight((current,Provider)=><Provider>{current}</Provider>,children)\n"
           " return <IdentityProvider>{content}</IdentityProvider>\n}\n")
    for name, text in {'src/providers/AppProviders.tsx': app,
            'src/extensions/providers.ts': 'export const extensionProviders=[]\n',
            'src/routes/pageRegistry.ts': 'export const APP_PAGE_ROUTE_TREE=[]\n',
            'src/utils/pageRouteTree.ts': 'export type ResolvedPageRoute=unknown\n',
            'src/platform/identity/IdentityContext.tsx': 'export const IdentityProvider=({children})=>children\n'}.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    installation = verify_target(root)
    if not transfer:
        return {'installationPlanId': installation}

    def public_tool(action: str, _target: Path, source_root: str, **_kwargs: object) -> dict:
        """仅模拟公开初始化的目标 BASE；不把设计 BASE 复制进目标。"""
        if action == 'initialize':
            for name, text in {'.agent-ui/project.json': json.dumps({'sourceRoot': source_root}),
                    '.agent-ui/source-lock.json': json.dumps({'sourceRoot': source_root, 'items': {}}),
                    source_root + '/app-ui.json': '{}',
                    source_root + '/application/project-config.tsx': 'export {}'}.items():
                path = _target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        return {'status': 'passed'}

    with patch('app.services.agent_ui_design_transfer.run_public_tool', side_effect=public_tool), \
         patch('app.services.agent_ui_design_transfer.validate_target', return_value={'static': 'passed'}):
        for action in ('initialize', 'confirm'):
            plan = prepare_transfer_plan(root, installation_plan_id=installation, target_source_root='src/正式助手')
            if plan['status'] != 'reviewable':
                raise AssertionError(plan)
            record = execute_transfer(root, installation_plan_id=installation, plan_id=plan['planId'],
                target_source_root='src/正式助手', action=action, thread_id='fixture', run_id=plan['planId'])
            if record['state'] not in {'initialized', 'verified'}:
                raise AssertionError(record['message'])
    return record
