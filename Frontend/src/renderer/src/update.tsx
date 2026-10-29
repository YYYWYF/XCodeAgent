import React, { useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import { Button, Progress, Spin } from 'antd'
import { CloudDownloadOutlined, CheckCircleOutlined } from '@ant-design/icons'
import type { AppUpdateState } from '../../shared/appUpdate'
import { ApplicationThemeProvider, useApplicationTheme } from './hooks/useApplicationTheme'
import { loadApplicationTheme } from './service/applicationSettings'
import { cx } from './utils'
import 'antd/dist/antd.less'
import './styles/global.less'
import './update.less'

/** 将更新传输字节数格式化为紧凑且可读的大小。 */
function fileSize(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 渲染启动检测、下载与用户确认，不在页面发起网络请求或执行安装文件。 */
export function UpdateApp(): JSX.Element {
  const { theme } = useApplicationTheme()
  const [state, setState] = useState<AppUpdateState>()
  const [actionError, setActionError] = useState('')
  useEffect(() => {
    const api = window.devAgentStudio?.appUpdate
    let active = true
    /** 仅接受更新的快照，防止首次查询覆盖实时下载进度。 */
    const receive = (next: AppUpdateState): void => {
      if (active)
        setState((current) => (!current || next.revision >= current.revision ? next : current))
    }
    const unsubscribe = api?.onStateChanged(receive)
    void api
      ?.getState()
      .then(receive)
      .catch(() => {
        if (active) setActionError('无法读取更新状态，请关闭窗口后重新打开应用。')
      })
    return () => {
      active = false
      unsubscribe?.()
    }
  }, [])

  /** 将用户动作交给受控 IPC，异常在原窗口展示而非产生未处理拒绝。 */
  const act = async (action: 'install' | 'continueDevelopment' | 'quit'): Promise<void> => {
    setActionError('')
    try {
      await window.devAgentStudio?.appUpdate[action]()
    } catch {
      setActionError('操作未完成，请重试或关闭窗口退出应用。')
    }
  }
  const downloaded = state?.phase === 'downloaded'
  const installing = state?.phase === 'installing'
  const failed = state?.phase === 'error'
  return (
    <main className={cx('update-page')} data-theme={theme}>
      <header className={cx('update-header')}>
        <span className={cx('update-icon')}>
          {downloaded ? <CheckCircleOutlined /> : <CloudDownloadOutlined />}
        </span>
        <div>
          <strong>DevAgent Studio</strong>
          <small>当前版本 {state?.currentVersion || '…'}</small>
        </div>
      </header>
      {downloaded ? (
        <section className={cx('update-confirm')} role="dialog" aria-labelledby="update-title">
          <h1 id="update-title">新版本已就绪</h1>
          <p>
            当前版本低于可用版本，新版本 {state.latestAppVersion} 已下载完成，是否立即重启安装？
          </p>
          {!state.isPackaged && <small>开发模式不执行安装，可继续开发。</small>}
          <footer>
            <Button
              type="primary"
              onClick={() => void act(state.isPackaged ? 'install' : 'continueDevelopment')}
            >
              {state.isPackaged ? '立即安装' : '继续开发'}
            </Button>
            <Button onClick={() => void act('quit')}>退出</Button>
          </footer>
        </section>
      ) : (
        <section className={cx('update-status')} aria-live="polite">
          <h1>
            {failed
              ? '暂时无法更新'
              : installing
                ? '正在准备安装'
                : state?.phase === 'downloading'
                  ? `正在下载 ${state.latestAppVersion}`
                  : '正在检测更新'}
          </h1>
          {state?.phase === 'downloading' ? (
            <>
              <Progress percent={Number(state.percent.toFixed(1))} strokeColor="#6b3cf0" />
              <small>
                {fileSize(state.transferred)} / {fileSize(state.total)}
              </small>
            </>
          ) : (
            !failed && <Spin />
          )}
          <p>{state?.message || '请稍候，正在获取可用版本。'}</p>
        </section>
      )}
      {actionError && (
        <p className={cx('update-error')} role="alert">
          {actionError}
        </p>
      )}
    </main>
  )
}

/** 首次绘制前读取主题，避免更新窗口闪现错误配色。 */
async function bootstrapUpdate(): Promise<void> {
  await loadApplicationTheme()
  ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
    <React.StrictMode>
      <ApplicationThemeProvider>
        <UpdateApp />
      </ApplicationThemeProvider>
    </React.StrictMode>
  )
}
void bootstrapUpdate()
