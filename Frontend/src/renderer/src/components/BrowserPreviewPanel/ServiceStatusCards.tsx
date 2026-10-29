import { ApiOutlined, CheckCircleFilled, CloudServerOutlined, CloseCircleFilled, ExclamationCircleFilled, LoadingOutlined } from '@ant-design/icons'
import { Alert, Button, Typography } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type { PreviewRuntimePayload, PreviewServiceState } from '../../service/previewRuntime'
import { openPreviewWindow } from '../../utils/previewUrl'
import './ServiceStatusCards.less'

export type ServiceLayer = 'frontend' | 'backend' | 'agent_runtime'
export const serviceLayers: ServiceLayer[] = ['frontend', 'backend', 'agent_runtime']
export const statusMeta: Record<PreviewServiceState['status'], { label: string; icon: ReactElement; tone: string }> = {
  starting: { label: '启动中', icon: <LoadingOutlined spin />, tone: 'accent' },
  running: { label: '运行中', icon: <CheckCircleFilled />, tone: 'accent' },
  failed: { label: '启动失败', icon: <CloseCircleFilled />, tone: 'danger' },
  stopped: { label: '未启动', icon: <ExclamationCircleFilled />, tone: 'neutral' },
  skipped: { label: '无需启动', icon: <CheckCircleFilled />, tone: 'neutral' }
}

/** 统一三类服务在卡片及独立日志中的名称。 */
export function serviceLabel(layer: ServiceLayer): string {
  return layer === 'frontend' ? '前端服务' : layer === 'backend' ? 'Java 后端' : 'Agent Runtime'
}

/** 返回真实服务状态说明，不把缺少 Java 等同于 Runtime 不需要启动。 */
function serviceHint(layer: ServiceLayer, status: PreviewServiceState['status']): string {
  if (status === 'skipped') return `当前应用无需启动${serviceLabel(layer)}`
  if (status === 'starting') return '正在执行依赖与就绪检测'
  if (status === 'running') return '进程已受控运行，可访问当前服务'
  if (status === 'failed') return '请查看本次启动日志和具体错误后处理'
  return '尚未建立本次启动进程'
}

/** 从当前服务事实提取端口，不使用固定 Runtime 端口。 */
function servicePort(service?: PreviewServiceState): string {
  if (service?.port && service.port > 0) return String(service.port)
  if (!service?.url) return ''
  try {
    return new URL(service.url).port
  } catch {
    return ''
  }
}

/** 用现有主题样式展示三类服务，并在独立 Electron 窗口打开 Runtime 接口调试。 */
export default function ServiceStatusCards({ runtime }: { runtime?: PreviewRuntimePayload['runtime'] }): ReactElement {
  const [openError, setOpenError] = useState('')

  /** 仅打开当前受管 loopback Runtime 的 API 文档，不携带模型或调试密钥。 */
  const openRuntime = async (service: PreviewServiceState): Promise<void> => {
    setOpenError('')
    try {
      const url = new URL(service.url || '')
      if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port)
        throw new Error('Runtime 调试地址无效，请重新启动服务。')
      await openPreviewWindow(new URL('/docs', url.origin).toString())
    } catch (error) {
      setOpenError(error instanceof Error ? error.message : '无法打开 Runtime 接口调试。')
    }
  }

  return (
    <section className="preview-service-section" aria-labelledby="preview-service-status-heading">
      <div className="preview-section-heading">
        <div><h3 id="preview-service-status-heading">服务状态</h3></div>
        <span className="preview-section-caption">受控进程 · 就绪检测</span>
      </div>
      <div className="preview-service-grid">
        {serviceLayers.map((layer) => {
          const service = runtime?.[layer]
          const status = service?.status || 'stopped'
          const meta = statusMeta[status]
          const LayerIcon = layer === 'frontend' ? ApiOutlined : CloudServerOutlined
          const port = servicePort(service)
          return (
            <article key={layer} className={`preview-service-card is-${status}${layer === 'agent_runtime' ? ' is-runtime' : ''}`}>
              <div className="preview-service-card__topline">
                <div className="preview-service-card__identity">
                  <span className="preview-service-card__icon"><LayerIcon /></span>
                  <strong>{serviceLabel(layer)}</strong>
                </div>
                <span className={`preview-service-status-pill is-${meta.tone}`}><span>{meta.icon}</span>{meta.label}</span>
              </div>
              <div className="preview-service-card__meter" aria-hidden="true"><span /></div>
              <p className="preview-service-card__hint">{serviceHint(layer, status)}</p>
              <div className="preview-service-card__endpoint">
                <span>服务端口</span>
                {port ? <Typography.Text copyable={{ tooltips: ['复制端口', '已复制'] }}>{port}</Typography.Text>
                  : <span className="is-empty">{status === 'skipped' ? '无需端口' : '等待端口'}</span>}
              </div>
              {service?.message && <div className={`preview-service-card__message is-${meta.tone}`}><span>{meta.icon}</span><span>{service.message}</span></div>}
              {layer === 'agent_runtime' && <Button disabled={status !== 'running' || !service?.url}
                onClick={() => service && void openRuntime(service)}>打开接口调试</Button>}
            </article>
          )
        })}
      </div>
      {openError && <Alert type="error" showIcon message={openError} className="preview-service-callout is-error" />}
    </section>
  )
}
