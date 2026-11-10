import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import {
  useUncommittedChangesStore,
  type UncommittedChangesStore
} from '../src/renderer/src/hooks/useUncommittedChangesStore'

type GitRequest = { workspace: string; release: (count: number, failed?: boolean) => void }
type GitHarness = {
  requests: GitRequest[]
  focus: () => void
  state: () => UncommittedChangesStore
  update: (workspace: string, revision: string) => void
}

/** 等待官方 AG-UI 客户端消费完已交付的事件，不用定时重试模拟生产刷新。 */
async function settle(): Promise<void> {
  await act(async () => {
    await new Promise<void>((resolve) => setImmediate(resolve))
  })
}

/** 挂载真实 Hook 和官方 AG-UI 客户端，以受控流重现焦点风暴、慢请求和工作区切换。 */
async function withGitHarness(run: (harness: GitHarness) => Promise<void>): Promise<void> {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  const target = new EventTarget()
  Object.assign(target, { devAgentStudio: { agentBaseUrl: 'http://git-status.test' } })
  Object.defineProperty(globalThis, 'window', { configurable: true, value: target })
  const requests: GitRequest[] = []
  let state!: UncommittedChangesStore
  let root: ReactTestRenderer | undefined
  /** 只观察公开 Hook 的角标状态，不替换其刷新实现。 */
  function Probe({ workspace, revision }: { workspace: string; revision: string }): null {
    state = useUncommittedChangesStore(workspace, revision)
    return null
  }
  globalThis.fetch = async (_url, init) => {
    const payload = JSON.parse(String(init?.body))
    const { workspaceRoot, action } = payload.forwardedProps.versionControl
    assert.equal(action, 'inspect_all')
    return new Promise<Response>((resolve) => {
      requests.push({
        workspace: workspaceRoot,
        /** 交付完整标准流；失败同样正常结束，验证不会触发无条件重试。 */
        release: (count, failed = false): void => {
          const paths = Array.from({ length: count }, (_, index) => `src/file-${index}.ts`)
          const snapshot = {
            workspaceRoot,
            repositoryRoot: workspaceRoot,
            branch: 'main',
            head: 'a'.repeat(40),
            headMessage: 'baseline',
            fingerprint: 'b'.repeat(64),
            dirty: count > 0,
            hasStagedChanges: false,
            files: [],
            requestedPaths: paths,
            eligiblePaths: paths,
            codePaths: paths,
            unavailablePaths: []
          }
          const value = {
            schemaVersion: 1,
            runId: payload.runId,
            threadId: payload.threadId,
            action,
            status: failed ? 'failed' : 'completed',
            snapshot,
            error: failed ? { message: 'Git 暂时不可用' } : undefined
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
  try {
    await act(async () => {
      root = create(createElement(Probe, { workspace: '/workspace/a', revision: '1' }))
    })
    await run({
      requests,
      state: () => state,
      focus: () => {
        target.dispatchEvent(new Event('focus'))
      },
      update: (workspace, revision) => {
        root!.update(createElement(Probe, { workspace, revision }))
      }
    })
  } finally {
    await act(async () => {
      root?.unmount()
      requests.forEach((request) => request.release(0))
    })
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
  }
}

test('Git 角标不因控制台归还焦点循环请求，显式并发刷新共用一次读取', async () => {
  await withGitHarness(async (harness) => {
    assert.equal(harness.requests.length, 1)
    const first = harness.state().refresh()
    assert.equal(harness.state().refresh(), first)
    for (let index = 0; index < 30; index++) harness.focus()
    assert.equal(harness.requests.length, 1)
    harness.requests[0].release(2)
    await settle()
    assert.equal(harness.state().count, 2)
    for (let index = 0; index < 30; index++) harness.focus()
    await settle()
    assert.equal(harness.requests.length, 1)
    const explicit = harness.state().refresh()
    await settle()
    assert.equal(harness.requests.length, 2)
    harness.requests[1].release(0)
    await act(async () => {
      await explicit
    })
    assert.equal(harness.state().count, 0)
  })
})

test('Git 读取期间多次构建变化只补读最新状态，仍能更新角标', async () => {
  await withGitHarness(async (harness) => {
    await act(async () => {
      harness.update('/workspace/a', '2')
    })
    await act(async () => {
      harness.update('/workspace/a', '3')
    })
    assert.equal(harness.requests.length, 1)
    harness.requests[0].release(1)
    await settle()
    assert.equal(harness.requests.length, 2)
    harness.requests[1].release(4)
    await settle()
    assert.equal(harness.state().count, 4)
    assert.equal(harness.requests.length, 2)
  })
})

test('切换工作区再返回时旧响应不能覆盖角标，返回后重新读取当前现场', async () => {
  await withGitHarness(async (harness) => {
    await act(async () => {
      harness.update('/workspace/b', '1')
    })
    assert.equal(harness.requests.length, 2)
    harness.requests[1].release(3)
    await settle()
    assert.equal(harness.state().count, 3)
    await act(async () => {
      harness.update('/workspace/a', '1')
    })
    assert.equal(harness.state().count, 0)
    harness.requests[0].release(99)
    await settle()
    assert.equal(harness.state().count, 0)
    assert.equal(harness.requests.length, 3)
    harness.requests[2].release(2)
    await settle()
    assert.equal(harness.state().count, 2)
  })
})

test('Git 读取失败后不会因聚焦自动重试，显式刷新仍可恢复', async () => {
  await withGitHarness(async (harness) => {
    harness.requests[0].release(0, true)
    await settle()
    harness.focus()
    await settle()
    assert.equal(harness.requests.length, 1)
    const explicit = harness.state().refresh()
    await settle()
    harness.requests[1].release(5)
    await act(async () => {
      await explicit
    })
    assert.equal(harness.state().count, 5)
  })
})
