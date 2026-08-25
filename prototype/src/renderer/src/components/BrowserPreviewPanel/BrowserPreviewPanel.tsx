import { CheckCircleFilled, MessageOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  ApplicationConfig,
  ApplicationMenuItem,
  DevelopmentPlanningPageOption
} from '../../typings'
import {
  composePreviewUrl,
  cx,
  navigatePreviewHistory,
  normalizePreviewUrl,
  openExternalPreviewUrl
} from '../../utils'
import RichLoading from '../AiChatPanel/components/DesignProgress/RichLoading'
import './BrowserPreviewPanel.less'
import BrowserPreviewToolbar, { type PreviewViewport } from './BrowserPreviewToolbar'
import { useElementInspector } from './useElementInspector'

const { Text } = Typography

type Props = {
  applicationMode?: boolean
  application: ApplicationConfig
  requestKey?: string
  requestedUrl?: string
  errorMessage?: string
  pages?: DevelopmentPlanningPageOption[]
  previewBaseUrl?: string
  selectedPagePath?: string
  /** 在应用预览中显示验收意见与确认控件。 */
  acceptanceEnabled?: boolean
  /** 当前版本是否已经完成验收。 */
  acceptanceAccepted?: boolean
  /** 提交应用预览内的验收通过动作。 */
  onAcceptApplication?: () => void
  /** 验收控件是否只读。 */
  acceptanceReadOnly?: boolean
  /** 点击不通过后回到验收对话，由产品 Agent 引导用户输入意见。 */
  onSubmitAcceptanceFeedback?: () => void
  onInspectingChange?: (active: boolean) => void
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
  applicationMode = false,
  application,
  requestKey,
  requestedUrl,
  errorMessage: externalError,
  pages = [],
  previewBaseUrl = '',
  selectedPagePath = '',
  acceptanceEnabled = false,
  acceptanceAccepted = false,
  onAcceptApplication,
  acceptanceReadOnly = false,
  onSubmitAcceptanceFeedback,
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
  const [frameLoading, setFrameLoading] = useState(true)
  const previewUrl = navigation.history[navigation.index]
  const frameKey = `${previewUrl}-${refreshKey}`
  const elementInspector = useElementInspector({
    enabled: !applicationMode,
    frameKey,
    onInspectingChange,
    previewUrl
  })

  useEffect(() => {
    setDraftUrl(previewUrl)
    setOpenError('')
    // 用户手动导航或刷新时，之前的启动错误已无关，一并清除
    setLaunchError('')
    // 切换/刷新地址时重置 iframe 加载态，等待新一轮 onLoad。
    setFrameLoading(true)
  }, [previewUrl, refreshKey])

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
    <section className={cx('browser-preview-panel', applicationMode && 'application-mode')}>
      {!applicationMode ? (
        <BrowserPreviewToolbar
          draftUrl={draftUrl}
          elementInspectorActive={elementInspector.active}
          elementInspectorReady={elementInspector.ready}
          navigationIndex={navigation.index}
          navigationLength={navigation.history.length}
          onDraftUrlChange={setDraftUrl}
          onNavigate={navigateTo}
          onNavigateHistory={(direction) =>
            setNavigation((current) => ({
              ...current,
              index:
                direction === 'back'
                  ? Math.max(0, current.index - 1)
                  : Math.min(current.history.length - 1, current.index + 1)
            }))
          }
          onOpenInBrowser={openInBrowser}
          onPageChange={handlePageChange}
          onRefresh={() => setRefreshKey((key) => key + 1)}
          onToggleInspector={elementInspector.toggle}
          onViewportChange={setViewport}
          pageOptions={pageOptions}
          selectedPage={selectedPage}
          viewport={viewport}
        />
      ) : null}

      <div className={cx('browser-preview-stage')}>
        {frameLoading && (
          <div className={cx('browser-preview-loading')}>
            <RichLoading bare title="正在加载页面预览…" />
          </div>
        )}
        <div className={cx('browser-preview-viewport', viewport)}>
          <iframe
            key={frameKey}
            onLoad={() => setFrameLoading(false)}
            ref={elementInspector.iframeRef}
            className={cx('browser-preview-frame')}
            src={previewUrl}
            title={`${application.name} 网页预览`}
            sandbox="allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
          />
        </div>
      </div>

      {applicationMode && acceptanceEnabled ? (
        <footer className={cx('browser-preview-acceptance-bar')} aria-label="应用验收确认">
          <div className={cx('browser-preview-acceptance-title')}>版本验收</div>
          <div className={cx('browser-preview-acceptance-prompt')}>
            {acceptanceAccepted
              ? '已依据需求文档基线验收通过。'
              : '请依据需求文档基线验收当前应用。'}
          </div>
          {acceptanceAccepted ? (
            <span className={cx('browser-preview-acceptance-passed')}>
              <CheckCircleFilled /> 已通过
            </span>
          ) : (
            <div className={cx('browser-preview-acceptance-actions')}>
              <Button
                aria-label="验收不通过并进入对话"
                disabled={acceptanceReadOnly}
                icon={<MessageOutlined />}
                onClick={onSubmitAcceptanceFeedback}
                type="default"
              >
                <span className={cx('browser-preview-acceptance-action-full')}>不通过，进入对话</span>
                <span className={cx('browser-preview-acceptance-action-compact')}>不通过</span>
              </Button>
              <Button
                aria-label="验收通过"
                disabled={acceptanceReadOnly}
                icon={<CheckCircleFilled />}
                onClick={onAcceptApplication}
                type="primary"
              >
                <span className={cx('browser-preview-acceptance-action-full')}>验收通过</span>
                <span className={cx('browser-preview-acceptance-action-compact')}>通过</span>
              </Button>
            </div>
          )}
        </footer>
      ) : null}

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
