import type { ApplicationMenuItem, ApplicationSchemaConfig, WorkflowRunPayload } from '../typings'

export interface TemplateInitRequest {
  appName: string
  appIcon: string
  senario: string
  terminal: string
  layout: ApplicationSchemaConfig['layout']
  theme: ApplicationSchemaConfig['theme']
  datasource: ApplicationSchemaConfig['datasource']
  env: string[]
  menus: ApplicationSchemaConfig['menus']
  auth: ApplicationSchemaConfig['auth']
  track: ApplicationSchemaConfig['track']
  apiTrack: ApplicationSchemaConfig['apiTrack']
}

export interface TemplateInitResponse {
  code: number
  message: string
  data: {
    templateVersion: string
    generatedAt: number
    fileCount?: number
  }
}

type TemplatePageWriteItem = {
  pageKey: string
  name?: string
}

/** 默认前端模板仓库地址。 */
export const DEFAULT_TEMPLATE_REPO_URL = 'https://github.com/ruyue1/frontend-template.git'

/**
 * 拉取前端模板工程代码。
 *
 * 在 Electron 桌面客户端中，通过主进程执行 `git clone`，把模板工程完整代码
 * 放到 `<projectPath>/frontend/` 目录下；非 Electron 环境
 * （如纯浏览器）下回退为只返回模板元信息。
 *
 * @param schema 应用 schema，用于读取应用名称等元信息
 * @param projectPath 当前应用指定的项目位置（workspaceRoot）
 */
export async function fetchTemplateCode(
  schema: ApplicationSchemaConfig,
  projectPath: string
): Promise<TemplateInitResponse> {
  const appName = schema.appName.trim()
  if (!appName) {
    throw new Error('应用名称不能为空，无法拉取模板工程。')
  }
  if (!projectPath || !projectPath.trim()) {
    throw new Error('项目位置不能为空，无法拉取模板工程。')
  }

  const workspaceApi = window.xcodeAgent?.workspace
  if (!workspaceApi?.cloneTemplate) {
    // 非 Electron 环境：回退为仅返回元信息
    console.warn('[templateApi] 当前环境不支持拉取模板工程，跳过 git clone。')
    return {
      code: 0,
      message: 'success',
      data: {
        templateVersion: '1.0.0',
        generatedAt: Date.now(),
        fileCount: 0
      }
    }
  }

  await workspaceApi.cloneTemplate({
    projectPath,
    appName,
    templateUrl: DEFAULT_TEMPLATE_REPO_URL
  })

  return {
    code: 0,
    message: 'success',
    data: {
      templateVersion: '1.0.0',
      generatedAt: Date.now()
    }
  }
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

/** 读取 workflow 中最新的 ProjectPlan，供模板文件和菜单初始化使用。 */
function projectPlanFromWorkflow(
  workflow: WorkflowRunPayload | undefined
): Record<string, unknown> | undefined {
  if (!workflow) return undefined
  for (const source of [workflow.result, workflow.state]) {
    const projectPlan = source?.project_plan
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

/** 递归把 ProjectPlan.frontend_pages 转成可写入模板 menus.ts 的 ApplicationMenuItem[]。 */
function buildTemplateMenuItems(
  value: unknown,
  pageKeysByIdentity: Map<string, string>,
  parentAbsolutePath = '',
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
    const resolvedPath =
      pagePath || (absolutePath === parentAbsolutePath ? '' : absolutePath.replace(/^\/+/, '') || pageKey)
    return [{
      key: pageKey,
      name: typeof record.name === 'string' ? record.name : pageKey,
      path: resolvedPath
    }]
  })
}

/** 递归拍平 ProjectPlan.frontend_pages，仅保留真正的页面叶子。 */
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

  const frontendPages = flattenFrontendPages(projectPlan.frontend_pages)
  if (!frontendPages.length) return { menuItems: [], pages: [] }

  const pageKeysByIdentity = new Map<string, string>()
  const seenPageKeys = new Set<string>()
  const pages: TemplatePageWriteItem[] = []
  frontendPages.forEach((page, index) => {
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
      projectPlan.frontend_pages,
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
 * @param workflow 规划完成后的 workflow 快照，含 ProjectPlan.frontend_pages
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
  console.log(
    '[templateApi] generateApplicationTemplateFiles 调用: ' +
      JSON.stringify({
        appName,
        projectPath,
        pagesCount: artifacts.pages.length,
        pages: artifacts.pages,
        menuItems: artifacts.menuItems,
        hasWorkflow: Boolean(workflow),
        resultKeys: workflow?.result ? Object.keys(workflow.result) : [],
        stateKeys: workflow?.state ? Object.keys(workflow.state) : [],
        hasProjectPlanInResult: Boolean(
          (workflow?.result as Record<string, unknown> | undefined)?.project_plan
        ),
        hasProjectPlanInState: Boolean(
          (workflow?.state as Record<string, unknown> | undefined)?.project_plan
        )
      })
  )
  if (artifacts.pages.length === 0) {
    console.warn('[templateApi] 未从规划结果中提取到页面清单，跳过页面文件生成。')
    return { written: [] }
  }

  const workspaceApi = window.xcodeAgent?.workspace
  console.log('[templateApi] workspaceApi 存在: ' + Boolean(workspaceApi) + ', writeTemplatePages: ' + Boolean(workspaceApi?.writeTemplatePages))
  if (!workspaceApi?.writeTemplatePages) {
    console.warn('[templateApi] 当前环境不支持写入页面文件，跳过。')
    return { written: [] }
  }

  console.log('[templateApi] 调用 writeTemplatePages IPC, payload=' + JSON.stringify(artifacts))
  const result = await workspaceApi.writeTemplatePages({
    projectPath,
    appName,
    pages: artifacts.pages,
    menuItems: artifacts.menuItems
  })
  console.log('[templateApi] writeTemplatePages 返回: ' + JSON.stringify(result))

  return { written: result?.written ?? [] }
}
