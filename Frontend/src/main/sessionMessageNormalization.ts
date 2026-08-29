type JsonRecord = Record<string, unknown>

const MESSAGE_APPROVAL_STATUSES = new Set([
  'pending',
  'approved_once',
  'approved_always',
  'feedback'
])
const PROCESS_STEP_KINDS = new Set(['reasoning', 'tool', 'command', 'workflow'])
const PROCESS_STEP_STATUSES = new Set(['running', 'completed', 'failed', 'requires_user_input'])
const CHECK_STATUSES = new Set(['running', 'passed', 'skipped', 'failed'])
const DAG_STAGE_STATUSES = new Set(['pending', 'running', 'completed', 'failed'])

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
  const revisionHandoff = normalizeSessionRevisionHandoff(message.revisionHandoff)
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
    ...(processSteps.length > 0 ? { processSteps } : {}),
    ...(revisionHandoff ? { revisionHandoff } : {})
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

/** 只保留来源会话跳转所需的本地 session/thread 与 impact 身份。 */
function normalizeSessionRevisionHandoff(value: unknown): JsonRecord | undefined {
  if (
    !isJsonRecord(value) ||
    !['formal_revision', 'revision_development'].includes(stringValue(value.kind))
  ) return undefined
  const formalBranch = stringValue(value.formalBranch).trim()
  const targetSessionId = stringValue(value.targetSessionId).trim().slice(0, 512)
  const targetConversationThreadId = stringValue(value.targetConversationThreadId)
    .trim()
    .slice(0, 512)
  const impactInteractionId = stringValue(value.impactInteractionId).trim().slice(0, 256)
  const changeId = stringValue(value.changeId).trim().slice(0, 256)
  const request = stringValue(value.request).trim().slice(0, 16_000)
  if (
    !['design_stage_revision', 'workbench_plan_revision'].includes(formalBranch) ||
    !targetSessionId ||
    !targetConversationThreadId ||
    !impactInteractionId ||
    !request
  ) {
    return undefined
  }
  if (value.kind === 'revision_development' && !changeId) return undefined
  return {
    kind: value.kind,
    formalBranch,
    targetSessionId,
    targetConversationThreadId,
    impactInteractionId,
    ...(changeId ? { changeId } : {}),
    request
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
    const dagGeneration = normalizeSessionDagGeneration(valueItem.dagGeneration)
    const workspaceInspection = normalizeSessionWorkspaceInspection(valueItem.workspaceInspection)
    steps.push({
      id,
      kind,
      status,
      title: stringValue(valueItem.title),
      detail: stringValue(valueItem.detail).slice(-24_000),
      ...(typeof valueItem.result === 'string' ? { result: valueItem.result.slice(-24_000) } : {}),
      sequence: typeof valueItem.sequence === 'number' ? valueItem.sequence : 0,
      appendDetail: valueItem.appendDetail === true,
      ...(checks.length > 0 ? { checks } : {}),
      ...(dagGeneration ? { dagGeneration } : {}),
      ...(workspaceInspection ? { workspaceInspection } : {})
    })
  }
  return steps
}

/** 规范化工作区检查快照，仅持久化安全计数、标签和相对路径。 */
function normalizeSessionWorkspaceInspection(value: unknown): JsonRecord | undefined {
  if (!isJsonRecord(value)) return undefined
  const manifest = isJsonRecord(value.fileManifest) ? value.fileManifest : {}
  const codeGraph = isJsonRecord(value.codeGraph) ? value.codeGraph : {}
  const projectRoots = normalizeSessionWorkspacePaths(value.projectRoots, 40)
  const entrypoints = normalizeSessionWorkspacePaths(value.entrypoints, 80)
  const schemaVersion = stringValue(value.schemaVersion).slice(0, 80)
  const revision = stringValue(value.revision).slice(0, 80)
  if (
    !schemaVersion &&
    !revision &&
    Object.keys(manifest).length === 0 &&
    projectRoots.length === 0
  ) {
    return undefined
  }
  return {
    schemaVersion,
    revision,
    cacheHit: value.cacheHit === true,
    fileManifest: {
      totalFiles: nonNegativeSessionInteger(manifest.totalFiles),
      sourceFiles: nonNegativeSessionInteger(manifest.sourceFiles),
      truncated: manifest.truncated === true
    },
    techStack: normalizeSessionStringList(value.techStack, 40, 160),
    projectRoots,
    entrypoints,
    codeGraph: {
      provider: stringValue(codeGraph.provider).slice(0, 80) || 'none',
      available: codeGraph.available === true
    }
  }
}

