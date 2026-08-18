import { app, shell, BrowserWindow, ipcMain, dialog, Menu, Tray, nativeImage } from 'electron'
import { join } from 'path'
import crypto from 'node:crypto'
import { execFile } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'
import icon from '../../resources/icon.png?asset'
import { XCODE_AGENT_ENV } from './env'
import { getBackendBaseUrl, startBackendService, stopBackendService } from './backendService'
import { normalizePersistentSessionMessage } from './sessionMessageNormalization'
import { setupApplicationSettingsIpc } from './applicationSettings'
import { lstatIfPresent, movePathToTrashIfPresent, removeDirectoryIfPresent } from './filesystem'
import { readManagedWorkspaceApplication } from './managedWorkspace'
import {
  clearAuthState,
  ensureXcodeAgentDataDir,
  getAccessToken,
  getXcodeAgentDataDir,
  hasValidAuthToken,
  loginWithCmbDeviceFlow
} from './auth'

let mainWindow: BrowserWindow | null = null
let loginWindow: BrowserWindow | null = null
let tray: Tray | null = null
let isQuitting = false
const previewWindows = new Set<BrowserWindow>()
const launchedPreviewWorkspaces = new Map<string, string>()
const hasSingleInstanceLock = app.requestSingleInstanceLock()
let primaryStartupPromise: Promise<boolean> | null = null

/** 返回 Electron 用户数据目录中的应用列表文件路径。 */
function getApplicationsFile(): string {
  return path.join(app.getPath('userData'), 'applications.json')
}

/** 返回工作区元数据目录中的应用配置文件路径。 */
function getWorkspaceApplicationFile(workspaceRoot: string): string {
  return path.join(workspaceRoot, '.xcodeagent', 'application.json')
}

/** 校验新应用目标目录未被已有应用或其他文件占用，避免创建失败后污染工作区。 */
async function assertNewProjectDirectory(projectPath: string): Promise<void> {
  const existing = await lstatIfPresent(projectPath)
  if (!existing) return
  if (!existing.isDirectory() || existing.isSymbolicLink()) {
    throw new Error('新应用项目目录必须是非符号链接目录，请选择独立的项目目录。')
  }

  const applicationFile = getWorkspaceApplicationFile(projectPath)
  const lifecycleFile = path.join(projectPath, '.xcodeagent', 'application-lifecycle.json')
  if ((await lstatIfPresent(applicationFile)) || (await lstatIfPresent(lifecycleFile))) {
    throw new Error('当前工作区已属于另一个应用，请为新应用选择独立的项目目录。')
  }

  const entries = await fs.readdir(projectPath)
  if (entries.length > 0) {
    throw new Error('新应用项目目录必须是不存在或为空的独立目录。')
  }
}

type WorkbenchPageOption = {
  key: string
  pageId: string
  label: string
  path: string
  purpose: string
  designed: boolean
  detailPlanStatus?: string
  hasDetailPlan: boolean
  taskSummary?: WorkbenchTaskSummary
}

type WorkbenchTaskSummary = {
  total: number
  pending: number
  running: number
  completed: number
  failed: number
}

type WorkbenchPageTreeNode = {
  key: string
  type: 'menu' | 'page'
  label: string
  uniquePath?: string
  path?: string
  pageId?: string
  purpose?: string
  designed?: boolean
  detailPlanStatus?: string
  hasDetailPlan?: boolean
  children?: WorkbenchPageTreeNode[]
}

/** 规范化页面树节点名称，避免空白字符串在界面上显示为空占位。 */
function normalizeWorkbenchNodeLabel(value: unknown, fallback: string): string {
  const label = String(value || '').trim()
  return label || fallback
}

type WorkbenchApiContract = {
  id: string
  label: string
  dataSourceIds: string[]
  endpoints: Array<{
    apiContractId: string
    id: string
    method: string
    path: string
    summary: string
    detailPlanStatus?: string
    hasDetailPlan?: boolean
    designed?: boolean
  }>
}

type WorkbenchEntityOption = {
  id: string
  label: string
  purpose: string
  dataSourceType: string
  fields?: Array<{
    name: string
    label?: string
    type: string
    required?: boolean
  }>
  detail?: Record<string, unknown>
  designed: boolean
  detailPlanStatus?: string
  hasDetailPlan: boolean
}

/** 将 API 路径统一为带前导斜杠的目录形式。 */
function normalizeApiPath(value: unknown, fallback = '/api'): string {
  const text = String(value || '').trim()
  if (!text) return fallback
  return `/${text}`.replace(/\/+/g, '/').replace(/\/$/, '') || '/'
}

/** 把数组或对象字典统一展开成记录数组，兼容不同 ProjectPlan 保存形态。 */
function recordItems(value: unknown): Array<Record<string, unknown>> {
  const items = Array.isArray(value)
    ? value
    : value && typeof value === 'object'
      ? Object.values(value as Record<string, unknown>)
      : []
  return items.filter(
    (item): item is Record<string, unknown> =>
      Boolean(item) && typeof item === 'object' && !Array.isArray(item)
  )
}

/** 判断当前计划页面节点是否为菜单目录节点。 */
function isFrontendMenuNode(record: Record<string, unknown>): boolean {
  const pageId = String(record.pageId || record.id || '').trim()
  return Array.isArray(record.children) && !pageId
}

/** 递归拍平当前计划页面，仅保留真正的业务页面叶子。 */
function flattenFrontendPageRecords(value: unknown): Array<Record<string, unknown>> {
  const flattened: Array<Record<string, unknown>> = []
  recordItems(value).forEach((record) => {
    const pageId = String(record.pageId || record.id || '').trim()
    if (pageId) flattened.push(record)
    if (Array.isArray(record.children)) {
      flattened.push(...flattenFrontendPageRecords(record.children))
    }
  })
  return flattened
}

/** 将页面 id 转成与后端详情文件一致的安全文件名。 */
function detailFileStem(value: string, prefix: string): string {
  const normalized = value.replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^[-_]+|[-_]+$/g, '')
  return `${prefix}${normalized || 'unknown'}`
}

/** 检查选中接口是否已经存在外置详情 JSON。 */
async function endpointDetailPlanExists(
  workspaceRoot: string,
  apiContractId: string,
  endpointId: string
): Promise<boolean> {
  const detailPath = path.join(
    workspaceRoot,
    '.xcodeagent',
    'plans',
    'endpoints',
    `${detailFileStem(`${apiContractId}--${endpointId}`, 'endpoint--')}.json`
  )
  try {
    const content = await fs.readFile(detailPath, 'utf8')
    return Boolean(content.trim())
  } catch {
    return false
  }
}

/** 读取选中实体的外置详情 JSON，未设计或读取失败时返回 undefined。 */
async function readEntityDetailPlan(
  workspaceRoot: string,
  entityId: string
): Promise<Record<string, unknown> | undefined> {
  const detailPath = path.join(
    workspaceRoot,
    '.xcodeagent',
    'plans',
    'entities',
    `${detailFileStem(entityId, 'entity--')}.json`
  )
  try {
    const content = await fs.readFile(detailPath, 'utf8')
    if (!content.trim()) return undefined
    const parsed: unknown = JSON.parse(content)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : undefined
  } catch {
    return undefined
  }
}

/** 从当前计划的 pages 中按页面标识收集应用大纲页面。 */
function projectPlanPages(value: unknown): Map<string, Record<string, unknown>> {
  const result = new Map<string, Record<string, unknown>>()
  if (!value || typeof value !== 'object' || Array.isArray(value)) return result
  const pages = flattenFrontendPageRecords((value as Record<string, unknown>).pages)
  pages.forEach((page) => {
    const record = page
    const pageId = String(record.id || record.pageId || '').trim()
    if (pageId) result.set(pageId, record)
  })
  return result
}

/** 从当前计划的 pages 生成工作台页面目录。 */
function projectPlanPageOptions(value: unknown): WorkbenchPageOption[] {
  return [...projectPlanPages(value).entries()].map(([pageId, record], index) => ({
    key: pageId,
    pageId,
    label: normalizeWorkbenchNodeLabel(record.name, pageId),
    path: String(record.path || '/'),
    purpose: normalizeWorkbenchNodeLabel(record.description || record.name, `页面 ${index + 1}`),
    designed: false,
    detailPlanStatus: '',
    hasDetailPlan: false
  }))
}

