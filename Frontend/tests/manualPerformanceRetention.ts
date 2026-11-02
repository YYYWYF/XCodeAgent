import { createElement, type ReactElement } from 'react'
import { createRoot } from 'react-dom/client'
import { AgUiChatSession } from '../src/renderer/src/service/agUiAgent'
import {
  SessionRuntimeProvider,
  useSessionRuntimeStore,
  type SessionRuntimeStore
} from '../src/renderer/src/components/AiChatPanel/hooks/useSessionRuntimeStore'
import { createSessionIdentity } from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import { UiReadVisibility } from '../src/renderer/src/hooks/uiReadVisibility'

/** 在已运行的 Electron 渲染器中验证真实 SDK 与 Provider；只拦截专用验证 URL，不调用模型或写用户项目。 */
export async function runElectronRetentionCheck(): Promise<void> {
  const panel = document.createElement('section')
  panel.id = 'performance-retention-validation'
  panel.style.cssText =
    'position:fixed;z-index:99999;inset:20px auto auto 20px;padding:24px;max-width:640px;background:var(--wb-panel-bg,#fff);color:var(--wb-text,#222);border:2px solid var(--wb-primary,#6b3cf0);border-radius:12px'
  document.body.append(panel)
  const host = document.createElement('div')
  const summary = document.createElement('pre')
  panel.append(host, summary)
  const root = createRoot(host)
  let runtime!: SessionRuntimeStore
  /** 直接订阅生产会话缓存，不模拟 React effect 或执行占用。 */
  function Probe(): ReactElement {
    runtime = useSessionRuntimeStore()
    return createElement('strong', null, 'Electron 后台任务与缓存验证（受控 AG-UI 流）')
  }
  /** 显隐只传递展示可见性，任务与 Provider 始终保持挂载。 */
  const render = (visible: boolean): void =>
    root.render(
      createElement(
        SessionRuntimeProvider,
        null,
        createElement(UiReadVisibility.Provider, { value: visible }, createElement(Probe))
      )
    )
  /** 等待 Electron 的真实 React 提交与消息调度，不访问主应用私有状态。 */
  const settle = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 80))
  /** 用明确失败消息中断验证，最终结果直接呈现在 Electron 内。 */
  const check = (condition: boolean, message: string): void => {
    if (!condition) throw new Error(message)
  }
  const savedFetch = window.fetch
  let requests = 0,
    cancelled = 0
  const url = 'http://127.0.0.1:8000/__performance_validation__/run'
  window.fetch = async (input, init) => {
    if (String(input) !== url) return savedFetch(input, init)
    requests++
    const request = JSON.parse(String(init?.body))
    const encoder = new TextEncoder()
    return new Response(
      new ReadableStream({
        /** 用标准生命周期延迟输出，展示隐藏期间任务仍在持续发送真实 SDK 消息。 */
        async start(controller) {
          const events = [
            { type: 'RUN_STARTED', threadId: request.threadId, runId: request.runId },
            { type: 'TEXT_MESSAGE_START', messageId: 'background-result', role: 'assistant' },
            ...['后台任务', '持续执行', '并完成'].map((delta) => ({
              type: 'TEXT_MESSAGE_CONTENT',
              messageId: 'background-result',
              delta
            })),
            { type: 'TEXT_MESSAGE_END', messageId: 'background-result' },
            { type: 'RUN_FINISHED', threadId: request.threadId, runId: request.runId, result: {} }
          ]
          for (const event of events) {
            controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
            await new Promise((resolve) => setTimeout(resolve, 180))
          }
          controller.close()
        },
        /** 验证视图隐藏和缓存压力不会取消正在执行的 SDK 请求。 */
        cancel() {
          cancelled++
        }
      }),
      { headers: { 'Content-Type': 'text/event-stream' } }
    )
  }
  const identity = createSessionIdentity({
    workspaceRoot: '/electron-performance-fixture',
    editorMode: 'frontend',
    sessionId: 'background',
    threadId: 'original-background-thread',
    workflowId: 'performance',
    workbenchPhase: 'development'
  })
  try {
    render(true)
    await settle()
    const agent = new AgUiChatSession(identity.threadId, url)
    runtime.registerSession(identity, [], agent)
    runtime.acquireSessionExecution(identity, true)
    const run = agent.sendMessage('受控后台验证', {
      workspaceRoot: identity.workspaceRoot,
      onContent: (content) =>
        runtime.setSessionMessages(identity.key, [
          { id: 1, role: 'assistant', content, createdAt: 1 }
        ])
    })
    await settle()
    render(false)
    for (let index = 0; index < 10; index++) {
      const other = createSessionIdentity({
        ...identity,
        sessionId: `history-${index}`,
        threadId: `history-thread-${index}`
      })
      runtime.registerSession(other, [
        { id: 1, role: 'assistant', content: `已保存 ${index}`, createdAt: 1 }
      ])
    }
    const dirty = createSessionIdentity({
      ...identity,
      sessionId: 'dirty',
      threadId: 'dirty-thread'
    })
    runtime.registerSession(dirty, [])
    runtime.setSessionMessages(dirty.key, [
      { id: 1, role: 'user', content: '尚未保存内容', createdAt: 1 }
    ])
    runtime.setDraftByKey(dirty.key, '尚未发送草稿')
    await settle()
    check(runtime.hasSessionMessages(identity.key), '后台会话被回收')
    check(runtime.sessionExecutions.length === 1, '后台执行登记丢失')
    const result = await run
    check(result.answer.includes('并完成'), '后台消息未完整到达')
    check(requests === 1 && cancelled === 0, '发生重发或意外取消')
    const token = runtime.beginSessionSave(identity.key, runtime.getSessionMessages(identity.key))
    runtime.finishSessionSave(token, true)
    runtime.releaseSessionExecution(identity.key)
    render(true)
    await settle()
    check(
      runtime.getSessionMessages(identity.key)[0].content === '后台任务持续执行并完成',
      '恢复可见后正文不完整'
    )
    check(runtime.getIdentity(identity.key)?.threadId === identity.threadId, '恢复改变了原 Thread')
    check(runtime.getSessionMessages(dirty.key)[0].content === '尚未保存内容', '未保存正文丢失')
    check(runtime.draftForKey(dirty.key) === '尚未发送草稿', '未发送草稿丢失')
    check(Object.keys(runtime.agUiSessionsRef.current).length <= 4, '闲置会话缓存未受限')
    summary.textContent = JSON.stringify(
      {
        status: 'PASS',
        requestCount: requests,
        cancelled,
        backgroundCompleted: true,
        originalThreadPreserved: true,
        unsavedPreserved: true,
        draftPreserved: true,
        retainedClients: Object.keys(runtime.agUiSessionsRef.current).length
      },
      null,
      2
    )
  } catch (error) {
    summary.textContent = `FAIL: ${String(error)}`
    throw error
  } finally {
    window.fetch = savedFetch
    const close = document.createElement('button')
    close.textContent = '关闭验证结果'
    close.onclick = () => {
      root.unmount()
      panel.remove()
    }
    panel.append(close)
  }
}
