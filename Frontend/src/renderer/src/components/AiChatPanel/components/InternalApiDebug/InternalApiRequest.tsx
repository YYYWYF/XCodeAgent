import { SendOutlined } from '@ant-design/icons'
import { Alert, Button, Tooltip } from 'antd'
import { useMemo } from 'react'
import type { ReactNode } from 'react'
import { sendInternalApiRequest, type InternalDebugDefinition, type InternalDebugTarget } from '../../../../service/internalApiDebug'
import DebugRequestFields from '../../../DataSourcesPage/DebugRequestFields'
import DebugResponsePanel from '../../../DataSourcesPage/DebugResponsePanel'
import { internalDebugDraft, internalDebugStorageKey } from './internalApiDebugModel'
import { debugRequestUrl, useInternalApiDebugSession } from './useInternalApiDebugSession'
import '../../../DataSourcesPage/ExternalApiDebug.less'

type Address = { method: string; path: string }

/** 将接口地址与调试操作分组，便于面板变窄时整体换行。 */
export function InternalApiRequestBar({ selector, address, serviceControls, busy, onSend, onCancel }: {
  selector: ReactNode; address?: Address; serviceControls: ReactNode
  busy?: boolean; onSend?: () => void; onCancel?: () => void
}) {
  return <div className="internal-api-debug__request-bar">
    <div className="internal-api-debug__endpoint" role="group" aria-label="接口、请求方式和路径">
      {selector}
      {address && <>
        <code className="internal-api-debug__method" data-method={address.method}>{address.method}</code>
        <Tooltip title={address.path}><code className="internal-api-debug__route">{address.path}</code></Tooltip>
      </>}
    </div>
    <div className="internal-api-debug__request-actions" role="group" aria-label="接口调试操作">
      <Button type="primary" className="internal-api-debug__send" icon={busy ? undefined : <SendOutlined />}
        onClick={busy ? onCancel : onSend} disabled={!busy && !onSend}>{busy ? '取消' : '发送'}</Button>
      {serviceControls}
    </div>
  </div>
}

/** 绑定当前内部接口的草稿与调用，不以服务端口作为草稿身份。 */
export default function InternalApiRequest({ selector, serviceControls, target, definition, service, canSend }: {
  selector: ReactNode
  serviceControls: ReactNode
  target: InternalDebugTarget
  definition: InternalDebugDefinition
  service?: { base: string; attemptId: string }
  canSend: boolean
}) {
  const defaults = useMemo(() => internalDebugDraft(definition), [definition])
  const session = useInternalApiDebugSession({
    storageKey: internalDebugStorageKey(target, definition), defaults,
    /** 请求提交复合接口和运行轮次，方法与路径由当前正式定义核对。 */
    request: (values, signal) => {
      if (!canSend || !service) throw new Error('后端服务尚未就绪，请查看服务状态。')
      return sendInternalApiRequest({
        ...target, ...values, attemptId: service.attemptId, expectedMethod: definition.method, expectedPath: definition.path
      }, signal)
    }
  })
  const url = service ? debugRequestUrl(service.base, session.draft) : ''
  const sample = session.result?.response
  return <section className="external-api-debug internal-api-debug__exchange" aria-label="请求与响应">
    <InternalApiRequestBar selector={selector} address={definition} serviceControls={serviceControls} busy={session.busy}
      onSend={canSend ? session.send : undefined} onCancel={session.cancel} />
    {url && <code className="debug-url" title={url}>{url}</code>}
    {!canSend && <Alert className="internal-api-debug__availability" type="info" showIcon
      message="后端服务就绪后可发送请求。" />}
    {session.saveFailed && <p className="debug-save-error" role="status">调试内容保存失败</p>}
    <DebugRequestFields draft={session.draft} auth={session.auth} update={session.setDraft}
      updateAuth={session.setAuth} allowCookie />
    <DebugResponsePanel {...session.responseProps} structureLabel="JSON 字段"
      responseSample={sample?.truncated ? undefined : sample?.body} />
  </section>
}
