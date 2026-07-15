import React, { useState } from 'react'
import ReactDOM from 'react-dom/client'
import { LoginOutlined } from '@ant-design/icons'
import { Button, Modal, Typography, message } from 'antd'
import 'antd/dist/antd.less'
import './styles/global.less'
import { cx } from './utils'
import './login.less'

const { Title } = Typography

type LoginTheme = 'light' | 'dark'

/** 读取登录窗口使用的主题，未设置时跟随系统偏好。 */
function getLoginTheme(): LoginTheme {
  const storedPreference = window.localStorage.getItem('xcode-agent-theme-preference')
  if (storedPreference === 'light' || storedPreference === 'dark') return storedPreference
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/** 将未知异常转换为登录界面可展示的消息。 */
function formatError(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export function LoginApp(): JSX.Element {
  const [loggingIn, setLoggingIn] = useState(false)
  const theme = getLoginTheme()

  /** 调用 Electron 登录流程并保持按钮加载状态。 */
  const handleLogin = async (): Promise<void> => {
    const authApi = window.xcodeAgent?.auth
    if (!authApi?.login) {
      message.error('当前环境不支持登录。')
      return
    }

    setLoggingIn(true)
    try {
      const result = await authApi.login()
      if (!result.ok) {
        throw new Error('登录失败')
      }
    } catch (error) {
      message.error(formatError(error, '登录失败'))
      setLoggingIn(false)
    }
  }

  return (
    <div className={cx('login-page')} data-theme={theme}>
      <Modal
        centered
        closable={false}
        footer={null}
        keyboard={false}
        maskClosable={false}
        open
        width={360}
        wrapClassName={cx('login-modal', `theme-${theme}`)}
      >
        <div className={cx('login-dialog')}>
          <Title level={3}>XCode Agent</Title>
          <Button
            block
            icon={<LoginOutlined />}
            loading={loggingIn}
            onClick={handleLogin}
            size="large"
            type="primary"
          >
            登录
          </Button>
        </div>
      </Modal>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <LoginApp />
  </React.StrictMode>
)
