// Mock preload：在 main.tsx 之前注入，构造 window.xcodeAgent（替代 Electron IPC）
// 并拦截后端 HTTP，让真实组件在浏览器里用 mock 数据运行。
// 抑制原型演示时的控制台噪音：antd v4 废弃 API 警告（Tooltip visible / Collapse
// expandIconPosition / Steps children 等，真实工程历史遗留）与 React DevTools 提示。
;(() => {
  const suppressed = (...args: unknown[]): boolean =>
    args.some((a) => {
      const text = String(a)
      return (
        text.includes('[antd:') ||
        text.includes('deprecated') ||
        text.includes('Download the React DevTools') ||
        text.includes('[xcodeagent-mock]')
      )
    })
  const origWarn = console.warn
  console.warn = (...args: unknown[]): void => {
    if (suppressed(args)) return
    origWarn(...args)
  }
  // antd v4 的 rc-util warning 弃用提示走 console.error（"Warning: [antd: ...]"），
  // 必须同样拦截，否则控制台仍会刷弃用警告。
  const origError = console.error
  console.error = (...args: unknown[]): void => {
    if (suppressed(args)) return
    origError(...args)
  }
  const origInfo = console.info
  console.info = (...args: unknown[]): void => {
    if (suppressed(args)) return
    origInfo(...args)
  }
})()
import {
  appDataByWorkspace,
  mockApplications,
  mockLifecycle,
  mockWorkspaceApplication,
  newAppScenario
} from './fixtures'
import { mockApplicationInPlanning } from './mockHttpAgent'
import { isEndpointDesigned, isPageDesigned } from './designState'

// 预览地址跟随当前页面主机名：本机访问走 127.0.0.1，局域网设备访问时自动指向原型所在机器。
const MOCK_APPLICATION_PREVIEW_URL = `http://${window.location.hostname || '127.0.0.1'}:5190`

// 用户创建应用的持久化键：mock 环境里预置演示应用恒以静态数据为准（保证演示可重放），
// 用户新建的应用落 localStorage，返回欢迎页或刷新后仍在"最近项目"里可重新打开。
const USER_APPLICATIONS_KEY = 'xcodeagent:prototype:user-applications'

/** 规范化工作区路径用于占用比较：统一分隔符并忽略大小写（Windows 路径语义）。 */
function normalizeWorkspacePath(path: unknown): string {
  return String(path ?? '')
    .trim()
    .replace(/[\\/]+/g, '\\')
    .toLowerCase()
}

