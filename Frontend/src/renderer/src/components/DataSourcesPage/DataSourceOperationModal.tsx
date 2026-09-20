import { confirmWorkspaceAction } from '../workspaceDialogs'
import { Alert, Button, Modal, Select } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useRef, useState } from 'react'
import type { DataSourceDirectory, DataSourceOperation } from '../../typings'
import { cx } from '../../utils'
import { OperationFields, operationDraftFromSource, type OperationDraft } from './ExternalApiFormParts'
import { buildJsonSchema } from './jsonSchema'
import { validateOperationParameters } from './dataSourceOperations'

type Props = {
  directories: DataSourceDirectory[]
  editing?: DataSourceOperation
  initialDirectoryId?: string
  onClose: () => void
  onSave: (operation: DataSourceOperation, directoryId: string) => Promise<void>
  open: boolean
  saving: boolean
  theme: 'light' | 'dark'
  embedded?: boolean
  hideDirectory?: boolean
  onDirtyChange?: (dirty: boolean) => void
  onDelete?: () => void
  onCreateDirectory?: () => void
}

/** 解析可选 JSON 样例并返回用户可理解的错误。 */
function parseSample(value: string, label: string): unknown {
  if (!value.trim()) return undefined
  try { return JSON.parse(value) } catch { throw new Error(`${label}必须是合法 JSON。`) }
}

/** 管理外部 API 单个接口及其目录归属的居中弹窗。 */
export default function DataSourceOperationModal({ directories, editing, initialDirectoryId, onClose, onSave, open, saving, theme, embedded, hideDirectory = false, onDirtyChange, onDelete, onCreateDirectory }: Props): ReactElement {
  const [operation, setOperation] = useState<OperationDraft>(() => operationDraftFromSource(editing))
  const [directoryId, setDirectoryId] = useState(initialDirectoryId || directories[0]?.id || '')
  const [error, setError] = useState('')
  const initializedTargetRef = useRef<string>()
  const baseline = useRef(JSON.stringify({ operation, directoryId }))
  const dirty = JSON.stringify({ operation, directoryId }) !== baseline.current
  useEffect(() => { onDirtyChange?.(dirty) }, [dirty, onDirtyChange])

  /** 独立弹窗自行保护输入；嵌入详情由父级统一保护关闭与切换。 */
  const cancel = (): void => {
    if (saving) return
    if (embedded || !dirty) { onClose(); return }
    confirmWorkspaceAction({ title: '放弃未保存修改？', okText: '放弃修改', cancelText: '继续编辑', onOk: onClose })
  }

  useEffect(() => {
    if (!open) {
      initializedTargetRef.current = undefined
      return
    }
    // 同一编辑会话中目录刷新不重建草稿，只有打开或切换接口才初始化。
    const target = editing?.id || 'new'
    if (initializedTargetRef.current === target) return
    initializedTargetRef.current = target
    const nextOperation = operationDraftFromSource(editing)
    const nextDirectory = initialDirectoryId || directories[0]?.id || ''
    baseline.current = JSON.stringify({ operation: nextOperation, directoryId: nextDirectory })
    setOperation(nextOperation)
    setDirectoryId(nextDirectory)
    setError('')
  }, [directories, editing, initialDirectoryId, open])

  /** 转换编辑草稿并保存接口与目录归属。 */
  const handleSave = async (): Promise<void> => {
    try {
      setError('')
      if (!operation.name.trim()) throw new Error('请输入接口名称。')
      if (!operation.path.trim()) throw new Error('请输入接口路径。')
      if (!directoryId) throw new Error('请选择接口所属目录。')
      const requestSample = parseSample(operation.requestSampleText, '请求样例')
      const responseSample = parseSample(operation.responseSampleText, '响应样例')
      const pathParameters = operation.pathParameters.map(({ rowId: _rowId, ...parameter }) => ({ ...parameter, name: parameter.name.trim() }))
      const queryParameters = operation.queryParameters.map(({ rowId: _rowId, ...parameter }) => ({ ...parameter, name: parameter.name.trim() }))
      validateOperationParameters(operation.path.trim(), pathParameters, queryParameters)
      const next: DataSourceOperation = {
        id: operation.id,
        name: operation.name.trim(),
        method: operation.method,
        path: operation.path.trim(),
        pathParameters,
        queryParameters,
        headers: operation.headers,
        requestSample,
        responseSample,
        requestStructure: buildJsonSchema(requestSample, operation.requestFieldDescriptionsDraft, operation.requestFieldTypesDraft),
        responseStructure: buildJsonSchema(responseSample, operation.responseFieldDescriptionsDraft, operation.responseFieldTypesDraft)
      }
      await onSave(next, directoryId)
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : '接口保存失败。')
    }
  }

  const form = <>
    {error ? <Alert className={cx('data-source-editor-error')} message={error} showIcon type="error" /> : null}
    <div className={cx('data-source-editor-form')}>
      {directories.length === 0 ? <Alert message="当前接口域缺少默认目录，请重新创建接口域。" type="warning" showIcon action={onCreateDirectory ? <Button type="link" onClick={onCreateDirectory}>创建目录</Button> : undefined} /> : hideDirectory ? null : <label><span>所属目录</span><Select disabled={saving} onChange={setDirectoryId} options={directories.map((directory) => ({ label: directory.name, value: directory.id }))} value={directoryId || undefined} /></label>}
      <fieldset disabled={saving} style={{ border: 0, padding: 0, minWidth: 0 }}><OperationFields onChange={setOperation} operation={operation} theme={theme} /></fieldset>
    </div>
  </>
  const actions = <div className={cx('data-source-modal-footer')}>
    {onDelete && editing ? <Button danger disabled={saving} onClick={onDelete}>删除接口</Button> : null}
    <Button disabled={saving} onClick={cancel}>取消</Button><Button disabled={saving || directories.length === 0} loading={saving} onClick={() => void handleSave()} type="primary">保存</Button>
  </div>
  if (embedded) return <div className="source-operation-editor">{form}{actions}</div>
  return (
    <Modal
      bodyStyle={{ maxHeight: 'calc(100vh - 170px)', overflowY: 'auto', padding: '0 28px 28px' }}
      centered className={cx('data-source-editor-modal')} destroyOnClose
      footer={actions}
      keyboard={!saving} maskClosable={!saving} onCancel={cancel} title={editing ? '编辑接口' : '新增接口'} visible={open} width={1100}
      wrapClassName={cx('data-source-editor-modal-wrap', `theme-${theme}`)}
    >
      {form}
    </Modal>
  )
}
