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
import type { BindingDraft, BindingSelection } from '../typings/endpointDesign'
import type { MappingAssistAction, MappingAssistRequest, MappingAssistProgress, MappingAssistFailure } from '../typings/endpointDesignAssist'
import { createAgUiHttpAgent } from './authentication'
import { AgUiBusinessError } from './agUiBusinessError'
import { isRuntimeEndpointPreparation } from '../typings/endpointDesign'
import type { EndpointPreparation, RuntimeEndpointPreparation } from '../typings/endpointDesign'

/** 沿既有 AG-UI 端点读取正式准备结果，在任何业务来源逻辑之前确定编辑器类型。 */
export async function requestEndpointPreparation(workspace: string, api: string, endpoint: string): Promise<EndpointPreparation> {
  const result = await runEndpointDesignAction('prepare', workspace, api, endpoint, undefined, '读取接口设计。')
  const preparation = result.preparation
  if (!preparation) throw new Error('接口未返回设计准备结果。')
  if (preparation.apiContractId !== api || preparation.endpointId !== endpoint) throw new Error('接口设计结果与当前目标不一致，请重新读取。')
  if (isRuntimeEndpointPreparation(preparation)) return preparation
  if (preparation.payload.endpoint?.kind !== 'business') throw new Error('接口未返回明确的业务或智能体设计类型，请刷新当前技术规划。')
  return preparation
}

/** 运行接口沿同一官方 AG-UI 动作读取专用分支，不进入 CRUD 工作台。 */
export async function requestRuntimeEndpointPreparation(workspace: string, api: string, endpoint: string): Promise<RuntimeEndpointPreparation> {
  const preparation = await requestEndpointPreparation(workspace, api, endpoint)
  if (!isRuntimeEndpointPreparation(preparation)) throw new Error('接口未返回当前运行接入设计。')
  return preparation
}

/** 只提交完整候选及版本，不触发 DAG 或运行调用。 */
export async function confirmRuntimeEndpointDesign(workspace: string, preparation: RuntimeEndpointPreparation): Promise<EndpointDesignSaveResult> {
  const result = await runEndpointDesignAction('save', workspace, preparation.apiContractId, preparation.endpointId,
    { draft: preparation.payload.draft, baseRevision: preparation.artifactRevision || undefined, technicalPlanHash: preparation.technicalPlanHash }, '确认运行接口接入设计。')
  if (!result.saved) throw new Error('接口未返回接入设计保存结果。')
  return result.saved
}

/** 保留本轮失败阶段，仍按原 AG-UI 业务失败处理，不自动重放模型调用。 */
export class MappingAssistError extends AgUiBusinessError {
  /** 只携带服务端签发的短期恢复凭据，不接受客户端中间配置。 */
  constructor(message: string, public readonly failure: MappingAssistFailure) { super(message) }
}

/** 返回 Endpoint 设计独立 AG-UI 路由地址。 */
function endpointDesignsUrl(): string {
  const baseUrl = window.devAgentStudio?.agentBaseUrl
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
    !['completed', 'failed', 'in_progress'].includes(String(payload.status))
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
  const preparation = await requestEndpointPreparation(workspaceRoot, apiContractId, endpointId)
  if (isRuntimeEndpointPreparation(preparation)) throw new Error('当前为智能体流式接口，请使用智能体接入设计，不能配置业务字段映射。')
  return preparation
}

/** 通过独立 AG-UI 接口保存字段映射配置，不触发主工作流。 */
export async function saveEndpointDesign(
  workspaceRoot: string,
  action: WorkflowApiDesignAction,
  baseRevision?: string | null,
  binding?: { bindingSelection?: BindingSelection; technicalPlanHash: string }
): Promise<EndpointDesignSaveResult> {
  const payload = await runEndpointDesignAction(
    'save',
    workspaceRoot,
    action.apiContractId,
    action.endpointId,
    { draft: action.draft, baseRevision: baseRevision || undefined, ...binding },
    '保存 Endpoint API 映射配置。'
  )
  if (!payload.saved) throw new Error('Endpoint 设计接口没有返回保存结果。')
  return payload.saved
}

