import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Modal, message } from 'antd'
import { renderToStaticMarkup } from 'react-dom/server'
import WorkbenchTopBar from '../src/renderer/src/components/WorkbenchTopBar'
import { WorkbenchPhaseProvider } from '../src/renderer/src/context/WorkbenchPhaseContext'
import { useReturnWelcome } from '../src/renderer/src/hooks/useReturnWelcome'

type Options = Parameters<typeof useReturnWelcome>[0]
type State = ReturnType<typeof useReturnWelcome>
type Dialog = Parameters<typeof Modal.confirm>[0]
type Request = { respond: (overrides?: Record<string, unknown>) => void }

test('明暗主题的顶部两个返回入口共同禁用，应用按钮显示加载图标', () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'window')
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      localStorage: { getItem: () => null, setItem: () => {} }
    }
  })
  try {
    for (const theme of ['light', 'dark'] as const) {
      for (const returningWelcome of [false, true]) {
        const topbar = createElement(WorkbenchTopBar, {
          application: { id: 'return-button-test', name: '测试应用' },
          workspaceRoot: '/workspace/test',
          onReturnWelcome: () => {},
          returningWelcome,
          theme,
          onThemeChange: () => {}
        })
        const html = renderToStaticMarkup(
          <WorkbenchPhaseProvider applicationId="return-button-test" versionId="v1">
            {topbar}
          </WorkbenchPhaseProvider>
        )
        const buttons = html.match(/<button[^>]*aria-busy="(?:true|false)"[^>]*>/g) || []
        assert.equal(buttons.length, 2)
        for (const button of buttons) {
          assert.equal(button.includes('disabled=""'), returningWelcome)
          assert.equal(button.includes('aria-busy="true"'), returningWelcome)
        }
        assert.equal(html.includes('anticon-loading'), returningWelcome)
      }
    }
  } finally {
    if (previous) Object.defineProperty(globalThis, 'window', previous)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})

/** 等待真实 AG-UI 客户端交付结果并刷新 React 状态。 */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => setImmediate(resolve))
  })
}

/** 使用真实 Hook 与官方 AG-UI 客户端，只控制网络时序和确认框操作。 */
async function withHarness(
  run: (harness: {
    state: () => State
    click: () => Promise<void>
    update: (overrides: Partial<Options>) => Promise<void>
    requests: Request[]
    dialogs: Dialog[]
    leaves: string[]
    destroyed: () => number
  }) => Promise<void>
): Promise<void> {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  const originalConfirm = Modal.confirm
  const originalInfo = message.info
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      devAgentStudio: { agentBaseUrl: 'http://return-welcome.test' }
    }
  })
  const requests: Request[] = []
  const dialogs: Dialog[] = []
  const leaves: string[] = []
  let destroyed = 0
  let state!: State
  let root: ReactTestRenderer | undefined
  let options: Options = {
    applicationKey: 'a',
    workspace: '/workspace/a',
    enabled: true,
    hasActiveExecution: false,
    /** 记录实际导航目标，检查旧回调不能误退新应用。 */
    onLeave: () => {
      leaves.push('a')
    }
  }
  Modal.confirm = (dialog) => {
    dialogs.push(dialog)
    return {
      destroy: () => {
        destroyed++
      },
      update: () => {}
    }
  }
  message.info = (() => {}) as typeof message.info
  globalThis.fetch = async (_url, init) => {
    const payload = JSON.parse(String(init?.body))
    const input = payload.forwardedProps.versionControl
    assert.equal(input.action, 'inspect_pending_count')
    return new Promise<Response>((resolve) => {
      requests.push({
        /** 返回完整 AG-UI 终态，允许注入失败或畸形业务结果。 */
        respond: (overrides = {}): void => {
          const value = {
            schemaVersion: 1,
            runId: payload.runId,
            threadId: payload.threadId,
            status: 'completed',
            action: input.action,
            workspaceRoot: input.workspaceRoot,
            pendingCodeCount: 0,
            ...overrides
          }
          const events = [
            { type: 'RUN_STARTED', runId: payload.runId, threadId: payload.threadId },
            { type: 'CUSTOM', name: 'version-control', value },
            { type: 'STATE_SNAPSHOT', snapshot: { versionControl: value } },
            {
              type: 'RUN_FINISHED',
              runId: payload.runId,
              threadId: payload.threadId,
              result: { versionControl: value }
            }
          ]
          resolve(
            new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
              headers: { 'Content-Type': 'text/event-stream' }
            })
          )
        }
      })
    })
  }
  /** 捕获 Hook 的公开状态与事件，不替换业务实现。 */
  function Probe(props: Options): null {
    state = useReturnWelcome(props)
    return null
  }
  try {
    await act(async () => {
      root = create(createElement(Probe, options))
    })
    await run({
      state: () => state,
      click: async () => {
        await act(async () => {
          void state.handleReturnWelcome()
        })
        await settle()
      },
      update: async (overrides) => {
        options = { ...options, ...overrides }
        await act(async () => {
          root!.update(createElement(Probe, options))
        })
      },
      requests,
      dialogs,
      leaves,
      destroyed: () => destroyed
    })
  } finally {
    await act(async () => {
      root?.unmount()
    })
    globalThis.fetch = originalFetch
    Modal.confirm = originalConfirm
    message.info = originalInfo
    Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
  }
}

