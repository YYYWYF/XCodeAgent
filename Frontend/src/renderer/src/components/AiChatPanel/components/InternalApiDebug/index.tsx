import { FileTextOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Select, Spin } from 'antd'
import { useEffect, useId, useMemo, useState } from 'react'
import type { DevelopmentArtifacts, DevelopmentPlanningApiContract } from '../../../../typings'
import { describeInternalApi, type InternalDebugDefinition, type InternalDebugTarget } from '../../../../service/internalApiDebug'
import { cx } from '../../../../utils'
import { previewServiceInMaintenance } from '../../../../service/previewRuntime'
import type { DebugServiceControl } from '../../hooks/usePreviewRuntime'
import DebugServiceLogs from '../DebugRuntime/DebugServiceLogs'
import DebugServiceStatusButton from '../DebugRuntime/DebugServiceControls'
import InternalApiRequest, { InternalApiRequestBar } from './InternalApiRequest'
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
  control: DebugServiceControl
  logsOpen: boolean
  onLogsOpenChange: (open: boolean) => void
}
type DefinitionState = { key: string; value?: InternalDebugDefinition; error?: string }

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
  const launchingBackend = control.busyLaunchLabel && control.busyLaunchLayer !== 'frontend'
  const canSend = Boolean(service && !launchingBackend && control.busyStopLayer !== 'backend' &&
    !previewServiceInMaintenance(runtime, 'backend'))
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
    <DebugServiceStatusButton control={control} layer="backend" address={service?.base} />
    <Button className="debug-runtime-utility" icon={<FileTextOutlined />}
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
    {logsOpen && <DebugServiceLogs id={logsId} layer="backend" runtime={runtime} logsExpanded={showExpanded}
      onExpandedChange={setExpanded} onClose={() => toggleLogs(false)} />}
  </section>
}
