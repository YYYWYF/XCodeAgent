import { AimOutlined, FileTextOutlined, InfoCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Select, Spin, Tooltip } from 'antd'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { ReactElement, ReactNode } from 'react'
import { previewServiceAttemptId } from '../../../../service/previewRuntime'
import type { DevelopmentArtifacts, DevelopmentPlanningPageOption } from '../../../../typings'
import type { ElementInspectorSelectionMessage } from '../../../BrowserPreviewPanel/elementInspectorProtocol'
import { useElementInspector } from '../../../BrowserPreviewPanel/useElementInspector'
import { useDebugSelection } from '../../hooks/useDebugSelection'
import type { DebugServiceControl } from '../../hooks/usePreviewRuntime'
import type { DebugSelectionRequest } from '../../types'
import DebugServiceLogs from '../DebugRuntime/DebugServiceLogs'
import DebugServiceStatusButton, { debugServiceStatusMessage } from '../DebugRuntime/DebugServiceControls'
import PageDebugSource from './PageDebugSource'
import { debugPageOptions, debugPageUrl } from './pageDebugModel'
import './PageDebug.less'

type Props = {
  workspaceRoot: string
  pages: DevelopmentPlanningPageOption[]
  artifacts?: DevelopmentArtifacts
  preferredPageId?: string
  selectionRequest?: DebugSelectionRequest
  catalogLoading?: boolean
  catalogError?: string
  onCatalogRetry: () => void
  control: DebugServiceControl
  active: boolean
  logsOpen: boolean
  onLogsOpenChange: (open: boolean) => void
}

type SessionProps = Pick<Props, 'workspaceRoot'> & {
  url: string
  label?: string
  placeholder: ReactNode
  showRequestNotice: boolean
  blockedAt?: number
}

const NOTICE_MS = 3000

/** 只提示本次可见期间实际受阻的请求，连续请求合并为一条定时消失的提示。 */
function PageDebugRequestNotice({ blockedAt }: { blockedAt?: number }): ReactElement | null {
  const [visibleSince] = useState(Date.now)
  const lastSeen = useRef(blockedAt ?? 0)
  const [shown, setShown] = useState<number>()

  useEffect(() => {
    if (!blockedAt) {
      setShown(undefined)
      return
    }
    const previous = lastSeen.current
    if (blockedAt < visibleSince || blockedAt <= previous) return
    lastSeen.current = blockedAt
    // 重连可能重送旧事件，页面也可能连续轮询失败，两者都不延长提示。
    if (Date.now() - blockedAt < NOTICE_MS && (previous < visibleSince || blockedAt - previous >= NOTICE_MS)) {
      setShown(blockedAt)
    }
  }, [blockedAt, visibleSince])

  useEffect(() => {
    if (shown === undefined) return
    const timer = setTimeout(() => setShown(undefined), NOTICE_MS)
    return () => clearTimeout(timer)
  }, [shown])

  return shown !== undefined && blockedAt !== undefined ? (
    <div className="page-debug__request-notice" role="status">
      <InfoCircleOutlined aria-hidden="true" />
      后端服务未启动，本次请求未完成。
    </div>
  ) : null
}

/** 路由和前端轮次驱动 iframe 与审查选择，后端启停不重载页面。 */
function PageDebugSession({ workspaceRoot, url, label, placeholder, showRequestNotice, blockedAt }: SessionProps): ReactElement {
  const [selection, setSelection] = useState<ElementInspectorSelectionMessage>()
  const [loaded, setLoaded] = useState(false)
  const inspector = useElementInspector({
    disabled: !url,
    frameKey: url,
    previewUrl: url,
    onSelectionChange: setSelection
  })

  return (
    <>
      <div className="page-debug__stage">
        {url ? (
          <>
            {showRequestNotice && <PageDebugRequestNotice blockedAt={blockedAt} />}
            <div className="page-debug__inspect-bar">
              {!loaded && <span className="page-debug__loading" role="status">正在加载页面…</span>}
              <Tooltip title={inspector.ready ? undefined : '等待页面元素审查就绪'}>
                <span>
                  <Button
                    size="small"
                    icon={<AimOutlined />}
                    aria-pressed={inspector.active}
                    disabled={!inspector.ready}
                    onClick={inspector.toggle}
                  >
                    {inspector.active ? '退出审查' : '审查元素'}
                  </Button>
                </span>
              </Tooltip>
            </div>
            <iframe
              ref={inspector.iframeRef}
              className="page-debug__frame"
              title={`${label || '当前页面'} · 页面调试预览`}
              src={url}
              onLoad={() => setLoaded(true)}
              sandbox="allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
            />
          </>
        ) : (
          <div className="page-debug__empty">{placeholder}</div>
        )}
      </div>
      {selection && (
        <PageDebugSource
          key={JSON.stringify([selection.tagName, selection.sourceLocation])}
          selection={selection}
          workspaceRoot={workspaceRoot}
        />
      )}
    </>
  )
}

