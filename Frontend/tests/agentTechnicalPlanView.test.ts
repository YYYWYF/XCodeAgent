import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import TechnicalPlanDocPanel from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanDocPanel'
import TechnicalPlanSummary from '../src/renderer/src/components/Welcome/TechnicalPlanSummary'
import agentDevelopmentDetailSource from '../src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/index.tsx?raw'
import agentSettingsViewSource from '../src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/AgentSettingsView.tsx?raw'
import agentSettingsSummarySource from '../src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/AgentSettingsSummary.tsx?raw'
import agentDependenciesViewSource from '../src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/AgentDependenciesView.tsx?raw'
import agentSettingsRevisionSource from '../src/renderer/src/service/agentSettingsRevision.ts?raw'

const agentPlan = {
  architecture: {
    frontend: 'React',
    backend: 'Java8 + Springboot',
    data: 'MySQL8 + Redis',
    agent_runtime: 'Python 3.12 + DeepAgents sidecar，通过 Java AG-UI SSE 网关调用。'
  },
  entities: [],
  api_contracts: [],
  pages: [],
  agent_contracts: [
    {
      agentId: 'agent_leave_assistant',
      runtime: {
        language: 'Python',
        pythonVersion: '3.12',
        framework: 'DeepAgents',
        agentFactory: 'create_deep_agent',
        deployment: 'sidecar',
        serviceName: 'agent-runtime'
      },
      source: { productPlanSha256: 'sha256:product-plan-example' },
      invocation: {
        transport: 'ag-ui-sse',
        gatewayEndpointId: 'agent_gateway_api.agent_leave_assistant_message',
        internalPath: '/internal/agents/agent_leave_assistant/run'
      },
      identity: {
        name: '请假助手',
        purpose: '帮助用户提交请假申请',
        boundaries: ['不得绕过请假审批']
      },
      interaction: {
        mode: 'conversation',
        supportsMultiTurn: true,
        clarification: { allowed: true }
      },
      capabilities: [
        {
          capabilityId: 'leave_application',
          toolIds: ['create_leave_application']
        }
      ],
      agentSettings: {
        prompt: {
          persona: { role: '请假业务助手', tone: '专业、清晰' },
          systemPrompt: '帮助用户提交请假申请。',
          constraints: ['写操作必须等待平台审批']
        },
        model: {
          selection: 'project_default',
          modelRef: 'project_default',
          requiredCapabilities: {
            streaming: true,
            toolCalling: true,
            structuredOutput: false,
            vision: false,
            observability: true
          },
          generation: { temperature: 0.2 }
        },
        memory: {
          shortTerm: {
            enabled: true,
            store: 'sqlite',
            scope: 'thread',
            connectionRef: 'agent_runtime_checkpoint'
          },
          longTerm: { enabled: false },
          archive: { enabled: false }
        },
        tools: {
          enabled: true,
          bindings: [
            {
              name: '提交请假申请',
              toolId: 'create_leave_application',
              description: '信息完整并经用户确认后提交请假申请。',
              accessMode: 'write',
              endpoint: {
                endpointId: 'leave_application_api.create',
                method: 'POST',
                path: '/api/leave-applications',
                requestSchemaRef: 'leave_application_api.CreateRequest',
                responseSchemaRef: 'leave_application_api.LeaveApplication'
              },
              approvalPolicy: 'platform_managed'
            }
          ]
        },
        skills: { enabled: false, loadingPolicy: 'explicit_only', bindings: [] },
        knowledge: {
          enabled: false,
          sources: [],
          retrieval: { strategy: 'semantic', topK: 5 },
          citationPolicy: 'disabled'
        },
        context: {
          sources: [
            { type: 'conversation', enabled: true },
            { type: 'knowledge_results', enabled: false }
          ],
          budget: {
            strategy: 'model_window',
            maxInputRatio: 0.7,
            reserveOutputRatio: 0.2
          },
          compression: { strategy: 'none' }
        }
      },
      evaluation: {
        productAcceptanceCriteria: ['用户能够完成请假申请'],
        engineeringCriteria: ['所有 Tool 均解析到已确认 Endpoint']
      },
      security: {
        directClientAccess: false,
        authForwarding: 'scoped-user-context',
        toolAuthorization: 'platform_enforced'
      },
      artifacts: {
        agentPath: 'agent-runtime/src/app/agent/agent_leave_assistant.py',
        toolAdapterPath: 'agent-runtime/src/app/tools/agent_leave_assistant_tools.py',
        testPath: 'agent-runtime/tests/test_agent_leave_assistant.py'
      },
      requiredChecks: ['pytest agent-runtime/tests/test_agent_leave_assistant.py']
    }
  ]
}

