import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type {
  EndpointDesignDetail,
  EndpointDesignPreparation,
  EndpointDesignSaveResult,
  EndpointDesignsPayload,
  WorkflowApiDesignAction
} from '../typings'
import { createAgUiHttpAgent } from './authentication'

/** 返回 Endpoint 设计独立 AG-UI 路由地址。 */
function endpointDesignsUrl(): string {
  const baseUrl = window.xcodeAgent?.agentBaseUrl
  return baseUrl
    ? `${baseUrl.replace(/\/$/, '')}/endpoint-designs/run`
    : '/api/agent/endpoint-designs/run'
}

/** 从未知事件值中读取合法的 Endpoint 设计状态。 */
function readPayload(value: unknown): EndpointDesignsPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<EndpointDesignsPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) return undefined
  return payload as EndpointDesignsPayload
}

/** 从 AG-UI 状态快照读取 Endpoint 设计结果。 */
function readState(value: unknown): EndpointDesignsPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readPayload((value as { endpointDesigns?: unknown }).endpointDesigns)
}

/** 读取当前 Endpoint 的正式映射设计，查询动作完全独立于工作流。 */
export async function requestEndpointDesignDetail(
  workspaceRoot: string,
  apiContractId: string,
  endpointId: string
): Promise<EndpointDesignDetail> {
  const payload = await runEndpointDesignAction(
    'get',
    workspaceRoot,
    apiContractId,
    endpointId,
    undefined,
    '读取 Endpoint API 映射结果。'
  )
  if (!payload.detail) throw new Error('Endpoint 设计接口没有返回详情。')
  return payload.detail
}

/** 通过独立 AG-UI 接口准备字段映射弹窗的编辑数据。 */
export async function requestEndpointDesignPreparation(
  workspaceRoot: string,
  apiContractId: string,
  endpointId: string
): Promise<EndpointDesignPreparation> {
  const payload = await runEndpointDesignAction(
    'prepare',
    workspaceRoot,
    apiContractId,
    endpointId,
    undefined,
    '准备 Endpoint API 映射配置。'
  )
  if (!payload.preparation) throw new Error('Endpoint 设计接口没有返回编辑数据。')
  return payload.preparation
}

/** 通过独立 AG-UI 接口保存字段映射配置，不触发主工作流。 */
export async function saveEndpointDesign(
  workspaceRoot: string,
  action: WorkflowApiDesignAction,
  baseRevision?: string | null
): Promise<EndpointDesignSaveResult> {
  const payload = await runEndpointDesignAction(
    'save',
    workspaceRoot,
    action.apiContractId,
    action.endpointId,
    { draft: action.draft, baseRevision: baseRevision || undefined },
    '保存 Endpoint API 映射配置。'
  )
  if (!payload.saved) throw new Error('Endpoint 设计接口没有返回保存结果。')
  return payload.saved
}

/** 运行一次独立 Endpoint 设计 AG-UI 动作并收敛最终状态。 */
async function runEndpointDesignAction(
  action: 'get' | 'prepare' | 'save',
  workspaceRoot: string,
  apiContractId: string,
  endpointId: string,
  extra: Record<string, unknown> | undefined,
  messageContent: string
): Promise<EndpointDesignsPayload> {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: endpointDesignsUrl(), threadId })
  const message: Message = { id: randomUUID(), role: 'user', content: messageContent }
  agent.addMessage(message)
  let payload: EndpointDesignsPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'endpoint-designs') payload = readPayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readState(event.snapshot) ?? payload
    }
  }
  const result = await agent.runAgent({
    forwardedProps: {
      endpointDesigns: { action, workspaceRoot, apiContractId, endpointId, ...extra }
    }
  }, subscriber)
  payload = readState(result.result) ?? readPayload((result.result as { endpointDesigns?: unknown })?.endpointDesigns) ?? payload
  if (!payload) throw new Error('Endpoint 设计接口没有返回有效状态。')
  if (payload.status === 'failed') throw new Error(payload.error?.message || 'Endpoint 设计接口操作失败。')
  return payload
}
