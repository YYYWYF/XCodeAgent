import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type {
  ApplicationPageContext,
  ApplicationPagePlan,
  ConfirmedPagePlan,
  PagePlanningAnswer,
  PagePlanningProgress,
  PagePlanningQuestion
} from '../typings'
import { createAgUiHttpAgent } from './authentication'

// 读取桌面端注入的后端地址，并为浏览器环境保留代理回退地址。
function getAgentBaseUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl ? agentBaseUrl.replace(/\/$/, '') : '/api/agent'
}

type PagePlanningAgUiPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'in_progress' | 'completed' | 'failed'
  action?: 'questions' | 'plan' | 'confirm'
  questions?: PagePlanningQuestion[]
  plan?: ApplicationPagePlan
  confirmation?: ConfirmedPagePlan
  progress?: PagePlanningProgress
  error?: { type?: string; message?: string }
}

// 返回独立页面规划 AG-UI 端点地址。
function getPagePlanningUrl(): string {
  return `${getAgentBaseUrl()}/application-page-planning/run`
}

// 校验页面规划自定义事件或状态快照的公共信封字段。
function readPagePlanningPayload(value: unknown): PagePlanningAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<PagePlanningAgUiPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['in_progress', 'completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as PagePlanningAgUiPayload
}

// 从 AG-UI 状态快照读取页面规划状态。
function readPagePlanningFromState(snapshot: unknown): PagePlanningAgUiPayload | undefined {
  if (!snapshot || typeof snapshot !== 'object') return undefined
  return readPagePlanningPayload((snapshot as { pagePlanning?: unknown }).pagePlanning)
}

// 从 AG-UI 最终结果读取页面规划状态。
function readPagePlanningFromResult(result: unknown): PagePlanningAgUiPayload | undefined {
  if (!result || typeof result !== 'object') return undefined
  return readPagePlanningPayload((result as { pagePlanning?: unknown }).pagePlanning)
}

// 运行一次页面规划动作，并把结构化阶段进度实时转发给界面。
async function runPagePlanningAgent(
  threadId: string,
  message: string,
  input: Record<string, unknown>,
  onProgress?: (progress: PagePlanningProgress) => void,
  onStreamingContent?: (content: string) => void
): Promise<PagePlanningAgUiPayload> {
  const agent = createAgUiHttpAgent({ url: getPagePlanningUrl(), threadId })
  const userMessage: Message = { id: randomUUID(), role: 'user', content: message }
  agent.addMessage(userMessage)

  let pagePlanning: PagePlanningAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'application-page-planning') {
        const nextPayload = readPagePlanningPayload(event.value)
        if (nextPayload?.progress) onProgress?.(nextPayload.progress)
        pagePlanning = nextPayload ?? pagePlanning
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      pagePlanning = readPagePlanningFromState(event.snapshot) ?? pagePlanning
    },
    onTextMessageContentEvent: ({ event, textMessageBuffer }) => {
      onStreamingContent?.(`${textMessageBuffer}${event.delta}`)
    },
    onTextMessageEndEvent: ({ textMessageBuffer }) => {
      onStreamingContent?.(textMessageBuffer)
    }
  }
  const result = await agent.runAgent(
    { forwardedProps: { pagePlanning: input } },
    subscriber
  )
  pagePlanning = readPagePlanningFromResult(result.result) ?? pagePlanning
  if (!pagePlanning) throw new Error('页面规划接口没有返回有效的 AG-UI 状态。')
  if (pagePlanning.status === 'failed') {
    throw new Error(pagePlanning.error?.message || '页面规划失败')
  }
  return pagePlanning
}

// 为一次新建应用规划会话创建稳定线程标识。
export function createPagePlanningThreadId(): string {
  return randomUUID()
}

// 请求决定页面与 API 设计所需的业务澄清问题。
export async function requestPagePlanningQuestions(
  application: ApplicationPageContext,
  threadId: string,
  onProgress?: (progress: PagePlanningProgress) => void,
  onStreamingContent?: (content: string) => void
): Promise<PagePlanningQuestion[]> {
  const response = await runPagePlanningAgent(
    threadId,
    `请根据「${application.name}」的应用场景提出页面规划细节问题。`,
    { action: 'questions', application },
    onProgress,
    onStreamingContent
  )
  if (!response.questions?.length) throw new Error('模型没有返回细节问题。')
  return response.questions
}

// 请求生成或修订可审核的页面、交互及 API 设计方案。
export async function requestApplicationPagePlan(
  application: ApplicationPageContext,
  answers: PagePlanningAnswer[],
  threadId: string,
  revision?: { currentPlan: ApplicationPagePlan; feedback: string },
  onProgress?: (progress: PagePlanningProgress) => void,
  onStreamingContent?: (content: string) => void
): Promise<ApplicationPagePlan> {
  const response = await runPagePlanningAgent(
    threadId,
    '请根据我的回答生成可审核的页面结构。',
    { action: 'plan', application, answers, ...revision },
    onProgress,
    onStreamingContent
  )
  if (!response.plan) throw new Error('模型没有返回页面结构。')
  return response.plan
}

// 用户确认后请求后端原子保存页面菜单与 API 设计。
export async function confirmApplicationPagePlan(
  workspaceRoot: string,
  plan: ApplicationPagePlan,
  threadId: string,
  onProgress?: (progress: PagePlanningProgress) => void
): Promise<ConfirmedPagePlan> {
  const response = await runPagePlanningAgent(threadId, '我确认使用这个页面结构。', {
    action: 'confirm',
    workspaceRoot,
    plan
  }, onProgress)
  if (!response.confirmation) throw new Error('页面规划接口没有返回确认结果。')
  return response.confirmation
}