/** 把当前计划的 pages 递归转换为工作台可展示的页面目录树。 */
function projectPlanPageTree(value: unknown): WorkbenchPageTreeNode[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return buildWorkbenchPageTree((value as Record<string, unknown>).pages)
}

/** 运行时把 ProductPlan 页面事实与 TechnicalPlan references 合并为工作台视图。 */
function technicalPlanWorkbenchView(
  technicalPlan: Record<string, unknown>,
  productPlan: Record<string, unknown>
): Record<string, unknown> {
  const references = new Map(
    recordItems(technicalPlan.pages).map((page) => [
      String(page.pageId || '').trim(),
      isJsonRecord(page.references) ? page.references : {}
    ])
  )
  return {
    ...technicalPlan,
    pages: recordItems(productPlan.pages).map((page) => ({
      ...page,
      references: references.get(String(page.pageId || '').trim()) || {}
    }))
  }
}

/** 递归构造工作台侧栏使用的页面树节点。 */
function buildWorkbenchPageTree(value: unknown): WorkbenchPageTreeNode[] {
  const nodes: WorkbenchPageTreeNode[] = []
  recordItems(value).forEach((record, index) => {
    if (isFrontendMenuNode(record)) {
      const uniquePath = String(record.unique_path || '').trim()
      const key = uniquePath || `menu-${index + 1}`
      const children = buildWorkbenchPageTree(record.children)
      if (children.length === 0) return
      nodes.push({
        key,
        type: 'menu',
        label: normalizeWorkbenchNodeLabel(record.name, `菜单 ${index + 1}`),
        uniquePath,
        children
      })
      return
    }
    const pageId = String(record.pageId || record.id || '').trim()
    if (!pageId) return
    nodes.push({
      key: pageId,
      type: 'page',
      label: normalizeWorkbenchNodeLabel(record.name, pageId),
      pageId,
      path: String(record.path || '/'),
      purpose: normalizeWorkbenchNodeLabel(record.description || record.name, `页面 ${index + 1}`),
      designed: false,
      detailPlanStatus: '',
      hasDetailPlan: false
    })
  })
  return nodes
}

/** 按 base_path 合并 ProjectPlan contracts，同一目录下展示所有具体 API。 */
function projectPlanApiContracts(value: unknown): WorkbenchApiContract[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  const contracts = recordItems((value as Record<string, unknown>).api_contracts)
  const groupedContracts = new Map<string, WorkbenchApiContract>()
  contracts.forEach((contract, contractIndex) => {
    const record = contract
    const contractId = String(record.id || `api-contract-${contractIndex + 1}`).trim()
    const dataSourceId = String(record.data_source_id || contractId).trim()
    const basePath = normalizeApiPath(record.base_path, `/${contractId}`)
    const endpoints = recordItems(record.endpoints)
    const group = groupedContracts.get(basePath) || {
      id: basePath,
      label: basePath,
      dataSourceIds: [],
      endpoints: []
    }
    if (dataSourceId && !group.dataSourceIds.includes(dataSourceId)) {
      group.dataSourceIds.push(dataSourceId)
    }
    group.endpoints.push(
      ...endpoints.flatMap((endpoint, endpointIndex) => {
        const endpointRecord = endpoint
        const method = String(endpointRecord.method || 'GET')
          .trim()
          .toUpperCase()
        const endpointPath = String(endpointRecord.path || '').trim()
        if (!endpointPath) return []
        const detailDesign =
          endpointRecord.detail_design &&
          typeof endpointRecord.detail_design === 'object' &&
          !Array.isArray(endpointRecord.detail_design)
            ? (endpointRecord.detail_design as Record<string, unknown>)
            : {}
        const hasDetailPlan = Boolean(detailDesign.json_path || endpointRecord.detail_plan_id)
        return [
          {
            apiContractId: contractId,
            id: String(endpointRecord.id || endpointIndex + 1),
            method,
            path: normalizeApiPath(endpointPath, '/'),
            summary: String(endpointRecord.summary || ''),
            designed: hasDetailPlan,
            detailPlanStatus: String(detailDesign.status || endpointRecord.detail_status || ''),
            hasDetailPlan
          }
        ]
      })
    )
    groupedContracts.set(basePath, group)
  })
  return [...groupedContracts.values()]
}

/** 从需求文档的 entities 生成工作台实体大纲选项。 */
function projectPlanEntities(value: unknown): WorkbenchEntityOption[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return recordItems((value as Record<string, unknown>).entities).map((record, index) => {
    const id = String(record.id || `entity-${index + 1}`).trim()
    const fields = Array.isArray(record.fields)
      ? record.fields
          .filter((field): field is Record<string, unknown> =>
            Boolean(field && typeof field === 'object' && !Array.isArray(field))
          )
          .map((field) => ({
            name: String(field.name || '').trim(),
            label:
              typeof field.label === 'string' && field.label.trim()
                ? field.label.trim()
                : undefined,
            type: String(field.type || 'text').trim(),
            required: field.required === true
          }))
          .filter((field) => Boolean(field.name))
      : []
    const detailDesign =
      record.detail_design &&
      typeof record.detail_design === 'object' &&
      !Array.isArray(record.detail_design)
        ? (record.detail_design as Record<string, unknown>)
        : {}
    const rawDataSource = record.data_source
    const dataSourceType =
      typeof rawDataSource === 'string'
        ? rawDataSource
        : rawDataSource && typeof rawDataSource === 'object' && !Array.isArray(rawDataSource)
          ? String((rawDataSource as Record<string, unknown>).type || '')
          : ''
    return {
      id,
      label: normalizeWorkbenchNodeLabel(record.name, id),
      purpose: normalizeWorkbenchNodeLabel(record.description || record.name, `实体 ${index + 1}`),
      dataSourceType,
      ...(fields.length > 0 ? { fields } : {}),
      designed: Boolean(detailDesign.json_path || record.detail_plan_id),
      detailPlanStatus: String(detailDesign.status || record.detail_status || ''),
      hasDetailPlan: Boolean(detailDesign.json_path || record.detail_plan_id)
    }
  })
}

/** 只根据外置实体详情文件补充每个实体的设计状态。 */
async function mergeWorkbenchEntityStatus(
  workspaceRoot: string,
  entities: WorkbenchEntityOption[]
): Promise<WorkbenchEntityOption[]> {
  return Promise.all(
    entities.map(async (entity) => {
      const detail = await readEntityDetailPlan(workspaceRoot, entity.id)
      const hasDetailPlan = Boolean(detail)
      return {
        ...entity,
        designed: hasDetailPlan,
        hasDetailPlan,
        ...(detail ? { detail } : {})
      }
    })
  )
}

/** 初始化页面设计状态；TechnicalPlan 的实现契约不代表用户已经开始页面设计。 */
function mergeWorkbenchPageStatus(
  pages: WorkbenchPageOption[],
  buildTaskPlan?: Record<string, unknown>
): WorkbenchPageOption[] {
  return pages.map((page) => ({
    ...page,
    designed: false,
    detailPlanStatus: '',
    hasDetailPlan: false,
    taskSummary: pageBuildTaskSummary(buildTaskPlan, page.pageId)
  }))
}

/** 把页面叶子的详细设计状态回写到页面目录树，保留菜单层级不变。 */
function mergeWorkbenchPageTreeStatus(
  pageTree: WorkbenchPageTreeNode[],
  pagesById: Map<string, WorkbenchPageOption>
): WorkbenchPageTreeNode[] {
  return pageTree.map((node) => {
    if (node.type === 'menu') {
      return {
        ...node,
        children: mergeWorkbenchPageTreeStatus(node.children || [], pagesById)
      }
    }
    const pageId = String(node.pageId || node.key || '').trim()
    const page = pagesById.get(pageId)
    return page
      ? {
          ...node,
          label: page.label,
          path: page.path,
          purpose: page.purpose,
          designed: page.designed,
          detailPlanStatus: page.detailPlanStatus,
          hasDetailPlan: page.hasDetailPlan
        }
      : node
  })
}

/** 读取正式 Build Task Plan；Markdown DAG 仅用于展示，不作为状态解析源。 */
async function readBuildTaskPlan(
  workspaceRoot: string
): Promise<Record<string, unknown> | undefined> {
  try {
    const content = await fs.readFile(
      path.join(workspaceRoot, '.xcodeagent', 'plans', 'build-task-plan.json'),
      'utf8'
    )
    const value = JSON.parse(content)
    return value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : undefined
  } catch {
    return undefined
  }
}

