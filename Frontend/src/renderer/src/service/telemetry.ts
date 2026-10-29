import { HttpAgent, randomUUID } from '@ag-ui/client'
import type { ChatSessionDevelopmentTarget } from './chatSessions'

type TelemetryEventRecord = {
  record_id?: string
  identity?: {
    project_id?: string
    conversation_id?: string
    thread_id?: string
    workflow_scope?: string
  }
  context?: {
    business_stage?: string
    node_name?: string
    target?: { type: 'page' | 'endpoint' | 'agent'; id: string; api_contract_id?: string }
  }
  data?: {
    event_name?: string
    payload?: {
      creation_source?: 'user' | 'new_app' | 'automatic'
      conversation_type?: 'regular' | 'temporary'
    }
  }
}

/** 通过独立 AG-UI 请求提交埋点，失败和超时只写日志，不触发业务或登录状态变化。 */
async function postTelemetryEvent(record: TelemetryEventRecord): Promise<void> {
  let timeoutId: ReturnType<typeof globalThis.setTimeout> | undefined
  try {
    const baseUrl = window.devAgentStudio?.agentBaseUrl
    // 埋点请求使用独立客户端，不接入会话消息和全局认证失败提示。
    const agent = new HttpAgent({
      url: baseUrl ? `${baseUrl.replace(/\/$/, '')}/telemetry/run` : '/api/agent/telemetry/run',
      threadId: `telemetry-${randomUUID()}`
    })
    await Promise.race([
      agent.runAgent({
        runId: randomUUID(),
        forwardedProps: {
          telemetry: { action: 'emit_event', record: { ...record, producer: { source: 'frontend' } } }
        }
      }, {
        onCustomEvent: ({ event }) => {
          if (event.name === 'telemetry' && event.value?.status === 'failed') {
            console.warn('telemetry.submit_rejected')
          }
        }
      }),
      new Promise<never>((_resolve, reject) => {
        timeoutId = globalThis.setTimeout(() => {
          reject(new Error('telemetry.timeout'))
          agent.abortRun()
        }, 5_000)
      })
    ])
  } catch {
    console.warn('telemetry.submit_failed')
  } finally {
    if (timeoutId !== undefined) globalThis.clearTimeout(timeoutId)
  }
}

/** 非阻塞提交业务埋点，不返回发送结果，调用方无需 await。 */
export function emitTelemetryEvent(record: TelemetryEventRecord): void {
  void postTelemetryEvent(record)
}

/** 构建阶段会话创建事件，以已持久化会话 ID 标识创建事实。 */
export function emitPlanningConversationCreated(
  projectId: string,
  conversationId: string,
  threadId: string,
  stage: 'requirements' | 'technical_planning'
): void {
  emitTelemetryEvent({
    record_id: `conversation_created:${projectId}:${conversationId}`,
    identity: { project_id: projectId, conversation_id: conversationId, thread_id: threadId },
    context: {
      business_stage: stage,
      node_name: stage === 'requirements' ? 'requirements' : 'technical_planning_begin'
    },
    data: { event_name: 'conversation_created', payload: { creation_source: 'automatic' } }
  })
}

/** 构建用户新建对话事件，使用创建时绑定的目标区分页面、API 和直接需求。 */
export function emitConversationCreated(input: {
  projectId: string
  conversationId: string
  threadId: string
  conversationType: 'regular' | 'temporary'
  businessStage?: string
  target?: ChatSessionDevelopmentTarget
}): void {
  const target = input.target
  emitTelemetryEvent({
    record_id: `conversation_created:${input.projectId}:${input.conversationId}`,
    identity: {
      project_id: input.projectId,
      conversation_id: input.conversationId,
      thread_id: input.threadId
    },
    context: {
      ...(input.businessStage ? { business_stage: input.businessStage } : {}),
      ...(target ? {
        target: target.type === 'agent'
          ? { type: 'agent', id: target.agentId }
          : target.type === 'page'
          ? { type: 'page', id: target.pageId }
          : { type: 'endpoint', id: target.endpointId, api_contract_id: target.apiContractId }
      } : {})
    },
    data: {
      event_name: 'conversation_created',
      payload: { creation_source: 'user', conversation_type: input.conversationType }
    }
  })
}
