import { createHash } from 'node:crypto'
import { promises as fs } from 'node:fs'
import path from 'node:path'

export type WorkbenchAgentOption = {
  key: string
  agentId: string
  label: string
  purpose: string
  boundaries: string[]
  capabilities: Array<{
    capabilityId: string
    name: string
    expectedResult: string
    toolIds: string[]
  }>
  entryPageIds: string[]
  entryActions: Array<{ pageId: string; pageLabel: string; actionIds: string[] }>
  interaction: Record<string, unknown>
  contractHash: string
  technicalPlanSha256: string
  agentSettings: Record<string, Record<string, unknown>>
  dependencies: {
    gateway: Record<string, unknown>
    tools: Array<Record<string, unknown>>
    entities: Array<Record<string, unknown>>
    pages: Array<Record<string, unknown>>
    runtime: Record<string, unknown>
  }
  runtime: Record<string, unknown>
  security: Record<string, unknown>
  artifacts: Array<{ kind: 'agent' | 'tool_adapter' | 'test'; path: string; exists: boolean }>
  requiredChecks: string[]
  taskSummary?: {
    total: number
    pending: number
    running: number
    completed: number
    failed: number
  }
}

/** 从当前 ProductPlan 与 TechnicalPlan 生成开发工作台的只读 Agent 投影。 */
export async function projectWorkbenchAgents(
  workspaceRoot: string,
  productPlan: Record<string, unknown>,
  technicalPlan: Record<string, unknown>,
  buildTaskPlan?: Record<string, unknown>
): Promise<{ agents: WorkbenchAgentOption[]; invalid: string[] }> {
  const productAgents = recordItems(productPlan.agents)
  const contracts = recordItems(technicalPlan.agent_contracts)
  const invalid: string[] = []
  const agents: WorkbenchAgentOption[] = []
  const technicalPlanSha256 = await fileSha256(
    path.join(workspaceRoot, '.xcodeagent', 'plans', 'technical-plan.json')
  )
  for (const productAgent of productAgents) {
    const agentId = String(productAgent.agentId || '').trim()
    const matches = contracts.filter((item) => String(item.agentId || '').trim() === agentId)
    if (!agentId || matches.length !== 1) {
      invalid.push(`plans/technical-plan.json#agent_contracts:${agentId || 'unknown'}`)
      continue
    }
    agents.push(
      await projectAgent(
        workspaceRoot,
        productPlan,
        technicalPlan,
        productAgent,
        matches[0],
        buildTaskPlan,
        technicalPlanSha256
      )
    )
  }
  const productIds = new Set(productAgents.map((item) => String(item.agentId || '').trim()))
  contracts.forEach((contract) => {
    const agentId = String(contract.agentId || '').trim()
    if (!agentId || !productIds.has(agentId)) {
      invalid.push(`plans/product-plan.json#agents:${agentId || 'unknown'}`)
    }
  })
  return { agents, invalid: [...new Set(invalid)] }
}