/** 按页面 Unit 及其前置 Unit 汇总正式 Build DAG 中的任务执行状态。 */
function pageBuildTaskSummary(
  buildTaskPlan: Record<string, unknown> | undefined,
  pageId: string
): WorkbenchTaskSummary | undefined {
  if (!buildTaskPlan) return undefined
  const registry =
    buildTaskPlan.task_registry &&
    typeof buildTaskPlan.task_registry === 'object' &&
    !Array.isArray(buildTaskPlan.task_registry)
      ? (buildTaskPlan.task_registry as Record<string, unknown>)
      : {}
  const graph =
    buildTaskPlan.unit_graph &&
    typeof buildTaskPlan.unit_graph === 'object' &&
    !Array.isArray(buildTaskPlan.unit_graph)
      ? (buildTaskPlan.unit_graph as Record<string, unknown>)
      : {}
  const dependencies = new Map<string, string[]>()
  recordItems(graph.edges).forEach((edge) => {
    if (edge.type !== 'depends_on') return
    const source = String(edge.from || '').trim()
    const target = String(edge.to || '').trim()
    if (source && target) dependencies.set(target, [...(dependencies.get(target) || []), source])
  })

  const selectedUnits = new Set<string>()
  const pendingUnits = [`page:${pageId}`]
  while (pendingUnits.length > 0) {
    const unitId = pendingUnits.shift()
    if (!unitId || selectedUnits.has(unitId)) continue
    selectedUnits.add(unitId)
    pendingUnits.push(...(dependencies.get(unitId) || []))
  }

  const tasks = recordItems(registry).filter((task) =>
    selectedUnits.has(String(task.unit_id || task.unitId || 'application:root'))
  )
  if (tasks.length === 0) return undefined
  const statuses = tasks.map((task) => String(task.status || 'pending'))
  return {
    total: tasks.length,
    pending: statuses.filter((status) => status === 'pending').length,
    running: statuses.filter((status) => status === 'running').length,
    completed: statuses.filter((status) => status === 'completed' || status === 'already_satisfied')
      .length,
    failed: statuses.filter((status) => status === 'failed').length
  }
}

/** 只根据外置接口详情文件补充每个 endpoint 的设计状态。 */
async function mergeWorkbenchApiStatus(
  workspaceRoot: string,
  contracts: WorkbenchApiContract[]
): Promise<WorkbenchApiContract[]> {
  return Promise.all(
    contracts.map(async (contract) => ({
      ...contract,
      endpoints: await Promise.all(
        contract.endpoints.map(async (endpoint) => {
          const hasDetailPlan = await endpointDetailPlanExists(
            workspaceRoot,
            endpoint.apiContractId,
            endpoint.id
          )
          return {
            ...endpoint,
            designed: hasDetailPlan,
            hasDetailPlan
          }
        })
      )
    }))
  )
}

/** 判断是否已存在任意持久化详细设计（页面/接口/实体），用于首次设计解锁。 */
async function pageDesignDirectoryHasEntries(workspaceRoot: string): Promise<boolean> {
  for (const directory of ['pages', 'endpoints', 'entities']) {
    try {
      const entries = await fs.readdir(path.join(workspaceRoot, '.xcodeagent', 'plans', directory))
      if (entries.length > 0) return true
    } catch (error: unknown) {
      const errnoException = error as NodeJS.ErrnoException
      if (errnoException?.code === 'ENOENT') continue
      throw error
    }
  }
  return false
}

/** 校验当前正式规划产物，并从 ProductPlan/TechnicalPlan 投射工作台大纲。 */
async function inspectWorkspacePlanningArtifacts(workspaceRoot: string): Promise<{
  ready: boolean
  hasPageDesigns: boolean
  missing: string[]
  invalid: string[]
  pages: WorkbenchPageOption[]
  pageTree: WorkbenchPageTreeNode[]
  apiContracts: WorkbenchApiContract[]
  entities: WorkbenchEntityOption[]
}> {
  const artifactRoot = path.join(workspaceRoot, '.xcodeagent')
  const artifacts = [
    { relativePath: 'specs/requirement-spec.md', format: 'markdown' },
    { relativePath: 'specs/requirement-spec.json', format: 'json' },
    { relativePath: 'plans/product-plan.md', format: 'markdown' },
    { relativePath: 'plans/technical-plan.md', format: 'markdown' }
  ]
  const missing: string[] = []
  const invalid: string[] = []
  let plannedPages = new Map<string, Record<string, unknown>>()
  let pageTree: WorkbenchPageTreeNode[] = []
  let apiContracts: WorkbenchApiContract[] = []
  let entities: WorkbenchEntityOption[] = []
  let requirementSpec: Record<string, unknown> | undefined

  for (const artifact of artifacts) {
    const artifactPath = path.join(artifactRoot, artifact.relativePath)
    try {
      const content = await fs.readFile(artifactPath, 'utf8')
      if (!content.trim()) {
        invalid.push(artifact.relativePath)
        continue
      }
      if (artifact.format === 'json') {
        const value = JSON.parse(content)
        if (
          !value ||
          typeof value !== 'object' ||
          Array.isArray(value) ||
          value.confirmation_status !== 'confirmed'
        ) {
          invalid.push(artifact.relativePath)
        } else if (artifact.relativePath === 'specs/requirement-spec.json') {
          requirementSpec = value as Record<string, unknown>
        }
      }
    } catch (error: unknown) {
      const errnoException = error as NodeJS.ErrnoException
      if (errnoException?.code === 'ENOENT') missing.push(artifact.relativePath)
      else invalid.push(artifact.relativePath)
    }
  }

  let productPlan: Record<string, unknown> | undefined
  let technicalPlan: Record<string, unknown> | undefined
  const currentPlanArtifacts = [
    {
      relativePath: 'plans/product-plan.json',
      contractField: 'schema_version',
      contractValue: 'product-plan.v4'
    },
    {
      relativePath: 'plans/technical-plan.json',
      contractField: 'artifact_type',
      contractValue: 'technical-plan'
    }
  ]
  // 工作台只读取当前 ProductPlan/TechnicalPlan 契约，不回退到旧计划文件。
  for (const artifact of currentPlanArtifacts) {
    try {
      const content = await fs.readFile(path.join(artifactRoot, artifact.relativePath), 'utf8')
      const plan = JSON.parse(content)
      if (
        !plan ||
        typeof plan !== 'object' ||
        Array.isArray(plan) ||
        plan[artifact.contractField] !== artifact.contractValue ||
        plan.confirmation_status !== 'confirmed'
      ) {
        invalid.push(artifact.relativePath)
        continue
      }
      if (artifact.contractValue === 'product-plan.v4') {
        productPlan = plan as Record<string, unknown>
      } else {
        technicalPlan = plan as Record<string, unknown>
      }
    } catch (error: unknown) {
      const errnoException = error as NodeJS.ErrnoException
      if (errnoException?.code === 'ENOENT') missing.push(artifact.relativePath)
      else invalid.push(artifact.relativePath)
    }
  }

  if (productPlan && technicalPlan) {
    const workbenchPlan = technicalPlanWorkbenchView(technicalPlan, productPlan)
    plannedPages = projectPlanPages(workbenchPlan)
    pageTree = projectPlanPageTree(workbenchPlan)
    apiContracts = projectPlanApiContracts(workbenchPlan)
  }
  entities = projectPlanEntities(requirementSpec)

  const buildTaskPlan = await readBuildTaskPlan(workspaceRoot)
  const pages = mergeWorkbenchPageStatus(
    projectPlanPageOptions({ pages: [...plannedPages.values()] }),
    buildTaskPlan
  )
  const pagesById = new Map(pages.map((page) => [page.pageId, page]))
  apiContracts = await mergeWorkbenchApiStatus(workspaceRoot, apiContracts)
  entities = await mergeWorkbenchEntityStatus(workspaceRoot, entities)
  const hasPageDesigns = await pageDesignDirectoryHasEntries(workspaceRoot)

  return {
    ready: missing.length === 0 && invalid.length === 0,
    hasPageDesigns,
    missing,
    invalid,
    pages,
    pageTree: mergeWorkbenchPageTreeStatus(pageTree, pagesById),
    apiContracts,
    entities
  }
}

