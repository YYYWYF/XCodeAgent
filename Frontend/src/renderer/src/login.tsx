import React, { useState } from 'react'
import ReactDOM from 'react-dom/client'
import { LoginOutlined } from '@ant-design/icons'
import { Button, Modal, Typography, message } from 'antd'
import 'antd/dist/antd.less'
import './styles/global.less'
import { cx } from './utils'
import './login.less'

const { Title } = Typography

function formatError(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export function LoginApp(): JSX.Element {
  const [loggingIn, setLoggingIn] = useState(false)

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
    <div className={cx('login-page')}>
      <Modal centered closable={false} footer={null} maskClosable={false} open width={360}>
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