/** 投射单个 Agent 的七段 Settings、依赖、产物状态和任务摘要。 */
async function projectAgent(
  workspaceRoot: string,
  productPlan: Record<string, unknown>,
  technicalPlan: Record<string, unknown>,
  productAgent: Record<string, unknown>,
  contract: Record<string, unknown>,
  buildTaskPlan: Record<string, unknown> | undefined,
  technicalPlanSha256: string
): Promise<WorkbenchAgentOption> {
  const agentId = String(contract.agentId || '').trim()
  const identity = record(contract.identity)
  const invocation = record(contract.invocation)
  const settings = record(contract.agentSettings)
  const tools = record(settings.tools)
  const pages = recordItems(productPlan.pages)
  const apiContracts = recordItems(technicalPlan.api_contracts)
  const entryPageIds = stringItems(productAgent.entryPageIds)
  const entryActions = recordItems(productAgent.pageActionBindings).map((binding) => {
    const pageId = String(binding.pageId || '').trim()
    const page = pages.find((item) => String(item.pageId || '').trim() === pageId)
    return {
      pageId,
      pageLabel: String(page?.name || page?.label || pageId),
      actionIds: stringItems(binding.actionIds)
    }
  })
  const gatewayId = String(invocation.gatewayEndpointId || '').trim()
  const gateway = findEndpoint(apiContracts, gatewayId)
  const toolDependencies = recordItems(tools.bindings).map((binding) => ({
    toolId: String(binding.toolId || ''),
    name: String(binding.name || binding.toolId || ''),
    accessMode: String(binding.accessMode || ''),
    endpoint: record(binding.endpoint)
  }))
  const entityIds = new Set<string>()
  toolDependencies.forEach((tool) => {
    const resolved = findEndpoint(apiContracts, String(tool.endpoint.endpointId || '').trim())
    stringItems(resolved.apiContract?.entity_ids).forEach((entityId) => entityIds.add(entityId))
  })
  const hash = contractHash(contract)
  return {
    key: `agent:${agentId}`,
    agentId,
    label: String(identity.name || productAgent.name || agentId),
    purpose: String(identity.purpose || productAgent.purpose || ''),
    boundaries: stringItems(identity.boundaries || productAgent.boundaries),
    capabilities: recordItems(contract.capabilities).map((item) => ({
      capabilityId: String(item.capabilityId || ''),
      name: String(item.name || ''),
      expectedResult: String(item.expectedResult || ''),
      toolIds: stringItems(item.toolIds)
    })),
    entryPageIds,
    entryActions,
    interaction: record(contract.interaction),
    contractHash: hash,
    technicalPlanSha256,
    agentSettings: Object.fromEntries(
      ['prompt', 'model', 'memory', 'tools', 'skills', 'knowledge', 'context'].map((key) => [
        key,
        record(settings[key])
      ])
    ),
    dependencies: {
      gateway: {
        endpointId: gatewayId,
        apiContractId: gateway.apiContractId,
        method: gateway.endpoint.method,
        path: gateway.endpoint.path
      },
      tools: toolDependencies,
      entities: [...entityIds].map((entityId) => ({ entityId })),
      pages: entryActions,
      runtime: await readAgentRuntimeManifest(workspaceRoot)
    },
    runtime: record(contract.runtime),
    security: record(contract.security),
    artifacts: await projectArtifacts(workspaceRoot, agentId, record(contract.artifacts)),
    requiredChecks: stringItems(contract.requiredChecks),
    taskSummary: agentTaskSummary(buildTaskPlan, agentId, hash)
  }
}

/** 只允许检查平台为当前 Agent 编译的三个固定相对路径。 */
async function projectArtifacts(
  workspaceRoot: string,
  agentId: string,
  artifacts: Record<string, unknown>
): Promise<WorkbenchAgentOption['artifacts']> {
  const expected = [
    ['agent', `agent-runtime/src/app/agent/${agentId}.py`, artifacts.agentPath],
    ['tool_adapter', `agent-runtime/src/app/tools/${agentId}_tools.py`, artifacts.toolAdapterPath],
    ['test', `agent-runtime/tests/test_${agentId}.py`, artifacts.testPath]
  ] as const
  return Promise.all(
    expected.map(async ([kind, expectedPath, declaredPath]) => {
      const relativePath = String(declaredPath || '') === expectedPath ? expectedPath : ''
      if (!relativePath) return { kind, path: expectedPath, exists: false }
      const target = path.resolve(workspaceRoot, relativePath)
      const root = path.resolve(workspaceRoot)
      if (!target.startsWith(`${root}${path.sep}`)) {
        return { kind, path: relativePath, exists: false }
      }
      try {
        const stat = await fs.lstat(target)
        return { kind, path: relativePath, exists: stat.isFile() && !stat.isSymbolicLink() }
      } catch {
        return { kind, path: relativePath, exists: false }
      }
    })
  )
}

