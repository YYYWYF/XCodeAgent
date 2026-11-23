import {
  CheckCircleFilled, CloudServerOutlined, CloseCircleFilled, ExclamationCircleFilled,
  LoadingOutlined, ReloadOutlined, StopOutlined, ToolOutlined
} from '@ant-design/icons'
import { Alert, Button, Typography } from 'antd'
import { useId } from 'react'
import type { ReactElement } from 'react'
import type { PreviewServiceState } from '../../../../service/previewRuntime'
import type { ServiceStatusControl } from '../../../BrowserPreviewPanel/ServiceStatusDrawer'
import { previewServiceActionAvailability, previewServiceLaunchLabel } from '../../../BrowserPreviewPanel/serviceStatusPolicy'
import '../../../BrowserPreviewPanel/ServiceStatusDrawer.less'

export const statusMeta: Record<
  PreviewServiceState['status'],
  { label: string; icon: ReactElement; tone: string }
> = {
  starting: { label: '启动中', icon: <LoadingOutlined spin />, tone: 'accent' },
  running: { label: '运行中', icon: <CheckCircleFilled />, tone: 'accent' },
  failed: { label: '启动失败', icon: <CloseCircleFilled />, tone: 'danger' },
  stopped: { label: '未启动', icon: <ExclamationCircleFilled />, tone: 'neutral' },
  skipped: { label: '无需启动', icon: <CheckCircleFilled />, tone: 'neutral' }
}

/** 按后端进程状态提示调试页的维护操作。 */
function serviceHint(status: PreviewServiceState['status'], resourceFailure: boolean): string {
  if (status === 'skipped') return '当前项目无需启动后端进程'
  if (status === 'starting') return '正在执行依赖、构建与就绪检测'
  if (status === 'running') return '后端服务已就绪，可发送请求'
  if (status === 'failed') return resourceFailure
    ? '请先处理实体表或内置数据库配置，再重新启动'
    : '请查看运行日志，必要时诊断并修复'
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

/** 展示后端进程状态，并通过预览运行时停止后端服务。 */
export function InternalBackendServiceCard({ control, address }: {
  control: ServiceStatusControl; address?: string
}): ReactElement {
  const runtime = control.snapshot?.runtime
  const service = runtime?.backend
  const status = service?.status || 'stopped'
  const stopped = status === 'stopped' && service?.stoppedByUser
  const stopping = control.busyStopLayer === 'backend' || runtime?.maintenance?.action === 'stop_backend'
  const meta = statusMeta[status]
  const port = servicePort(service)
  const busy = control.busy
  return (
    <article className={`preview-service-card is-${status}`}>
      <div className="preview-service-card__topline">
        <div className="preview-service-card__identity">
          <span className="preview-service-card__icon">
            <CloudServerOutlined />
          </span>
          <strong>后端服务</strong>
        </div>
        <span className={`preview-service-status-pill is-${meta.tone}`}>
          <span>{meta.icon}</span>
          {stopped ? '已停止' : meta.label}
        </span>
      </div>
      <div className="preview-service-card__meter" aria-hidden="true">
        <span />
      </div>
      <p className="preview-service-card__hint">
        {stopped
          ? '可通过维护操作重新启动'
          : serviceHint(status, runtime?.failedStage === 'backend_database_resource')}
      </p>
      <div className="preview-service-card__endpoint">
        <span>{address ? '访问地址' : '服务端口'}</span>
        {address || port ? (
            <Typography.Text copyable={{ tooltips: [address ? '复制地址' : '复制端口', '已复制'] }}>
            {address || port}
          </Typography.Text>
        ) : (
          <span className="is-empty">{stopped ? '—' : '等待端口'}</span>
        )}
      </div>
      {service?.message && (
        <div className={`preview-service-card__message is-${meta.tone}`}>
          <span>{meta.icon}</span>
          <span>{service.message}</span>
        </div>
      )}
      <Button
        aria-label="停止后端服务"
        className="preview-service-card__stop"
        size="small"
        icon={<StopOutlined />}
        loading={stopping}
        disabled={status !== 'running' || busy || Boolean(runtime?.maintenance)}
        onClick={() => control.onStop('backend')}
      >
        {stopping ? '停止中' : '停止服务'}
      </Button>
    </article>
  )
}

/** 在调试页使用预览运行时提供的启动、诊断与修复操作。 */
export function InternalApiMaintenanceActions({ control }: { control: ServiceStatusControl }): ReactElement {
  const headingId = useId()
  const { blockedReason, busy, snapshot } = control
  const runtime = snapshot?.runtime
  const actionAvailability = previewServiceActionAvailability({ busy, blockedReason, repairAvailable: runtime?.repairAvailable })
  const actionTone = busy ? 'is-busy' : 'is-ready'
  const actionLabel = busy ? '处理中' : runtime ? '可操作' : '可启动'
  const launchLabel = previewServiceLaunchLabel(runtime)
  const launching = busy && !!control.busyLaunchLabel
  return (
    <section
      className="preview-service-actions"
      aria-labelledby={headingId}
    >
      <div className="preview-section-heading">
        <div>
          <h3 id={headingId}>维护操作</h3>
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
          onClick={control.onRestart}
        >
          {launching ? `${control.busyLaunchLabel}中` : launchLabel}
        </Button>
        <Button
          className="preview-service-action-button is-repair"
          type="primary"
          icon={<ToolOutlined />}
          disabled={!actionAvailability.canDiagnose}
          onClick={control.onDiagnose}
        >
          诊断并修复
        </Button>
      </div>
      {control.error && (
        <Alert
          className="preview-service-callout is-error"
          type="error"
          showIcon
          message={control.error}
        />
      )}
    </section>
  )
}
