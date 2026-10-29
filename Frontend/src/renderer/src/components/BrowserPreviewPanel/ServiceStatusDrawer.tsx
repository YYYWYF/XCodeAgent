import {
  CheckOutlined,
  CloudServerOutlined,
  CopyOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  ReloadOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Alert, Button, Drawer, Tabs } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { PreviewRuntimePayload } from '../../service/previewRuntime'
import ServiceStatusCards, { serviceLabel, serviceLayers, statusMeta } from './ServiceStatusCards'
import type { ServiceLayer } from './ServiceStatusCards'
import { previewServiceActionAvailability, previewServiceLaunchLabel } from './serviceStatusPolicy'
import DatabaseCheckPanel from './DatabaseCheckPanel'
import ServiceLogContent from './ServiceLogContent'
import { useServiceLogClipboard } from './useServiceLogClipboard'
import './ServiceStatusDrawer.less'
import './ServiceStatusDrawer.expanded.less'

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
  showDatabaseCheck?: boolean
  onOpenEntity?: (entityId: string) => void
}

/** 展示主题化的服务状态、维护动作和前后端独立日志。 */
export default function ServiceStatusDrawer(props: ServiceStatusControl): ReactElement {
  const { snapshot, blockedReason, busy } = props
  const [tab, setTab] = useState<ServiceLayer>('frontend')
  const [following, setFollowing] = useState(true)
  const { copied, copyError, copyLogs } = useServiceLogClipboard()
  const [logsExpanded, setLogsExpanded] = useState(false)
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
  const launchLabel = previewServiceLaunchLabel(runtime)
  const launching = busy && !!props.busyLaunchLabel

  useEffect(() => {
    if (runtime?.failedStage)
      setTab(runtime.failedStage.startsWith('agent_runtime') ? 'agent_runtime'
        : runtime.failedStage.startsWith('backend') ? 'backend' : 'frontend')
  }, [runtime?.attemptId, runtime?.failedStage])

  useEffect(() => {
    if (!props.open) setLogsExpanded(false)
  }, [props.open])

  /** 切换日志端并恢复跟随，确保新选择的日志从最新位置开始查看。 */
  const selectLogTab = (key: string): void => {
    if (key !== 'frontend' && key !== 'backend' && key !== 'agent_runtime') return
    setTab(key)
    setFollowing(true)
  }

  /** 关闭服务抽屉后定位实体，只改变浏览选择，不执行建表或推进工作流。 */
  const openEntity = (entityId: string): void => {
    props.setOpen(false)
    props.onOpenEntity?.(entityId)
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
      className={`preview-service-drawer${logsExpanded ? ' is-log-expanded' : ''}`}
    >
      <div className="preview-service-drawer__body">
        <ServiceStatusCards runtime={runtime} />

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
          {props.error && (
            <Alert
              className="preview-service-callout is-error"
              type="error"
              showIcon
              message={props.error}
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
              {logs.length} 个{serviceLabel(tab)}日志文件
            </span>
          </div>
          <Tabs
            className="preview-log-tabs"
            activeKey={tab}
            onChange={selectLogTab}
            items={serviceLayers.map((layer) => ({
              key: layer,
              label: (
                <span className="preview-log-tab-label">
                  <span
                    className={`preview-log-tab-dot is-${statusMeta[runtime?.[layer]?.status || 'stopped'].tone}`}
                  />
                  {serviceLabel(layer)}
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
                icon={copied ? <CheckOutlined /> : <CopyOutlined />}
                disabled={!content}
                onClick={() => void copyLogs(content)}
              >
                {copied ? '已复制' : '复制日志'}
              </Button>
              <Button
                size="small"
                className={following ? 'is-following' : ''}
                onClick={() => setFollowing(!following)}
              >
                {following ? '跟随最新' : '恢复跟随'}
              </Button>
              <Button
                size="small"
                icon={logsExpanded ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                aria-label={logsExpanded ? '还原日志高度' : '展开日志至抽屉高度'}
                aria-pressed={logsExpanded}
                onClick={() => setLogsExpanded((expanded) => !expanded)}
              >
                {logsExpanded ? '还原' : '展开'}
              </Button>
            </div>
          </div>
          {copyError && (
            <Alert
              type="error"
              showIcon
              message={copyError}
              className="preview-service-callout is-error"
            />
          )}
          <ServiceLogContent
            key={`${runtime?.attemptId || ''}:${tab}`}
            content={content}
            label={`${tab === 'frontend' ? '前端' : '后端'}启动日志`}
            following={following}
            expanded={logsExpanded}
            onPauseFollowing={() => setFollowing(false)}
            onCopy={copyLogs}
          />
        </section>
        {props.showDatabaseCheck && <DatabaseCheckPanel report={runtime?.databaseCheck}
          onOpenEntity={props.onOpenEntity ? openEntity : undefined} />}
      </div>
    </Drawer>
  )
}
