import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, Fragment } from 'react'
import { act, create } from 'react-test-renderer'
import { notification } from 'antd'
import type { ArgsProps } from 'antd/lib/notification'
import WorkbenchErrorNotification from '../src/renderer/src/components/WorkbenchErrorNotification'
import { UiReadVisibility } from '../src/renderer/src/hooks/uiReadVisibility'
import { connectionErrorNoticeId } from '../src/renderer/src/components/AiChatPanel/globalFallbackState'
import { initialConnectionState, completeConnectionRequest, failConnectionRequest } from '../src/renderer/src/service/connectionState'

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
    let connection = failConnectionRequest(completeConnectionRequest(initialConnectionState(), 1, 100), 2, 'offline', 200)
    await act(async () => { renderer!.update(view(connectionErrorNoticeId(connection, 'backend'), 'workspace-B')) })
    notices.at(-1)!.onClose!()
    const beforeReconnect = notices.length
    // 同一次断连重试和切换会话保持去重，失败时间变化不能制造新故障。
    connection = failConnectionRequest(connection, 3, 'offline', 250)
    await act(async () => { renderer!.update(view(connectionErrorNoticeId(connection, 'backend'), 'workspace-B', 'session-C')) })
    assert.equal(notices.length, beforeReconnect)
    connection = completeConnectionRequest(connection, 4, 300)
    await act(async () => { renderer!.update(view(undefined, 'workspace-B')) })
    connection = failConnectionRequest(connection, 5, 'offline', 400)
    await act(async () => { renderer!.update(view(connectionErrorNoticeId(connection, 'backend'), 'workspace-B')) })
    assert.equal(notices.length, beforeReconnect + 1, '连接恢复后再次同样断连属于新的故障轮次')
    await act(async () => { renderer!.unmount() })
    renderer = undefined
    assert.ok(closed.every((closedKey) => closedKey === key))
  } finally {
    if (renderer) await act(async () => { renderer!.unmount() })
    notification.useNotification = originalHook
    notification.close = originalClose
  }
})

test('退出工作台立即关闭所属浮层，隐藏期间不重弹，返回时保留未关闭提示的操作', async () => {
  const originalHook = notification.useNotification
  const originalClose = notification.close
  const notices: ArgsProps[] = []
  const closed: string[] = []
  /** 保存当前通知和关闭回执，验证真实组件的可见性 effect。 */
  const open = (args: ArgsProps): void => { notices.push(args) }
  notification.useNotification = () => [{ open, success: open, error: open, info: open, warning: open }, createElement(Fragment)]
  notification.close = (key): void => { closed.push(key) }
  let renderer: ReturnType<typeof create> | undefined
  /** 保留组件挂载，只切换 AppEntryPage 同一个可见性 Context。 */
  const view = (visible: boolean, noticeId = 'stopped-generation') => createElement(
    UiReadVisibility.Provider, { value: visible },
    createElement(WorkbenchErrorNotification, { workspace: 'workspace-A', noticeId,
      children: createElement('button', null, '继续生成') })
  )
  try {
    await act(async () => { renderer = create(view(true)) })
    assert.equal(notices.length, 1)
    const key = notices[0].key!
    await act(async () => { renderer!.update(view(false)) })
    assert.equal(closed.at(-1), key)
    // 关闭动画的迟到回调不能把离开工作台误记为用户永久关闭。
    notices[0].onClose?.()
    await act(async () => { renderer!.update(view(false, 'new-background-error')) })
    assert.equal(notices.length, 1)
    await act(async () => { renderer!.update(view(true)) })
    assert.equal(notices.length, 2)
    assert.equal(notices.at(-1)?.key, key)
    notices.at(-1)!.onClose?.()
    await act(async () => { renderer!.update(view(false)) })
    await act(async () => { renderer!.update(view(true)) })
    assert.equal(notices.length, 2, '用户主动关闭的提示返回工作台后仍保持关闭')
  } finally {
    if (renderer) await act(async () => { renderer!.unmount() })
    notification.useNotification = originalHook
    notification.close = originalClose
  }
})
