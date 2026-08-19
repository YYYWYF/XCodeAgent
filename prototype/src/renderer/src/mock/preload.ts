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
import {
  isAgentDesigned,
  isEndpointDesigned,
  isEntityDesigned,
  isPageDesigned
} from './designState'
import type { DevelopmentPlanningAgent } from '../agentDevelopment'

// 预览地址跟随当前页面主机名：本机访问走 127.0.0.1，局域网设备访问时自动指向原型所在机器。
const MOCK_APPLICATION_PREVIEW_URL = `http://${window.location.hostname || '127.0.0.1'}:5190`

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
function withDesignedPages(
  artifacts: {
    pages: unknown[]
    pageTree: unknown[]
    apiContracts: unknown[]
    entities: any[]
    agents: DevelopmentPlanningAgent[]
  },
  versionKey?: string
) {
  const markPage = (page: any) =>
    isPageDesigned(page?.pageId || '', versionKey)
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
      return isEndpointDesigned(apiContractId, rawEndpointId, versionKey)
        ? { ...endpoint, designed: true, hasDetailPlan: true }
        : endpoint
    })
  })
  return {
    ...artifacts,
    pages: artifacts.pages.map(markPage),
    pageTree: artifacts.pageTree.map(markNode),
    apiContracts: artifacts.apiContracts.map(markEndpoint),
    entities: artifacts.entities.map((entity) =>
      isEntityDesigned(String(entity.entityId || ''), versionKey)
        ? { ...entity, designed: true, hasDetailPlan: true, detailPlanStatus: 'confirmed' }
        : entity
    ),
    agents: artifacts.agents.map((agent) =>
      isAgentDesigned(agent.id, versionKey)
        ? { ...agent, designed: true, hasDetailPlan: true, detailPlanStatus: 'confirmed' }
        : agent
    )
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
function asPendingPlanningArtifacts(artifacts: {
  pages: any[]
  pageTree: any[]
  apiContracts: any[]
  entities: any[]
  agents: DevelopmentPlanningAgent[]
}) {
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
    entities: artifacts.entities.map((entity: any) => ({
      ...entity,
      designed: false,
      hasDetailPlan: false,
      detailPlanStatus: 'pending'
    })),
    agents: artifacts.agents.map((agent) => ({
      ...agent,
      designed: false,
      hasDetailPlan: false,
      detailPlanStatus: 'pending'
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
    load: () => ok({ applications: mockApplications }),
    save: (applications: unknown[]) => ok({ applications }),
    deleteProject: noop,
    deleteAgentDirectory: noop
  },
  workspace: {
    // CreateApplicationAction 期望 { canceled, path }（目录选择器）
    selectDirectory: () => ok({ canceled: false, path: newAppScenario().workspaceRoot }),
    // CreateApplicationAction 用返回值的 .path 作为 application.workspaceRoot
    createProjectDirectory: (payload: { workspacePath?: string }) =>
      ok({ path: payload?.workspacePath || appDataByWorkspace().workspaceRoot }),
    cloneTemplate: (payload: unknown) => ok(payload),
    writeTemplatePages: (payload: unknown) => ok(payload),
    readApplication: ({ workspaceRoot }: { workspaceRoot?: string }) =>
      ok({ application: findAppSchema(workspaceRoot) }),
    inspectPlanningArtifacts: ({ workspaceRoot, applicationId, versionId }: { workspaceRoot?: string; applicationId?: string; versionId?: string }) => {
      const artifacts = appDataByWorkspace(workspaceRoot).planningArtifacts
      const isCompletedDemoVersion = applicationId === 'app-pms-new' && versionId === 'app-pms-new-v1-3'
      return ok(
        withDesignedPages(
          isCompletedDemoVersion ? artifacts : asPendingPlanningArtifacts(artifacts as never),
          versionId
        )
      )
    }
  },
  sessions: {
    // 各应用镜像的对话历史来自 mock-data/{pms-new,pms-design,pms-dev}/chat-sessions.ts。
    listWorkspaces: () => ok([]),
    list: ({ workspaceRoot, editorMode, applicationId }: { workspaceRoot?: string; editorMode?: string; applicationId?: string }) => {
      // 规划(设计)阶段的应用不返回已设计页会话，工作台只显示应用规划会话。
      if (mockApplicationInPlanning(workspaceRoot || '', applicationId)) return ok({ sessions: [] })
      const sessions = appDataByWorkspace(workspaceRoot).chatSessions(
        workspaceRoot || '',
        (editorMode || 'frontend') as never
      ) as Array<{
        id: string; title: string; editorMode: string; threadId: string; pageId?: string
        savedFiles?: unknown[]; apiContractId?: string; endpointId?: string; sessionKind?: string; versionId?: string
        createdAt: number; updatedAt: number; messages: unknown[]
      }>
      // 合并走完的实时会话（页面/接口开发、审查），否则切回开发阶段后大纲点页面/接口，
      // list 只返回静态 mock，实时开发会话（messageCount>0）找不到 → 会话历史丢失。
      const saved = mockSavedSessions(workspaceRoot || '')
      const merged = mergeMockSessions(sessions, saved)
      const summaries = merged.map((s) => ({
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
    return Promise.resolve(
      new Response(JSON.stringify({ applications: mockApplications }), {
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
