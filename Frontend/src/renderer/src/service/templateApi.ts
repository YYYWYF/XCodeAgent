import type {
  ApplicationConfig,
  ApplicationLifecycle,
  ApplicationMenuItem,
  ApplicationSchemaConfig,
  TemplateDownloadResult,
  WorkflowRunPayload
} from '../typings'
import {
  completeApplicationTemplateGeneration,
  prepareApplicationTemplateGeneration
} from './applicationLifecycle'

/** 默认前端模板仓库地址。 */
export const DEFAULT_FRONTEND_TEMPLATE_REPO_URL = 'https://github.com/ruyue1/frontend-template.git'

/** 默认后端模板仓库地址。 */
export const DEFAULT_BACKEND_TEMPLATE_REPO_URL = 'https://github.com/Hupy2118/springboot-template.git'

/** 控制应用模板下载与初始化流程是否启用。 */
export const APPLICATION_TEMPLATE_GENERATION_ENABLED = true

const readinessTasks = new Map<string, Promise<ApplicationLifecycle>>()

type TemplatePageWriteItem = {
  pageKey: string
  name?: string
}

/** 携带模板下载结构化结果，供后端 manifest 记录失败现场。 */
class TemplateDownloadError extends Error {
  readonly result: TemplateDownloadResult

  /** 创建包含下载明细的模板下载错误。 */
  constructor(message: string, result: TemplateDownloadResult) {
    super(message)
    this.name = 'TemplateDownloadError'
    this.result = result
  }
}

/** 将工作区转换成当前桌面平台可稳定去重的任务键。 */
function templateReadinessKey(workspaceRoot: string): string {
  const value = workspaceRoot.trim().replace(/[\\/]+$/, '')
  return window.xcodeAgent?.platform === 'win32' ? value.toLowerCase() : value
}

/** 汇总前后端下载错误，避免第三次失败后被静默吞掉。 */
function templateDownloadErrorMessage(result: TemplateDownloadResult): string {
  const messages = result.failedTargets.map((target) => {
    const detail = result.targets[target]
    return `${target}（尝试 ${detail.attempt} 次）：${detail.error || '模板下载失败'}`
  })
  return messages.join('；') || '模板下载失败。'
}

/** 通过 Electron 主进程拉取或复用前后端模板，并返回每个仓库的尝试结果。 */
export async function fetchTemplateCode(
  schema: ApplicationSchemaConfig,
  projectPath: string
): Promise<TemplateDownloadResult> {
  const appName = schema.appName.trim()
  if (!appName) throw new Error('应用名称不能为空，无法拉取模板工程。')
  if (!projectPath.trim()) throw new Error('项目位置不能为空，无法拉取模板工程。')

  const cloneTemplate = window.xcodeAgent?.workspace?.cloneTemplate
  if (!cloneTemplate) throw new Error('当前环境不支持模板工程下载。')
  const result = await cloneTemplate({
    projectPath,
    appName,
    frontendTemplateUrl: DEFAULT_FRONTEND_TEMPLATE_REPO_URL,
    backendTemplateUrl: DEFAULT_BACKEND_TEMPLATE_REPO_URL
  })
  if (!result.ok) throw new TemplateDownloadError(templateDownloadErrorMessage(result), result)
  return result
}

