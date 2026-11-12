import assert from 'node:assert/strict'
import { test } from 'node:test'
import { act, create } from 'react-test-renderer'
import { createElement } from 'react'
import { useWorkbenchLifecycleRecovery } from '../src/renderer/src/pages/useWorkbenchLifecycleRecovery'
import type { ApplicationLifecycle } from '../src/renderer/src/typings'

test('入口结果直接复用；无入口结果才补读，切换工作区拒绝迟到响应', async () => {
  const application = { id: 'same-app', appName: '应用', workspaceRoot: '/original' }
  const entry = { application: { id: 'same-app' }, revision: 8 } as ApplicationLifecycle
  const accepted: ApplicationLifecycle[] = []
  let reads = 0
  let release!: (value: ApplicationLifecycle) => void
  /** 记录读取范围，并允许原目录回执在切换后迟到。 */
  const read = async () => { reads++; return new Promise<ApplicationLifecycle>((resolve) => { release = resolve }) }
  /** 使用稳定回调核对真实 Hook 的 effect 行为。 */
  const onChange = (value: ApplicationLifecycle): void => { accepted.push(value) }
  /** 挂载生产恢复 Hook，不挂载其余工作台业务组件。 */
  function Harness({ workspace, initial }: { workspace: string; initial?: ApplicationLifecycle }): null {
    useWorkbenchLifecycleRecovery({ ...application, workspaceRoot: workspace }, initial, onChange, read)
    return null
  }
  let renderer!: ReturnType<typeof create>
  await act(async () => { renderer = create(createElement(Harness, { workspace: '/original', initial: entry })) })
  assert.equal(reads, 0)
  assert.deepEqual(accepted, [entry])
  await act(async () => { renderer.update(createElement(Harness, { workspace: '/original' })) })
  const oldRelease = release
  await act(async () => { renderer.update(createElement(Harness, { workspace: '/clone' })) })
  assert.equal(reads, 2)
  await act(async () => { oldRelease({ ...entry, revision: 99 }) })
  assert.deepEqual(accepted, [entry])
  await act(async () => { release({ ...entry, revision: 2 }) })
  assert.equal(accepted.at(-1)?.revision, 2)
  act(() => renderer.unmount())
})
