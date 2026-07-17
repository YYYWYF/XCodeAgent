import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type { ApplicationDevelopmentPlan, ConfirmedDevelopmentPlan, DevelopmentPlanningAnswer, DevelopmentPlanningProgress, DevelopmentPlanningQuestion } from '../typings'
import { createAgUiHttpAgent } from './authentication'

type DevelopmentPlanningPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'in_progress' | 'completed' | 'failed'
  action?: 'plan' | 'confirm'
  questions?: DevelopmentPlanningQuestion[]
  plan?: ApplicationDevelopmentPlan
  confirmation?: ConfirmedDevelopmentPlan
  progress?: DevelopmentPlanningProgress
  error?: { message?: string }
}

// 读取桌面端后端地址，并为浏览器开发环境保留代理地址。
function getDevelopmentPlanningUrl(): string {
  const baseUrl = window.xcodeAgent?.agentBaseUrl?.replace(/\/$/, '') || '/api/agent'
  return `${baseUrl}/application-development-planning/run`
}

// 校验开发计划 AG-UI 自定义事件的公共信封字段。
function readPayload(value: unknown): DevelopmentPlanningPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<DevelopmentPlanningPayload>
  if (payload.schemaVersion !== 1 || typeof payload.runId !== 'string' || typeof payload.threadId !== 'string' || !['in_progress', 'completed', 'failed'].includes(String(payload.status))) return undefined
  return payload as DevelopmentPlanningPayload
}

// 从 AG-UI 状态快照中读取开发计划状态。
function readState(snapshot: unknown): DevelopmentPlanningPayload | undefined {
  if (!snapshot || typeof snapshot !== 'object') return undefined
  return readPayload((snapshot as { developmentPlanning?: unknown }).developmentPlanning)
}

// 从 AG-UI 最终运行结果中读取开发计划状态。
function readResult(result: unknown): DevelopmentPlanningPayload | undefined {
  if (!result || typeof result !== 'object') return undefined
  return readPayload((result as { developmentPlanning?: unknown }).developmentPlanning)
}

// 运行一次独立开发计划动作，并实时转发阶段与模型文本。
async function runDevelopmentPlanningAgent(threadId: string, message: string, input: Record<string, unknown>, onProgress?: (progress: DevelopmentPlanningProgress) => void, onStreamingContent?: (content: string) => void): Promise<DevelopmentPlanningPayload> {
  const agent = createAgUiHttpAgent({ url: getDevelopmentPlanningUrl(), threadId })
  const userMessage: Message = { id: randomUUID(), role: 'user', content: message }
  agent.addMessage(userMessage)
  let payload: DevelopmentPlanningPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'application-development-planning') return
      const next = readPayload(event.value)
      if (next?.progress) onProgress?.(next.progress)
      payload = next ?? payload
    },
    onStateSnapshotEvent: ({ event }) => { payload = readState(event.snapshot) ?? payload },
    onTextMessageContentEvent: ({ event, textMessageBuffer }) => { onStreamingContent?.(`${textMessageBuffer}${event.delta}`) },
    onTextMessageEndEvent: ({ textMessageBuffer }) => { onStreamingContent?.(textMessageBuffer) }
  }
  const result = await agent.runAgent({ forwardedProps: { developmentPlanning: input } }, subscriber)
  payload = readResult(result.result) ?? payload
  if (!payload) throw new Error('开发计划接口没有返回有效的 AG-UI 状态。')
  if (payload.status === 'failed') throw new Error(payload.error?.message || '生成开发计划失败')
  return payload
}

// 为工作台开发计划会话创建稳定线程标识。
export function createDevelopmentPlanningThreadId(): string { return randomUUID() }

// 请求模型生成澄清问题或完整应用开发计划。
export async function requestApplicationDevelopmentPlan(workspaceRoot: string, selectedPageKey: string, answers: DevelopmentPlanningAnswer[], threadId: string, onProgress?: (progress: DevelopmentPlanningProgress) => void, onStreamingContent?: (content: string) => void): Promise<{ questions?: DevelopmentPlanningQuestion[]; plan?: ApplicationDevelopmentPlan }> {
  const response = await runDevelopmentPlanningAgent(threadId, `请基于当前 application.json 生成页面 ${selectedPageKey} 的应用开发计划。`, { action: 'plan', workspaceRoot, selectedPageKey, answers }, onProgress, onStreamingContent)
  if (!response.questions?.length && !response.plan) throw new Error('模型没有返回问题或开发计划。')
  return { questions: response.questions, plan: response.plan }
}

// 用户确认后把完整任务清单原子写入 application.json。
export async function confirmApplicationDevelopmentPlan(workspaceRoot: string, selectedPageKey: string, plan: ApplicationDevelopmentPlan, threadId: string, onProgress?: (progress: DevelopmentPlanningProgress) => void): Promise<ConfirmedDevelopmentPlan> {
  const response = await runDevelopmentPlanningAgent(threadId, '我确认使用这个页面开发计划。', { action: 'confirm', workspaceRoot, selectedPageKey, plan }, onProgress)
  if (!response.confirmation) throw new Error('开发计划接口没有返回确认结果。')
  return response.confirmation
}
