import { HttpAgent, randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type {
  ApplicationPageContext,
  ApplicationPagePlan,
  ConfirmedPagePlan,
  PagePlanningAnswer,
  PagePlanningQuestion
} from '../typings'

function getAgentBaseUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl ? agentBaseUrl.replace(/\/$/, '') : '/api/agent'
}

type PagePlanningAgUiPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'questions' | 'plan' | 'confirm'
  questions?: PagePlanningQuestion[]
  plan?: ApplicationPagePlan
  confirmation?: ConfirmedPagePlan
  error?: { type?: string; message?: string }
}

function getPagePlanningUrl(): string {
  return `${getAgentBaseUrl()}/application-page-planning/run`
}

function readPagePlanningPayload(value: unknown): PagePlanningAgUiPayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<PagePlanningAgUiPayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as PagePlanningAgUiPayload
}

function readPagePlanningFromState(snapshot: unknown): PagePlanningAgUiPayload | undefined {
  if (!snapshot || typeof snapshot !== 'object') return undefined
  return readPagePlanningPayload((snapshot as { pagePlanning?: unknown }).pagePlanning)
}

function readPagePlanningFromResult(result: unknown): PagePlanningAgUiPayload | undefined {
  if (!result || typeof result !== 'object') return undefined
  return readPagePlanningPayload((result as { pagePlanning?: unknown }).pagePlanning)
}

async function runPagePlanningAgent(
  threadId: string,
  message: string,
  input: Record<string, unknown>
): Promise<PagePlanningAgUiPayload> {
  const agent = new HttpAgent({ url: getPagePlanningUrl(), threadId })
  const userMessage: Message = { id: randomUUID(), role: 'user', content: message }
  agent.addMessage(userMessage)

  let pagePlanning: PagePlanningAgUiPayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name === 'application-page-planning') {
        pagePlanning = readPagePlanningPayload(event.value) ?? pagePlanning
      }
    },
    onStateSnapshotEvent: ({ event }) => {
      pagePlanning = readPagePlanningFromState(event.snapshot) ?? pagePlanning
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

export function createPagePlanningThreadId(): string {
  return randomUUID()
}

export async function requestPagePlanningQuestions(
  application: ApplicationPageContext,
  threadId: string
): Promise<PagePlanningQuestion[]> {
  const response = await runPagePlanningAgent(
    threadId,
    `请根据「${application.name}」的应用场景提出页面规划细节问题。`,
    { action: 'questions', application }
  )
  if (!response.questions?.length) throw new Error('模型没有返回细节问题。')
  return response.questions
}

export async function requestApplicationPagePlan(
  application: ApplicationPageContext,
  answers: PagePlanningAnswer[],
  threadId: string,
  revision?: { currentPlan: ApplicationPagePlan; feedback: string }
): Promise<ApplicationPagePlan> {
  const response = await runPagePlanningAgent(
    threadId,
    '请根据我的回答生成可审核的页面结构。',
    { action: 'plan', application, answers, ...revision }
  )
  if (!response.plan) throw new Error('模型没有返回页面结构。')
  return response.plan
}

export async function confirmApplicationPagePlan(
  workspaceRoot: string,
  plan: ApplicationPagePlan,
  threadId: string
): Promise<ConfirmedPagePlan> {
  const response = await runPagePlanningAgent(threadId, '我确认使用这个页面结构。', {
    action: 'confirm',
    workspaceRoot,
    plan
  })
  if (!response.confirmation) throw new Error('页面规划接口没有返回确认结果。')
  return response.confirmation
}
