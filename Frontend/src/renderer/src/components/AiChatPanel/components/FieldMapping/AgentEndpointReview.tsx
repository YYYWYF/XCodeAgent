import { Alert, Button, Descriptions, Space, Spin, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type { EndpointDesignSaveResult } from '../../../../typings'
import { requestEndpointPreparation, confirmRuntimeEndpointDesign, saveEndpointDesign } from '../../../../service/endpointDesigns'
import { isRuntimeEndpointPreparation } from '../../../../typings/endpointDesign'
import type { RuntimeEndpointDraft, EndpointPreparation } from '../../../../typings/endpointDesign'
import type { WorkflowApiDesignDraft } from '../../../../typings'

type Target = { apiContractId: string; endpointId: string }
type Props = { workspaceRoot: string; target: Target; preparation?: EndpointPreparation; onSaved: (target: Target, result: EndpointDesignSaveResult) => void | Promise<void> }

/** 在业务映射 Hook 挂载前审阅运行接口，不加载数据源目录。 */
export default function AgentEndpointReview({ workspaceRoot, target, preparation: initialPreparation, onSaved }: Props): ReactElement {
  const [preparation, setPreparation] = useState<EndpointPreparation>()
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
    requestEndpointPreparation(workspaceRoot, target.apiContractId, target.endpointId)
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
      const result = isRuntimeEndpointPreparation(preparation)
        ? await confirmRuntimeEndpointDesign(workspaceRoot, preparation)
        : await saveEndpointDesign(workspaceRoot, { action: 'confirm', ...target, draft: preparation.payload.draft },
          preparation.artifactRevision, { technicalPlanHash: preparation.technicalPlanHash })
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
    {preparation ? <>{isRuntimeEndpointPreparation(preparation)
      ? <AgentEndpointContract draft={preparation.payload.draft} /> : <AgentCallContract draft={preparation.payload.draft} />}
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
      <Descriptions.Item label="执行方式">{draft.interactionContract.mode === 'task' ? '单次业务任务' : '多轮会话'}</Descriptions.Item>
      <Descriptions.Item label="结果">{draft.interactionContract.resultSchema ? '经过 Schema 校验的结构化结果' : '文本回答'}</Descriptions.Item>
      <Descriptions.Item label="能力">内部工具、技能、知识按正式七模块配置启用，交互及写入继续遵守确认合同</Descriptions.Item>
    </Descriptions>
    <Alert type="info" showIcon message="本次确认只签认接入基线，不会调用模型或启动开发。修改智能体能力须进入正式修订。" />
    <Typography.Paragraph type="secondary">会话按可信用户、智能体和线程隔离。同一线程执行互斥；断流或停止后结果可能未知，系统不会自动重发。</Typography.Paragraph>
  </Space>
}

/** 业务接口只审阅正式调用映射，修改目标、返回方式或字段映射仍进入技术规划修订。 */
export function AgentCallContract({ draft }: { draft: Pick<WorkflowApiDesignDraft, 'agentCall'> }): ReactElement {
  const call = draft.agentCall
  if (!call) return <Alert type="error" showIcon message="缺少当前 Agent 调用合同，请重新读取技术规划。" />
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Typography.Title level={5}>业务接口智能体调用设计</Typography.Title>
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="目标智能体">{call.agentId}</Descriptions.Item>
      <Descriptions.Item label="返回方式">{call.responseMode === 'json' ? '一次性 JSON 业务响应' : 'AG-UI 流式结果'}</Descriptions.Item>
      <Descriptions.Item label="业务参数 → Agent 输入">{Object.entries(call.inputBindings).map(([target, source]) =>
        <div key={target}><code>{target || '整个对象'}</code> ← <code>{source || '整个对象'}</code></div>)}</Descriptions.Item>
      <Descriptions.Item label="Agent 结果 → 业务响应">{Object.entries(call.resultBindings).map(([target, source]) =>
        <div key={target}><code>{target || '整个对象'}</code> ← <code>{source || '整个对象'}</code></div>)}</Descriptions.Item>
    </Descriptions>
    <Typography.Paragraph type="secondary">复用当前智能体的内部执行能力。待交互不表示检测成功；修改调用合同请发起技术规划修订，再确认新版设计。</Typography.Paragraph>
  </Space>
}