/** 读取预置应用 + 本机持久化的用户应用；按 id 与工作区路径去重，预置应用优先。 */
function mergedMockApplications(): typeof mockApplications {
  const presetIds = new Set<string>(mockApplications.map((app) => app.id))
  // 预置演示应用的工作区路径不允许用户应用重复登记：新建旅程必须选择其它目录，
  // 否则最近项目会出现同路径的两条应用，回退演示与新旅程互相污染。
  const presetPaths = new Set(
    mockApplications.map((app) => normalizeWorkspacePath(app.workspaceRoot))
  )
  let saved: unknown[] = []
  try {
    const raw = window.localStorage.getItem(USER_APPLICATIONS_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    if (Array.isArray(parsed)) saved = parsed
  } catch {
    saved = []
  }
  const userApps = saved.filter(
    (app) =>
      app &&
      typeof app === 'object' &&
      typeof (app as { id?: unknown }).id === 'string' &&
      !presetIds.has((app as { id: string }).id) &&
      !presetPaths.has(normalizeWorkspacePath((app as { workspaceRoot?: unknown }).workspaceRoot))
  )
  return [...mockApplications, ...(userApps as typeof mockApplications)]
}

/** 只把非预置应用写入持久层；预置应用的展示状态不落盘，刷新即恢复演示基线。 */
function persistUserApplications(applications: unknown[]): unknown[] {
  const presetIds = new Set<string>(mockApplications.map((app) => app.id))
  const list = Array.isArray(applications) ? applications : []
  const userApps = list.filter(
    (app) =>
      app &&
      typeof app === 'object' &&
      typeof (app as { id?: unknown }).id === 'string' &&
      !presetIds.has((app as { id: string }).id)
  )
  try {
    window.localStorage.setItem(USER_APPLICATIONS_KEY, JSON.stringify(userApps))
  } catch {
    // 隐私模式等场景写入失败时退化为内存态，不影响当次演示。
  }
  return list
}

// 模拟 ipcRenderer.invoke 的成功返回。
const ok = <T>(data: T): Promise<T> => Promise.resolve(data)
const noop = (): Promise<unknown> => Promise.resolve({})

// 实时会话（页面/接口开发、审查）在走完时存到 window 内存。list/read 必须合并它们，
// 否则切回开发阶段后大纲点页面/接口，静态 mock 会话找不到实时开发历史 → 会话丢失。
function mockSavedSessions(workspaceRoot?: string): Array<Record<string, unknown>> {
  const all = (window as unknown as { __mockSavedSessions?: Array<Record<string, unknown>> })
    .__mockSavedSessions || []
  if (!workspaceRoot) return all
  return all.filter(
    (session) => !session.workspaceRoot || session.workspaceRoot === workspaceRoot
  )
}

// 根据工作区路径找到对应应用场景的 schema；找不到回退 pms-new。
function findAppSchema(workspaceRoot?: string) {
  return appDataByWorkspace(workspaceRoot).app.schema
}

// 把运行时已确认设计的页面标记到规划产物，避免设计完成后仍显示未设计。
// 注意：artifacts 里自带的 designed 状态保留，只把 designState 中本会话内确认设计的页面额外标为 designed。
function withDesignedPages(artifacts: { pages: unknown[]; pageTree: unknown[]; apiContracts: unknown[] }) {
  const markPage = (page: any) =>
    isPageDesigned(page?.pageId || '')
      ? { ...page, designed: true, hasDetailPlan: true, detailPlanStatus: 'confirmed' }
      : page
  const markNode = (node: any): any => {
    if (node.children) return { ...node, children: node.children.map(markNode) }
    return markPage(node)
  }
  // 接口契约：确认过详细设计的 endpoint 标记 designed，供详情选择器与大纲显示状态。
  const markEndpoint = (contract: any): any => ({
    ...contract,
    endpoints: (contract.endpoints || []).map((endpoint: any, index: number) => {
      const rawEndpointId = endpoint.id || String(index + 1)
      const apiContractId = endpoint.apiContractId || contract.id
      return isEndpointDesigned(apiContractId, rawEndpointId)
        ? { ...endpoint, designed: true, hasDetailPlan: true }
        : endpoint
    })
  })
  return {
    ...artifacts,
    pages: artifacts.pages.map(markPage),
    pageTree: artifacts.pageTree.map(markNode),
    apiContracts: artifacts.apiContracts.map(markEndpoint)
  }
}

/** 合并静态剧本与本轮实时会话；实时消息优先，但必须继承静态剧本补充的跨产物归属。 */
function mergeMockSessions(
  scriptedSessions: Array<Record<string, unknown>>,
  savedSessions: Array<Record<string, unknown>>
): Array<Record<string, unknown>> {
  const scriptedById = new Map(
    scriptedSessions.map((session) => [String(session.id || ''), session] as const)
  )
  const savedIds = new Set(savedSessions.map((session) => String(session.id || '')))
  const enrichedSaved = savedSessions.map((saved) => {
    const scripted = scriptedById.get(String(saved.id || ''))
    if (!scripted) return saved
    return {
      ...scripted,
      ...saved,
      pageId: saved.pageId || scripted.pageId,
      apiContractId: saved.apiContractId || scripted.apiContractId,
      endpointId: saved.endpointId || scripted.endpointId,
      endpointLabel: saved.endpointLabel || scripted.endpointLabel,
      versionId: saved.versionId || scripted.versionId
    }
  })
  return [
    ...enrichedSaved,
    ...scriptedSessions.filter((session) => !savedIds.has(String(session.id || '')))
  ]
}

// 把共享的 v1.3 完成态规划产物还原为新应用/新迭代的待开发基线，再叠加本次运行已确认的任务。
function asPendingPlanningArtifacts(artifacts: { pages: any[]; pageTree: any[]; apiContracts: any[] }) {
  const resetPage = (page: any): any => ({
    ...page,
    designed: false,
    hasDetailPlan: false,
    detailPlanStatus: 'pending'
  })
  const resetNode = (node: any): any =>
    node.children
      ? { ...node, children: node.children.map(resetNode) }
      : resetPage(node)
  return {
    ...artifacts,
    pages: artifacts.pages.map(resetPage),
    pageTree: artifacts.pageTree.map(resetNode),
    apiContracts: artifacts.apiContracts.map((contract: any) => ({
      ...contract,
      endpoints: (contract.endpoints || []).map((endpoint: any) => ({
        ...endpoint,
        designed: false,
        hasDetailPlan: false
      }))
    })),
    hasPageDesigns: false
  }
}

// window.xcodeAgent 的 mock 实现（覆盖 preload/index.ts 暴露的全部命名空间）。
const xcodeAgent = {
  isElectron: false,
  agentBaseUrl: 'http://localhost:8000',
  platform: 'win32',
  auth: {
    login: () => ok({ authenticated: true }),
    status: () => ok({ authenticated: true }),
    getAccessToken: () => ok({ accessToken: 'mock-token' }),
    reauthenticate: () => ok({ authenticated: true })
  },
  applications: {
    load: () => ok({ applications: mergedMockApplications() }),
    save: (applications: unknown[]) => ok({ applications: persistUserApplications(applications) }),
    deleteProject: noop,
    deleteAgentDirectory: noop
  },
  workspace: {
    // CreateApplicationAction 期望 { canceled, path }（目录选择器）。
    // mock 选择器返回一个全新目录：预置演示应用的工作区路径不能被新建旅程复用，
    // 否则最近项目会出现同路径的两条应用，回退演示与新旅程互相污染。
    selectDirectory: () => {
      const demoRoot = newAppScenario().workspaceRoot
      const demoParent = demoRoot.replace(/[\\/]+$/, '').replace(/[\\/][^\\/]+$/, '')
      return ok({ canceled: false, path: `${demoParent}\\new-app-${Date.now()}` })
    },
    // 拒绝复用已有应用的工作区目录，与桌面端“已有 XCodeAgent 应用目录不能复用”的规则一致；
    // 手动输入预置演示应用路径同样会被拦截。
    createProjectDirectory: (payload: { workspacePath?: string }) => {
      const requested = normalizeWorkspacePath(payload?.workspacePath)
      const occupied = mergedMockApplications().find(
        (app) => normalizeWorkspacePath(app.workspaceRoot) === requested
      )
      if (occupied) {
        return Promise.reject(
          new Error(`该目录已被应用「${occupied.name}」使用，已有 XCodeAgent 应用目录不能复用。`)
        )
      }
      return ok({ path: payload?.workspacePath || appDataByWorkspace().workspaceRoot })
    },
    cloneTemplate: (payload: unknown) => ok(payload),
    writeTemplatePages: (payload: unknown) => ok(payload),
    readApplication: ({ workspaceRoot }: { workspaceRoot?: string }) =>
      ok({ application: findAppSchema(workspaceRoot) }),
    inspectPlanningArtifacts: ({ workspaceRoot, applicationId, versionId }: { workspaceRoot?: string; applicationId?: string; versionId?: string }) => {
      const artifacts = appDataByWorkspace(workspaceRoot).planningArtifacts
      const isCompletedDemoVersion = applicationId === 'app-pms-new' && versionId === 'app-pms-new-v1-3'
      return ok(withDesignedPages(isCompletedDemoVersion ? artifacts : asPendingPlanningArtifacts(artifacts as never)))
    }
  },
  sessions: {
    // 各应用镜像的对话历史来自 mock-data/{pms-new,pms-design,pms-dev}/chat-sessions.ts。
    listWorkspaces: () => ok([]),
    list: ({ workspaceRoot, editorMode, applicationId }: { workspaceRoot?: string; editorMode?: string; applicationId?: string }) => {
      // 规划(需求分析/项目规划)阶段的应用不返回静态镜像的已设计页会话；但运行期保存的对话必须照常返回——
      // 需求分析/项目规划旅程本就处于规划阶段集合，若连实时会话一起隐藏，任何一次目录重载
      // 都会把默认常规对话清空，用户视角就是“默认对话点进去就没了”。
      const inPlanning = mockApplicationInPlanning(workspaceRoot || '', applicationId)
      const scriptedSessions = inPlanning
        ? []
        : (appDataByWorkspace(workspaceRoot).chatSessions(
            workspaceRoot || '',
            (editorMode || 'frontend') as never
          ) as Array<{
            id: string; title: string; editorMode: string; threadId: string; pageId?: string
            createdByUser?: boolean; savedFiles?: unknown[]; apiContractId?: string; endpointId?: string; sessionKind?: string; versionId?: string
            createdAt: number; updatedAt: number; messages: unknown[]
          }>)
      // 合并走完的实时会话（页面/接口开发、审查），否则切回开发阶段后大纲点页面/接口，
      // list 只返回静态 mock，实时开发会话（messageCount>0）找不到 → 会话历史丢失。
      const saved = mockSavedSessions(workspaceRoot || '')
      const merged = mergeMockSessions(scriptedSessions, saved)
      const summaries = merged.map((s) => ({
        createdByUser: s.createdByUser,
        id: s.id,
        title: s.title,
        editorMode: s.editorMode,
        threadId: s.threadId,
        savedFiles: s.savedFiles,
        pageId: s.pageId,
        apiContractId: s.apiContractId,
        endpointId: s.endpointId,
        // 阶段默认会话依赖该字段声明文档产物归属；丢失后点击需求/计划只会换右侧文档，
        // 对话仍停留在原会话。
        sessionKind: s.sessionKind,
        versionId: s.versionId,
        createdAt: s.createdAt,
        updatedAt: s.updatedAt,
        messageCount: Array.isArray(s.messages) ? s.messages.length : 0
      }))
      return ok({ sessions: summaries })
    },
    read: ({ workspaceRoot, editorMode, sessionId }: { workspaceRoot?: string; editorMode?: string; sessionId?: string }) => {
      if (mockApplicationInPlanning(workspaceRoot || '')) return ok({ session: null })
      const sessions = appDataByWorkspace(workspaceRoot).chatSessions(
        workspaceRoot || '',
        (editorMode || 'frontend') as never
      ) as Array<Record<string, unknown>>
      const savedSessions = mockSavedSessions(workspaceRoot || '')
      const session = mergeMockSessions(sessions, savedSessions).find((s) => s.id === sessionId)
      return ok({ session: session || null })
    },
    save: (payload: { session?: unknown }) => {
      // 走完的实时会话存到 window 内存，方便控制台导出对比（不落 localStorage）。
      const session = payload?.session as { id?: string } | undefined
      if (session?.id) {
        const all = ((window as unknown as { __mockSavedSessions?: unknown[] }).__mockSavedSessions || []) as unknown[]
        ;(window as unknown as { __mockSavedSessions?: unknown[] }).__mockSavedSessions = [
          session,
          ...all.filter((s) => (s as { id?: string }).id !== session.id)
        ]
      }
      return ok({ session })
    },
    delete: noop
  },
  browser: {
    openExternal: (url: string) => {
      window.open(url, '_blank')
      return ok(undefined)
    },
    openPreviewWindow: (url: string) => {
      window.open(url, '_blank')
      return ok(undefined)
    }
  },
  projectPreview: {
    registerWorkspace: noop,
    unregisterWorkspace: noop
  }
}

// Proxy 兜底：组件若调用了未 mock 的方法，返回 resolve 空值的 Proxy，避免崩溃。
;(window as unknown as { xcodeAgent: unknown }).xcodeAgent = new Proxy(xcodeAgent, {
  get(target, prop, receiver) {
    if (typeof prop === 'string' && prop in target) {
      return Reflect.get(target, prop, receiver)
    }
    // 任意未覆盖属性 → 一个“任意调用都 resolve(undefined)”的 Proxy。
    return new Proxy(function () {}, {
      get: () => () => Promise.resolve(undefined),
      apply: () => Promise.resolve(undefined)
    })
  }
})

// 拦截后端 HTTP：按 URL 关键字返回 mock JSON，其余透传（多半会失败，组件多已 catch）。
const realFetch = window.fetch.bind(window)
window.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
  const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  let path: string
  try {
    path = new URL(raw, window.location.origin).pathname
  } catch {
    path = raw
  }

  if (path.includes('local-applications')) {
    if ((init || {}).method === 'PUT') {
      // 与 IPC mock 相同的持久化语义：只落非预置应用。
      const rawBody = typeof (init || {}).body === 'string' ? ((init || {}).body as string) : ''
      const body = rawBody ? JSON.parse(rawBody) : {}
      persistUserApplications((body as { applications?: unknown[] }).applications || [])
      return Promise.resolve(new Response(JSON.stringify({ applications: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      }))
    }
    return Promise.resolve(
      new Response(JSON.stringify({ applications: mergedMockApplications() }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
  }
  if (path.includes('lifecycle')) {
    return Promise.resolve(
      new Response(JSON.stringify(mockLifecycle), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
  }
  if (path.includes('project-launch') || path.includes('projects/launch') || path.includes('preview')) {
    // 返回独立的生成应用地址，预览面板再据此拼出具体业务页面路由。
    return Promise.resolve(
      new Response(
        JSON.stringify({
          status: path.includes('stop') ? 'stopped' : 'running',
          preview_url: MOCK_APPLICATION_PREVIEW_URL,
          message: 'mock 预览服务已就绪'
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    )
  }
  return realFetch(input as RequestInfo | URL, init)
}

// 防御：真实代码里仍有少量直接访问 window.xcodeAgent 的子属性（可选链已容错）。
// 这里显式声明已就绪。
void mockWorkspaceApplication
