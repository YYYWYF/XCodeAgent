import assert from 'node:assert/strict'
import { createElement, type ReactElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { renderToStaticMarkup } from 'react-dom/server'
import { usePreviewRuntime } from '../src/renderer/src/components/AiChatPanel/hooks/usePreviewRuntime'
import { SessionRuntimeProvider } from '../src/renderer/src/components/AiChatPanel/hooks/useSessionRuntimeStore'
import type { SessionIdentity } from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import type { PreviewRuntimePayload } from '../src/renderer/src/service/previewRuntime'
import PreviewRepairControls from '../src/renderer/src/components/BrowserPreviewPanel/PreviewRepairControls'

/** 使用标准事件响应真实 AG-UI 客户端，保留请求的会话和运行身份。 */
function response(threadId: string, runId: string, value: PreviewRuntimePayload): Response {
  const events = [
    { type: 'RUN_STARTED', threadId, runId },
    { type: 'CUSTOM', name: 'preview-runtime', value },
    { type: 'STATE_SNAPSHOT', snapshot: { previewRuntime: value } },
    { type: 'RUN_FINISHED', threadId, runId, result: { previewRuntime: value } }
  ]
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'Content-Type': 'text/event-stream' }
  })
}

const originalFetch = globalThis.fetch
const originalWindow = globalThis.window
const identity: SessionIdentity = {
  key: 'cancel-preview-key',
  sessionId: 'cancel-session',
  threadId: 'cancel-thread',
  workflowId: 'cancel-workflow',
  workbenchPhase: 'development',
  editorMode: 'frontend',
  workspaceRoot: '/workspace',
  entryKey: 'preview-repair:cancel-test'
}

try {
  for (const failFirstCancel of [false, true]) {
    let root: ReactTestRenderer | undefined
    let controller: ReturnType<typeof usePreviewRuntime> | undefined
    let releaseDiagnosis: (() => void) | undefined
    let diagnosis: Promise<void> | undefined
    let messages: AgentChatMessage[] = []
    let cancelCount = 0
    const cancelThreads: string[] = []
    const runtime: NonNullable<PreviewRuntimePayload['runtime']> = {
      attemptId: 'attempt',
      status: 'failed',
      repairAvailable: true,
      frontend: { status: 'failed' },
      backend: { status: 'skipped' }
    }
    const payload: PreviewRuntimePayload = { status: 'completed', runtime }
    Object.defineProperty(globalThis, 'window', {
      configurable: true,
      value: { devAgentStudio: { agentBaseUrl: 'http://preview.test' }, setTimeout }
    })

    globalThis.fetch = async (_url, init) => {
      const body = JSON.parse(String(init?.body))
      const action = body.forwardedProps.previewRuntime.action
      if (action === 'watch') {
        // 状态订阅保持在途；卸载时响应取消，避免测试启动无限快照循环。
        return await new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new Error('watch aborted')), {
            once: true
          })
        })
      }
      if (action === 'diagnose') {
        // 刻意模拟不响应 AbortSignal 的迟到网络结果，验证旧请求不会覆盖已停止状态。
        return await new Promise<Response>((resolve) => {
          releaseDiagnosis = () =>
            resolve(
              response(body.threadId, body.runId, {
                ...payload,
                repair: { status: 'awaiting_confirmation', planId: 'late-plan' }
              })
            )
        })
      }
      if (action === 'cancel') {
        cancelThreads.push(body.threadId)
        cancelCount++
        if (failFirstCancel && cancelCount === 1) throw new TypeError('cancel disconnected')
        return response(body.threadId, body.runId, {
          ...payload,
          repair: { status: 'stopped', message: '修复已停止' }
        })
      }
      return response(body.threadId, body.runId, payload)
    }

    /** 真实挂载工作台 hook，后台订阅、取消和 React 状态更新均走生产实现。 */
    function Probe(): ReactElement {
      controller = usePreviewRuntime({
        workspace: identity.workspaceRoot,
        activeSession: identity,
        localBlocked: false,
        createSession: async () => identity,
        discardSession: async () => {},
        persistSession: async () => {},
        setMessages: (_key, value) => {
          messages = value
        },
        getMessages: () => messages,
        onReady: () => {}
      })
      return createElement('output')
    }

    try {
      await act(async () => {
        root = create(createElement(SessionRuntimeProvider, null, createElement(Probe)))
      })
      await act(async () => {
        diagnosis = controller!.act('diagnose')
      })
      assert.ok(releaseDiagnosis)
      assert.equal(controller!.repairBusy, true)
      await act(async () => {
        await controller!.act('cancel')
      })
      if (failFirstCancel) {
        assert.equal(controller!.repairState?.status, 'running', '取消失败必须恢复可重试的停止入口')
        const buttons = renderToStaticMarkup(
          createElement(PreviewRepairControls, {
            repair: controller!.repairState,
            busy: controller!.repairBusy,
            onAction: controller!.act,
            onRevision: () => {}
          })
        )
        assert.equal((buttons.match(/disabled=""/g) || []).length, 1, '确认禁用，停止按钮仍可点击')
        await act(async () => {
          await controller!.act('cancel')
        })
      }
      assert.equal(controller!.repairState?.status, 'stopped')
      assert.equal(controller!.repairBusy, false)
      await act(async () => {
        releaseDiagnosis!()
        await diagnosis
      })
      assert.equal(controller!.repairState?.status, 'stopped', '迟到诊断结果不能恢复待确认状态')
      assert.equal(controller!.repairBusy, false)
      assert.ok(messages.some((message) => message.content.includes('修复已停止')))
      assert.ok(cancelThreads.every((thread) => thread === identity.threadId))
    } finally {
      releaseDiagnosis?.()
      await diagnosis
      await act(async () => root?.unmount())
    }
  }
  console.log('preview cancellation, retry and late response isolation: passed')
} finally {
  globalThis.fetch = originalFetch
  Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
}