type EditorMode = 'frontend' | 'backend'

type SessionWorkspaceSummary = {
  workspaceRoot: string
  name: string
  sessionCount: number
  frontendCount: number
  backendCount: number
  latestUpdatedAt: number
  latestTitle: string
}

type ChatSessionSummary = {
  id: string
  title: string
  editorMode: EditorMode
  threadId: string
  apiContractId?: string
  endpointId?: string
  endpointLabel?: string
  entityId?: string
  entityLabel?: string
  pageId?: string
  createdAt: number
  updatedAt: number
  messageCount: number
}

type JsonRecord = Record<string, unknown>

type NormalizedChatSession = {
  id: string
  title: string
  editorMode: EditorMode
  threadId: string
  apiContractId?: string
  endpointId?: string
  endpointLabel?: string
  entityId?: string
  entityLabel?: string
  pageId?: string
  createdAt: number
  updatedAt: number
  workspaceRoot: string
  messages: JsonRecord[]
}

/** 返回随应用发布的初始应用列表文件路径。 */
function getSeedApplicationsFile(): string {
  return path.join(__dirname, '..', 'data', 'applications.json')
}

/** 确保应用列表文件存在，并在首次运行时写入种子数据。 */
async function ensureApplicationsFile(): Promise<string> {
  const applicationsFile = getApplicationsFile()
  await fs.mkdir(path.dirname(applicationsFile), { recursive: true })

  try {
    await fs.access(applicationsFile)
    return applicationsFile
  } catch {
    // Continue and seed the file below.
  }

  let seedValue = '[]\n'
  try {
    seedValue = await fs.readFile(getSeedApplicationsFile(), 'utf8')
  } catch {
    // Keep an empty store when the seed file is not shipped.
  }

  await fs.writeFile(applicationsFile, seedValue, 'utf8')
  return applicationsFile
}

/** 读取持久化应用列表，非数组内容按空列表处理。 */
async function readApplications(): Promise<unknown[]> {
  const applicationsFile = await ensureApplicationsFile()
  const rawValue = await fs.readFile(applicationsFile, 'utf8')
  const parsed: unknown = JSON.parse(rawValue || '[]')
  return Array.isArray(parsed) ? parsed : []
}

/** 校验并持久化应用列表。 */
async function writeApplications(applications: unknown): Promise<void> {
  if (!Array.isArray(applications)) {
    throw new Error('applications must be an array')
  }

  const applicationsFile = await ensureApplicationsFile()
  await fs.writeFile(applicationsFile, `${JSON.stringify(applications, null, 2)}\n`, 'utf8')
}

/** 仅将带有 XCodeAgent 项目标识的安全工作区目录移入系统回收站。 */
async function trashProjectDirectory(workspaceRoot: unknown): Promise<void> {
  const projectRoot = resolveWorkspaceRoot(workspaceRoot)
  const protectedRoots = new Set(
    [
      path.parse(projectRoot).root,
      path.resolve(app.getPath('home')),
      path.resolve(app.getPath('userData')),
      path.resolve(getXcodeAgentDataDir())
    ].map(pathComparisonKey)
  )
  if (protectedRoots.has(pathComparisonKey(projectRoot))) {
    throw new Error('不能删除系统、用户或 XCodeAgent 数据目录')
  }

  const projectMetadataFile = getWorkspaceApplicationFile(projectRoot)
  const projectStats = await lstatIfPresent(projectRoot)
  if (!projectStats) return
  if (!projectStats.isDirectory() || projectStats.isSymbolicLink()) {
    throw new Error('只能删除非符号链接的项目目录')
  }

  try {
    await fs.access(projectMetadataFile)
  } catch {
    throw new Error('该目录不是由 XCodeAgent 管理的项目，不能直接删除')
  }

  await movePathToTrashIfPresent(projectRoot, (targetPath) => shell.trashItem(targetPath))
}

/** 仅删除受控工作区内部由 XCodeAgent 生成的规划与运行目录。 */
async function deleteProjectAgentDirectory(workspaceRoot: unknown): Promise<void> {
  const projectRoot = resolveWorkspaceRoot(workspaceRoot)
  const protectedRoots = new Set(
    [
      path.parse(projectRoot).root,
      path.resolve(app.getPath('home')),
      path.resolve(app.getPath('userData')),
      path.resolve(getXcodeAgentDataDir())
    ].map(pathComparisonKey)
  )
  if (protectedRoots.has(pathComparisonKey(projectRoot))) {
    throw new Error('不能清理系统、用户或 XCodeAgent 数据目录')
  }

  const projectStats = await lstatIfPresent(projectRoot)
  if (!projectStats) return
  if (!projectStats.isDirectory() || projectStats.isSymbolicLink()) {
    throw new Error('只能清理非符号链接的项目目录')
  }

  const agentDirectory = path.join(projectRoot, '.xcodeagent')
  const agentStats = await lstatIfPresent(agentDirectory)
  if (!agentStats) return
  if (!agentStats.isDirectory() || agentStats.isSymbolicLink()) {
    throw new Error('只能删除工作区内非符号链接的 .xcodeagent 目录')
  }
  try {
    await fs.access(getWorkspaceApplicationFile(projectRoot))
  } catch {
    throw new Error('该目录不包含 XCodeAgent 应用标识，不能清理')
  }

  await removeDirectoryIfPresent(agentDirectory)
}

/** 注册应用列表读取和保存所需的 IPC。 */
function setupApplicationStorageIpc(): void {
  ipcMain.handle('applications:load', async () => ({
    applications: await readApplications()
  }))

  ipcMain.handle('applications:save', async (_event, applications) => {
    await writeApplications(applications)
    return { ok: true }
  })

  ipcMain.handle('applications:delete-project', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot)
    await trashProjectDirectory(workspaceRoot)
    // 项目移入回收站后同步转移环境级会话，避免同一路径重建时继承旧项目历史。
    await movePathToTrashIfPresent(getWorkspaceSessionRoot(workspaceRoot), (targetPath) =>
      shell.trashItem(targetPath)
    )
    return { ok: true }
  })

  ipcMain.handle('applications:delete-agent-directory', async (_event, payload = {}) => {
    await deleteProjectAgentDirectory(payload.workspaceRoot)
    return { ok: true }
  })
}

/** 校验并规范化允许由 Electron 打开的外部 HTTP 地址。 */
function normalizeExternalUrl(url: unknown): string {
  if (typeof url !== 'string') {
    throw new Error('url must be a string')
  }

  const parsedUrl = new URL(url)
  if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
    throw new Error('Only http and https URLs can be opened')
  }

  return parsedUrl.toString()
}

/** 注册系统浏览器和独立预览窗口相关 IPC。 */
function setupBrowserIpc(): void {
  ipcMain.handle('browser:open-external', async (_event, url) => {
    await shell.openExternal(normalizeExternalUrl(url))
    return { ok: true }
  })

  ipcMain.handle('browser:open-preview-window', async (_event, url) => {
    const previewWindow = new BrowserWindow({
      fullscreen: true,
      minWidth: 960,
      minHeight: 640,
      title: '全屏预览',
      backgroundColor: '#ffffff',
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true
      }
    })

    previewWindows.add(previewWindow)
    previewWindow.once('closed', () => {
      previewWindows.delete(previewWindow)
    })
    previewWindow.webContents.setWindowOpenHandler(({ url: nextUrl }) => {
      shell.openExternal(nextUrl)
      return { action: 'deny' }
    })
    await previewWindow.loadURL(normalizeExternalUrl(url))
    previewWindow.show()
    return { ok: true }
  })
}

/** 记录本次 Electron 会话中被工作台启动过的项目预览工作区。 */
function setupProjectPreviewIpc(): void {
  ipcMain.handle('project-preview:register-workspace', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot)
    launchedPreviewWorkspaces.set(pathComparisonKey(workspaceRoot), workspaceRoot)
    return { ok: true }
  })

  ipcMain.handle('project-preview:unregister-workspace', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot)
    launchedPreviewWorkspaces.delete(pathComparisonKey(workspaceRoot))
    return { ok: true }
  })
}

