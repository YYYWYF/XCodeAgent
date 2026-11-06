import { createElement, useCallback, useState, type ReactElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import type { WorkflowApiDatabaseOperation, WorkflowApiDesignDraft } from '../src/renderer/src/typings'
import type { GeneratedMapping, MappingAssistAction, MappingAssistRequest } from '../src/renderer/src/typings/endpointDesignAssist'
import { useMappingAssist, type MappingAssistState } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/useMappingAssist'
import { assistRequest } from './mappingAssistModel.test'

type Input = MappingAssistRequest & { action: MappingAssistAction; mapping?: GeneratedMapping; instruction?: string; clarification?: string; retryToken?: string }
export type AssistTestServer = {
  held: boolean; failStage: boolean; operation: WorkflowApiDatabaseOperation | null
  requests: Array<{ input: Input; release: () => void }>
}

/** 为只读审阅的挂载补齐宿主构造器和图标样式容器，结束后精确还原全局环境。 */
export function installMappingDomConstructors(): () => void {
  type StyleNode = { tagName: string; parentNode: object | null; innerHTML: string; setAttribute: (name: string, value: string) => void; getAttribute: (name: string) => string | null }
  const head = {
    children: [] as StyleNode[],
    /** 供图标样式插入到容器首部。 */
    get firstChild(): StyleNode | undefined { return this.children[0] },
    /** 图标库只向样式容器插入节点，不挂载实际 React 宿主元素。 */
    appendChild(node: StyleNode): StyleNode { this.children.push(node); node.parentNode = this; return node },
    /** 保留样式 prepend 顺序，供 rc-util 的动态样式注入使用。 */
    insertBefore(node: StyleNode, before?: StyleNode): StyleNode {
      const index = before ? this.children.indexOf(before) : -1
      this.children.splice(index < 0 ? this.children.length : index, 0, node); node.parentNode = this; return node
    },
    /** 删除 rc-util 用于定位样式父容器的临时节点。 */
    removeChild(node: StyleNode): void {
      const index = this.children.indexOf(node)
      if (index >= 0) this.children.splice(index, 1)
      node.parentNode = null
    }
  }
  const document = {
    head, body: head,
    /** 当前测试仅查询图标样式插入位置。 */
    querySelector: (): typeof head => head,
    /** 用属性表承载样式去重标识，避免模拟组件自身行为。 */
    createElement: (tag: string): StyleNode => {
      const attributes = new Map<string, string>()
      return { tagName: tag.toUpperCase(), parentNode: null, innerHTML: '',
        setAttribute: (name, value) => { attributes.set(name, value) },
        getAttribute: (name) => attributes.get(name) || null }
    },
    /** 告知样式库当前容器仍属于本次模拟文档。 */
    contains: (node: unknown): boolean => node === head || head.children.some((child) => child === node)
  }
  const constructors = { Element: class {}, HTMLElement: class {}, SVGElement: class {}, ShadowRoot: class {}, document,
    window: { document } }
  const originals = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, constructor] of Object.entries(constructors)) {
    if (typeof Reflect.get(globalThis, name) !== 'undefined') continue
    originals.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, { configurable: true, value: constructor })
  }
  /** 只恢复本次补齐的全局对象，避免影响其他测试提供的浏览器环境。 */
  return (): void => {
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else Reflect.deleteProperty(globalThis, name)
    }
  }
}

