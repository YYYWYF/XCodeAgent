import { Alert, Button, Modal, Spin } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  EndpointDesignPreparation,
  EndpointDesignSaveResult,
  WorkflowApiDesignAction
} from '../../../../typings'
import { requestEndpointDesignPreparation, saveEndpointDesign } from '../../../../service/endpointDesigns'
import ApiDesignPanel from './ApiDesignPanel'
import { AgUiBusinessError } from '../../../../service/agUiBusinessError'
import { endpointRecoveryScope, type EndpointRecoveryReporter } from '../../hooks/useEndpointDesignRecovery'
import './ApiDesignConfigModal.less'

export type ApiDesignConfigTarget = {
  apiContractId: string
  endpointId: string
  label?: string
}

type Props = {
  open: boolean
  target?: ApiDesignConfigTarget
  workspaceRoot?: string
  onEndpointRecovery?: EndpointRecoveryReporter
  onClose: () => void
  onSaved?: (target: ApiDesignConfigTarget, result: EndpointDesignSaveResult) => void | Promise<void>
}

/** 提供独立于主 Workflow 的 Endpoint 字段映射配置弹窗。 */
export default function ApiDesignConfigModal({
  open,
  target,
  workspaceRoot,
  onClose,
  onSaved,
  onEndpointRecovery
}: Props): ReactElement {
  const [preparation, setPreparation] = useState<EndpointDesignPreparation>()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const scope = endpointRecoveryScope(workspaceRoot || '', target)
  const activeScope = useRef('')
  activeScope.current = open ? scope : ''
  const preparationRef = useRef(preparation)
  preparationRef.current = preparation
  const recoveryRef = useRef(onEndpointRecovery)
  recoveryRef.current = onEndpointRecovery
  const retryRef = useRef<() => Promise<void>>(async () => {})
  const locked = useRef(false)
  const generation = useRef(0)
  /** 独立弹窗报告原接口身份，读重试不重放正式保存。 */
  const report = (slot: string, reason?: unknown): void => {
    recoveryRef.current?.({ workspaceRoot: workspaceRoot || '', scopeKey: scope, source: 'advanced', slot, reason, retry: () => retryRef.current() })
  }
  /** 保留已挂载编辑器的草稿，只读取当前版本，变化时提示用户明确重新处理。 */
  const recover = async (): Promise<void> => {
    if (!workspaceRoot || !target || locked.current) return
    locked.current = true
    setLoading(true)
    const requestGeneration = generation.current
    try {
      const fresh = await requestEndpointDesignPreparation(workspaceRoot, target.apiContractId, target.endpointId)
      if (activeScope.current !== scope || generation.current !== requestGeneration) return
      const current = preparationRef.current
      if (!current) setPreparation(fresh)
      setError(current && (current.artifactRevision !== fresh.artifactRevision || current.technicalPlanHash !== fresh.technicalPlanHash)
        ? '服务端契约或正式映射已变化；当前输入保留，请核对后明确重新配置。' : '')
      report('*')
    } catch (reason) {
      if (activeScope.current === scope && generation.current === requestGeneration) { setError(reason instanceof Error ? reason.message : '同步映射状态失败。'); report('read', reason) }
      throw reason
    } finally { locked.current = false; if (generation.current === requestGeneration) setLoading(false) }
  }
  retryRef.current = recover

  useEffect(() => {
    generation.current += 1
    if (!open || !target || !workspaceRoot) return
    let disposed = false
    setLoading(true)
    setError('')
    setPreparation(undefined)
    requestEndpointDesignPreparation(workspaceRoot, target.apiContractId, target.endpointId)
      .then((value) => {
        if (!disposed) { setPreparation(value); report('prepare') }
      })
      .catch((reason: unknown) => {
        if (!disposed) { setError(reason instanceof Error ? reason.message : '准备 API 映射配置失败。'); report('prepare', reason) }
      })
      .finally(() => {
        if (!disposed) setLoading(false)
      })
    return () => {
      disposed = true
      generation.current += 1
      report('*')
    }
  }, [open, target, workspaceRoot])

  /** 保存当前弹窗草稿，保存完成后通知门禁刷新，但不自动确认继续开发。 */
  const handleAction = async (action: WorkflowApiDesignAction): Promise<void> => {
    if (!workspaceRoot || !target || locked.current) return
    locked.current = true
    setSaving(true)
    setError('')
    const requestGeneration = generation.current
    let saved = false
    try {
      const result = await saveEndpointDesign(workspaceRoot, action, preparation?.artifactRevision)
      saved = true
      if (activeScope.current !== scope || generation.current !== requestGeneration) return
      report('*')
      await onSaved?.(target, result)
      onClose()
    } catch (reason) {
      if (activeScope.current === scope && generation.current === requestGeneration) {
        setError(reason instanceof Error ? reason.message : '保存 API 映射配置失败。')
        report('save', saved ? new AgUiBusinessError('映射已保存，但本地门禁刷新失败，请同步状态。') : reason)
      }
    } finally {
      locked.current = false
      setSaving(false)
    }
  }

  /** 关闭弹窗前明确提示草稿不会被保存，避免误丢字段映射。 */
  const handleCancel = (): void => {
    if (locked.current) return
    Modal.confirm({
      title: '放弃本次映射编辑？',
      content: '当前输入尚未保存，关闭后会丢失本次修改。',
      okText: '放弃编辑',
      cancelText: '继续编辑',
      onOk: onClose
    })
  }

  return (
    <Modal
      className="api-design-config-modal"
      destroyOnClose
      footer={null}
      onCancel={handleCancel}
      open={open}
      title={target?.label ? `配置 API 映射：${target.label}` : '配置 API 字段映射'}
      width={1120}
    >
      {loading ? <div className="api-design-config-modal-loading"><Spin /> 正在准备字段映射配置…</div> : null}
      {error ? <Alert className="api-design-config-modal-error" message={error} showIcon type="error" action={<Button loading={loading} disabled={saving} onClick={() => void recover().catch(() => {})}>同步状态</Button>} /> : null}
      {preparation ? (
        <ApiDesignPanel
          disabled={saving || loading}
          onAction={(action) => void handleAction(action)}
          payload={preparation.payload}
          submitHint="保存配置后返回开发门禁，可继续配置其他接口；全部配置完成后点击“确认”统一检测，检测通过后再确认继续开发。"
          submitLabel="保存配置"
          workspaceRoot={workspaceRoot}
        />
      ) : null}
    </Modal>
  )
}
