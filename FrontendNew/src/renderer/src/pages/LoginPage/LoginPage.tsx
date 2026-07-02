import { Button, message } from 'antd'
import { useState } from 'react'
import { useApi } from '../../context/ApiContext'
import './LoginPage.less'

function LoginPage(): React.JSX.Element {
  const api = useApi()
  const [loading, setLoading] = useState(false)

  const handleLogin = async (): Promise<void> => {
    setLoading(true)

    try {
      await api.login()
    } catch (error) {
      console.error(error)
      message.error('登录失败，请稍后重试')
      setLoading(false)
    }
  }

  return (
    <main className="login-page">
      <section className="login-card">
        <h1 className="login-card__title">DevAgent Cloud</h1>
        <Button
          block
          loading={loading}
          size="large"
          type="primary"
          onClick={() => void handleLogin()}
        >
          模拟登录
        </Button>
      </section>
    </main>
  )
}

export default LoginPage