/** 请求本地后端停止指定工作区的生成项目预览服务。 */
async function stopGeneratedProjectPreview(workspaceRoot: string): Promise<void> {
  const response = await fetch(`${getBackendBaseUrl().replace(/\/$/, '')}/api/projects/stop`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace: workspaceRoot })
  })
  if (!response.ok) {
    throw new Error(`Stop project preview failed: ${response.status}`)
  }
  const result = (await response.json()) as { status?: unknown; message?: unknown }
  if (result.status === 'failed') {
    throw new Error(
      typeof result.message === 'string' ? result.message : 'Project preview stop failed'
    )
  }
}

/** 显式退出 Electron 前停止本次打开过的所有生成项目预览。 */
async function stopGeneratedProjectPreviewsBeforeQuit(): Promise<void> {
  const workspaces = Array.from(launchedPreviewWorkspaces.entries())
  for (const [workspaceKey, workspaceRoot] of workspaces) {
    try {
      await stopGeneratedProjectPreview(workspaceRoot)
      launchedPreviewWorkspaces.delete(workspaceKey)
    } catch (error) {
      console.error(`Failed to stop generated project preview: ${workspaceRoot}`, error)
    }
  }
}

/** 校验并返回支持的编辑器模式。 */
function assertEditorMode(value: unknown): EditorMode {
  if (value !== 'frontend' && value !== 'backend') {
    throw new Error('editorMode must be frontend or backend')
  }
  return value
}

/** 校验并返回可安全用于文件名的会话标识。 */
function assertSessionId(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+$/.test(value)) {
    throw new Error('sessionId contains invalid characters')
  }
  return value
}

/** 校验并解析工作区绝对路径。 */
function resolveWorkspaceRoot(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error('workspaceRoot must be a non-empty string')
  }
  return path.resolve(value)
}

/** 生成符合宿主文件系统大小写语义的绝对路径比较键。 */
function pathComparisonKey(value: string): string {
  const resolved = path.normalize(path.resolve(value))
  return process.platform === 'win32' ? resolved.toLowerCase() : resolved
}

/** 根据工作区名称和绝对路径哈希生成稳定的会话目录键。 */
function getWorkspaceSessionKey(workspaceRoot: unknown): string {
  const resolvedWorkspaceRoot = resolveWorkspaceRoot(workspaceRoot)
  const workspaceName =
    path
      .basename(resolvedWorkspaceRoot)
      .replace(/[^A-Za-z0-9._-]/g, '-')
      .slice(0, 80) || 'workspace'
  const workspaceHash = crypto
    .createHash('sha1')
    .update(pathComparisonKey(resolvedWorkspaceRoot))
    .digest('hex')
    .slice(0, 12)
  return `${workspaceName}-${workspaceHash}`
}

/** 返回指定工作区在环境数据目录中的会话根目录。 */
function getWorkspaceSessionRoot(workspaceRoot: unknown): string {
  return path.join(getXcodeAgentDataDir(), 'sessions', getWorkspaceSessionKey(workspaceRoot))
}

/** 返回当前环境全部会话工作区的存储根目录。 */
function getSessionStorageRoot(): string {
  return path.join(getXcodeAgentDataDir(), 'sessions')
}

/** 返回指定工作区和编辑器模式对应的会话目录。 */
function getSessionsDir(workspaceRoot: unknown, editorMode: unknown): string {
  return path.join(getWorkspaceSessionRoot(workspaceRoot), assertEditorMode(editorMode))
}

/** 返回校验后的单个会话文件路径。 */
function getSessionFile(workspaceRoot: unknown, editorMode: unknown, sessionId: unknown): string {
  return path.join(getSessionsDir(workspaceRoot, editorMode), `${assertSessionId(sessionId)}.json`)
}

/** 创建环境级会话目录并更新其工作区元数据。 */
async function ensureSessionsDir(workspaceRoot: unknown, editorMode: unknown): Promise<string> {
  const resolvedWorkspaceRoot = resolveWorkspaceRoot(workspaceRoot)
  const workspaceSessionRoot = getWorkspaceSessionRoot(resolvedWorkspaceRoot)
  const sessionsDir = path.join(workspaceSessionRoot, assertEditorMode(editorMode))
  await fs.mkdir(sessionsDir, { recursive: true })
  await fs.writeFile(
    path.join(workspaceSessionRoot, 'workspace.json'),
    `${JSON.stringify({ workspaceRoot: resolvedWorkspaceRoot, updatedAt: Date.now() }, null, 2)}\n`,
    'utf8'
  )
  return sessionsDir
}

/** 将规范化会话转换为列表展示所需的摘要。 */
function sessionSummary(session: NormalizedChatSession): ChatSessionSummary {
  const messages = Array.isArray(session.messages) ? session.messages : []
  return {
    id: String(session.id || ''),
    title: String(session.title || '新对话'),
    editorMode: assertEditorMode(session.editorMode),
    threadId: String(session.threadId || ''),
    apiContractId: session.apiContractId,
    endpointId: session.endpointId,
    endpointLabel: session.endpointLabel,
    entityId: session.entityId,
    entityLabel: session.entityLabel,
    pageId: session.pageId,
    createdAt: Number(session.createdAt || Date.now()),
    updatedAt: Number(session.updatedAt || Date.now()),
    messageCount: messages.length
  }
}

/** 判断未知值是否为非数组 JSON 对象。 */
function isJsonRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/** 校验外部会话数据并转换为可持久化的统一结构。 */
function normalizeSession(session: unknown): NormalizedChatSession {
  if (!isJsonRecord(session)) {
    throw new Error('session must be an object')
  }

  const editorMode = assertEditorMode(session.editorMode)
  const id = assertSessionId(session.id)
  const messages = Array.isArray(session.messages)
    ? session.messages.filter(isJsonRecord).map(normalizePersistentSessionMessage)
    : []

  const pageId = normalizeSessionPageId(session.pageId) || inferSessionPageId(messages)
  const endpointContext = normalizeSessionEndpointContext(session, messages)
  const entityContext = normalizeSessionEntityContext(session, messages)
  return {
    id,
    title: String(session.title || '新对话'),
    editorMode,
    threadId: String(session.threadId || id),
    ...(endpointContext.apiContractId ? { apiContractId: endpointContext.apiContractId } : {}),
    ...(endpointContext.endpointId ? { endpointId: endpointContext.endpointId } : {}),
    ...(endpointContext.endpointLabel ? { endpointLabel: endpointContext.endpointLabel } : {}),
    ...(entityContext.entityId ? { entityId: entityContext.entityId } : {}),
    ...(entityContext.entityLabel ? { entityLabel: entityContext.entityLabel } : {}),
    ...(pageId ? { pageId } : {}),
    createdAt: Number(session.createdAt || Date.now()),
    updatedAt: Number(session.updatedAt || Date.now()),
    workspaceRoot: typeof session.workspaceRoot === 'string' ? session.workspaceRoot : '',
    messages
  }
}

/** 规范化页面会话标识，避免空字符串污染持久化索引。 */
function normalizeSessionPageId(value: unknown): string | undefined {
  const pageId = typeof value === 'string' ? value.trim() : ''
  return pageId || undefined
}

/** 规范化接口会话标识，避免空字符串污染持久化索引。 */
function normalizeSessionEndpointField(value: unknown): string | undefined {
  const text = typeof value === 'string' ? value.trim() : ''
  return text || undefined
}

/** 从会话字段或旧版 Workflow 快照推断 API endpoint 会话归属。 */
function normalizeSessionEndpointContext(
  session: JsonRecord,
  messages: JsonRecord[]
): { apiContractId?: string; endpointId?: string; endpointLabel?: string } {
  const explicit = {
    apiContractId: normalizeSessionEndpointField(session.apiContractId),
    endpointId: normalizeSessionEndpointField(session.endpointId),
    endpointLabel: normalizeSessionEndpointField(session.endpointLabel)
  }
  if (explicit.apiContractId && explicit.endpointId) return explicit
  const inferred = inferSessionEndpointContext(messages)
  return {
    apiContractId: explicit.apiContractId || inferred.apiContractId,
    endpointId: explicit.endpointId || inferred.endpointId,
    endpointLabel:
      explicit.endpointLabel || inferred.endpointLabel || inferEndpointLabelFromTitle(session.title)
  }
}

