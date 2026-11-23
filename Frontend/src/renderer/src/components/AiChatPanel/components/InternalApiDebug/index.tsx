import { CloudServerOutlined, FileTextOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Popover, Select, Spin } from 'antd'
import { useEffect, useId, useMemo, useState } from 'react'
import type { DevelopmentArtifacts, DevelopmentPlanningApiContract } from '../../../../typings'
import { describeInternalApi, type InternalDebugDefinition, type InternalDebugTarget } from '../../../../service/internalApiDebug'
import { cx } from '../../../../utils'
import type { ServiceStatusControl } from '../../../BrowserPreviewPanel/ServiceStatusDrawer'
import InternalApiLogs from './InternalApiLogs'
import InternalApiRequest, { InternalApiRequestBar } from './InternalApiRequest'
import { InternalApiMaintenanceActions, InternalBackendServiceCard, statusMeta } from './InternalApiServiceControls'
import { completedInternalEndpoints, internalDebugService, internalDebugStorageKey } from './internalApiDebugModel'
import './InternalApiDebug.less'

type Props = {
  workspaceRoot: string
  iterationId: string
  contracts: DevelopmentPlanningApiContract[]
  artifacts?: DevelopmentArtifacts
  catalogLoading?: boolean
  catalogError?: string
  onCatalogRetry?: () => void
  control: ServiceStatusControl
  logsOpen: boolean
  onLogsOpenChange: (open: boolean) => void
}
type DefinitionState = { key: string; value?: InternalDebugDefinition; error?: string }
const serviceActions = ['restart', 'stop', 'stop_backend', 'confirm']

/** 让弹层留在当前工作台主题下。 */
function workspacePopup(trigger: HTMLElement): HTMLElement {
  return trigger.closest<HTMLElement>(`.${cx('workbench-shell')}`) ?? trigger.parentElement ?? document.body
}

