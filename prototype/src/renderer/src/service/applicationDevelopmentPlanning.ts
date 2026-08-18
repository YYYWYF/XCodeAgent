import { randomUUID } from '@ag-ui/client'
import type {
  ApplicationDevelopmentPlan,
  ConfirmedDevelopmentPlan,
  DevelopmentPlanningAnswer,
  DevelopmentPlanningProgress,
  DevelopmentPlanningQuestion
} from '../typings'
import { runAgUiAction, type AgUiPayloadEnvelope } from './agUiClient'

type DevelopmentPlanningPayload = AgUiPayloadEnvelope & {
  status: 'in_progress' | 'completed' | 'failed'
  action?: 'plan' | 'confirm'
  questions?: DevelopmentPlanningQuestion[]
  plan?: ApplicationDevelopmentPlan
  confirmation?: ConfirmedDevelopmentPlan
  progress?: DevelopmentPlanningProgress
  error?: { message?: string }
}

const DEVELOPMENT_PLANNING_STATUS_LIST = ['in_progress', 'completed', 'failed'] as const
const DEVELOPMENT_PLANNING_EVENT = 'application-development-planning'
const DEVELOPMENT_PLANNING_KEY = 'developmentPlanning'

// 读取桌面端后端地址，并为浏览器开发环境保留代理地址。
function getDevelopmentPlanningUrl(): string {
  const baseUrl = window.xcodeAgent?.agentBaseUrl?.replace(/\/$/, '') || '/api/agent'
  return `${baseUrl}/application-development-planning/run`
}

// 运行一次独立开发计划动作，并实时转发阶段与模型文本。
// payload 合并/校验/失败抛错由 runAgUiAction 处理；progress 仅随 customEvent 推进，流式文本随 onTextDelta。
async function runDevelopmentPlanningAgent(
  threadId: string,
  message: string,
  input: Record<string, unknown>,
  onProgress?: (progress: DevelopmentPlanningProgress) => void,
  onStreamingContent?: (content: string) => void
): Promise<DevelopmentPlanningPayload> {
  return runAgUiAction<DevelopmentPlanningPayload>({
    url: getDevelopmentPlanningUrl(),
    threadId,
    message,
    eventName: DEVELOPMENT_PLANNING_EVENT,
    stateKey: DEVELOPMENT_PLANNING_KEY,
    forwardedProps: { [DEVELOPMENT_PLANNING_KEY]: input },
    statusList: DEVELOPMENT_PLANNING_STATUS_LIST,
    onCustomEventPayload: (next) => {
      if (next.progress) onProgress?.(next.progress)
    },
    onTextDelta: onStreamingContent,
    emptyMessage: '开发计划接口没有返回有效的 AG-UI 状态。',
    failedMessage: '生成开发计划失败'
  })
}

// 为工作台开发计划会话创建稳定线程标识。
export function createDevelopmentPlanningThreadId(): string {
  return randomUUID()
}

// 请求模型生成澄清问题或完整应用开发计划。
export async function requestApplicationDevelopmentPlan(
  workspaceRoot: string,
  selectedPageKey: string,
  answers: DevelopmentPlanningAnswer[],
  threadId: string,
  onProgress?: (progress: DevelopmentPlanningProgress) => void,
  onStreamingContent?: (content: string) => void
): Promise<{ questions?: DevelopmentPlanningQuestion[]; plan?: ApplicationDevelopmentPlan }> {
  const response = await runDevelopmentPlanningAgent(
    threadId,
    `请基于当前 application.json 生成页面 ${selectedPageKey} 的应用开发计划。`,
    { action: 'plan', workspaceRoot, selectedPageKey, answers },
    onProgress,
    onStreamingContent
  )
  if (!response.questions?.length && !response.plan) {
    throw new Error('模型没有返回问题或开发计划。')
  }
  return { questions: response.questions, plan: response.plan }
}

// 用户确认后把完整任务清单原子写入 application.json。
export async function confirmApplicationDevelopmentPlan(
  workspaceRoot: string,
  selectedPageKey: string,
  plan: ApplicationDevelopmentPlan,
  threadId: string,
  onProgress?: (progress: DevelopmentPlanningProgress) => void
): Promise<ConfirmedDevelopmentPlan> {
  const response = await runDevelopmentPlanningAgent(
    threadId,
    '我确认使用这个页面开发计划。',
    { action: 'confirm', workspaceRoot, selectedPageKey, plan },
    onProgress
  )
  if (!response.confirmation) throw new Error('开发计划接口没有返回确认结果。')
  return response.confirmation
}