/** 选择已完成的页面，使用当前工作区前端服务进行桌面预览与调试。 */
export default function PageDebugPanel({
  workspaceRoot,
  pages,
  artifacts,
  preferredPageId,
  selectionRequest,
  catalogLoading,
  catalogError,
  onCatalogRetry,
  control,
  active,
  logsOpen,
  onLogsOpenChange
}: Props): ReactElement {
  const logsId = useId()
  const [pageId, setPageId, selectionRequestKey] = useDebugSelection(selectionRequest)
  const [reloadKey, setReloadKey] = useState(0)
  const [expanded, setExpanded] = useState(false)
  const options = useMemo(() => debugPageOptions(pages, artifacts), [pages, artifacts])
  const picked = options.find((page) => page.pageId === (pageId ?? preferredPageId)) ?? options[0]
  const runtime = control.snapshot?.runtime
  const url = debugPageUrl(control.snapshot, picked?.path)
  const launching =
    control.busy && Boolean(control.busyLaunchLabel) && control.busyLaunchLayer !== 'backend'
  const starting = launching || runtime?.frontend.status === 'starting'
  const canStart = !control.busy && !runtime?.maintenance && !starting
  const showExpanded = logsOpen && expanded

  /** 从完成提示跳转时露出页面，保留普通高度的日志面板。 */
  useEffect(() => {
    if (selectionRequest) setExpanded(false)
  }, [selectionRequest])

  /** 关闭面板时复位展开状态，保留正在调试的页面。 */
  function toggleLogs(open: boolean): void {
    if (!open) setExpanded(false)
    onLogsOpenChange(open)
  }

  let placeholder: ReactNode
  if (!picked) {
    placeholder = catalogLoading ? (
      <Spin tip="正在读取页面目录" />
    ) : (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={catalogError ? '暂时无法读取页面' : '当前迭代暂无已完成的页面'}
      />
    )
  } else if (starting || (!runtime && !control.error)) {
    placeholder = <Spin tip={starting ? '正在启动前端服务' : '正在读取服务状态'} />
  } else if (runtime?.frontend.status === 'running') {
    placeholder =
      !control.error && !control.snapshot?.pageDebug?.frontendAttemptId ? (
        <Spin tip="正在连接页面调试入口" />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前路由没有可用的前端调试地址" />
      )
  } else {
    placeholder = (
      <>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={debugServiceStatusMessage('frontend', runtime?.frontend) || '前端服务未启动'}
        />
        <Button
          type="primary"
          icon={<ReloadOutlined />}
          disabled={!canStart}
          onClick={() => control.onRestartService('frontend')}
        >
          启动服务
        </Button>
      </>
    )
  }

  return (
    <section className="page-debug" aria-label="页面调试">
      <div className="page-debug__workspace" hidden={showExpanded}>
        {active && (
          <header className="page-debug__toolbar">
            <div className="page-debug__target" role="group" aria-label="页面和路由">
              <Select
                className="page-debug__selector"
                size="small"
                aria-label="选择调试页面"
                placeholder="选择页面"
                value={picked?.pageId}
                disabled={!options.length}
                loading={catalogLoading}
                options={options.map((page) => ({ value: page.pageId, label: page.label }))}
                onChange={setPageId}
              />
              <code className="page-debug__route" title={picked?.path}>
                {picked?.path || '—'}
              </code>
            </div>
            <div className="page-debug__actions">
              <Button
                type="primary"
                icon={<ReloadOutlined />}
                disabled={!url}
                onClick={() => setReloadKey((current) => current + 1)}
              >
                重新加载
              </Button>
              <DebugServiceStatusButton
                control={control}
                layer="frontend"
                address={runtime?.frontend.status === 'running' ? runtime.frontend.url : undefined}
              />
              <Button
                className="debug-runtime-utility"
                icon={<FileTextOutlined />}
                aria-expanded={logsOpen}
                aria-controls={logsOpen ? logsId : undefined}
                onClick={() => toggleLogs(!logsOpen)}
              >
                运行日志
              </Button>
            </div>
          </header>
        )}
        {catalogError && (
          <Alert
            className="page-debug__alert"
            type="error"
            showIcon
            message={catalogError}
            action={
              <Button size="small" disabled={catalogLoading} onClick={onCatalogRetry}>
                重新读取
              </Button>
            }
          />
        )}
        {control.error && (
          <Alert className="page-debug__alert" type="error" showIcon message={control.error} />
        )}
        <PageDebugSession
          key={JSON.stringify([
            previewServiceAttemptId(runtime, 'frontend'),
            picked?.pageId,
            url,
            // 从完成提示主动打开同一个页面时，也要回到目标路由。
            selectionRequestKey,
            reloadKey
          ])}
          workspaceRoot={workspaceRoot}
          url={url}
          label={picked?.label}
          placeholder={placeholder}
          showRequestNotice={active && !showExpanded && (runtime?.backend.status === 'stopped' || runtime?.backend.status === 'skipped')}
          blockedAt={control.snapshot?.pageDebug?.backendRequestBlockedAt}
        />
      </div>
      {logsOpen && (
        <DebugServiceLogs
          id={logsId}
          layer="frontend"
          runtime={runtime}
          logsExpanded={showExpanded}
          onExpandedChange={setExpanded}
          onClose={() => toggleLogs(false)}
        />
      )}
    </section>
  )
}
