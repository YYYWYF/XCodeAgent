import { Alert, Modal, Spin } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  EndpointDesignPreparation,
  EndpointDesignSaveResult,
  WorkflowApiDesignAction
} from '../../../../typings'
import { requestEndpointDesignPreparation, saveEndpointDesign } from '../../../../service/endpointDesigns'
import ApiDesignPanel from './ApiDesignPanel'
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
  onClose: () => void
  onSaved?: (target: ApiDesignConfigTarget, result: EndpointDesignSaveResult) => void | Promise<void>
}

/** 提供独立于主 Workflow 的 Endpoint 字段映射配置弹窗。 */
export default function ApiDesignConfigModal({
  open,
  target,
  workspaceRoot,
  onClose,
  onSaved
}: Props): ReactElement {
  const [preparation, setPreparation] = useState<EndpointDesignPreparation>()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open || !target || !workspaceRoot) return
    let disposed = false
    setLoading(true)
    setError('')
    setPreparation(undefined)
    requestEndpointDesignPreparation(workspaceRoot, target.apiContractId, target.endpointId)
      .then((value) => {
        if (!disposed) setPreparation(value)
      })
      .catch((reason: unknown) => {
        if (!disposed) setError(reason instanceof Error ? reason.message : '准备 API 映射配置失败。')
      })
      .finally(() => {
        if (!disposed) setLoading(false)
      })
    return () => {
      disposed = true
    }
  }, [open, target, workspaceRoot])

  /** 保存当前弹窗草稿，保存完成后通知门禁刷新，但不自动确认继续开发。 */
  const handleAction = async (action: WorkflowApiDesignAction): Promise<void> => {
    if (!workspaceRoot || !target || saving) return
    setSaving(true)
    setError('')
    try {
      const result = await saveEndpointDesign(workspaceRoot, action, preparation?.artifactRevision)
      await onSaved?.(target, result)
      onClose()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存 API 映射配置失败。')
    } finally {
      setSaving(false)
    }
  }

  /** 关闭弹窗前明确提示草稿不会被保存，避免误丢字段映射。 */
  const handleCancel = (): void => {
    if (saving) return
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
      {error ? <Alert className="api-design-config-modal-error" message={error} showIcon type="error" /> : null}
      {preparation ? (
        <ApiDesignPanel
          disabled={saving}
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
