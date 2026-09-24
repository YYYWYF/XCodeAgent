import { Alert, Button, Input, Spin, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type { DevelopmentArtifactProgress } from '../../../../typings'
import {
  generateDirectEndpointDraft,
  readDirectEndpointContract,
  saveDirectEndpointContract
} from '../../../../service/directDevelopment'
import type { DirectEndpointContract } from '../../../../service/directDevelopment'
import { cx } from '../../../../utils'
import DevelopmentTargetDetail from './DevelopmentTargetDetail'

type Props = {
  apiContractId: string
  endpointId: string
  title: string
  summary?: string
  progress?: DevelopmentArtifactProgress
  disabled?: boolean
  workspaceRoot?: string
  onStartDevelopment: () => void
}

/** 编辑请求体和响应体 JSON Schema，明确区分契约确认与接口代码完成。 */
export default function DirectApiContractPanel({
  apiContractId, endpointId, title, summary, progress, disabled, workspaceRoot,
  onStartDevelopment
}: Props): ReactElement {
  const [contract, setContract] = useState<DirectEndpointContract>()
  const [requestText, setRequestText] = useState('null')
  const [responseText, setResponseText] = useState('{}')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [edited, setEdited] = useState(false)

  useEffect(() => {
    let active = true
    if (!workspaceRoot) return undefined
    setBusy(true)
    setError('')
    setContract(undefined)
    readDirectEndpointContract(workspaceRoot, apiContractId, endpointId)
      .then((value) => {
        if (!active) return
        setContract(value)
        setRequestText(JSON.stringify(value.requestSchema, null, 2))
        setResponseText(JSON.stringify(value.responseSchema, null, 2))
        setEdited(false)
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'API 契约读取失败。')
      })
      .finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [apiContractId, endpointId, workspaceRoot])

  /** 单次生成可编辑草稿，不将模型输出自动确认。 */
  const handleGenerate = async (): Promise<void> => {
    if (!workspaceRoot || busy) return
    setBusy(true)
    setError('')
    try {
      const value = await generateDirectEndpointDraft(workspaceRoot, apiContractId, endpointId)
      setContract(value)
      setRequestText(JSON.stringify(value.requestSchema, null, 2))
      setResponseText(JSON.stringify(value.responseSchema, null, 2))
      setEdited(true)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '模型生成 API 契约失败。')
    } finally {
      setBusy(false)
    }
  }

  /** 校验编辑器 JSON 并保存用户审核后的正式 API 契约。 */
  const handleSave = async (): Promise<void> => {
    if (!workspaceRoot || !contract || busy) return
    setBusy(true)
    setError('')
    try {
      const requestSchema = JSON.parse(requestText) as Record<string, unknown> | null
      const responseSchema = JSON.parse(responseText) as Record<string, unknown>
      const saved = await saveDirectEndpointContract(
        workspaceRoot, contract, requestSchema, responseSchema
      )
      setContract(saved)
      setEdited(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'API 契约保存失败。')
    } finally {
      setBusy(false)
    }
  }

  const confirmed = contract?.status === 'confirmed' && !edited
  return (
    <DevelopmentTargetDetail
      actionLabel="开始开发接口代码"
      description={summary}
      disabled={disabled || busy || !confirmed}
      extra={
        <div className={cx('direct-api-contract')}>
          <Alert
            message="请求体与响应体可由模型起草，也可直接编辑；保存即确认契约。实体 SQL 的确认不是保存 API 契约的前置条件。"
            showIcon
            type="info"
          />
          {busy ? <p><Spin /> 正在处理接口契约…</p> : null}
          {error ? <Alert message={error} showIcon type="error" /> : null}
          {contract?.status === 'stale' ? (
            <Alert message="TechnicalPlan 已变化，API 契约需重新确认；当前展示的是最新计划草稿。" showIcon type="warning" />
          ) : null}
          <Typography.Text type="secondary">关联实体：{contract?.entityIds.join('、') || '无'}</Typography.Text>
          <div className={cx('direct-api-contract-actions')}>
            <Button disabled={busy || !workspaceRoot} onClick={() => { void handleGenerate() }}>模型生成草稿</Button>
            <Button disabled={busy || !contract} onClick={() => { void handleSave() }} type="primary">保存并确认契约</Button>
          </div>
          {confirmed ? <Alert message="API 契约已确认。接口代码仍须单独开发和测试。" showIcon type="success" /> : null}
          <label htmlFor="direct-request-schema">请求体 JSON Schema（无请求体填 null）</label>
          <Input.TextArea
            id="direct-request-schema"
            autoSize={{ minRows: 6, maxRows: 20 }}
            disabled={busy || !contract}
            value={requestText}
            onChange={(event) => { setRequestText(event.target.value); setEdited(true) }}
          />
          <label htmlFor="direct-response-schema">响应体 JSON Schema</label>
          <Input.TextArea
            id="direct-response-schema"
            autoSize={{ minRows: 8, maxRows: 24 }}
            disabled={busy || !contract}
            value={responseText}
            onChange={(event) => { setResponseText(event.target.value); setEdited(true) }}
          />
        </div>
      }
      hint="先确认 API 契约，再启动接口代码生成；接口完成状态以 Build 和测试结果为准。"
      kind="endpoint"
      progress={progress}
      subtitle={`${contract?.method || ''} ${contract?.path || ''}`}
      title={title}
      onStart={onStartDevelopment}
    />
  )
}