/** 展示当前迭代的正式接口、共享后端维护与底部日志。 */
export default function InternalApiDebugPanel({
  workspaceRoot, iterationId, contracts, artifacts, catalogLoading, catalogError, onCatalogRetry,
  control, logsOpen, onLogsOpenChange
}: Props) {
  const logsId = useId()
  const [expanded, setExpanded] = useState(false)
  const [selection, setSelection] = useState<string>()
  const [definitionState, setDefinitionState] = useState<DefinitionState>()
  const [retry, setRetry] = useState(0)
  const endpoints = useMemo(() => completedInternalEndpoints(contracts, artifacts), [contracts, artifacts])
  const selected = endpoints.find((endpoint) => endpoint.key === selection) ?? endpoints[0]
  const contractId = selected?.apiContractId
  const endpointId = selected?.endpointId
  const target = useMemo<InternalDebugTarget | undefined>(() => contractId && endpointId && iterationId
    ? { workspaceRoot, iterationId, apiContractId: contractId, endpointId }
    : undefined, [workspaceRoot, iterationId, contractId, endpointId])
  const definitionKey = JSON.stringify([target, selected?.method, selected?.path, retry])
  const detail = definitionState?.key === definitionKey ? definitionState : undefined
  const definition = detail?.value

  /** 仅接收当前选择的定义，切换接口或迭代时取消旧请求。 */
  useEffect(() => {
    if (!target) return
    const abort = new AbortController()
    const timeout = window.setTimeout(() => {
      setDefinitionState({ key: definitionKey, error: '读取接口定义超时，请重试。' })
      abort.abort()
    }, 15_000)
    void describeInternalApi(target, abort.signal).then((value) => {
      if (!abort.signal.aborted) setDefinitionState({ key: definitionKey, value })
    }).catch((error: unknown) => {
      if (!abort.signal.aborted) setDefinitionState({
        key: definitionKey, error: error instanceof Error ? error.message : '读取接口定义失败。'
      })
    }).finally(() => window.clearTimeout(timeout))
    return () => {
      window.clearTimeout(timeout)
      abort.abort()
    }
  }, [target, definitionKey])

  const runtime = control.snapshot?.runtime
  const service = internalDebugService(runtime)
  const backend = runtime?.backend
  const status = statusMeta[backend?.status ?? 'stopped']
  const stopping = control.busyStopLayer === 'backend' || runtime?.maintenance?.action === 'stop_backend'
  const stopped = backend?.status === 'stopped' && backend.stoppedByUser
  const statusTone = backend?.status === 'running' && !stopping ? 'running' : status.tone
  const statusLabel = stopping ? '停止中' : stopped ? '已停止' : backend ? status.label : '读取状态'
  const canSend = Boolean(service && !control.busyLaunchLabel && !stopping &&
    !serviceActions.includes(runtime?.maintenance?.action || ''))
  const showExpanded = logsOpen && expanded
  const endpointsError = catalogError || artifacts?.catalogError

  /** 折回日志时保留请求组件，下次展开恢复常规高度。 */
  function toggleLogs(open: boolean): void {
    if (!open) setExpanded(false)
    onLogsOpenChange(open)
  }

  const selector = <Select className="internal-api-debug__selector" size="small" aria-label="选择内部接口" showSearch
    placeholder="选择接口" value={selected?.key} disabled={!endpoints.length}
    optionLabelProp="selectedLabel" optionFilterProp="searchText" dropdownMatchSelectWidth={350}
    dropdownStyle={{ maxWidth: 'calc(100vw - 24px)' }} dropdownClassName="internal-api-debug__options"
    getPopupContainer={workspacePopup} onChange={setSelection}
    options={endpoints.map((endpoint) => ({
      value: endpoint.key,
      selectedLabel: <span title={endpoint.name}>{endpoint.name}</span>,
      searchText: `${endpoint.name} ${endpoint.method} ${endpoint.path}`,
      label: <span className="internal-api-debug__option">
        <strong>{endpoint.name}</strong><code>{endpoint.method} {endpoint.path}</code>
      </span>
    }))} />

  const serviceControls = <>
    <Popover placement="bottomRight" trigger="click" getPopupContainer={workspacePopup}
      overlayClassName="internal-api-debug__service-popover"
      content={<div className="internal-api-debug__service-content">
        <InternalBackendServiceCard control={control} address={service?.base} />
        <InternalApiMaintenanceActions control={control} />
      </div>}>
      <Button className="internal-api-debug__utility" icon={<CloudServerOutlined />}
        aria-label={`服务状态：${statusLabel}`} title={statusLabel}>
        服务状态 <span className={`internal-api-debug__service-status is-${statusTone}`} aria-hidden="true" />
      </Button>
    </Popover>
    <Button className="internal-api-debug__utility" icon={<FileTextOutlined />}
      aria-expanded={logsOpen} aria-controls={logsOpen ? logsId : undefined}
      onClick={() => toggleLogs(!logsOpen)}>运行日志</Button>
  </>

  return <section className="internal-api-debug" aria-label="接口调试">
    <div className="internal-api-debug__workspace" hidden={showExpanded}>
      <div className="internal-api-debug__scroll">
        {target && definition
          ? <InternalApiRequest key={internalDebugStorageKey(target, definition)} selector={selector} serviceControls={serviceControls}
              target={target} definition={definition} service={service} canSend={canSend} />
          : <>
              <InternalApiRequestBar selector={selector} address={selected} serviceControls={serviceControls} />
              {detail?.error ? <Alert type="error" showIcon message={detail.error}
                action={<Button size="small" onClick={() => setRetry((value) => value + 1)}>重试</Button>} />
                : catalogLoading || target ? <div className="internal-api-debug__placeholder" role="status"><Spin /> 正在读取接口定义…</div>
                : endpointsError ? <Alert type="error" showIcon message={endpointsError}
                  action={onCatalogRetry ? <Button size="small" onClick={onCatalogRetry}>重试</Button> : undefined} />
                : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前迭代暂无已完成的接口。" />}
            </>}
      </div>
    </div>
    {logsOpen && <div id={logsId} className="internal-api-debug__logs" data-expanded={showExpanded}>
      <InternalApiLogs runtime={runtime} logsExpanded={showExpanded}
        onExpandedChange={setExpanded} onClose={() => toggleLogs(false)} />
    </div>}
  </section>
}
