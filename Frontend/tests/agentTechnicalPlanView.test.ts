import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import TechnicalPlanDocPanel from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanDocPanel'
import TechnicalPlanSummary from '../src/renderer/src/components/Welcome/TechnicalPlanSummary'

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
            vision: false
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
