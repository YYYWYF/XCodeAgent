import { Alert } from 'antd'
import type { ReactElement } from 'react'
import type { PreviewRuntimePayload } from '../../service/previewRuntime'
import { cx } from '../../utils'
import './PreviewAuthorizationAlert.less'

/** 独立于服务状态显示权限提醒。 */
export default function PreviewAuthorizationAlert({
  runtime
}: {
  runtime?: PreviewRuntimePayload['runtime']
}): ReactElement | null {
  const advisory = runtime?.authorizationAdvisory
  if (advisory?.applicable !== true || advisory.seedReady !== false) {
    return null
  }
  return (
    <Alert
      className={cx('preview-authorization-alert')}
      type="warning"
      showIcon
      message={advisory.title}
      description={advisory.message}
    />
  )
}