/** 读取模板 manifest 中已经由主进程验证过的 Agent Runtime 摘要。 */
async function readAgentRuntimeManifest(workspaceRoot: string): Promise<Record<string, unknown>> {
  try {
    const raw = await fs.readFile(
      path.join(workspaceRoot, '.xcodeagent', 'template-generation-manifest.json'),
      'utf8'
    )
    const manifest = JSON.parse(raw) as Record<string, unknown>
    const target = record(record(record(manifest.steps).download).targets).agentRuntime
    const runtime = record(target)
    return {
      required: runtime.required === true,
      status: String(runtime.status || ''),
      repositoryUrl: String(runtime.repositoryUrl || ''),
      branch: String(runtime.branch || ''),
      commitSha: String(runtime.commitSha || '')
    }
  } catch {
    return { required: true, status: 'missing' }
  }
}

/** 计算与后端相同的排序紧凑 JSON SHA-256。 */
function contractHash(contract: Record<string, unknown>): string {
  return `sha256:${createHash('sha256').update(canonicalJson(contract), 'utf8').digest('hex')}`
}

/** 读取正式 TechnicalPlan 文件并生成带算法前缀的 CAS 哈希。 */
async function fileSha256(filePath: string): Promise<string> {
  const content = await fs.readFile(filePath)
  return `sha256:${createHash('sha256').update(content).digest('hex')}`
}

/** 递归生成按键排序且无多余空白的 JSON 文本。 */
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value as Record<string, unknown>)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record(value)[key])}`)
      .join(',')}}`
  }
  return JSON.stringify(value)
}

/** 汇总当前 Agent Contract 对应 Build Context 的任务状态。 */
function agentTaskSummary(
  buildTaskPlan: Record<string, unknown> | undefined,
  agentId: string,
  contractSha: string
): WorkbenchAgentOption['taskSummary'] {
  const context = record(buildTaskPlan?.build_context)
  const target = record(context.target)
  if (
    String(target.type || '') !== 'agent' ||
    String(target.id || '') !== agentId ||
    (context.contract_hash && String(context.contract_hash) !== contractSha)
  ) {
    return undefined
  }
  const requiredUnits = new Set(stringItems(context.required_unit_ids))
  const tasks = Object.values(record(buildTaskPlan?.task_registry)).filter(
    (item): item is Record<string, unknown> => {
      const task = record(item)
      return Boolean(Object.keys(task).length) && requiredUnits.has(String(task.unit_id || ''))
    }
  )
  const count = (statuses: string[]): number =>
    tasks.filter((task) => statuses.includes(String(task.status || 'pending'))).length
  return {
    total: tasks.length,
    pending: count(['pending', 'not_started']),
    running: count(['running']),
    completed: count(['completed', 'already_satisfied']),
    failed: count(['failed'])
  }
}

/** 在 TechnicalPlan 中唯一查找 Endpoint 及所属 API Contract。 */
function findEndpoint(
  apiContracts: Array<Record<string, unknown>>,
  endpointId: string
): { apiContractId: string; apiContract?: Record<string, unknown>; endpoint: Record<string, unknown> } {
  const matches = apiContracts.flatMap((apiContract) =>
    recordItems(apiContract.endpoints)
      .filter((endpoint) => String(endpoint.id || '').trim() === endpointId)
      .map((endpoint) => ({ apiContractId: String(apiContract.id || ''), apiContract, endpoint }))
  )
  return matches.length === 1 ? matches[0] : { apiContractId: '', endpoint: {} }
}

/** 将未知对象安全收敛为普通记录。 */
function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** 将未知数组过滤为对象数组。 */
function recordItems(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === 'object' && !Array.isArray(item)
      )
    : []
}

/** 将未知数组过滤为非空字符串数组。 */
function stringItems(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item || '').trim()).filter(Boolean)
    : []
}
