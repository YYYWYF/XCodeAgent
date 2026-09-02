import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  AimOutlined,
  DesktopOutlined,
  ExpandOutlined,
  MobileOutlined,
  ReloadOutlined,
  TabletOutlined
} from '@ant-design/icons'
import { Button, Input, Segmented, Select, Tooltip, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  ApplicationConfig,
  ApplicationMenuItem,
  DevelopmentPlanningPageOption,
  InspectedElementContext
} from '../../typings'
import {
  composePreviewUrl,
  cx,
  navigatePreviewHistory,
  normalizePreviewUrl,
  openExternalPreviewUrl
} from '../../utils'
import './BrowserPreviewPanel.less'
import { useElementInspector } from './useElementInspector'

const { Text } = Typography

type PreviewViewport = 'desktop' | 'tablet' | 'mobile'

type Props = {
  application: ApplicationConfig
  requestKey?: string
  requestedUrl?: string
  errorMessage?: string
  pages?: DevelopmentPlanningPageOption[]
  previewBaseUrl?: string
  selectedPagePath?: string
  onInspectingChange?: (active: boolean) => void
  onElementContextChange?: (context: InspectedElementContext | undefined) => void
}

type PreviewPageOption = {
  label: string
  value: string
}

/** 递归提取应用菜单中的页面名称和路由，用于没有 ProjectPlan 页面清单的预览入口。 */
function menuPreviewPages(items: ApplicationMenuItem[]): PreviewPageOption[] {
  return items.flatMap((item) => [
    ...(item.type === 'page' ? [{ label: item.label, value: item.path }] : []),
    ...menuPreviewPages(item.children || [])
  ])
}

/** 展示可由 Workflow 目标地址驱动的内嵌浏览器预览。 */
export default function BrowserPreviewPanel({
  application,
  requestKey,
  requestedUrl,
  errorMessage: externalError,
  pages = [],
  previewBaseUrl = '',
  selectedPagePath = '',
  onElementContextChange,
  onInspectingChange
}: Props): ReactElement {
  const pageOptions = useMemo<PreviewPageOption[]>(() => {
    if (pages.length > 0) {
      return pages.map((page) => ({ label: page.label, value: page.path }))
    }
    const menuPages = menuPreviewPages(application.menus.items)
    if (menuPages.length > 0) return menuPages
    return application.pages.map((page) => ({ label: page, value: '/' }))
  }, [application.menus.items, application.pages, pages])
  const initialPagePath = selectedPagePath || pageOptions[0]?.value || '/'
  const initialUrl =
    normalizePreviewUrl(requestedUrl || '') ||
    composePreviewUrl(previewBaseUrl, initialPagePath) ||
    'about:blank'
  const [navigation, setNavigation] = useState(() => ({ history: [initialUrl], index: 0 }))
  const [draftUrl, setDraftUrl] = useState(initialUrl)
  const [selectedPage, setSelectedPage] = useState(initialPagePath)
  const [viewport, setViewport] = useState<PreviewViewport>('desktop')
  const [refreshKey, setRefreshKey] = useState(0)
  const [openError, setOpenError] = useState('')
  const [launchError, setLaunchError] = useState(externalError || '')
  const previewUrl = navigation.history[navigation.index]
  const frameKey = `${previewUrl}-${refreshKey}`
  const elementInspector = useElementInspector({
    frameKey,
    onElementContextChange,
    onInspectingChange,
    previewUrl
  })

  useEffect(() => {
    setDraftUrl(previewUrl)
    setOpenError('')
    // 用户手动导航或刷新时，之前的启动错误已无关，一并清除
    setLaunchError('')
  }, [previewUrl])

  useEffect(() => {
    if (!requestedUrl) return
    // 从外部收到新的 preview 地址（如 launch 成功返回），清除此前可能的启动错误
    setLaunchError('')
    setNavigation((current) => navigatePreviewHistory(current, requestedUrl))
  }, [requestKey, requestedUrl])

  useEffect(() => {
    setLaunchError(externalError || '')
  }, [externalError])

  useEffect(() => {
    if (!selectedPagePath) return
    setSelectedPage(selectedPagePath)
  }, [selectedPagePath])

  /** 将手动输入的地址加入预览导航历史。 */
  const navigateTo = (rawUrl: string): void => {
    const nextUrl = normalizePreviewUrl(rawUrl)
    if (!nextUrl || nextUrl === previewUrl) {
      setDraftUrl(previewUrl)
      return
    }

    setNavigation((current) => navigatePreviewHistory(current, nextUrl))
  }

  /** 使用当前前端端口和用户选择的页面路由切换内嵌预览。 */
  const handlePageChange = (pagePath: string): void => {
    setSelectedPage(pagePath)
    const targetUrl = composePreviewUrl(previewBaseUrl, pagePath)
    if (!targetUrl) {
      setLaunchError(externalError || '前端服务尚未启动完成，暂时无法预览页面')
      return
    }
    setLaunchError('')
    setNavigation((current) => navigatePreviewHistory(current, targetUrl))
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
        <Tooltip
          title={
            elementInspector.active
              ? '退出元素审查'
              : elementInspector.ready
                ? '审查预览页面中的元素'
                : '当前预览页面尚未准备好元素审查'
          }
        >
          <span className={cx('browser-inspector-button-shell')}>
            <Button
              aria-label={elementInspector.active ? '退出审查' : '审查元素'}
              aria-pressed={elementInspector.active}
              className={cx('browser-inspector-button')}
              disabled={!elementInspector.ready && !elementInspector.active}
              icon={<AimOutlined />}
              onClick={elementInspector.toggle}
              type="primary"
            >
              {elementInspector.active ? '退出审查' : '审查元素'}
            </Button>
          </span>
        </Tooltip>
        <Select
          aria-label="页面"
          className={cx('browser-page-select')}
          options={pageOptions}
          value={selectedPage}
          onChange={handlePageChange}
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
            key={frameKey}
            ref={elementInspector.iframeRef}
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

      {launchError && !openError && (
        <Text className={cx('browser-preview-error')} type="warning">
          {launchError}
        </Text>
      )}
    </section>
  )
}
