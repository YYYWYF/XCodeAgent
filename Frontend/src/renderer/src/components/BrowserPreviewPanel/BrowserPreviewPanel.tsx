import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  DesktopOutlined,
  ExpandOutlined,
  MobileOutlined,
  ReloadOutlined,
  TabletOutlined
} from '@ant-design/icons'
import { Button, Input, Segmented, Select, Tooltip, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import type { ApplicationConfig } from '../../typings'
import {
  cx,
  getInitialPreviewUrl,
  navigatePreviewHistory,
  normalizePreviewUrl,
  openExternalPreviewUrl,
  storePreviewUrl
} from '../../utils'
import './BrowserPreviewPanel.less'

const { Text } = Typography

type PreviewViewport = 'desktop' | 'tablet' | 'mobile'

type Props = {
  application: ApplicationConfig
  requestKey?: string
  requestedUrl?: string
}

/** 展示可由 Workflow 目标地址驱动的内嵌浏览器预览。 */
export default function BrowserPreviewPanel({
  application,
  requestKey,
  requestedUrl
}: Props): ReactElement {
  const initialUrl = normalizePreviewUrl(requestedUrl || '') || getInitialPreviewUrl(application.id)
  const [navigation, setNavigation] = useState(() => ({ history: [initialUrl], index: 0 }))
  const [draftUrl, setDraftUrl] = useState(initialUrl)
  const [selectedPage, setSelectedPage] = useState(application.defaultPage || application.pages[0])
  const [viewport, setViewport] = useState<PreviewViewport>('desktop')
  const [refreshKey, setRefreshKey] = useState(0)
  const [openError, setOpenError] = useState('')
  const previewUrl = navigation.history[navigation.index]

  const pageOptions = useMemo(
    () => application.pages.map((page) => ({ label: page, value: page })),
    [application.pages]
  )

  useEffect(() => {
    setDraftUrl(previewUrl)
    setOpenError('')
    storePreviewUrl(application.id, previewUrl)
  }, [application.id, previewUrl])

  useEffect(() => {
    if (!requestedUrl) return
    setNavigation((current) => navigatePreviewHistory(current, requestedUrl))
  }, [requestKey, requestedUrl])

  /** 将手动输入的地址加入预览导航历史。 */
  const navigateTo = (rawUrl: string): void => {
    const nextUrl = normalizePreviewUrl(rawUrl)
    if (!nextUrl || nextUrl === previewUrl) {
      setDraftUrl(previewUrl)
      return
    }

    setNavigation((current) => navigatePreviewHistory(current, nextUrl))
  }

  /** 在系统浏览器中打开当前地址栏指向的预览页面。 */
  const openInBrowser = async (): Promise<void> => {
    const targetUrl = normalizePreviewUrl(draftUrl) || previewUrl
    if (!targetUrl) return

    setOpenError('')

    try {
      await openExternalPreviewUrl(targetUrl)
    } catch (error) {
      setOpenError(error instanceof Error ? error.message : '无法打开浏览器')
    }
  }

  return (
    <section className={cx('browser-preview-panel')}>
      <header className={cx('browser-preview-toolbar')}>
        <div className={cx('browser-window-controls')} aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div className={cx('browser-navigation')}>
          <Tooltip title="后退">
            <Button
              aria-label="后退"
              disabled={navigation.index === 0}
              icon={<ArrowLeftOutlined />}
              onClick={() =>
                setNavigation((current) => ({
                  ...current,
                  index: Math.max(0, current.index - 1)
                }))
              }
              type="text"
            />
          </Tooltip>
          <Tooltip title="前进">
            <Button
              aria-label="前进"
              disabled={navigation.index >= navigation.history.length - 1}
              icon={<ArrowRightOutlined />}
              onClick={() =>
                setNavigation((current) => ({
                  ...current,
                  index: Math.min(current.history.length - 1, current.index + 1)
                }))
              }
              type="text"
            />
          </Tooltip>
          <Tooltip title="刷新">
            <Button
              aria-label="刷新"
              icon={<ReloadOutlined />}
              onClick={() => setRefreshKey((key) => key + 1)}
              type="text"
            />
          </Tooltip>
        </div>
        <Input.Search
          aria-label="预览地址"
          className={cx('browser-address-input')}
          enterButton="访问"
          onChange={(event) => setDraftUrl(event.target.value)}
          onSearch={navigateTo}
          value={draftUrl}
        />
        <Select
          aria-label="页面"
          className={cx('browser-page-select')}
          options={pageOptions}
          value={selectedPage}
          onChange={setSelectedPage}
        />
        <Segmented
          aria-label="视口"
          className={cx('browser-viewport-switcher')}
          options={[
            { label: <DesktopOutlined />, value: 'desktop' },
            { label: <TabletOutlined />, value: 'tablet' },
            { label: <MobileOutlined />, value: 'mobile' }
          ]}
          value={viewport}
          onChange={(value) => setViewport(value as PreviewViewport)}
        />
        <Tooltip title="在系统浏览器打开">
          <Button
            aria-label="在系统浏览器打开"
            icon={<ExpandOutlined />}
            onClick={openInBrowser}
            type="primary"
          />
        </Tooltip>
      </header>

      <div className={cx('browser-preview-stage')}>
        <div className={cx('browser-preview-viewport', viewport)}>
          <iframe
            key={`${previewUrl}-${refreshKey}`}
            className={cx('browser-preview-frame')}
            src={previewUrl}
            title={`${application.name} 网页预览`}
            sandbox="allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
          />
        </div>
      </div>

      {openError && (
        <Text className={cx('browser-preview-error')} type="danger">
          {openError}
        </Text>
      )}
    </section>
  )
}
