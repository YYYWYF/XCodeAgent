import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, Fragment } from 'react'
import { act, create } from 'react-test-renderer'
import { notification } from 'antd'
import type { ArgsProps } from 'antd/lib/notification'
import WorkbenchErrorNotification from '../src/renderer/src/components/WorkbenchErrorNotification'

/** 验证真实组件的关闭、更新和隔离行为，Ant Design 浮层由 Electron 验证。 */
test('统一通知关闭后不随会话更新重弹，新错误可展示且保留最新操作', async () => {
  const originalHook = notification.useNotification
  const originalClose = notification.close
  const notices: ArgsProps[] = []
  const closed: string[] = []
  const open = (args: ArgsProps): void => { notices.push(args) }
  const api = { open, success: open, error: open, info: open, warning: open }
  notification.useNotification = () => [api, createElement(Fragment)]
  notification.close = (key): void => { closed.push(key) }
  let renderer: ReturnType<typeof create> | undefined
  let retried = ''
  /** 用操作回调模拟会话变化，确保通知更新后使用当前动作而非旧闭包。 */
  const view = (noticeId?: string, workspace = 'workspace-A', session = 'session-A') =>
    createElement(WorkbenchErrorNotification, {
      workspace,
      noticeId,
      children: createElement('button', { onClick: () => { retried = session } }, session)
    })
  try {
    await act(async () => { renderer = create(view('preview-error')) })
    const key = notices[0].key!
    assert.equal(notices[0].placement, 'bottomRight')
    assert.equal(notices[0].bottom, 24)
    assert.equal(notices[0].duration, 0)
    await act(async () => { renderer!.update(view('preview-error', 'workspace-A', 'session-B')) })
    assert.equal(notices.at(-1)?.key, key)
    const latestButton = notices.at(-1)!.description as ReturnType<typeof createElement>
    ;(latestButton.props as { onClick: () => void }).onClick()
    assert.equal(retried, 'session-B')
    notices.at(-1)!.onClose!()
    const count = notices.length
    await act(async () => { renderer!.update(view('preview-error', 'workspace-A', 'session-C')) })
    assert.equal(notices.length, count)
    // 离开再返回所属阶段，也不能重新打开已经主动关闭的相同错误。
    await act(async () => { renderer!.update(view()) })
    await act(async () => { renderer!.update(view('preview-error')) })
    assert.equal(notices.length, count)
    await act(async () => { renderer!.update(view('new-task-error')) })
    assert.equal(notices.length, count + 1)
    assert.equal(notices.at(-1)?.key, key)
    await act(async () => { renderer!.update(view('preview-error', 'workspace-B')) })
    assert.equal(notices.length, count + 2)
    await act(async () => { renderer!.update(view(undefined, 'workspace-B')) })
    assert.equal(closed.at(-1), key)
    await act(async () => { renderer!.unmount() })
    renderer = undefined
    assert.ok(closed.every((closedKey) => closedKey === key))
  } finally {
    if (renderer) await act(async () => { renderer!.unmount() })
    notification.useNotification = originalHook
    notification.close = originalClose
  }
})
