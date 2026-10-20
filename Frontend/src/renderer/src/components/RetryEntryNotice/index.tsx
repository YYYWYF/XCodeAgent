import { CloseOutlined, InfoCircleOutlined } from '@ant-design/icons'
import { useState, type ReactElement } from 'react'
import { cx } from '../../utils'
import './RetryEntryNotice.less'

/** 无可定位节点时仅提供可关闭提示，不再显示无效重试。 */
export default function RetryEntryNotice(): ReactElement | null {
  const [closed, setClosed] = useState(false)
  return closed ? null : (
    <section className={cx('retry-entry-notice')} role="status" aria-live="polite">
      <InfoCircleOutlined className={cx('retry-entry-notice-icon')} aria-hidden />
      <div className={cx('retry-entry-notice-content')}>
        <span>找不到恢复入口，请手动发起流程。</span>
        <span className={cx('retry-entry-notice-description')}>可以在下方重新描述需求，或新建对话继续。</span>
      </div>
      <button className={cx('retry-entry-notice-close')} type="button"
        aria-label="关闭恢复提示" onClick={() => setClosed(true)}>
        <CloseOutlined aria-hidden />
      </button>
    </section>
  )
}