/** 从会话字段或旧版 Workflow 快照推断实体会话归属。 */
function normalizeSessionEntityContext(
  session: JsonRecord,
  messages: JsonRecord[]
): { entityId?: string; entityLabel?: string } {
  const explicit = {
    entityId: normalizeSessionEndpointField(session.entityId),
    entityLabel: normalizeSessionEndpointField(session.entityLabel)
  }
  if (explicit.entityId) return explicit
  const inferred = inferSessionEntityContext(messages)
  return {
    entityId: explicit.entityId || inferred.entityId,
    entityLabel:
      explicit.entityLabel ||
      inferred.entityLabel ||
      inferSessionEntityLabelFromTitle(session.title)
  }
}

/** 从旧版消息中的 Workflow 状态快照推断页面会话归属。 */
function inferSessionPageId(messages: JsonRecord[]): string | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const workflow = messages[index].workflow
    if (!isJsonRecord(workflow)) continue
    const state = isJsonRecord(workflow.state) ? workflow.state : undefined
    const result = isJsonRecord(workflow.result) ? workflow.result : undefined
    const pageId =
      normalizeSessionPageId(state?.selectedPageId) ||
      normalizeSessionPageId(result?.selectedPageId)
    if (pageId) return pageId
  }
  return undefined
}

/** 从旧版消息中的 Workflow 状态快照推断 API endpoint 会话归属。 */
function inferSessionEndpointContext(messages: JsonRecord[]): {
  apiContractId?: string
  endpointId?: string
  endpointLabel?: string
} {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const workflow = messages[index].workflow
    if (!isJsonRecord(workflow)) continue
    const state = isJsonRecord(workflow.state) ? workflow.state : undefined
    const result = isJsonRecord(workflow.result) ? workflow.result : undefined
    const summary = isJsonRecord(workflow.summary) ? workflow.summary : undefined
    const clarification = isJsonRecord(summary?.clarification) ? summary.clarification : undefined
    const review = isJsonRecord(clarification?.review) ? clarification.review : undefined
    const reviewSummary = isJsonRecord(review?.summary) ? review.summary : undefined
    const apiContractId =
      normalizeSessionEndpointField(state?.selectedApiContractId) ||
      normalizeSessionEndpointField(state?.selected_api_contract_id) ||
      normalizeSessionEndpointField(result?.selectedApiContractId) ||
      normalizeSessionEndpointField(result?.selected_api_contract_id) ||
      normalizeSessionEndpointField(reviewSummary?.selectedApiContractId)
    const endpointId =
      normalizeSessionEndpointField(state?.selectedEndpointId) ||
      normalizeSessionEndpointField(state?.selected_endpoint_id) ||
      normalizeSessionEndpointField(result?.selectedEndpointId) ||
      normalizeSessionEndpointField(result?.selected_endpoint_id) ||
      normalizeSessionEndpointField(reviewSummary?.selectedEndpointId)
    if (apiContractId && endpointId) return { apiContractId, endpointId }
  }
  return {}
}

/** 从旧版消息中的 Workflow 状态快照推断实体会话归属。 */
function inferSessionEntityContext(messages: JsonRecord[]): {
  entityId?: string
  entityLabel?: string
} {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const workflow = messages[index].workflow
    if (!isJsonRecord(workflow)) continue
    const state = isJsonRecord(workflow.state) ? workflow.state : undefined
    const result = isJsonRecord(workflow.result) ? workflow.result : undefined
    const summary = isJsonRecord(workflow.summary) ? workflow.summary : undefined
    const clarification = isJsonRecord(summary?.clarification) ? summary.clarification : undefined
    const review = isJsonRecord(clarification?.review) ? clarification.review : undefined
    const reviewSummary = isJsonRecord(review?.summary) ? review.summary : undefined
    const entityId =
      normalizeSessionEndpointField(state?.selectedEntityId) ||
      normalizeSessionEndpointField(state?.selected_entity_id) ||
      normalizeSessionEndpointField(result?.selectedEntityId) ||
      normalizeSessionEndpointField(result?.selected_entity_id) ||
      normalizeSessionEndpointField(reviewSummary?.selectedEntityId)
    if (entityId) return { entityId }
  }
  return {}
}

/** 从会话标题中恢复实体展示名，兼容旧标题。 */
function inferSessionEntityLabelFromTitle(value: unknown): string | undefined {
  const title = typeof value === 'string' ? value.trim() : ''
  const matched = title.match(/(?:设计实体|确认实体|开始设计实体|查看已生成实体计划)：(.+)$/)
  return matched?.[1]?.trim() || undefined
}

/** 从会话标题中恢复接口展示名，兼容旧标题。 */
function inferEndpointLabelFromTitle(value: unknown): string | undefined {
  const title = typeof value === 'string' ? value.trim() : ''
  const matched = title.match(/(?:设计接口|确认接口|开始设计接口|查看已生成接口计划)：(.+)$/)
  return matched?.[1]?.trim() || undefined
}

async function readSessionSummariesFromDir(
  sessionsDir: string,
  editorMode: EditorMode
): Promise<ChatSessionSummary[]> {
  let entries
  try {
    entries = await fs.readdir(sessionsDir, { withFileTypes: true })
  } catch {
    return []
  }

  const sessions: ChatSessionSummary[] = []
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith('.json')) continue
    try {
      const rawValue = await fs.readFile(path.join(sessionsDir, entry.name), 'utf8')
      const session = normalizeSession(JSON.parse(rawValue || '{}'))
      if (session.editorMode !== editorMode) continue
      sessions.push(sessionSummary(session))
    } catch {
      // Ignore malformed session files so one bad record does not hide the workspace.
    }
  }
  return sessions
}

async function listSessionWorkspaces(): Promise<SessionWorkspaceSummary[]> {
  const sessionsRoot = getSessionStorageRoot()
  let entries
  try {
    entries = await fs.readdir(sessionsRoot, { withFileTypes: true })
  } catch {
    return []
  }

  const workspaces: SessionWorkspaceSummary[] = []
  for (const entry of entries) {
    if (!entry.isDirectory()) continue

    const workspaceSessionRoot = path.join(sessionsRoot, entry.name)
    let workspaceRoot: string
    try {
      const rawWorkspace = await fs.readFile(
        path.join(workspaceSessionRoot, 'workspace.json'),
        'utf8'
      )
      const workspaceRecord = JSON.parse(rawWorkspace || '{}')
      workspaceRoot = resolveWorkspaceRoot(workspaceRecord.workspaceRoot)
    } catch {
      continue
    }

    const frontendSessions = await readSessionSummariesFromDir(
      path.join(workspaceSessionRoot, 'frontend'),
      'frontend'
    )
    const backendSessions = await readSessionSummariesFromDir(
      path.join(workspaceSessionRoot, 'backend'),
      'backend'
    )
    const allSessions = [...frontendSessions, ...backendSessions].sort(
      (a, b) => b.updatedAt - a.updatedAt
    )
    if (allSessions.length === 0) continue

    const latestSession = allSessions[0]
    workspaces.push({
      workspaceRoot,
      name: path.basename(workspaceRoot) || workspaceRoot,
      sessionCount: allSessions.length,
      frontendCount: frontendSessions.length,
      backendCount: backendSessions.length,
      latestUpdatedAt: latestSession.updatedAt,
      latestTitle: latestSession.title
    })
  }

  return workspaces.sort((a, b) => b.latestUpdatedAt - a.latestUpdatedAt)
}