/** 过滤持久化快照中的绝对路径和父目录跳转。 */
function normalizeSessionWorkspacePaths(value: unknown, limit: number): JsonRecord[] {
  if (!Array.isArray(value)) return []
  const paths: JsonRecord[] = []
  for (const item of value) {
    if (!isJsonRecord(item)) continue
    const path = stringValue(item.path).trim().slice(0, 1_000).replaceAll('\\', '/')
    if (
      !path ||
      path.startsWith('/') ||
      /^[a-z]:\//i.test(path) ||
      path.split('/').includes('..')
    ) {
      continue
    }
    paths.push({
      path,
      kind: stringValue(item.kind).trim().slice(0, 80) || 'unknown'
    })
    if (paths.length >= limit) break
  }
  return paths
}

/** 规范化 DAG 生成快照，仅持久化阶段、任务摘要和安全产物标签。 */
function normalizeSessionDagGeneration(value: unknown): JsonRecord | undefined {
  if (!isJsonRecord(value) || !Array.isArray(value.stages)) return undefined
  const stages = value.stages
    .filter(isJsonRecord)
    .map((stage) => ({
      id: stringValue(stage.id).slice(0, 240),
      name: stringValue(stage.name).slice(0, 500),
      status: stringValue(stage.status),
      detail: stringValue(stage.detail).slice(0, 1_000)
    }))
    .filter((stage) => stage.id && stage.name && DAG_STAGE_STATUSES.has(stage.status))
  if (stages.length === 0) return undefined

  const tasks = Array.isArray(value.tasks)
    ? value.tasks
        .filter(isJsonRecord)
        .map((task) => ({
          id: stringValue(task.id).slice(0, 240),
          title: stringValue(task.title).slice(0, 500),
          owner: stringValue(task.owner).slice(0, 80),
          status: DAG_STAGE_STATUSES.has(stringValue(task.status))
            ? stringValue(task.status)
            : 'pending',
          dependencies: normalizeSessionStringList(task.dependencies, 200, 240),
          changePaths: normalizeSessionStringList(task.changePaths, 200, 1_000),
          acceptanceCriteria: normalizeSessionStringList(task.acceptanceCriteria, 100, 1_000)
        }))
        .filter((task) => task.id && task.title)
    : []
  const summary = isJsonRecord(value.summary) ? value.summary : {}
  const artifacts = Array.isArray(value.artifacts)
    ? value.artifacts
        .filter(isJsonRecord)
        .map((artifact) => ({
          id: stringValue(artifact.id).slice(0, 240),
          name: stringValue(artifact.name).slice(0, 500),
          kind: artifact.kind === 'markdown' ? 'markdown' : 'internal',
          status: 'saved',
          ...(artifact.kind === 'markdown' && typeof artifact.path === 'string'
            ? { path: artifact.path.slice(0, 1_000) }
            : {})
        }))
        .filter((artifact) => artifact.id && artifact.name)
    : []

  return {
    stages,
    tasks,
    summary: {
      unitCount: nonNegativeSessionInteger(summary.unitCount),
      taskCount: nonNegativeSessionInteger(summary.taskCount),
      edgeCount: nonNegativeSessionInteger(summary.edgeCount),
      batchCount: nonNegativeSessionInteger(summary.batchCount),
      frontendCount: nonNegativeSessionInteger(summary.frontendCount),
      dataSourceCount: nonNegativeSessionInteger(summary.dataSourceCount),
      isValid: summary.isValid === true
    },
    artifacts
  }
}

/** 裁剪并去重 DAG 快照中的字符串列表。 */
function normalizeSessionStringList(
  value: unknown,
  itemLimit: number,
  textLimit: number
): string[] {
  if (!Array.isArray(value)) return []
  return [
    ...new Set(value.map((item) => stringValue(item).trim().slice(0, textLimit)).filter(Boolean))
  ].slice(0, itemLimit)
}

/** 把 DAG 摘要字段转换为非负整数。 */
function nonNegativeSessionInteger(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.min(1_000_000, Math.max(0, Math.trunc(value)))
    : 0
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
