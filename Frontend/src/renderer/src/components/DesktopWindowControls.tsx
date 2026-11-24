import { BlockOutlined, BorderOutlined, CloseOutlined, MinusOutlined } from '@ant-design/icons'
import { message } from 'antd'
import { useEffect, useState } from 'react'
import type { WindowControlAction } from '../../../shared/windowControls'
import { cx } from '../utils'
import './DesktopWindowControls.less'

/** 渲染 Windows 和 macOS 共用的主窗口操作，随原生最大化状态更新图标。 */
export default function DesktopWindowControls(): JSX.Element | null {
  const controls = typeof window === 'undefined' ? undefined : window.devAgentStudio?.windowControls
  const [maximized, setMaximized] = useState(false)

  useEffect(() => {
    if (!controls) return
    let active = true
    let receivedUpdate = false
    const unsubscribe = controls.onMaximizedChanged((next) => {
      receivedUpdate = true
      setMaximized(next)
    })
    // 订阅后再读取快照，避免较晚返回的初始值覆盖窗口事件。
    void controls.getMaximized().then(
      (initial) => {
        if (active && !receivedUpdate) setMaximized(initial)
      },
      () => undefined
    )
    return () => {
      active = false
      unsubscribe()
    }
  }, [controls])

  if (!controls) return null

  /** 交由主进程执行窗口操作，调用失败时反馈给用户。 */
  function perform(action: WindowControlAction): void {
    void controls?.perform(action).catch(() => {
      message.error('窗口操作失败，请重试。')
    })
  }

  const maximizeLabel = maximized ? '还原窗口' : '最大化窗口'
  return (
    <div className={cx('desktop-window-controls')} role="group" aria-label="窗口控制">
      <button
        type="button"
        aria-label="最小化窗口"
        title="最小化窗口"
        onClick={() => perform('minimize')}
      >
        <MinusOutlined aria-hidden="true" />
      </button>
      <button
        type="button"
        aria-label={maximizeLabel}
        title={maximizeLabel}
        onClick={() => perform('toggle-maximize')}
      >
        {maximized ? <BlockOutlined aria-hidden="true" /> : <BorderOutlined aria-hidden="true" />}
      </button>
      <button
        type="button"
        aria-label="关闭窗口"
        title="关闭窗口"
        className={cx('desktop-window-close')}
        onClick={() => perform('close')}
      >
        <CloseOutlined aria-hidden="true" />
      </button>
    </div>
  )
}
