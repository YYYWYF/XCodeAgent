import { randomUUID } from '@ag-ui/client'
import type { DevelopmentArtifactProgress } from '../typings'
import { createAgUiHttpAgent } from './authentication'

export type PageArtifactTask = {
  id: string
  title: string
  description: string
  owner: string
  unitId: string
  scope: 'page' | 'dependency'
  status: string
  failureReason: string
  updatedAt: string
  dependencies: Array<{ id: string; title: string; inScope: boolean }>
}
export type PageArtifactEndpoint = {
  apiContractId: string
  endpointId: string
  name: string
  method: string
  path: string
  summary: string
  progress?: DevelopmentArtifactProgress | null
}
export type PageArtifactOverview = {
  page: {
    id: string
    name: string
    path: string
    description: string
    confirmationStatus: string
  }
  progress?: DevelopmentArtifactProgress | null
  taskPlanAvailable: boolean
  taskPlanConfirmedAt?: string
  tasks: PageArtifactTask[]
  files: Array<{ path: string; taskIds: string[] }>
  endpoints: PageArtifactEndpoint[]
  issues: { progress?: string; tasks?: string; endpoints?: string }
}

/** 使用官方 AG-UI 客户端独立读取页面事实，不运行模型或进入开发工作流。 */
export async function readPageArtifactOverview(
  workspaceRoot: string,
  pageId: string,
  signal?: AbortSignal
): Promise<PageArtifactOverview> {
  const base = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '') || '/api/agent'
  const agent = createAgUiHttpAgent({ url: `${base}/page-artifacts/run`, threadId: randomUUID() })
  /** 将视图取消传递到标准客户端，停止不再需要的只读请求。 */
  const abort = (): void => agent.abortRun()
  signal?.addEventListener('abort', abort, { once: true })
  try {
    if (signal?.aborted) throw new DOMException('已取消读取', 'AbortError')
    const result = await agent.runAgent({
      forwardedProps: { pageArtifact: { action: 'get', workspaceRoot, pageId } }
    })
    const payload = (
      result.result as {
        pageArtifact?: {
          status?: string
          overview?: PageArtifactOverview
          error?: { message?: string }
        }
      }
    )?.pageArtifact
    if (signal?.aborted) throw new DOMException('已取消读取', 'AbortError')
    if (payload?.status !== 'completed')
      throw new Error(payload?.error?.message || '页面概览读取失败。')
    const overview = payload.overview
    if (
      !overview ||
      overview.page?.id !== pageId ||
      !Array.isArray(overview.tasks) ||
      !Array.isArray(overview.files) ||
      !Array.isArray(overview.endpoints) ||
      !overview.issues
    ) {
      throw new Error('页面概览没有返回完整的当前页面数据。')
    }
    return overview
  } finally {
    signal?.removeEventListener('abort', abort)
  }
}
