import { randomUUID } from '@ag-ui/client'
import { createAgUiHttpAgent } from './authentication'

export type TaskRepairAction = 'get' | 'generate' | 'regenerate' | 'discard' | 'confirm'
export type BuildTaskRepairRequest = {
  action: TaskRepairAction
  workspaceRoot: string
  sourceRunId: string
  sourceThreadId: string
  sessionId: string
  taskId: string
  feedback?: string
  draftDigest?: string
}
export type RepairTaskView = {
  title: string
  description: string
  files: { path: string; operation: string; description: string }[]
  checks: string[]
}
export type BuildTaskRepairDraft = {
  status: 'pending'
  sourceRunId: string
  sourceThreadId: string
  sessionId: string
  taskId: string
  draftDigest: string
  reason: string
  feedback: string
  before: RepairTaskView
  after: RepairTaskView
}
export type RepairTaskAction = (
  taskId: string,
  action: TaskRepairAction,
  feedback?: string,
  draftDigest?: string
) => Promise<BuildTaskRepairDraft | null>

/** 使用独立 AG-UI 端点处理任务草稿，确认后的执行由标准 Workflow 流接管。 */
export function getBuildTaskRepairUrl(): string {
  const origin = window.devAgentStudio?.agentBaseUrl?.replace(/\/$/, '')
  return origin ? `${origin}/build-task-repair/run` : '/api/agent/build-task-repair/run'
}

/** 通过官方 SDK 消费完整生命周期，缺少成功回执时不假设草稿操作完成。 */
export async function runBuildTaskRepairAction(
  request: BuildTaskRepairRequest,
  onProgress?: (message: string) => void
): Promise<BuildTaskRepairDraft | null> {
  const agent = createAgUiHttpAgent({ url: getBuildTaskRepairUrl(), threadId: randomUUID() })
  let completed = false
  let failure = ''
  let draft: BuildTaskRepairDraft | null = null
  await agent.runAgent(
    { runId: randomUUID(), forwardedProps: { buildTaskRepair: request } },
    {
      onCustomEvent: ({ event }) => {
        if (event.name !== 'build-task-repair') return
        if (event.value?.status === 'failed') failure = event.value.error?.message || '任务修复失败。'
        if (event.value?.status === 'completed') {
          completed = true
          draft = event.value.draft || null
        }
        if (event.value?.message) onProgress?.(event.value.message)
      }
    }
  )
  if (failure || !completed) throw new Error(failure || '未收到任务修复回执，请重新读取草稿。')
  return draft
}
