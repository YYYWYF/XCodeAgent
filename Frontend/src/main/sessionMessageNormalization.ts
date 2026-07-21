type JsonRecord = Record<string, unknown>

const MESSAGE_APPROVAL_STATUSES = new Set([
  'pending',
  'approved_once',
  'approved_always',
  'feedback'
])
const PROCESS_STEP_KINDS = new Set(['reasoning', 'tool', 'command', 'workflow'])
const PROCESS_STEP_STATUSES = new Set(['running', 'completed', 'failed'])
const CHECK_STATUSES = new Set(['running', 'passed', 'skipped', 'failed'])

/** 规范化单条会话消息，并保留可恢复 Agent 执行界面的扩展字段。 */
export function normalizePersistentSessionMessage(message: JsonRecord): JsonRecord {
  const normalizedMessage = {
    id: Number(message.id || Date.now()),
    role: message.role === 'assistant' ? 'assistant' : 'user',
    content: String(message.content || ''),
    createdAt: Number(message.createdAt || Date.now())
  }
  const orchestration = cloneJsonRecord(message.orchestration)
  const approval = cloneJsonRecord(message.approval)
  const codeChanges = cloneJsonRecord(message.codeChanges)
  const workflow = cloneJsonRecord(message.workflow)
  const skills = normalizeSessionMessageSkills(message.skills)
  const toolCalls = normalizeSessionToolCalls(message.toolCalls)
  const processSteps = normalizeSessionProcessSteps(message.processSteps)
  const approvalStatus =
    typeof message.approvalStatus === 'string' &&
    MESSAGE_APPROVAL_STATUSES.has(message.approvalStatus)
      ? message.approvalStatus
      : undefined

  return {
    ...normalizedMessage,
    ...(orchestration ? { orchestration } : {}),
    ...(approval ? { approval } : {}),
    ...(approvalStatus ? { approvalStatus } : {}),
    ...(codeChanges ? { codeChanges } : {}),
    ...(workflow ? { workflow } : {}),
    ...(skills.length > 0 ? { skills } : {}),
    ...(toolCalls.length > 0 ? { toolCalls } : {}),
    ...(processSteps.length > 0 ? { processSteps } : {})
  }
}

/** 深拷贝可序列化 JSON 对象，无法序列化时返回空值。 */
function cloneJsonRecord(value: unknown): JsonRecord | undefined {
  if (!isJsonRecord(value)) return undefined
  try {
    return JSON.parse(JSON.stringify(value)) as JsonRecord
  } catch {
    return undefined
  }
}

/** 判断未知值是否为非数组 JSON 对象。 */
function isJsonRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/** 规范化用户消息携带的技能名称和描述快照。 */
function normalizeSessionMessageSkills(value: unknown): JsonRecord[] {
  if (!Array.isArray(value)) return []
  const names = new Set<string>()
  return value
    .filter(isJsonRecord)
    .map((item) => ({
      name: typeof item.name === 'string' ? item.name.trim() : '',
      description: typeof item.description === 'string' ? item.description.trim() : ''
    }))
    .filter((item) => {
      if (!item.name || names.has(item.name)) return false
      names.add(item.name)
      return true
    })
}

/** 规范化工具调用快照，避免未知字段进入会话文件。 */
function normalizeSessionToolCalls(value: unknown): JsonRecord[] {
  if (!Array.isArray(value)) return []
  return value
    .filter(isJsonRecord)
    .map((item) => ({
      id: stringValue(item.id),
      name: stringValue(item.name) || 'unknown',
      args: stringValue(item.args).slice(-24_000),
      ...(typeof item.result === 'string' ? { result: item.result.slice(-24_000) } : {}),
      status: item.status === 'completed' ? 'completed' : 'running'
    }))
    .filter((item) => item.id)
}

/** 规范化 Agent 步骤快照及其集成测试检查项。 */
function normalizeSessionProcessSteps(value: unknown): JsonRecord[] {
  if (!Array.isArray(value)) return []
  const steps: JsonRecord[] = []
  for (const valueItem of value) {
    if (!isJsonRecord(valueItem)) continue
    const kind = stringValue(valueItem.kind)
    const status = stringValue(valueItem.status)
    const id = stringValue(valueItem.id)
    if (!id || !PROCESS_STEP_KINDS.has(kind) || !PROCESS_STEP_STATUSES.has(status)) continue
    const checks = normalizeSessionChecks(valueItem.checks)
    steps.push({
      id,
      kind,
      status,
      title: stringValue(valueItem.title),
      detail: stringValue(valueItem.detail).slice(-24_000),
      ...(typeof valueItem.result === 'string' ? { result: valueItem.result.slice(-24_000) } : {}),
      sequence: typeof valueItem.sequence === 'number' ? valueItem.sequence : 0,
      appendDetail: valueItem.appendDetail === true,
      ...(checks.length > 0 ? { checks } : {})
    })
  }
  return steps
}

/** 规范化集成测试检查项，只持久化安全的可见摘要。 */
function normalizeSessionChecks(value: unknown): JsonRecord[] {
  if (!Array.isArray(value)) return []
  const ids = new Set<string>()
  return value
    .filter(isJsonRecord)
    .map((item) => ({
      id: stringValue(item.id),
      name: stringValue(item.name),
      status: stringValue(item.status),
      required: item.required === true,
      ...(typeof item.evidence === 'string' ? { evidence: item.evidence.slice(0, 1_000) } : {})
    }))
    .filter((item) => {
      if (!item.id || !item.name || !CHECK_STATUSES.has(item.status) || ids.has(item.id)) {
        return false
      }
      ids.add(item.id)
      return true
    })
}

/** 将未知值安全转换为字符串。 */
function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}
