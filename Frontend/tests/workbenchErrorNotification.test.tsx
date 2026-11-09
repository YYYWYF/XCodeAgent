import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, Fragment } from 'react'
import { act, create } from 'react-test-renderer'
import { notification } from 'antd'
import type { ArgsProps } from 'antd/lib/notification'
import WorkbenchErrorNotification from '../src/renderer/src/components/WorkbenchErrorNotification'
import WorkbenchRecoveryNotice from '../src/renderer/src/components/AiChatPanel/components/WorkbenchRecoveryNotice'
import { UiReadVisibility } from '../src/renderer/src/hooks/uiReadVisibility'
import { connectionErrorNoticeId, recoveryNoticeIsInline } from '../src/renderer/src/components/AiChatPanel/globalFallbackState'
import { initialConnectionState, completeConnectionRequest, failConnectionRequest } from '../src/renderer/src/service/connectionState'

test('会话任务失败常驻聊天区，切换与返回不丢失重试，全局故障继续使用通知', async () => {
  const originalHook = notification.useNotification
  const originalClose = notification.close
  const notices: ArgsProps[] = []
  const closed: string[] = []
  /** 记录真实包装组件是否把当前失败发送给全局通知。 */
  const open = (args: ArgsProps): void => { notices.push(args) }
  const api = { open, success: open, error: open, info: open, warning: open }
  notification.useNotification = () => [api, createElement(Fragment)]
  notification.close = (key): void => { closed.push(key) }
  let renderer: ReturnType<typeof create> | undefined
  let retries = 0
  /** 模拟任务失败与全局断连的归属切换，保留同一个生产包装组件。 */
  const view = (inline: boolean, visible = true, noticeId: string | undefined = 'failure') => createElement(
    UiReadVisibility.Provider, { value: visible },
    createElement(WorkbenchRecoveryNotice, { workspace: 'workspace', noticeId, inline,
      children: createElement('button', { onClick: () => { retries++ } }, '重试') })
  )
  try {
    await act(async () => { renderer = create(view(true)) })
    assert.equal(notices.length, 0, '任务失败不能弹到右下角')
    assert.equal(renderer!.root.findAllByProps({ 'aria-label': '当前会话恢复操作' }).length, 1)
    await act(async () => { renderer!.root.findByType('button').props.onClick() })
    assert.equal(retries, 1)
    await act(async () => { renderer!.update(view(true, false)) })
    assert.equal(renderer!.root.findAllByType('button').length, 0)
    await act(async () => { renderer!.update(view(true)) })
    assert.equal(renderer!.root.findAllByType('button').length, 1)
    assert.equal(notices.length, 0)
    await act(async () => { renderer!.update(view(false)) })
    assert.equal(renderer!.root.findAllByProps({ 'aria-label': '当前会话恢复操作' }).length, 0)
    assert.equal(notices.length, 1, '共享连接故障仍使用全局通知')
    await act(async () => { renderer!.update(view(true)) })
    assert.ok(closed.length > 0, '返回任务提示时撤下原全局浮层')
    assert.equal(renderer!.root.findAllByType('button').length, 1)
    await act(async () => { renderer!.update(view(true, true, '')) })
    assert.equal(renderer!.root.findAllByType('button').length, 0, '当前失败消失后撤下旧重试')
  } finally {
    if (renderer) await act(async () => { renderer!.unmount() })
    notification.useNotification = originalHook
    notification.close = originalClose
  }
})

test('需求、技术规划、开发任务与交接按会话归属显示，全局连接和共享读取故障不进入聊天区', () => {
  const session = { revisionHandoff: false, connectionStatus: 'healthy', planningEndRequested: false,
    hasRecoveryIncident: false, pendingRevisionStart: false, sharedReadError: false,
    previewRetry: false, hasSessionOwner: true }
  // 需求和技术规划校验失败、开发会话异常均已由调用方核验当前会话身份。
  assert.equal(recoveryNoticeIsInline(session), true)
  assert.equal(recoveryNoticeIsInline({ ...session, hasRecoveryIncident: true }), true)
  assert.equal(recoveryNoticeIsInline({ ...session, planningEndRequested: true }), true)
  assert.equal(recoveryNoticeIsInline({ ...session, pendingRevisionStart: true }), true)
  assert.equal(recoveryNoticeIsInline({ ...session, revisionHandoff: true, connectionStatus: 'unavailable' }), true)
  assert.equal(recoveryNoticeIsInline({ ...session, connectionStatus: 'unavailable' }), false)
  assert.equal(recoveryNoticeIsInline({ ...session, sharedReadError: true }), false)
  assert.equal(recoveryNoticeIsInline({ ...session, previewRetry: true }), false)
  assert.equal(recoveryNoticeIsInline({ ...session, hasSessionOwner: false }), false)
})

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
    connection = failConnectionRequest(connection, 4, '后台连接正在恢复，请稍候。', 275)
    await act(async () => { renderer!.update(view(connectionErrorNoticeId(connection, 'backend'), 'workspace-B', 'session-C')) })
    assert.equal(notices.length, beforeReconnect, '同次断连错误文案变化不能重弹已关闭的提示')
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