/** 用标准 AG-UI 事件响应真实客户端；可控网络延迟不模拟 React 状态或请求函数。 */
function response(input: Input, threadId: string, runId: string, failed: boolean, operation: AssistTestServer['operation']): Response {
  const external = input.selection.sourceType === 'external_api'
  const mapping = external ? { externalApiBindings: [], fieldMappings: [], implementationDescription: 'AI 外部映射说明' }
    : operation ? { databaseOperation: operation, databaseQuery: null, databaseWrites: [], fieldMappings: [], implementationDescription: 'AI 生成说明' } : null
  const draft = { ...input.draft, ...input.mapping }
  if (!draft.databaseQuery) delete draft.databaseQuery
  const value = { schemaVersion: 1, threadId, runId, action: input.action, status: failed ? 'failed' : 'completed',
    error: failed ? { message: '返回步骤生成失败' } : undefined,
    assistFailure: failed ? { retryToken: 'c'.repeat(32), failedStage: 'return', completedStages: ['operation', 'query'] } : undefined,
    assistance: { baseline: { draftHash: 'a'.repeat(64), metadataHash: 'b'.repeat(64) }, mapping, operation: external ? null : operation, operationReason: external ? '使用已绑定外部 API' : '根据契约识别操作', questions: ['请核对业务规则'], reviews: [] },
    applied: { draft, areas: ['return', 'description'], targets: [] },
    refinedRule: { description: '按用户要求保留两位小数', questions: [] } }
  const events = [{ type: 'RUN_STARTED', threadId, runId },
    { type: 'TEXT_MESSAGE_START', messageId: runId, role: 'assistant' },
    { type: 'CUSTOM', name: 'endpoint-designs', value }, { type: 'STATE_SNAPSHOT', snapshot: { endpointDesigns: value } },
    { type: 'TEXT_MESSAGE_CONTENT', messageId: runId, delta: '完成' }, { type: 'TEXT_MESSAGE_END', messageId: runId },
    { type: 'RUN_FINISHED', threadId, runId, result: { endpointDesigns: value } }]
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } })
}

/** 保存并还原全局网络；刻意允许取消后的请求迟到，用生产代码证明隔离。 */
export async function withAssistServer(run: (server: AssistTestServer) => Promise<void>): Promise<void> {
  const originalWindow = globalThis.window, originalFetch = globalThis.fetch
  const server: AssistTestServer = { held: false, failStage: false, operation: 'read', requests: [] }
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { devAgentStudio: { agentBaseUrl: 'http://assist.test' } } })
  globalThis.fetch = async (_url, init) => {
    const payload = JSON.parse(String(init?.body))
    const input: Input = payload.forwardedProps.endpointDesigns
    return new Promise<Response>((resolve) => {
      const failed = server.failStage && input.action === 'generate_mapping'
      const operation = server.operation
      const release = (): void => resolve(response(input, payload.threadId, payload.runId, failed, operation))
      server.requests.push({ input, release })
      if (!server.held) release()
    })
  }
  try { await run(server) } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
  }
}

export type MappingHarness = {
  state: () => MappingAssistState; edits: WorkflowApiDesignDraft[]
  setRequest: (value: MappingAssistRequest) => void
}

/** 真实挂载辅助 hook，并由原草稿回填回调驱动父组件更新。 */
export async function withMappingHarness(run: (harness: MappingHarness, server: AssistTestServer) => Promise<void>): Promise<void> {
  await withAssistServer(async (server) => {
    let root: ReactTestRenderer | undefined, current!: MappingAssistState
    let setRequest!: MappingHarness['setRequest']
    const edits: WorkflowApiDesignDraft[] = []
    /** 模拟工作台父层持有草稿，辅助 hook 无权自行落盘。 */
    function Probe(): ReactElement {
      const [request, updateRequest] = useState(assistRequest)
      setRequest = updateRequest
      const onEdit = useCallback((draft: WorkflowApiDesignDraft): void => {
        edits.push(draft); updateRequest((value) => ({ ...value, draft }))
      }, [])
      current = useMappingAssist(request, { columns: ['price'] }, onEdit)
      return createElement('output', { phase: current.phase, undo: current.canUndo, canApply: current.canApply })
    }
    try {
      await act(async () => { root = create(createElement(Probe)) })
      await run({ state: () => current, edits, setRequest: (value) => setRequest(value) }, server)
    } finally { await act(async () => root?.unmount()) }
  })
}
