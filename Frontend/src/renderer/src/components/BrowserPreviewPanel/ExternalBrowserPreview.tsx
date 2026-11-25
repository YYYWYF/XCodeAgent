import { Button } from 'antd'
import { useId, useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../utils'
import { openExternalPreviewUrl } from '../../utils/previewUrl'

/** 提供系统浏览器入口，并在当前地址的打开请求失败后保留重试。 */
export default function ExternalBrowserPreview({ url }: { url: string }): ReactElement {
  const hintId = useId()
  const [opening, setOpening] = useState(false)
  const [error, setError] = useState('')

  /** 仅在用户点击时调用系统浏览器，并反馈本次请求结果。 */
  const openInBrowser = async (): Promise<void> => {
    setError('')
    setOpening(true)
    try {
      await openExternalPreviewUrl(url)
    } catch (reason) {
      const detail = reason instanceof Error && reason.message ? ` ${reason.message}` : ''
      setError(`无法打开浏览器，请重试。${detail}`)
    } finally {
      setOpening(false)
    }
  }

  return (
    <div className={cx('browser-preview-service-empty')}>
      <Button
        aria-busy={opening}
        aria-describedby={hintId}
        className={cx('browser-external-button')}
        disabled={opening}
        onClick={openInBrowser}
        type="primary"
      >
        {opening ? '正在打开浏览器…' : '跳转浏览器预览'}
      </Button>
      <p id={hintId}>一号通登录无法内嵌iframe运行</p>
      {error && (
        <p className={cx('browser-external-error')} role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
