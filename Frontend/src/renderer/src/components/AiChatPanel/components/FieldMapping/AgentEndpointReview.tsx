import { Alert, Button, Descriptions, Space, Spin, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type { EndpointDesignSaveResult } from '../../../../typings'
import { requestRuntimeEndpointPreparation, confirmRuntimeEndpointDesign } from '../../../../service/endpointDesigns'
import type { RuntimeEndpointDraft, RuntimeEndpointPreparation } from '../../../../typings/endpointDesign'

type Target = { apiContractId: string; endpointId: string }
type Props = { workspaceRoot: string; target: Target; preparation?: RuntimeEndpointPreparation; onSaved: (target: Target, result: EndpointDesignSaveResult) => void | Promise<void> }

/** 在业务映射 Hook 挂载前审阅运行接口，不加载数据源目录。 */
export default function AgentEndpointReview({ workspaceRoot, target, preparation: initialPreparation, onSaved }: Props): ReactElement {
  const [preparation, setPreparation] = useState<RuntimeEndpointPreparation>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [generation, setGeneration] = useState(0)
  const [saved, setSaved] = useState<EndpointDesignSaveResult>()
  const [syncPending, setSyncPending] = useState(false)
  const locked = useRef(false)
  const requestGeneration = useRef(0)
  useEffect(() => {
    let disposed = false
    requestGeneration.current += 1
    locked.current = false
    setSaved(undefined); setSyncPending(false)
    if (generation === 0 && initialPreparation && initialPreparation.apiContractId === target.apiContractId
      && initialPreparation.endpointId === target.endpointId) {
      setPreparation(initialPreparation); setBusy(false); setError('')
      return () => { disposed = true; requestGeneration.current += 1 }
    }
    setBusy(true); setError(''); setPreparation(undefined)
    requestRuntimeEndpointPreparation(workspaceRoot, target.apiContractId, target.endpointId)
      .then((value) => { if (!disposed) setPreparation(value) })
      .catch((reason: unknown) => { if (!disposed) setError(reason instanceof Error ? reason.message : '读取接入设计失败。') })
      .finally(() => { if (!disposed) setBusy(false) })
    return () => { disposed = true; requestGeneration.current += 1 }
  }, [workspaceRoot, target.apiContractId, target.endpointId, generation, initialPreparation])
  /** 确认当前候选与并发版本，不启动开发或调用模型。 */
  const confirm = async (): Promise<void> => {
    if (!preparation || busy || locked.current || saved) return
    if (preparation.apiContractId !== target.apiContractId || preparation.endpointId !== target.endpointId) return
    locked.current = true
    const currentGeneration = requestGeneration.current
    setBusy(true); setError('')
    try {
      const result = await confirmRuntimeEndpointDesign(workspaceRoot, preparation)
      if (requestGeneration.current !== currentGeneration) return
      setPreparation({ ...preparation, artifactRevision: result.artifactRevision })
      setSaved(result)
      try { await onSaved(target, result) }
      catch {
        if (requestGeneration.current === currentGeneration) {
          setSyncPending(true); setError('接入设计已保存，但工作台状态同步失败。请同步已保存状态，无需再次确认。')
        }
      }
    } catch (reason) {
      if (requestGeneration.current === currentGeneration) setError(reason instanceof Error ? reason.message : '确认接入设计失败。')
    } finally {
      if (requestGeneration.current === currentGeneration) { locked.current = false; setBusy(false) }
    }
  }
  /** 保存后只重试本地状态通知，不能把状态刷新失败当成再次提交设计。 */
  const syncSaved = async (): Promise<void> => {
    if (!saved || locked.current) return
    const currentGeneration = requestGeneration.current
    locked.current = true; setBusy(true)
    try {
      await onSaved(target, saved)
      if (requestGeneration.current === currentGeneration) { setSyncPending(false); setError('') }
    } catch {
      if (requestGeneration.current === currentGeneration) setError('接入设计已保存，工作台状态仍未同步，请稍后再次同步。')
    } finally {
      if (requestGeneration.current === currentGeneration) { locked.current = false; setBusy(false) }
    }
  }
  return <Space direction="vertical" style={{ width: '100%' }}>
    {busy && !preparation ? <Spin /> : null}
    {error ? <Alert type={saved ? 'warning' : 'error'} showIcon message={error} action={syncPending
      ? <Button disabled={busy} onClick={() => void syncSaved()}>同步已保存状态</Button>
      : <Button disabled={busy} onClick={() => setGeneration((value) => value + 1)}>重新读取</Button>} /> : null}
    {saved && <Alert type="success" showIcon message="接入设计已确认并保存。" description="回到 API 设计前置检查，点击“确认并检测”继续。" />}
    {preparation ? <><AgentEndpointContract draft={preparation.payload.draft} />
      <Button type="primary" disabled={busy || Boolean(saved) || Boolean(error)} loading={busy} onClick={() => void confirm()}>
        {saved ? '接入设计已确认' : '审阅并确认接入设计'}</Button></> : null}
  </Space>
}

/** 展示固定调用合同和确认边界，所有状态继承既有明暗主题。 */
export function AgentEndpointContract({ draft }: { draft: RuntimeEndpointDraft }): ReactElement {
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Typography.Title level={5}>智能体流式接口接入设计</Typography.Title>
    <Descriptions bordered column={1} size="small">
      <Descriptions.Item label="API Contract">{draft.apiContractId}</Descriptions.Item>
      <Descriptions.Item label="Endpoint">{draft.endpointId}</Descriptions.Item>
      <Descriptions.Item label="绑定智能体">{draft.sourceBinding.agentId}</Descriptions.Item>
      <Descriptions.Item label="调用">POST {draft.invocation.path}</Descriptions.Item>
      <Descriptions.Item label="协议">AG-UI / SSE，官方客户端</Descriptions.Item>
      <Descriptions.Item label="能力">文本会话；内部工具、技能、知识按正式七模块配置启用，交互及写入继续遵守确认合同</Descriptions.Item>
    </Descriptions>
    <Alert type="info" showIcon message="本次确认只签认接入基线，不会调用模型或启动开发。修改智能体能力须进入正式修订。" />
    <Typography.Paragraph type="secondary">会话按页面与智能体隔离。同一线程执行互斥；断流或停止后结果可能未知，请明确新建会话，系统不会自动重发。</Typography.Paragraph>
  </Space>
}