test('返回轻量检查合并重复点击，干净时返回，确认期间保持锁且取消可重试', async () => {
  await withHarness(async (h) => {
    await h.click()
    await h.click()
    assert.equal(h.requests.length, 1)
    assert.equal(h.state().returningWelcome, true)
    h.requests[0].respond({ pendingCodeCount: 2 })
    await settle()
    assert.equal(h.dialogs.length, 1)
    assert.equal(h.state().returningWelcome, true)
    assert.match(String(h.dialogs[0].content), /2 个文件/)
    await h.click()
    assert.equal(h.requests.length, 1)
    await act(async () => {
      h.dialogs[0].onCancel?.()
    })
    assert.equal(h.state().returningWelcome, false)
    await h.click()
    h.requests[1].respond()
    await settle()
    assert.deepEqual(h.leaves, ['a'])
    assert.equal(h.state().returningWelcome, false)
  })
})

test('确认仍然返回后只导航一次，任务运行和无工作区时直接返回', async () => {
  await withHarness(async (h) => {
    await h.click()
    h.requests[0].respond({ pendingCodeCount: 1 })
    await settle()
    await act(async () => {
      h.dialogs[0].onOk?.()
      h.dialogs[0].onOk?.()
    })
    assert.deepEqual(h.leaves, ['a'])
    await h.update({ hasActiveExecution: true })
    await h.click()
    await h.update({ hasActiveExecution: false, workspace: '' })
    await h.click()
    assert.equal(h.leaves.length, 3)
    assert.equal(h.requests.length, 1)
  })
})

test('Git 失败与非法计数按原行为返回，不弹提交确认框', async () => {
  for (const overrides of [
    { status: 'failed', error: { message: 'Git unavailable' } },
    { pendingCodeCount: -1 },
    { pendingCodeCount: 0.5 },
    { pendingCodeCount: '1' },
    { action: 'inspect_all' },
    { workspaceRoot: '' }
  ]) {
    await withHarness(async (h) => {
      await h.click()
      h.requests[0].respond(overrides)
      await settle()
      assert.deepEqual(h.leaves, ['a'])
      assert.equal(h.dialogs.length, 0)
      assert.equal(h.state().returningWelcome, false)
    })
  }
})

test('A→B→A 和离开页面拒绝迟到响应，应用切换关闭旧确认框', async () => {
  await withHarness(async (h) => {
    await h.click()
    await h.update({ applicationKey: 'b', workspace: '/workspace/b' })
    await h.update({ applicationKey: 'a', workspace: '/workspace/a' })
    await h.click()
    h.requests[0].respond({ pendingCodeCount: 5 })
    await settle()
    assert.equal(h.dialogs.length, 0)
    assert.equal(h.state().returningWelcome, true)
    h.requests[1].respond({ pendingCodeCount: 1 })
    await settle()
    assert.equal(h.dialogs.length, 1)
    await h.update({ applicationKey: 'b', workspace: '/workspace/b' })
    assert.equal(h.destroyed(), 1)
    await act(async () => {
      h.dialogs[0].onOk?.()
    })
    assert.equal(h.leaves.length, 0)
    await h.click()
    await h.update({ enabled: false })
    h.requests[2].respond()
    await settle()
    assert.equal(h.leaves.length, 0)
    assert.equal(h.state().returningWelcome, false)
  })
})
