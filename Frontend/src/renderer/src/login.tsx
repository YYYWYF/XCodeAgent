import React, { useState } from 'react'
import ReactDOM from 'react-dom/client'
import {
  ArrowRightOutlined,
  CheckCircleFilled,
  CloseOutlined,
  CodeOutlined,
  SafetyCertificateOutlined
} from '@ant-design/icons'
import { Button, Typography, message } from 'antd'
import 'antd/dist/antd.less'
import './styles/global.less'
import { cx } from './utils'
import './login.less'

const { Paragraph, Text, Title } = Typography

type LoginTheme = 'light' | 'dark'

const CAPABILITIES = [
  'Workflow 智能编排',
  '本地工作区安全访问',
  '多 Agent 协同开发'
]

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

/** 渲染带有自定义关闭控件和设备流入口的无边框登录窗口。 */
export function LoginApp(): JSX.Element {
  const [loggingIn, setLoggingIn] = useState(false)
  const theme = getLoginTheme()

  /** 请求关闭当前登录窗口，主进程会按既有逻辑隐藏窗口并保留应用后台运行。 */
  const handleClose = (): void => {
    window.close()
  }

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
      <div aria-hidden="true" className={cx('login-grid')} />
      <div aria-hidden="true" className={cx('login-orb', 'login-orb-primary')} />
      <div aria-hidden="true" className={cx('login-orb', 'login-orb-secondary')} />

      <main className={cx('login-shell')}>
        <section className={cx('login-brand-panel')}>
          <header className={cx('login-brand-header')}>
            <div className={cx('login-brand')}>
              <span className={cx('login-logo')}>
                <CodeOutlined />
              </span>
              <strong>XcodeAgent</strong>
            </div>
          </header>

          <div className={cx('login-hero')}>
            <Text className={cx('login-kicker')}>BUILD / ORCHESTRATE / DELIVER</Text>
            <Title level={1}>让每一次构建，都更接近答案</Title>
            <Paragraph>
              从需求梳理到代码交付，在一个可追踪、可确认的智能工程工作台中完成。
            </Paragraph>
          </div>

          <div aria-hidden="true" className={cx('login-terminal')}>
            <div className={cx('login-terminal-bar')}>
              <span /><span /><span />
              <code>agent://secure-session</code>
            </div>
            <div className={cx('login-terminal-body')}>
              <code><em>$</em> xcode-agent connect --workspace</code>
              <code><b>✓</b> runtime ready · context protected</code>
              <span className={cx('login-terminal-cursor')} />
            </div>
          </div>

          <div className={cx('login-capabilities')}>
            {CAPABILITIES.map((item) => (
              <span key={item}>
                <CheckCircleFilled />
                {item}
              </span>
            ))}
          </div>
        </section>

        <section className={cx('login-auth-panel')}>
          <button
            aria-label="关闭登录窗口"
            className={cx('login-close')}
            onClick={handleClose}
            type="button"
          >
            <CloseOutlined />
          </button>

          <div className={cx('login-auth-copy')}>
            <span className={cx('login-auth-icon')}>
              <SafetyCertificateOutlined />
            </span>
            <Title level={2}>欢迎回来</Title>
            <Paragraph>登录后继续访问你的工作区、会话与智能开发流程。</Paragraph>
          </div>

          <div className={cx('login-action')}>
            <Button
              block
              icon={<ArrowRightOutlined />}
              loading={loggingIn}
              onClick={handleLogin}
              size="large"
              type="primary"
            >
              {loggingIn ? '登录中' : '启动安全登录'}
            </Button>
            <Text className={cx('login-action-hint')}>
              <SafetyCertificateOutlined /> 凭证仅保存在当前运行环境
            </Text>
          </div>
        </section>
      </main>
    </div>
  )
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <LoginApp />
  </React.StrictMode>
)
