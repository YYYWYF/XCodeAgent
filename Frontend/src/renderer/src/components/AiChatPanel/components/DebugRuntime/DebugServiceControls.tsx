import {
  CheckCircleFilled,
  CloudServerOutlined,
  CloseCircleFilled,
  ExclamationCircleFilled,
  LoadingOutlined,
  ReloadOutlined,
  StopOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Alert, Button, Popover, Typography } from 'antd'
import { useId } from 'react'
import type { ReactElement } from 'react'
import type { PreviewServiceLayer, PreviewServiceState } from '../../../../service/previewRuntime'
import { cx } from '../../../../utils'
import type { DebugServiceControl } from '../../hooks/usePreviewRuntime'
import { previewServiceActionAvailability } from '../../../BrowserPreviewPanel/serviceStatusPolicy'
import '../../../BrowserPreviewPanel/ServiceStatusDrawer.less'
import './DebugRuntime.less'

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

/** 按当前进程状态提示调试页的维护操作。 */
function serviceHint(
  layer: PreviewServiceLayer,
  status: PreviewServiceState['status'],
  resourceFailure: boolean
): string {
  if (status === 'skipped') return '当前项目无需启动后端进程'
  if (status === 'starting') return '正在执行依赖、构建与就绪检测'
  if (status === 'running')
    return layer === 'backend' ? '后端服务已就绪，可发送请求' : '前端服务已就绪，可调试页面'
  if (status === 'failed')
    return resourceFailure
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

type ServiceProps = { control: DebugServiceControl; layer: PreviewServiceLayer; address?: string }

/** 联动预览跳过前端时记录的是其他服务的启动原因，页面调试只显示前端自身的消息。 */
export function debugServiceStatusMessage(layer: PreviewServiceLayer, service?: PreviewServiceState): string | undefined {
  if (layer === 'frontend' && service?.status === 'stopped' && !service.stoppedByUser) return undefined
  return service?.message
}

/** 展示指定进程状态，并通过预览运行时停止当前服务。 */
function DebugServiceCard({ control, layer, address }: ServiceProps): ReactElement {
  const runtime = control.snapshot?.runtime
  const service = runtime?.[layer]
  const status = service?.status || 'stopped'
  const stopped = status === 'stopped' && service?.stoppedByUser
  const stopping =
    control.busyStopLayer === layer || runtime?.maintenance?.action === `stop_${layer}`
  const label = layer === 'backend' ? '后端服务' : '前端服务'
  const meta = statusMeta[status]
  const message = debugServiceStatusMessage(layer, service)
  const port = servicePort(service)
  const busy = control.busy
  return (
    <article className={`preview-service-card is-${status}`}>
      <div className="preview-service-card__topline">
        <div className="preview-service-card__identity">
          <span className="preview-service-card__icon">
            <CloudServerOutlined />
          </span>
          <strong>{label}</strong>
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
          : serviceHint(layer, status, service?.failedStage === 'backend_database_resource')}
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
      {message && (
        <div className={`preview-service-card__message is-${meta.tone}`}>
          <span>{meta.icon}</span>
          <span>{message}</span>
        </div>
      )}
      <Button
        aria-label={`停止${label}`}
        className="preview-service-card__stop"
        size="small"
        icon={<StopOutlined />}
        loading={stopping}
        disabled={status !== 'running' || busy || Boolean(runtime?.maintenance)}
        onClick={() => control.onStop(layer)}
      >
        {stopping ? '停止中' : '停止服务'}
      </Button>
    </article>
  )
}

/** 展示当前服务的启动、诊断与修复操作。 */
function DebugServiceMaintenance({ control, layer }: ServiceProps): ReactElement {
  const headingId = useId()
  const { blockedReason, busy, snapshot } = control
  const runtime = snapshot?.runtime
  const service = runtime?.[layer]
  const occupied = busy || Boolean(runtime?.maintenance)
  const actionAvailability = previewServiceActionAvailability({
    busy: occupied,
    blockedReason,
    repairAvailable: service?.repairAvailable
  })
  const actionTone = busy ? 'is-busy' : 'is-ready'
  const actionLabel = busy ? '处理中' : runtime ? '可操作' : '可启动'
  const launchLabel = service?.status === 'running' ? '重启服务' : '启动服务'
  const launching =
    busy &&
    Boolean(control.busyLaunchLabel) &&
    (!control.busyLaunchLayer || control.busyLaunchLayer === layer)
  return (
    <section className="preview-service-actions" aria-labelledby={headingId}>
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
          onClick={() => control.onRestartService(layer)}
        >
          {launching ? `${control.busyLaunchLabel}中` : launchLabel}
        </Button>
        <Button
          className="preview-service-action-button is-repair"
          type="primary"
          icon={<ToolOutlined />}
          disabled={!actionAvailability.canDiagnose}
          onClick={() => control.onDiagnoseService(layer)}
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

/** 让两个调试页的弹层沿用同一工作台主题及定位。 */
function workspacePopup(trigger: HTMLElement): HTMLElement {
  return (
    trigger.closest<HTMLElement>(`.${cx('workbench-shell')}`) ??
    trigger.parentElement ??
    document.body
  )
}

/** 提供原接口调试样式的服务状态弹层，按入口绑定到前端或后端。 */
export default function DebugServiceStatusButton({
  control,
  layer,
  address
}: ServiceProps): ReactElement {
  const runtime = control.snapshot?.runtime
  const service = runtime?.[layer]
  const status = statusMeta[service?.status ?? 'stopped']
  const stopping =
    control.busyStopLayer === layer || runtime?.maintenance?.action === `stop_${layer}`
  const stopped = service?.status === 'stopped' && service.stoppedByUser
  const label = stopping ? '停止中' : stopped ? '已停止' : service ? status.label : '读取状态'
  const tone = service?.status === 'running' && !stopping ? 'running' : status.tone
  return (
    <Popover
      placement="bottomRight"
      trigger="click"
      getPopupContainer={workspacePopup}
      overlayClassName="debug-service-popover"
      content={
        <div className="debug-service-content">
          <DebugServiceCard control={control} layer={layer} address={address} />
          <DebugServiceMaintenance control={control} layer={layer} />
        </div>
      }
    >
      <Button
        className="debug-runtime-utility"
        icon={<CloudServerOutlined />}
        aria-label={`服务状态：${label}`}
        title={label}
      >
        服务状态 <span className={`debug-service-indicator is-${tone}`} aria-hidden="true" />
      </Button>
    </Popover>
  )
}
