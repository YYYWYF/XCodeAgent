import { ExclamationCircleOutlined } from '@ant-design/icons'
import { Button, Modal, Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import {
  resetAuthenticationFailure,
  subscribeAuthenticationFailure,
  type AuthenticationFailure
} from '../../service/authentication'
import { cx } from '../../utils'
import './AuthenticationFailureGate.less'

const { Text, Title } = Typography

type Props = {
  children: ReactNode
}

/** 在任意产品页面上阻断认证失败，并将用户带回登录窗口。 */
export function AuthenticationFailureGate({ children }: Props): JSX.Element {
  const [failure, setFailure] = useState<AuthenticationFailure>()
  const [redirecting, setRedirecting] = useState(false)
  const [redirectError, setRedirectError] = useState('')

  useEffect(
    () =>
      subscribeAuthenticationFailure((nextFailure) => {
        setFailure((currentFailure) => currentFailure ?? nextFailure)
      }),
    []
  )

  /** 清理已失效的登录态并打开登录窗口。 */
  const handleConfirm = async (): Promise<void> => {
    const authApi = window.xcodeAgent?.auth
    if (!authApi?.reauthenticate) {
      setRedirectError('当前环境不支持重新登录。')
      return
    }

    setRedirecting(true)
    setRedirectError('')
    try {
      await authApi.reauthenticate()
      resetAuthenticationFailure()
      setFailure(undefined)
    } catch (error) {
      setRedirectError(error instanceof Error ? error.message : '无法打开登录窗口。')
    } finally {
      setRedirecting(false)
    }
  }

  return (
    <>
      {children}
      <Modal
        centered
        closable={false}
        footer={null}
        keyboard={false}
        maskClosable={false}
        maskTransitionName=""
        open={Boolean(failure)}
        transitionName=""
        width={420}
        wrapClassName={cx('authentication-failure-modal', 'theme-light')}
        zIndex={10000}
      >
        <div className={cx('authentication-failure-content')}>
          <ExclamationCircleOutlined aria-hidden="true" />
          <Title level={3}>认证失败</Title>
          <Text>登录状态已失效，请重新登录后继续使用。</Text>
          {redirectError ? (
            <Text className={cx('authentication-failure-error')} role="alert">
              {redirectError}
            </Text>
          ) : null}
          <Button
            block
            loading={redirecting}
            onClick={() => void handleConfirm()}
            size="large"
            type="primary"
          >
            确定
          </Button>
        </div>
      </Modal>
    </>
  )
}