/** 注册工作区读取、选择和项目创建相关 IPC。 */
function setupWorkspaceIpc(): void {
  ipcMain.handle('workspace:inspect-planning-artifacts', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot)
    return inspectWorkspacePlanningArtifacts(workspaceRoot)
  })

  ipcMain.handle('workspace:read-application', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot)
    const applicationConfig = await readManagedWorkspaceApplication(workspaceRoot)
    return { application: applicationConfig }
  })

  ipcMain.handle('workspace:select-directory', async (_event, options = {}) => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: typeof options.title === 'string' ? options.title : '选择工作目录',
      properties: ['openDirectory', 'createDirectory']
    })

    return {
      canceled: result.canceled,
      path: result.filePaths[0]
    }
  })

  ipcMain.handle('workspace:create-project-directory', async (_event, payload = {}) => {
    if (typeof payload.workspacePath !== 'string' || !payload.workspacePath.trim()) {
      throw new Error('workspacePath must be a non-empty string')
    }
    if (
      !payload.applicationConfig ||
      typeof payload.applicationConfig !== 'object' ||
      Array.isArray(payload.applicationConfig)
    ) {
      throw new Error('applicationConfig must be an object')
    }

    const projectPath = path.resolve(payload.workspacePath)
    await assertNewProjectDirectory(projectPath)
    if (!(await lstatIfPresent(projectPath))) {
      await fs.mkdir(projectPath, { recursive: false })
    }
    const applicationFile = getWorkspaceApplicationFile(projectPath)
    await fs.mkdir(path.dirname(applicationFile), { recursive: true })
    await fs.writeFile(applicationFile, `${JSON.stringify(payload.applicationConfig, null, 2)}\n`, {
      encoding: 'utf8',
      flag: 'wx'
    })

    return {
      ok: true,
      path: projectPath
    }
  })

  type TemplateCloneTargetResult = {
    status: 'succeeded' | 'failed' | 'pending'
    attempt: number
    path: string
    error?: string
  }

  /** 判断模板目录是否包含可识别的工程入口文件。 */
  async function isTemplateDirectoryReady(
    targetDir: string,
    targetDirName: string
  ): Promise<boolean> {
    const markers =
      targetDirName === 'frontend'
        ? ['package.json']
        : ['pom.xml', 'build.gradle', 'build.gradle.kts']
    for (const marker of markers) {
      if (await lstatIfPresent(path.join(targetDir, marker))) return true
    }
    return false
  }

  /** 拉取单个模板仓库；已有有效目录直接复用，失败时最多尝试三次。 */
  async function cloneGitRepo(
    templateUrl: string,
    projectPath: string,
    targetDirName: string
  ): Promise<TemplateCloneTargetResult> {
    const targetDir = path.join(projectPath, targetDirName)
    await fs.mkdir(path.dirname(targetDir), { recursive: true })

    if (await isTemplateDirectoryReady(targetDir, targetDirName)) {
      return { status: 'succeeded', attempt: 0, path: targetDir }
    }

    const existing = await lstatIfPresent(targetDir)
    if (existing) {
      const entries = existing.isDirectory() ? await fs.readdir(targetDir) : ['occupied']
      if (entries.length > 0) {
        return {
          status: 'failed',
          attempt: 0,
          path: targetDir,
          error: `${targetDirName} 目录已存在但不是可识别的模板工程，为避免覆盖现有文件已停止下载。`
        }
      }
    }

    let cloneError: Error | null = null
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        await removeDirectoryIfPresent(targetDir)
      } catch (error) {
        cloneError = error instanceof Error ? error : new Error(String(error))
        if (attempt === 3) break
        continue
      }
      try {
        await new Promise<void>((resolve, reject) => {
          execFile(
            'git',
            ['clone', '--depth', '1', templateUrl, targetDir],
            {
              timeout: 120000,
              maxBuffer: 10 * 1024 * 1024,
              windowsHide: true,
              env: {
                ...process.env,
                GIT_TERMINAL_PROMPT: '0',
                GCM_INTERACTIVE: 'Never'
              }
            },
            (error, _stdout, stderr) => {
              if (error) {
                reject(new Error(`git clone 失败：${error.message}${stderr ? `\n${stderr}` : ''}`))
                return
              }
              resolve()
            }
          )
        })
        if (!(await isTemplateDirectoryReady(targetDir, targetDirName))) {
          throw new Error(`git clone 完成，但 ${targetDirName} 模板缺少工程入口文件。`)
        }
        cloneError = null
        await removeDirectoryIfPresent(path.join(targetDir, '.git'))
        return { status: 'succeeded', attempt, path: targetDir }
      } catch (error) {
        cloneError = error instanceof Error ? error : new Error(String(error))
      }
    }

    try {
      await removeDirectoryIfPresent(targetDir)
    } catch (cleanupError) {
      const cleanupMessage =
        cleanupError instanceof Error ? cleanupError.message : String(cleanupError)
      cloneError = new Error(
        `${cloneError?.message || '模板下载失败'}；清理半成品失败：${cleanupMessage}`
      )
    }
    return {
      status: 'failed',
      attempt: 3,
      path: targetDir,
      error: cloneError?.message || `${targetDirName} 模板下载失败。`
    }
  }

  // 从远程模板仓库拉取前后端模板工程，放到 <项目位置>/frontend/ 和 <项目位置>/backend/ 下。
  ipcMain.handle('workspace:clone-template', async (_event, payload = {}) => {
    if (typeof payload.projectPath !== 'string' || !payload.projectPath.trim()) {
      throw new Error('projectPath must be a non-empty string')
    }
    if (typeof payload.appName !== 'string' || !payload.appName.trim()) {
      throw new Error('appName must be a non-empty string')
    }
    const frontendUrl =
      typeof payload.frontendTemplateUrl === 'string' && payload.frontendTemplateUrl.trim()
        ? payload.frontendTemplateUrl.trim()
        : 'https://github.com/ruyue1/frontend-template.git'
    const backendUrl =
      typeof payload.backendTemplateUrl === 'string' && payload.backendTemplateUrl.trim()
        ? payload.backendTemplateUrl.trim()
        : 'https://github.com/Hupy2118/springboot-template.git'

    const projectPath = path.resolve(payload.projectPath)

    const frontend = await cloneGitRepo(frontendUrl, projectPath, 'frontend')
    const backend =
      frontend.status === 'failed'
        ? {
            status: 'pending' as const,
            attempt: 0,
            path: path.join(projectPath, 'backend'),
            error: '前端模板下载失败，后端模板尚未开始下载。'
          }
        : await cloneGitRepo(backendUrl, projectPath, 'backend')
    const failedTargets = (['frontend', 'backend'] as const).filter(
      (target) => ({ frontend, backend })[target].status === 'failed'
    )
    return {
      ok: failedTargets.length === 0,
      status: failedTargets.length === 0 ? 'succeeded' : 'failed',
      failedTargets,
      targets: { frontend, backend }
    }
  })
}

function setupSessionStorageIpc(): void {
  ipcMain.handle('sessions:list-workspaces', async () => ({
    workspaces: await listSessionWorkspaces()
  }))

  ipcMain.handle('sessions:list', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot)
    const editorMode = assertEditorMode(payload.editorMode)
    const sessionsDir = await ensureSessionsDir(workspaceRoot, editorMode)

    const entries = await fs.readdir(sessionsDir, { withFileTypes: true })
    const sessions: ChatSessionSummary[] = []
    for (const entry of entries) {
      if (!entry.isFile() || !entry.name.endsWith('.json')) continue
      try {
        const rawValue = await fs.readFile(path.join(sessionsDir, entry.name), 'utf8')
        const session = normalizeSession(JSON.parse(rawValue || '{}'))
        sessions.push(sessionSummary(session))
      } catch {
        // Ignore malformed session files so one bad record does not hide the full history.
      }
    }

    sessions.sort((a, b) => b.updatedAt - a.updatedAt)
    return { sessions }
  })

  ipcMain.handle('sessions:read', async (_event, payload = {}) => {
    const sessionFile = getSessionFile(payload.workspaceRoot, payload.editorMode, payload.sessionId)
    const rawValue = await fs.readFile(sessionFile, 'utf8')
    return { session: normalizeSession(JSON.parse(rawValue || '{}')) }
  })

  ipcMain.handle('sessions:save', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot)
    const session = normalizeSession({
      ...payload.session,
      workspaceRoot
    })
    await ensureSessionsDir(workspaceRoot, session.editorMode)
    await fs.writeFile(
      getSessionFile(workspaceRoot, session.editorMode, session.id),
      `${JSON.stringify(session, null, 2)}\n`,
      'utf8'
    )
    return { ok: true, session: sessionSummary(session) }
  })

  ipcMain.handle('sessions:delete', async (_event, payload = {}) => {
    const sessionFile = getSessionFile(payload.workspaceRoot, payload.editorMode, payload.sessionId)
    await fs.rm(sessionFile, { force: true })
    return { ok: true }
  })
}

