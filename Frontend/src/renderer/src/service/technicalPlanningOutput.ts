import type { WorkflowRunPayload } from '../typings'
import type { TechnicalPlanningOutputEntry } from '../typings/technicalPlanningOutput'

const MAX_ENTRIES = 160
const MAX_CHARACTERS = 64_000

/** 消费 SDK 已解析的 CustomEvent 值，只接纳同一运行和线程的公开输出。 */
export function appendTechnicalPlanningOutput(
  workflow: WorkflowRunPayload | undefined,
  value: unknown
): WorkflowRunPayload | undefined {
  if (!workflow || !value || typeof value !== 'object' || Array.isArray(value)) return workflow
  const payload = value as Record<string, unknown>
  if (payload.runId !== workflow.runId || payload.threadId !== workflow.threadId ||
      !['text', 'activity', 'retry'].includes(String(payload.kind)) ||
      typeof payload.text !== 'string' || !payload.text || typeof payload.timestamp !== 'string') return workflow
  if (payload.kind !== 'text' && (typeof payload.id !== 'string' || !payload.id ||
      !['running', 'completed', 'failed'].includes(String(payload.status)))) return workflow

  const entries = [...(workflow.technicalPlanningOutput?.entries || [])]
  const last = entries.at(-1)
  if (payload.kind === 'text' && last?.kind === 'text') {
    entries[entries.length - 1] = { ...last, text: last.text + payload.text }
  } else {
    const entry: TechnicalPlanningOutputEntry = {
      id: payload.kind === 'text' ? `text-${payload.timestamp}-${entries.length}` : String(payload.id),
      kind: payload.kind as TechnicalPlanningOutputEntry['kind'],
      text: payload.text,
      timestamp: payload.timestamp,
      ...(payload.kind !== 'text' ? { status: payload.status as TechnicalPlanningOutputEntry['status'] } : {})
    }
    const index = payload.kind === 'text' ? -1 : entries.findIndex((item) => item.id === entry.id)
    if (index < 0) entries.push(entry)
    else entries[index] = { ...entry, timestamp: entries[index].timestamp }
  }
  // 输出只承担展示，限制本轮内存；裁剪旧内容不能影响 Graph 状态或正式文档。
  let truncated = workflow.technicalPlanningOutput?.truncated || false
  let characters = entries.reduce((total, item) => total + item.text.length, 0)
  while (entries.length > MAX_ENTRIES || (characters > MAX_CHARACTERS && entries.length > 1)) {
    characters -= entries.shift()!.text.length
    truncated = true
  }
  if (characters > MAX_CHARACTERS && entries[0]) {
    entries[0] = { ...entries[0], text: entries[0].text.slice(-MAX_CHARACTERS) }
    truncated = true
  }
  return { ...workflow, technicalPlanningOutput: { entries, truncated } }
}

/** 同一运行的后续业务快照保留已接收输出，新请求不得继承旧输出。 */
export function retainTechnicalPlanningOutput(
  previous: WorkflowRunPayload | undefined,
  incoming: WorkflowRunPayload
): WorkflowRunPayload {
  return previous?.runId === incoming.runId && previous.threadId === incoming.threadId &&
    previous.technicalPlanningOutput && !incoming.technicalPlanningOutput
    ? { ...incoming, technicalPlanningOutput: previous.technicalPlanningOutput }
    : incoming
}
