import type { ApplicationSchemaConfig, WorkflowRunPayload } from '../typings'

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
    projectZipUrl?: string
    templateVersion: string
    generatedAt: number
    fileCount?: number
  }
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

  const cloneResult = await workspaceApi.cloneTemplate({
    projectPath,
    appName,
    templateUrl: DEFAULT_TEMPLATE_REPO_URL
  })

  return {
    code: 0,
    message: 'success',
    data: {
      projectZipUrl: cloneResult?.templateUrl || DEFAULT_TEMPLATE_REPO_URL,
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

/** 从 workflow 中提取规划出的前端页面清单（ProjectPlan.frontend_pages）。 */
function extractFrontendPages(
  workflow: WorkflowRunPayload | undefined
): Array<{ pageKey: string; name?: string; menuPath: string }> {
  if (!workflow) return []
  const sources = [workflow.result, workflow.state]
  for (const source of sources) {
    const projectPlan = source?.project_plan
    if (projectPlan && typeof projectPlan === 'object') {
      const plan = projectPlan as Record<string, unknown>
      const frontendPages = plan.frontend_pages
      if (!Array.isArray(frontendPages)) continue
      const seen = new Set<string>()
      const result: Array<{ pageKey: string; name?: string; menuPath: string }> = []
      frontendPages.forEach((page: Record<string, unknown>, index: number) => {
        // 优先用 pageId（后端生成的英文标识），否则从 path 推导
        const pageId = typeof page?.pageId === 'string' ? page.pageId.trim() : ''
        let pageKey = ''
        if (pageId && /^[A-Za-z][A-Za-z0-9_-]*$/.test(pageId)) {
          // pageId 转成 PascalCase 作为目录名
          pageKey = pageId
            .split(/[-_\s]+/)
            .filter(Boolean)
            .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
            .join('')
        } else {
          pageKey = pageKeyFromPath(page?.path, index)
        }
        if (!pageKey || seen.has(pageKey)) return
        seen.add(pageKey)
        // 菜单 path：取路由最后一段，去掉前导/尾随斜杠（/duty-list -> duty-list，/ -> home）
        const rawPath = String(page?.path ?? '').trim()
        const menuPath = rawPath.replace(/^\/+|\/+$/g, '').split('/').filter(Boolean).pop() || pageKey.charAt(0).toLowerCase() + pageKey.slice(1)
        result.push({
          pageKey,
          name: typeof page?.name === 'string' ? page.name : undefined,
          menuPath
        })
      })
      return result
    }
  }
  return []
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

  const pages = extractFrontendPages(workflow)
  console.log(
    '[templateApi] generateApplicationTemplateFiles 调用: ' +
      JSON.stringify({
        appName,
        projectPath,
        pagesCount: pages.length,
        pages,
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
  if (pages.length === 0) {
    console.warn('[templateApi] 未从规划结果中提取到页面清单，跳过页面文件生成。')
    return { written: [] }
  }

  const workspaceApi = window.xcodeAgent?.workspace
  console.log('[templateApi] workspaceApi 存在: ' + Boolean(workspaceApi) + ', writeTemplatePages: ' + Boolean(workspaceApi?.writeTemplatePages))
  if (!workspaceApi?.writeTemplatePages) {
    console.warn('[templateApi] 当前环境不支持写入页面文件，跳过。')
    return { written: [] }
  }

  console.log('[templateApi] 调用 writeTemplatePages IPC, pages=' + JSON.stringify(pages))
  const result = await workspaceApi.writeTemplatePages({
    projectPath,
    appName,
    pages
  })
  console.log('[templateApi] writeTemplatePages 返回: ' + JSON.stringify(result))

  return { written: result?.written ?? [] }
}
