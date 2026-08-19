import type {
  ApplicationConfig,
  ApplicationSchemaConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntity,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption
} from '../typings'
import type { DevelopmentPlanningAgent } from '../agentDevelopment'

const STORAGE_KEY = 'aistudio-applications'
const LOCAL_FILE_API = '/api/local-applications'
export const APPLICATIONS_CHANGED_EVENT = 'aistudio-applications-changed'

// 判断创建规划是否已经完成；工作台内部运行状态不得影响该结果。
export function isApplicationCreationComplete(lifecycle?: ApplicationLifecycle): boolean {
  return lifecycle?.initialization.stage === 'ready_for_workbench'
}

// 判断应用是否已完成创建规划；当前生命周期是新应用工作台准入的唯一权威来源。
export function canOpenApplicationWorkbench(
  application: ApplicationConfig,
  lifecycle?: ApplicationLifecycle
): boolean {
  if (application.source !== 'new') return true
  return isApplicationCreationComplete(lifecycle)
}

function normalizeApplications(value: unknown): ApplicationConfig[] {
  return Array.isArray(value) ? (value as ApplicationConfig[]) : []
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function cacheApplications(applications: ApplicationConfig[]) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(applications))
}

// 通知当前渲染窗口重新校验依赖应用索引的派生状态。
function notifyApplicationsChanged() {
  window.dispatchEvent(new Event(APPLICATIONS_CHANGED_EVENT))
}

// 订阅应用索引变化，并返回用于 React effect 清理的取消函数。
export function subscribeApplicationsChanged(listener: () => void): () => void {
  window.addEventListener(APPLICATIONS_CHANGED_EVENT, listener)
  return () => window.removeEventListener(APPLICATIONS_CHANGED_EVENT, listener)
}

export function loadCachedApplications() {
  try {
    const rawValue = window.localStorage.getItem(STORAGE_KEY)
    if (!rawValue) return []
    return normalizeApplications(JSON.parse(rawValue))
  } catch {
    return []
  }
}

export async function loadStoredApplications() {
  const electronApplications = window.aiStudio?.applications

  if (electronApplications) {
    try {
      const data = await electronApplications.load()
      const applications = normalizeApplications(data.applications)
      cacheApplications(applications)
      return applications
    } catch (error) {
      console.warn(error)
    }
  }

  try {
    const response = await fetch(LOCAL_FILE_API)
    if (!response.ok) throw new Error(`Load applications failed: ${response.status}`)

    const data = (await response.json()) as { applications?: unknown }
    const applications = normalizeApplications(data.applications)
    cacheApplications(applications)
    return applications
  } catch {
    return loadCachedApplications()
  }
}

export async function saveStoredApplications(applications: ApplicationConfig[]) {
  cacheApplications(applications)

  const electronApplications = window.aiStudio?.applications

  if (electronApplications) {
    try {
      await electronApplications.save(applications)
      notifyApplicationsChanged()
      return
    } catch (error) {
      console.warn(error)
    }
  }

  try {
    const response = await fetch(LOCAL_FILE_API, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ applications })
    })
    if (!response.ok) throw new Error(`Save applications failed: ${response.status}`)
  } catch (error) {
    // 文件写入仅在本地开发服务中可用，失败时保留 localStorage 兜底。
    console.warn(error)
  }
  notifyApplicationsChanged()
}

// 从首页应用索引中移除指定项目，不会删除工作区中的任何文件。
export async function removeStoredApplication(applicationId: string) {
  const applications = await loadStoredApplications()
  await saveStoredApplications(
    applications.filter((application) => application.id !== applicationId)
  )
}

// 请求桌面主进程删除受 AIStudio 管理的真实项目目录。
export async function deleteStoredProject(workspaceRoot: string) {
  const electronApplications = window.aiStudio?.applications
  if (!electronApplications?.deleteProject) {
    throw new Error('当前环境不支持删除本地项目目录')
  }
  await electronApplications.deleteProject({ workspaceRoot })
}

// 请求桌面主进程仅删除工作区内由初始化计划生成的 .aistudio 目录。
export async function deleteStoredAgentDirectory(workspaceRoot: string) {
  const electronApplications = window.aiStudio?.applications
  if (!electronApplications?.deleteAgentDirectory) {
    throw new Error('当前环境不支持删除初始化计划目录')
  }
  await electronApplications.deleteAgentDirectory({ workspaceRoot })
}

export async function loadWorkspaceApplicationConfig(
  workspaceRoot: string
): Promise<ApplicationSchemaConfig> {
  const workspaceApi = window.aiStudio?.workspace
  if (!workspaceApi?.readApplication) {
    throw new Error('当前环境不支持读取工作区 application.json')
  }

  const result = await workspaceApi.readApplication({ workspaceRoot })
  if (!isRecord(result.application)) {
    throw new Error('工作区 application.json 格式无效')
  }
  return result.application as unknown as ApplicationSchemaConfig
}

// 检查正式规划产物，并返回 ProjectPlan 页面大纲及 pages 目录设计状态。
export async function inspectWorkspacePlanningArtifacts(
  workspaceRoot: string,
  applicationId?: string,
  versionId?: string
): Promise<{
  ready: boolean
  hasPageDesigns: boolean
  missing: string[]
  invalid: string[]
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
  apiContracts: DevelopmentPlanningApiContract[]
  entities: DevelopmentPlanningEntity[]
  agents: DevelopmentPlanningAgent[]
}> {
  const workspaceApi = window.aiStudio?.workspace
  if (!workspaceApi?.inspectPlanningArtifacts) {
    throw new Error('当前环境不支持检查工作区规划产物')
  }
  return workspaceApi.inspectPlanningArtifacts({ workspaceRoot, applicationId, versionId })
}
