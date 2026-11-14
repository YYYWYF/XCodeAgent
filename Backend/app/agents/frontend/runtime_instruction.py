"""只向显式 Runtime 页面补正式 Agent 会话合同，不影响 Java 前端生成提示。"""

import json

from app.services.runtime_business_contract import serves_python_business


def shared_host_instruction(contracts: list[dict]) -> str:
    """页面任务仅使用已转入 Host 的布局和显隐 API，不取得共享源码写权限。"""
    shared = [contract for contract in contracts if contract.get('host')]
    if not contracts:
        return ''
    if len(shared) != len(contracts) or any(contract.get('mode') != 'shared_host' for contract in shared):
        raise ValueError('同批 Agent 页面不得混用固定会话资产与共享 Host。')
    return (
        '## Shared Agent Host page boundary\n'
        'The platform has already reviewed and registered SharedAgentHostProvider in extensionProviders. '
        'It owns the single persistent AgentUIProvider and Agent outside the Router page subtree. '
        'Never mount another Agent/Provider/adapter in any page. Do not modify the Router, Shell, '
        'extension registry, Host provider, shared application layer, AppUIModel or package-owned files. '
        'These files are read-only and never become page task allowed_paths. '
        'Read host.providerPath and import only from host.componentModule below. '
        'A standalone_page renders <SharedAgentPage pageId="<exact pageId>" /> inside the original page layout; '
        'it is a placement slot, not a second conversation. '
        'STANDALONE LAYOUT: the shared conversation IS the page content. The existing application Shell '
        'already owns application navigation; the shared Agent owns its thread sidebar, welcome, messages and composer. '
        'Do not wrap the slot in ProCard, Card, PageContainer, a centered/max-width panel, rounded/shadowed '
        'page frame, another sidebar, a hero title/description or an explanatory implementation footer. '
        'Use a flat full-width flex column and let the slot consume the available Shell content height, '
        'Remove the existing Shell content padding/background frame only while this standalone page is mounted, '
        'using a page-scoped parent selector (for example .ant-pro-layout-content:has(> .your-page-class)); '
        'do not edit the global Shell or remove spacing from ordinary business pages. '
        'with min-width:0/min-height:0. In a page-scoped stylesheet set the direct .shared-agent-page-slot '
        'to height:100% inside its flexible remaining-space region; the default standalone slot height is '
        'not a sizing contract for an embedded Shell. Derive available height from the actual Shell, '
        'not a nested 100vh page or an arbitrary fixed minimum height. '
        'Retain confirmed non-Agent business/navigation actions in a compact toolbar. Do not drop required actions '
        'or information. Follow existing semantic theme tokens in light/dark modes. '
        'This standalone rule takes precedence over generic ProCard/page-template advice only around the conversation. '
        'A floating_panel keeps all ordinary business content '
        'and uses useSharedAgentPanel().openPanel/closePanel/panelOpen for the confirmed launcher actions. '
        'Use useSharedAgentContext({actionId, contextItems:[{id,label,value}], input}) from host.componentModule '
        'to publish the current selected record and typed business form input when declared. Values are current page state; '
        'do not invent data or copy model credentials. The Host transports these through standard AG-UI Context. '
        'Page changes and hiding never reset key/thread/draft or abort a running conversation. '
        'The Host resets the entire session on logout or identity change; user profile is presentation, not authorization. '
        'Use the confirmed per-page presentation props already transferred with the design; do not regenerate defaults. '
        'When host.connection=blocked_T27, leave real sending disabled and report pending integration; '
        'never supply /agent, a Mock URL, Creator configuration, guessed forwardedProps or getRunContext as a workaround.\n'
        + json.dumps(shared, ensure_ascii=False, indent=2) + '\n\n'
    )


def runtime_frontend_instruction(plan: dict, tasks: list[dict]) -> str:
    """从当前已确认页面绑定生成同源 AG-UI 说明，不传播 Debug 或模型凭据。"""

    if not serves_python_business(plan):
        return ""
    task_contracts = [task.get('source_refs', {}).get('agent_ui', {}) for task in tasks]
    shared = [contract for contract in task_contracts if contract.get('host')]
    business_calls = [{"apiContractId": api["id"], "endpoint": endpoint} for api in plan.get("api_contracts", [])
                      for endpoint in api.get("endpoints", []) if endpoint.get("agentCall")]
    if shared:
        return ('## Shared Host Runtime boundary\n'
            'Keep the persistent Host and its connection admission unchanged. Page tasks cannot implement '
            'missing Runtime mappings or send requests around blocked_T27. Ordinary business actions remain '
            'bound to their separate confirmed API contracts.\n' + json.dumps(business_calls, ensure_ascii=False) + '\n\n')
    # Agent 后端归属不等于页面声明了会话 Surface；普通表单仍沿独立业务接口合同。
    if not business_calls:
        return ""
    return (
        "## Confirmed ordinary business Agent calls\n"
        "A business endpoint declaring agentCall.responseMode=json uses the normal confirmed business request/response. "
        "For agentCall.responseMode=ag-ui use @ag-ui/client HttpAgent and @ag-ui/core, put the declared business "
        "request in forwardedProps.input and consume the structured result from the final business event. "
        "Never parse SSE manually or infer success from RUN_FINISHED alone. Task surfaces use schema-driven "
        "business inputs, not a free-text conversation pretending to be a typed request.\n"
        + json.dumps(business_calls, ensure_ascii=False, indent=2) + "\n"
        + "\n"
    )
