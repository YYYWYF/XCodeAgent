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
        deployment: 'sidecar',
        serviceName: 'agent-runtime'
      },
      invocation: {
        transport: 'ag-ui-sse',
        gatewayEndpointId: 'agent_gateway_api.agent_leave_assistant_message',
        internalPath: '/internal/agents/agent_leave_assistant/run'
      },
      capabilityBindings: [
        {
          capabilityId: 'leave_application',
          toolIds: ['create_leave_application']
        }
      ],
      toolBindings: [
        {
          toolId: 'create_leave_application',
          apiContractId: 'leave_application_api',
          endpointId: 'leave_application_api.create',
          accessMode: 'write'
        }
      ],
      session: { supportsMultiTurn: true, memory: 'conversation' },
      security: { directClientAccess: false, authForwarding: 'scoped-user-context' },
      artifacts: {
        agentPath: 'agent-runtime/agents/agent_leave_assistant.py',
        toolAdapterPath: 'agent-runtime/tools/agent_leave_assistant_tools.py',
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