/** 运行一次独立 Endpoint 设计 AG-UI 动作并收敛最终状态。 */
async function runEndpointDesignAction(
  action: 'get' | 'prepare' | 'save' | 'save_draft' | 'discard_draft' | MappingAssistAction,
  workspaceRoot: string,
  apiContractId: string,
  endpointId: string,
  extra: Record<string, unknown> | undefined,
  messageContent: string,
  options: { signal?: AbortSignal; onProgress?: (progress: MappingAssistProgress) => void } = {}
): Promise<EndpointDesignsPayload> {
  const threadId = randomUUID()
  const agent = createAgUiHttpAgent({ url: endpointDesignsUrl(), threadId })
  const message: Message = { id: randomUUID(), role: 'user', content: messageContent }
  agent.addMessage(message)
  let payload: EndpointDesignsPayload | undefined
  let finished = false
  /** 进度只更新辅助运行状态，不覆盖最终业务结果。 */
  const accept = (value: EndpointDesignsPayload | undefined): void => {
    if (!value) return
    if (value.status === 'in_progress') { if (value.progress) options.onProgress?.(value.progress); return }
    payload = value
  }
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'endpoint-designs') accept(readPayload(event.value))
    },
    onStateSnapshotEvent: ({ event }) => {
      accept(readState(event.snapshot))
    },
    onRunFinishedEvent: () => { finished = true }
  }
  /** 取消当前独立请求，关闭 HTTP 流后由后端收口对应模型任务。 */
  const abort = (): void => agent.abortRun()
  options.signal?.addEventListener('abort', abort, { once: true })
  try {
    if (options.signal?.aborted) throw new DOMException('已取消 AI 辅助', 'AbortError')
    const result = await agent.runAgent({
      forwardedProps: {
        endpointDesigns: { action, workspaceRoot, apiContractId, endpointId, ...extra }
      }
    }, subscriber)
    if (options.signal?.aborted) throw new DOMException('已取消 AI 辅助', 'AbortError')
    accept(readState(result.result))
    if (!finished) throw new Error('Endpoint 设计连接已中断，请重新发起当前操作。')
    if (!payload) throw new Error('Endpoint 设计接口没有返回有效状态。')
    if (payload.status === 'failed') {
      const message = payload.error?.message || 'Endpoint 设计接口操作失败。'
      if (action === 'generate_mapping' && payload.assistFailure) throw new MappingAssistError(message, payload.assistFailure)
      throw new AgUiBusinessError(message)
    }
    return payload
  } finally { options.signal?.removeEventListener('abort', abort) }
}

/** 辅助调用复用独立 Endpoint AG-UI 流，不进入主 Workflow。 */
export async function requestMappingAssist(action: MappingAssistAction, request: MappingAssistRequest,
  extra: Record<string, unknown>, options: { signal?: AbortSignal; onProgress?: (progress: MappingAssistProgress) => void } = {}): Promise<EndpointDesignsPayload> {
  const { workspaceRoot, apiContractId, endpointId, ...input } = request
  return runEndpointDesignAction(action, workspaceRoot, apiContractId, endpointId, { ...input, ...extra },
    action === 'generate_mock_data' || action === 'apply_mock_data' ? '请求静态 Mock 数据辅助。' : '请求字段映射 AI 辅助。', options)
}

/** 持久化未确认草稿，既不生成正式产物也不推进主工作流。 */
export async function saveEndpointBindingDraft(workspaceRoot: string, value: BindingDraft): Promise<BindingDraft> {
  // 仅发送暂存请求声明的字段；草稿格式和保存时间属于后端返回的存储元数据。
  const input = {
    draft: value.draft,
    selection: value.selection,
    baseRevision: value.baseRevision,
    technicalPlanHash: value.technicalPlanHash
  }
  const result = await runEndpointDesignAction('save_draft', workspaceRoot, value.draft.apiContractId,
    value.draft.endpointId, input, '保存字段映射草稿。')
  if (!result.draft) throw new Error('接口未返回已保存草稿。')
  return result.draft
}

/** 用户明确放弃时只清理中间草稿，不修改已确认映射。 */
export async function discardEndpointBindingDraft(workspaceRoot: string, apiContractId: string, endpointId: string): Promise<void> {
  await runEndpointDesignAction('discard_draft', workspaceRoot, apiContractId, endpointId, undefined, '放弃字段映射草稿。')
}
