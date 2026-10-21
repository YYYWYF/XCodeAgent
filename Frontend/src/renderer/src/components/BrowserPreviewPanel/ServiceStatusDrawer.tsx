import {
  ApiOutlined,
  CheckCircleFilled,
  CloudServerOutlined,
  CloseCircleFilled,
  CopyOutlined,
  ExclamationCircleFilled,
  LoadingOutlined,
  ReloadOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Alert, Button, Drawer, Empty, Tabs, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type { PreviewRuntimePayload, PreviewServiceState } from '../../service/previewRuntime'
import { previewServiceActionAvailability } from './serviceStatusPolicy'
import './ServiceStatusDrawer.less'

export type ServiceStatusControl = {
  open: boolean
  setOpen: (open: boolean) => void
  snapshot?: PreviewRuntimePayload
  busy: boolean
  busyLaunchLabel?: '启动服务' | '重启服务'
  error: string
  blockedReason: string
  onRestart: () => void
  onDiagnose: () => void
}

type ServiceLayer = 'frontend' | 'backend'

const statusMeta: Record<
  PreviewServiceState['status'],
  { label: string; icon: ReactElement; tone: string }
> = {
  starting: { label: '启动中', icon: <LoadingOutlined spin />, tone: 'accent' },
  running: { label: '运行中', icon: <CheckCircleFilled />, tone: 'accent' },
  failed: { label: '启动失败', icon: <CloseCircleFilled />, tone: 'danger' },
  stopped: { label: '未启动', icon: <ExclamationCircleFilled />, tone: 'neutral' },
  skipped: { label: '无需启动', icon: <CheckCircleFilled />, tone: 'neutral' }
}

/** 返回服务层的中文名称，统一服务卡片和日志标签的用词。 */
function serviceLabel(layer: ServiceLayer): string {
  return layer === 'frontend' ? '前端服务' : '后端服务'
}

/** 返回服务层的说明，帮助用户在没有地址或失败时快速判断下一步。 */
function serviceHint(layer: ServiceLayer, status: PreviewServiceState['status']): string {
  if (status === 'skipped')
    return layer === 'backend' ? '当前项目无需启动后端进程' : '等待前端运行时就绪'
  if (status === 'starting') return '正在执行依赖、构建与就绪检测'
  if (status === 'running') return '进程已受控运行，可从预览地址访问'
  if (status === 'failed') return '请查看本次启动日志，必要时诊断并修复'
  return '尚未建立本次启动进程'
}

/** 返回服务实际端口，并兼容没有独立端口字段的旧运行快照。 */
function servicePort(service?: PreviewServiceState): string {
  if (service?.port && service.port > 0) return String(service.port)
  if (!service?.url) return ''
  try {
    const parsed = new URL(service.url)
    if (parsed.port) return parsed.port
    return parsed.protocol === 'https:' ? '443' : '80'
  } catch {
    return ''
  }
}

/** 展示主题化的服务状态、维护动作和前后端独立日志。 */
export default function ServiceStatusDrawer(props: ServiceStatusControl): ReactElement {
  const { snapshot, blockedReason, busy } = props
  const [tab, setTab] = useState<ServiceLayer>('frontend')
  const [following, setFollowing] = useState(true)
  const [copyError, setCopyError] = useState('')
  const logRef = useRef<HTMLPreElement>(null)
  const runtime = snapshot?.runtime
  const logs = runtime?.logs?.[tab] || []
  const content = logs
    .map(
      (log) =>
        `[${log.stage} · ${log.stream}] ${log.name}${log.truncated ? '（日志已截断，仅显示末尾）' : ''}\n${log.content}`
    )
    .join('\n\n')
  const actionAvailability = previewServiceActionAvailability({
    busy,
    blockedReason,
    repairAvailable: runtime?.repairAvailable
  })
  const actionTone = busy ? 'is-busy' : 'is-ready'
  const actionLabel = busy ? '处理中' : runtime ? '可操作' : '可启动'
  const launchLabel =
    runtime?.frontend.status === 'running' && runtime?.backend.status === 'running'
      ? '重启服务'
      : '启动服务'
  const launching = busy && !!props.busyLaunchLabel

  useEffect(() => {
    if (runtime?.failedStage)
      setTab(runtime.failedStage.startsWith('backend') ? 'backend' : 'frontend')
  }, [runtime?.attemptId, runtime?.failedStage])

  useEffect(() => {
    if (following && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [content, following, tab])

  /** 复制当前端的脱敏日志，并反馈剪贴板错误。 */
  const copyLogs = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(content)
      setCopyError('')
    } catch {
      setCopyError('无法访问剪贴板，请选择日志文本复制。')
    }
  }

  /** 切换日志端并恢复跟随，确保新选择的日志从最新位置开始查看。 */
  const selectLogTab = (key: string): void => {
    if (key !== 'frontend' && key !== 'backend') return
    setTab(key)
    setFollowing(true)
  }

  return (
    <Drawer
      title={
        <span className="preview-service-drawer-title">
          <span className="preview-service-drawer-title__icon">
            <CloudServerOutlined />
          </span>
          <span>服务状态</span>
        </span>
      }
      open={props.open}
      onClose={() => props.setOpen(false)}
      width="min(520px, 100%)"
      getContainer={false}
      rootClassName="preview-service-drawer"
    >
      <div className="preview-service-drawer__body">
        <section
          className="preview-service-section"
          aria-labelledby="preview-service-status-heading"
        >
          <div className="preview-section-heading">
            <div>
              <h3 id="preview-service-status-heading">服务状态</h3>
            </div>
            <span className="preview-section-caption">受控进程 · 就绪检测</span>
          </div>
          <div className="preview-service-grid">
            {(['frontend', 'backend'] as const).map((layer) => {
              const service = runtime?.[layer]
              const status = service?.status || 'stopped'
              const meta = statusMeta[status]
              const LayerIcon = layer === 'frontend' ? ApiOutlined : CloudServerOutlined
              const port = servicePort(service)
              return (
                <article key={layer} className={`preview-service-card is-${status}`}>
                  <div className="preview-service-card__topline">
                    <div className="preview-service-card__identity">
                      <span className="preview-service-card__icon">
                        <LayerIcon />
                      </span>
                      <strong>{serviceLabel(layer)}</strong>
                    </div>
                    <span className={`preview-service-status-pill is-${meta.tone}`}>
                      <span>{meta.icon}</span>
                      {meta.label}
                    </span>
                  </div>
                  <div className="preview-service-card__meter" aria-hidden="true">
                    <span />
                  </div>
                  <p className="preview-service-card__hint">{serviceHint(layer, status)}</p>
                  <div className="preview-service-card__endpoint">
                    <span>服务端口</span>
                    {port ? (
                      <Typography.Text copyable={{ tooltips: ['复制端口', '已复制'] }}>
                        {port}
                      </Typography.Text>
                    ) : (
                      <span className="is-empty">等待端口</span>
                    )}
                  </div>
                  {service?.message && (
                    <div className={`preview-service-card__message is-${meta.tone}`}>
                      <span>{meta.icon}</span>
                      <span>{service.message}</span>
                    </div>
                  )}
                </article>
              )
            })}
          </div>
        </section>

        <section
          className="preview-service-actions"
          aria-labelledby="preview-service-actions-heading"
        >
          <div className="preview-section-heading">
            <div>
              <h3 id="preview-service-actions-heading">维护操作</h3>
            </div>
            {(busy || !blockedReason) && (
              <span className={`preview-action-state ${actionTone}`}>
                <span /> {actionLabel}
              </span>
            )}
          </div>
          <div className="preview-service-actions__buttons">
            <Button
              className={`preview-service-action-button is-restart${launching ? ' is-launching' : ''}`}
              type="primary"
              icon={<ReloadOutlined />}
              loading={launching}
              disabled={!actionAvailability.canRestart}
              onClick={props.onRestart}
            >
              {launching ? `${props.busyLaunchLabel}中` : launchLabel}
            </Button>
            <Button
              className="preview-service-action-button is-repair"
              type="primary"
              icon={<ToolOutlined />}
              disabled={!actionAvailability.canDiagnose}
              onClick={props.onDiagnose}
            >
              诊断并修复
            </Button>
          </div>
          {(props.error || copyError) && (
            <Alert
              className="preview-service-callout is-error"
              type="error"
              showIcon
              message={props.error || copyError}
            />
          )}
        </section>

        <section className="preview-log-panel" aria-labelledby="preview-log-heading">
          <div className="preview-log-panel__header">
            <div className="preview-section-heading">
              <div>
                <h3 id="preview-log-heading">启动日志</h3>
              </div>
            </div>
            <span className="preview-log-count">
              {logs.length} 个{tab === 'frontend' ? '前端' : '后端'}日志文件
            </span>
          </div>
          <Tabs
            className="preview-log-tabs"
            activeKey={tab}
            onChange={selectLogTab}
            items={(['frontend', 'backend'] as const).map((layer) => ({
              key: layer,
              label: (
                <span className="preview-log-tab-label">
                  <span
                    className={`preview-log-tab-dot is-${statusMeta[runtime?.[layer]?.status || 'stopped'].tone}`}
                  />
                  {layer === 'frontend' ? '前端' : '后端'}
                  <em>{runtime?.logs?.[layer]?.length || 0}</em>
                </span>
              )
            }))}
          />
          <div className="preview-log-toolbar">
            <span className="preview-log-toolbar__hint">
              <span className="preview-log-live-dot" />
              {following ? '正在跟随最新输出' : '已暂停自动滚动'}
            </span>
            <div className="preview-log-toolbar__actions">
              <Button
                size="small"
                icon={<CopyOutlined />}
                disabled={!content}
                onClick={() => void copyLogs()}
              >
                复制日志
              </Button>
              <Button
                size="small"
                className={following ? 'is-following' : ''}
                onClick={() => setFollowing(!following)}
              >
                {following ? '跟随最新' : '恢复跟随'}
              </Button>
            </div>
          </div>
          {content ? (
            <pre
              ref={logRef}
              className="preview-service-logs"
              aria-label={`${tab === 'frontend' ? '前端' : '后端'}启动日志`}
              onScroll={(event) => {
                const node = event.currentTarget
                if (node.scrollHeight - node.scrollTop - node.clientHeight > 24) setFollowing(false)
              }}
            >
              {content}
            </pre>
          ) : (
            <div className="preview-log-empty">
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次启动暂无日志" />
            </div>
          )}
        </section>
      </div>
    </Drawer>
  )
}