/** 注册登录、内存 token 读取和重新认证所需的 Electron IPC。 */
function setupAuthIpc(): void {
  ipcMain.handle('auth:status', async () => ({
    authenticated: hasValidAuthToken()
  }))

  ipcMain.handle('auth:get-access-token', async () => ({
    accessToken: getAccessToken()
  }))

  ipcMain.handle('auth:login', async () => {
    await loginWithCmbDeviceFlow()
    if (loginWindow && !loginWindow.isDestroyed()) {
      loginWindow.destroy()
    }
    loginWindow = null
    createMainWindow()
    return { ok: true }
  })

  ipcMain.handle('auth:reauthenticate', async () => {
    await clearAuthState()
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.hide()
    }
    createLoginWindow()
    return { ok: true }
  })
}

type RendererPage = 'index' | 'login'

function loadRendererPage(targetWindow: BrowserWindow, pageName: RendererPage): void {
  const rendererUrl = process.env['ELECTRON_RENDERER_URL']
  if (rendererUrl) {
    const pageUrl =
      pageName === 'index' ? rendererUrl : `${rendererUrl.replace(/\/$/, '')}/${pageName}.html`
    void targetWindow.loadURL(pageUrl)
    return
  }

  void targetWindow.loadFile(join(__dirname, `../renderer/${pageName}.html`))
}

function createMainWindow(): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.show()
    mainWindow.focus()
    return
  }

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 720,
    minHeight: 600,
    title: 'XCode Agent',
    backgroundColor: '#f5f7fb',
    show: false,
    autoHideMenuBar: true,
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--xcode-agent-base-url=${getBackendBaseUrl()}`]
    }
  })

  mainWindow.setMenuBarVisibility(false)
  mainWindow.on('close', (event) => {
    if (isQuitting) return
    event.preventDefault()
    mainWindow?.hide()
  })
  mainWindow.on('closed', () => {
    mainWindow = null
  })
  mainWindow.on('ready-to-show', () => {
    mainWindow?.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  loadRendererPage(mainWindow, 'index')
}

/** 创建或聚焦带有自定义关闭控件的无边框登录窗口。 */
function createLoginWindow(): void {
  if (loginWindow && !loginWindow.isDestroyed()) {
    loginWindow.show()
    loginWindow.focus()
    return
  }

  loginWindow = new BrowserWindow({
    width: 880,
    height: 620,
    minWidth: 840,
    minHeight: 580,
    title: 'XCode Agent 登录',
    backgroundColor: '#2f1d49',
    frame: false,
    hasShadow: false,
    show: false,
    autoHideMenuBar: true,
    minimizable: false,
    maximizable: false,
    resizable: false,
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--xcode-agent-base-url=${getBackendBaseUrl()}`]
    }
  })

  loginWindow.setMenuBarVisibility(false)
  loginWindow.on('close', (event) => {
    if (isQuitting) return
    event.preventDefault()
    loginWindow?.hide()
  })
  loginWindow.on('closed', () => {
    loginWindow = null
  })
  loginWindow.on('ready-to-show', () => {
    loginWindow?.show()
  })

  loadRendererPage(loginWindow, 'login')
}

/** 根据主进程内存中的登录态打开主窗口或登录窗口。 */
async function openAuthenticatedWindow(): Promise<void> {
  if (hasValidAuthToken()) {
    if (loginWindow && !loginWindow.isDestroyed()) {
      loginWindow.hide()
    }
    createMainWindow()
    return
  }

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.hide()
  }
  createLoginWindow()
}

/** 从托盘发起统一的应用退出流程。 */
function quitFromTray(): void {
  isQuitting = true
  app.quit()
}

function setupTray(): void {
  if (tray) return

  const trayIcon =
    process.platform === 'darwin'
      ? nativeImage.createFromPath(icon).resize({ width: 16, height: 16 })
      : nativeImage.createFromPath(icon)
  if (process.platform === 'darwin') {
    trayIcon.setTemplateImage(true)
  }
  tray = new Tray(trayIcon)
  tray.setToolTip('XCode Agent')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: '打开主窗口',
        click: () => {
          void openAuthenticatedWindow()
        }
      },
      { type: 'separator' },
      {
        label: '退出',
        click: () => {
          quitFromTray()
        }
      }
    ])
  )
  tray.on('click', () => {
    void openAuthenticatedWindow()
  })
}

/** 在冷启动阶段清除残留认证；失败时提示用户并阻止应用继续初始化。 */
async function clearAuthStateBeforeStartup(): Promise<boolean> {
  try {
    await ensureXcodeAgentDataDir()
    await clearAuthState()
    return true
  } catch (error) {
    const authFile = path.join(getXcodeAgentDataDir(), 'auth.json')
    console.error('Failed to clear auth token during startup', error)
    dialog.showErrorBox(
      '认证状态清理失败',
      `无法清理本地登录凭证，应用将退出。\n请检查文件权限后重试：\n${authFile}`
    )
    app.quit()
    return false
  }
}

/** 初始化获得单实例锁的主进程，成功后才允许窗口恢复。 */
async function initializePrimaryApplication(): Promise<boolean> {
  // Set app user model id for windows
  if (process.platform === 'win32') {
    app.setAppUserModelId(process.execPath)
  }

  if (!(await clearAuthStateBeforeStartup())) return false

  // 仅非生产环境开放跨平台开发者工具快捷键。
  if (XCODE_AGENT_ENV.WORKING_DIR !== '.xcodeagent') {
    app.on('browser-window-created', (_, window) => {
      window.webContents.on('before-input-event', (_event, input) => {
        const isDevToolsShortcut =
          input.key === 'F12' ||
          ((input.control || input.meta) && input.alt && input.key.toLowerCase() === 'i')
        if (isDevToolsShortcut) {
          window.webContents.toggleDevTools()
        }
      })
    })
  }

  // IPC test
  ipcMain.on('ping', () => console.log('pong'))
  const backendBaseUrl = await startBackendService()
  console.log(`XCode Agent backend URL: ${backendBaseUrl}`)
  setupApplicationStorageIpc()
  setupApplicationSettingsIpc()
  setupAuthIpc()
  setupBrowserIpc()
  setupProjectPreviewIpc()
  setupWorkspaceIpc()
  setupSessionStorageIpc()
  setupTray()

  await openAuthenticatedWindow()

  app.on('activate', function () {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    void openAuthenticatedWindow()
  })

  return true
}

/** 处理主实例初始化中的非认证清理异常。 */
function handlePrimaryStartupFailure(error: unknown): boolean {
  console.error('Failed to start XCode Agent', error)
  app.quit()
  return false
}

/** 第二实例启动时等待主实例完成初始化，然后聚焦当前登录窗口或主窗口。 */
async function focusPrimaryWindowAfterStartup(): Promise<void> {
  const startupPromise = primaryStartupPromise
  if (!startupPromise || !(await startupPromise)) return
  await openAuthenticatedWindow()
}

/** 接收第二实例通知，避免第二进程触碰当前实例的认证文件。 */
function handleSecondInstance(): void {
  void focusPrimaryWindowAfterStartup().catch((error) => {
    console.error('Failed to focus the primary XCode Agent window', error)
  })
}

if (!hasSingleInstanceLock) {
  app.quit()
} else {
  app.on('second-instance', handleSecondInstance)
  primaryStartupPromise = app
    .whenReady()
    .then(initializePrimaryApplication)
    .catch(handlePrimaryStartupFailure)
}

let quitCleanupCompleted = false
let quitCleanupStarted = false

/** 在应用退出前清除认证状态并停止本地后端服务。 */
async function cleanupBeforeQuit(): Promise<void> {
  try {
    await clearAuthState()
  } catch (error) {
    console.error('Failed to clear auth token', error)
  }

  try {
    await stopGeneratedProjectPreviewsBeforeQuit()
  } catch (error) {
    console.error('Failed to stop generated project previews', error)
  }

  try {
    await stopBackendService()
  } catch (error) {
    console.error('Failed to stop backend service', error)
  }
}

if (hasSingleInstanceLock) {
  app.on('before-quit', (event) => {
    isQuitting = true
    if (quitCleanupCompleted) return

    event.preventDefault()
    if (quitCleanupStarted) return
    quitCleanupStarted = true

    void cleanupBeforeQuit().finally(() => {
      quitCleanupCompleted = true
      app.quit()
    })
  })

  // 普通关闭窗口时保持托盘运行，显式退出由 before-quit 统一清理。
  app.on('window-all-closed', () => {
    // 不在此处退出应用。
  })
}

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and require them here.