/** 执行一次模板 readiness：下载、页面/菜单增量对账，再通过后端完成门禁。 */
async function runApplicationTemplateReadiness(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationLifecycle> {
  const workspaceRoot = application.workspaceRoot || application.projectParentPath || ''
  let failureMessage = ''

  try {
    const downloadResult = await fetchTemplateCode(application.schema, workspaceRoot)
    await prepareApplicationTemplateGeneration(application, threadId, downloadResult)
  } catch (reason) {
    failureMessage = reason instanceof Error ? reason.message : String(reason)
    if (reason instanceof TemplateDownloadError) {
      try {
        await prepareApplicationTemplateGeneration(application, threadId, reason.result)
      } catch (prepareReason) {
        const prepareMessage =
          prepareReason instanceof Error ? prepareReason.message : String(prepareReason)
        failureMessage = `${failureMessage}；${prepareMessage}`
      }
    }
  }

  const lifecycle = await completeApplicationTemplateGeneration(
    application,
    threadId,
    !failureMessage,
    failureMessage || undefined
  )
  if (lifecycle.initialization.stage !== 'ready_for_workbench') {
    throw new Error(lifecycle.error?.message || failureMessage || '应用模板初始化未通过完成门禁。')
  }
  return lifecycle
}

/** 以工作区为粒度合并并发 readiness；任务结束后允许下一次进入重新对账。 */
export function ensureApplicationTemplateReadiness(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationLifecycle> {
  const workspaceRoot = application.workspaceRoot || application.projectParentPath || ''
  if (!workspaceRoot.trim()) return Promise.reject(new Error('应用缺少 workspaceRoot。'))
  const key = templateReadinessKey(workspaceRoot)
  const current = readinessTasks.get(key)
  if (current) return current

  const task = runApplicationTemplateReadiness(application, threadId)
  readinessTasks.set(key, task)
  void task
    .finally(() => {
      if (readinessTasks.get(key) === task) readinessTasks.delete(key)
    })
    .catch(() => undefined)
  return task
}

/** 从 ProjectPlan 的 path（如 /duty-list）推导英文 PascalCase pageKey（如 DutyList）。 */
function pageKeyFromPath(rawPath: unknown, fallbackIndex: number): string {
  const text = String(rawPath ?? '').trim()
  // 取路径最后一段作为基础名：/duty-list -> duty-list，/ -> home
  const last = text.replace(/^\/+|\/+$/g, '').split('/').filter(Boolean).pop()
  const base = last && last !== '' ? last : 'home'
  // 按连字符/下划线/空格拆分，每段首字母大写，拼接成 PascalCase
  const pascal = base
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join('')
  // 只保留字母数字，确保首字符是字母
  const cleaned = pascal.replace(/[^A-Za-z0-9]/g, '')
  if (/^[A-Za-z]/.test(cleaned)) return cleaned
  return `Page${fallbackIndex + 1}`
}

/** 从页面标识推导模板工程目录名，优先复用 pageId 的业务语义。 */
function pageKeyFromPageId(rawPageId: unknown, fallbackPath: unknown, fallbackIndex: number): string {
  const pageId = String(rawPageId ?? '').trim()
  if (!pageId) return pageKeyFromPath(fallbackPath, fallbackIndex)
  const pascal = pageId
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join('')
    .replace(/[^A-Za-z0-9]/g, '')
  if (/^[A-Za-z]/.test(pascal)) return pascal
  return pageKeyFromPath(fallbackPath, fallbackIndex)
}

/** 读取 workflow 中当前流程的计划，供模板文件和菜单初始化使用。 */
function projectPlanFromWorkflow(
  workflow: WorkflowRunPayload | undefined
): Record<string, unknown> | undefined {
  if (!workflow) return undefined
  const sources = [workflow.result, workflow.state]
  const applicationPlanning = sources.some((source) =>
    source?.workflow_scope === 'application_planning' || source?.workflowScope === 'application_planning'
  )
  for (const source of sources) {
    const projectPlan = applicationPlanning ? source?.technical_plan : source?.project_plan
    if (projectPlan && typeof projectPlan === 'object' && !Array.isArray(projectPlan)) {
      return projectPlan as Record<string, unknown>
    }
  }
  return undefined
}

/** 读取 ProjectPlan.app.route_root_path，供模板菜单生成相对路由段时剥离根前缀。 */
function projectPlanRouteRootPath(projectPlan: Record<string, unknown> | undefined): string {
  const app = projectPlan?.app
  if (!app || typeof app !== 'object' || Array.isArray(app)) return ''
  return normalizeAbsoluteRoute((app as Record<string, unknown>).route_root_path)
}

/** 为 ProjectPlan 页面叶子生成稳定标识，供页面目录和菜单叶子复用同一 key。 */
function frontendPageIdentity(page: Record<string, unknown>, fallbackIndex: number): string {
  const pageId = String(page.pageId ?? page.id ?? '').trim()
  if (pageId) return pageId
  const path = String(page.path ?? '').trim()
  if (path) return path
  return `page-${fallbackIndex + 1}`
}

/** 把任意路由规范成以 / 开头、无尾随 / 的绝对路径文本。 */
function normalizeAbsoluteRoute(rawPath: unknown): string {
  const value = String(rawPath ?? '').trim()
  if (!value) return ''
  const normalized = value.startsWith('/') ? value : `/${value}`
  return normalized.replace(/\/+/g, '/').replace(/\/$/, '') || '/'
}

/** 判断路由是否包含 React Router 动态路径段。 */
function hasReactRouterPathParam(rawPath: unknown): boolean {
  const value = String(rawPath ?? '').trim()
  if (!value) return false
  return value
    .split(/[?#]/, 1)[0]
    .split('/')
    .some((segment) => /^:[A-Za-z0-9_][A-Za-z0-9_-]*$/.test(segment))
}

/** 把子节点绝对路由转换成相对父节点的菜单 path，避免重复拼接父级路径。 */
function relativeMenuPath(currentAbsolutePath: string, parentAbsolutePath: string): string {
  if (!currentAbsolutePath) return ''
  if (!parentAbsolutePath) return currentAbsolutePath.replace(/^\/+/, '')
  if (currentAbsolutePath === parentAbsolutePath) return ''
  if (
    currentAbsolutePath !== parentAbsolutePath &&
    currentAbsolutePath.startsWith(`${parentAbsolutePath}/`)
  ) {
    return currentAbsolutePath.slice(parentAbsolutePath.length + 1)
  }
  return currentAbsolutePath.replace(/^\/+/, '')
}

/** 递归把当前计划 pages 转成可写入模板 menus.ts 的 ApplicationMenuItem[]。 */
function buildTemplateMenuItems(
  value: unknown,
  pageKeysByIdentity: Map<string, string>,
  parentAbsolutePath = '',
  rootAbsolutePath = parentAbsolutePath,
  counters = { menu: 0, page: 0 }
): ApplicationMenuItem[] {
  if (!Array.isArray(value)) return []
  return value.flatMap<ApplicationMenuItem>((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const record = item as Record<string, unknown>
    const pageId = String(record.pageId ?? record.id ?? '').trim()
    if (!pageId && Array.isArray(record.children)) {
      counters.menu += 1
      const absolutePath = normalizeAbsoluteRoute(record.unique_path)
      const currentPath = relativeMenuPath(absolutePath, parentAbsolutePath)
      const children = buildTemplateMenuItems(
        record.children,
        pageKeysByIdentity,
        absolutePath || parentAbsolutePath,
        rootAbsolutePath,
        counters
      )
      if (!children.length) return []
      return [{
        name: String(record.name || `菜单 ${counters.menu}`),
        ...(currentPath ? { path: currentPath } : {}),
        children
      }]
    }

    counters.page += 1
    const identity = frontendPageIdentity(record, counters.page - 1)
    const pageKey = pageKeysByIdentity.get(identity)
    if (!pageKey) return []
    const absolutePath = normalizeAbsoluteRoute(record.path)
    const pagePath = relativeMenuPath(absolutePath, parentAbsolutePath)
    const rootRelativePagePath = relativeMenuPath(absolutePath, rootAbsolutePath)
    // 当上游菜单路径与页面路径相同时，避免把可点击页面静默写成空 path。
    const resolvedPath =
      pagePath || rootRelativePagePath || absolutePath.replace(/^\/+/, '') || pageKey
    return [{
      key: pageKey,
      name: typeof record.name === 'string' ? record.name : pageKey,
      path: resolvedPath,
      ...(hasReactRouterPathParam(absolutePath) ? { hideInMenu: true } : {})
    }]
  })
}

/** 递归拍平当前计划 pages，仅保留真正的页面叶子。 */
function flattenFrontendPages(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const record = item as Record<string, unknown>
    const pageId = typeof record.pageId === 'string'
      ? record.pageId.trim()
      : typeof record.id === 'string'
        ? record.id.trim()
        : ''
    const self = pageId ? [record] : []
    const children = Array.isArray(record.children) ? flattenFrontendPages(record.children) : []
    return [...self, ...children]
  })
}

/** 从 workflow 中提取模板初始化所需的页面文件清单与菜单树。 */
function extractFrontendTemplateArtifacts(
  workflow: WorkflowRunPayload | undefined
): { menuItems: ApplicationMenuItem[]; pages: TemplatePageWriteItem[] } {
  const projectPlan = projectPlanFromWorkflow(workflow)
  if (!projectPlan) return { menuItems: [], pages: [] }

  const sourcePages = flattenFrontendPages(projectPlan.pages)
  if (!sourcePages.length) return { menuItems: [], pages: [] }

  const pageKeysByIdentity = new Map<string, string>()
  const seenPageKeys = new Set<string>()
  const pages: TemplatePageWriteItem[] = []
  sourcePages.forEach((page, index) => {
    const identity = frontendPageIdentity(page, index)
    let pageKey = pageKeyFromPageId(page.pageId ?? page.id, page.path, index)
    if (!pageKey) return
    if (seenPageKeys.has(pageKey)) {
      let suffix = 2
      while (seenPageKeys.has(`${pageKey}${suffix}`)) suffix += 1
      pageKey = `${pageKey}${suffix}`
    }
    seenPageKeys.add(pageKey)
    pageKeysByIdentity.set(identity, pageKey)
    pages.push({
      pageKey,
      name: typeof page.name === 'string' ? page.name : undefined
    })
  })

  return {
    pages,
    menuItems: buildTemplateMenuItems(
      projectPlan.pages,
      pageKeysByIdentity,
      projectPlanRouteRootPath(projectPlan)
    )
  }
}

/**
 * 根据规划产出的页面清单，在模板工程 frontend/src/pages/ 下
 * 追加每个页面的占位文件（<PageKey>/index.tsx，内容为 hello agent!）。
 *
 * @param schema 应用 schema，用于读取应用名称
 * @param projectPath 当前应用指定的项目位置
 * @param workflow 规划完成后的 workflow 快照，含当前计划 pages
 */
export async function generateApplicationTemplateFiles(
  schema: ApplicationSchemaConfig,
  projectPath: string,
  workflow: WorkflowRunPayload | undefined
): Promise<{ written: Array<{ pageKey: string; path: string }> }> {
  const appName = schema.appName.trim()
  if (!appName) {
    throw new Error('应用名称不能为空，无法生成页面文件。')
  }
  if (!projectPath || !projectPath.trim()) {
    throw new Error('项目位置不能为空，无法生成页面文件。')
  }

  const artifacts = extractFrontendTemplateArtifacts(workflow)
  if (artifacts.pages.length === 0) {
    console.warn('未从规划结果中提取到页面清单，跳过页面文件生成。')
    return { written: [] }
  }

  const workspaceApi = window.xcodeAgent?.workspace
  if (!workspaceApi?.writeTemplatePages) {
    console.warn('当前环境不支持写入页面文件，跳过模板页生成。')
    return { written: [] }
  }

  const result = await workspaceApi.writeTemplatePages({
    projectPath,
    appName,
    pages: artifacts.pages,
    menuItems: artifacts.menuItems
  })

  return { written: result?.written ?? [] }
}