const agentPlanMarkup = renderToStaticMarkup(
  createElement(TechnicalPlanDocPanel, {
    plan: agentPlan,
    productPlan: {}
  })
)
assert.match(agentPlanMarkup, /智能体契约/)
assert.match(agentPlanMarkup, /agent_leave_assistant/)
assert.match(agentPlanMarkup, /Python 3\.12/)
assert.match(agentPlanMarkup, /agent_gateway_api\.agent_leave_assistant_message/)
assert.match(agentPlanMarkup, /create_leave_application/)
assert.match(agentPlanMarkup, /scoped-user-context/)
assert.match(agentPlanMarkup, /AgentSettings/)
assert.match(agentPlanMarkup, /用户确认配置/)
assert.match(agentPlanMarkup, /请假业务助手/)
assert.match(agentPlanMarkup, /Temperature/)
assert.match(agentPlanMarkup, /Short-term/)
assert.match(agentPlanMarkup, /Skills \/ Knowledge \/ Context/)
assert.match(agentPlanMarkup, /当前 Runtime 尚未接入 Skill Loader/)
assert.match(agentPlanMarkup, /平台派生配置/)
assert.match(agentPlanMarkup, /只读/)
assert.match(agentPlanMarkup, /leave_application_api\.CreateRequest/)

const agentSummaryMarkup = renderToStaticMarkup(
  createElement(TechnicalPlanSummary, { plan: agentPlan })
)
assert.match(agentSummaryMarkup, /<strong>1<\/strong>.*智能体契约/)
assert.match(agentSummaryMarkup, /智能体运行时/)

const ordinaryPlanMarkup = renderToStaticMarkup(
  createElement(TechnicalPlanDocPanel, {
    plan: {
      architecture: { frontend: 'React', backend: 'Java8 + Springboot', data: 'MySQL8' },
      entities: [],
      api_contracts: [],
      pages: [],
      agent_contracts: []
    },
    productPlan: {}
  })
)
assert.doesNotMatch(ordinaryPlanMarkup, /智能体契约|智能体运行时|Python 3\.12/)

// Agent 工作台默认只展示七段可视化摘要，不再把不可控 JSON 作为编辑界面。
assert.doesNotMatch(agentSettingsViewSource, /JSON\.stringify\(value, null, 2\)/)
for (const label of [
  '人设与 System Prompt',
  '模型配置',
  '记忆模块',
  '工具配置',
  'Skills',
  '知识库配置',
  '上下文配置'
]) {
  assert.match(agentSettingsSummarySource, new RegExp(label))
}
assert.match(agentSettingsViewSource, /生成修改预览/)
assert.match(agentSettingsViewSource, /确认并应用/)
assert.match(agentSettingsViewSource, /结束任务并修改配置/)
assert.match(agentSettingsViewSource, /打开当前任务/)
assert.match(agentSettingsViewSource, /aria-label="查看 Agent Settings 来源"/)
assert.match(agentSettingsViewSource, /配置来源：已确认的 TechnicalPlan/)
assert.doesNotMatch(agentSettingsViewSource, /来源 TechnicalPlan/)
assert.match(agentSettingsViewSource, /平台模型列表接入后可选择/)
assert.match(agentSettingsViewSource, /label: '跟随项目默认模型'/)
assert.match(
  agentSettingsViewSource,
  /Boolean\(preview\) \|\| Boolean\(blockingExecution\)/
)
assert.match(agentSettingsSummarySource, /PlusOutlined/)
assert.match(agentSettingsSummarySource, /Checkbox/)
assert.match(agentSettingsSummarySource, /\$\{label\}接入功能还在开发中/)
assert.match(agentSettingsSummarySource, /comingSoonAction\('Skill 市场'\)/)
assert.match(agentSettingsSummarySource, /comingSoonAction\('知识库'\)/)
assert.match(agentSettingsSummarySource, /observability: '可观测'/)
assert.doesNotMatch(agentSettingsSummarySource, /capabilities\.observability !== false/)
assert.match(agentSettingsSummarySource, /right === 'observability'/)
assert.match(agentSettingsSummarySource, /capabilityEntries\.map/)
assert.match(agentSettingsSummarySource, /aria-label="Agent 模型策略"/)
assert.match(agentSettingsSummarySource, /agent-model-select/)
assert.doesNotMatch(agentSettingsSummarySource, /Temperature/)
assert.match(agentSettingsViewSource, /label="Temperature"/)
assert.match(agentDependenciesViewSource, /必要检查/)
assert.match(agentDependenciesViewSource, /Required checks · 开发完成后执行/)
assert.match(agentDependenciesViewSource, /agent-required-checks-list/)
assert.match(agentDependenciesViewSource, /requiredChecks\.map\(\(check, index\)/)
assert.match(agentDevelopmentDetailSource, /aria-label="查看 Contract Hash"/)
assert.match(agentDevelopmentDetailSource, /agent-contract-hash-tooltip/)
assert.doesNotMatch(agentDevelopmentDetailSource, /className=\{cx\('agent-contract-hash'\)\}/)
assert.match(agentSettingsRevisionSource, /@ag-ui\/client/)
assert.match(agentSettingsRevisionSource, /basedOnTechnicalPlanSha256/)
assert.match(agentSettingsRevisionSource, /agent-settings-revision/)
