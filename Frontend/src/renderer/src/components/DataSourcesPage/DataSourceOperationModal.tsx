import { confirmWorkspaceAction } from '../workspaceDialogs'
import { ApiOutlined } from '@ant-design/icons'
import { Alert, Button, Modal, Select } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useRef, useState } from 'react'
import type { DataSourceDirectory, DataSourceOperation, DataSourceOperationSection } from '../../typings'
import { cx } from '../../utils'
import { OperationFields, operationDraftFromSource, type OperationDraft } from './ExternalApiFormParts'
import { buildJsonSchema } from './jsonSchema'
import { validateOperationParameters } from './dataSourceOperations'
import { configuredSections, validateSelectedSections } from './dataSourceOperationConfig'

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
  onCreateDirectory?: () => void
}

/** 解析可选 JSON 样例并返回用户可理解的错误。 */
function parseSample(value: string, label: string): unknown {
  if (!value.trim()) return undefined
  try { return JSON.parse(value) } catch { throw new Error(`${label}必须是合法 JSON。`) }
}

/** 生成编辑会话快照，确保配置模块选择也参与脏状态判断。 */
function operationSnapshot(operation: OperationDraft, directoryId: string, sections: ReadonlySet<DataSourceOperationSection>): string {
  return JSON.stringify({ operation, directoryId, sections: [...sections].sort() })
}

/** 管理外部 API 单个接口及其目录归属的居中弹窗。 */
export default function DataSourceOperationModal({ directories, editing, initialDirectoryId, onClose, onSave, open, saving, theme, embedded, hideDirectory = false, onDirtyChange, onCreateDirectory }: Props): ReactElement {
  const [operation, setOperation] = useState<OperationDraft>(() => operationDraftFromSource(editing))
  const [directoryId, setDirectoryId] = useState(initialDirectoryId || directories[0]?.id || '')
  const [phase, setPhase] = useState<'selection' | 'edit'>(() => editing ? 'edit' : 'selection')
  const [sections, setSections] = useState<Set<DataSourceOperationSection>>(() => new Set(editing ? configuredSections(editing) : ['responseBody']))
  const [error, setError] = useState('')
  const initializedTargetRef = useRef<string>()
  const baseline = useRef(operationSnapshot(operation, directoryId, sections))
  const dirty = operationSnapshot(operation, directoryId, sections) !== baseline.current
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
    const nextSections = new Set(editing ? configuredSections(editing) : ['responseBody'] as DataSourceOperationSection[])
    baseline.current = operationSnapshot(nextOperation, nextDirectory, nextSections)
    setOperation(nextOperation)
    setDirectoryId(nextDirectory)
    setSections(nextSections)
    setPhase(editing ? 'edit' : 'selection')
    setError('')
  }, [directories, editing, initialDirectoryId, open])

  /** 校验基本信息及路径配置选择，完成新建流程第一步。 */
  const handleNext = (): void => {
    try {
      setError('')
      if (!operation.name.trim()) throw new Error('请输入接口名称。')
      if (!operation.path.trim()) throw new Error('请输入接口路径。')
      if (!directoryId) throw new Error('请选择接口所属目录。')
      if (/\{[^{}\/]+\}/.test(operation.path) && !sections.has('path')) throw new Error('当前路径包含占位符，请选择 Path 参数配置。')
      setPhase('edit')
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : '请先完善基本信息。')
    }
  }

  /** 转换编辑草稿并保存接口与目录归属。 */
  const handleSave = async (): Promise<void> => {
    try {
      setError('')
      if (!operation.name.trim()) throw new Error('请输入接口名称。')
      if (!operation.path.trim()) throw new Error('请输入接口路径。')
      if (!directoryId) throw new Error('请选择接口所属目录。')
      if (operation.description.trim().length > 500) throw new Error('接口描述不能超过 500 个字符。')
      validateSelectedSections(operation, sections)
      const requestSample = parseSample(operation.requestSampleText, '请求样例')
      const responseSample = parseSample(operation.responseSampleText, '响应样例')
      const pathParameters = operation.pathParameters.map(({ rowId: _rowId, ...parameter }) => ({ ...parameter, name: parameter.name.trim() }))
      const queryParameters = operation.queryParameters.map(({ rowId: _rowId, ...parameter }) => ({ ...parameter, name: parameter.name.trim() }))
      const headers = operation.headers.map((header) => ({ name: header.name.trim(), value: header.value.trim() }))
      if (headers.some((header) => !header.name)) throw new Error('Header 名称不能为空。')
      if (new Set(headers.map((header) => header.name.toLowerCase())).size !== headers.length) throw new Error('Header 名称不能重复。')
      validateOperationParameters(operation.path.trim(), pathParameters, queryParameters)
      const next: DataSourceOperation = {
        id: operation.id,
        name: operation.name.trim(),
        description: operation.description.trim(),
        method: operation.method,
        path: operation.path.trim(),
        pathParameters,
        queryParameters,
        headers,
        requestSample,
        responseSample,
        requestStructure: requestSample === undefined ? operation.requestStructure : buildJsonSchema(requestSample, operation.requestFieldDescriptionsDraft, operation.requestFieldTypesDraft),
        responseStructure: responseSample === undefined ? operation.responseStructure : buildJsonSchema(responseSample, operation.responseFieldDescriptionsDraft, operation.responseFieldTypesDraft)
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
      <fieldset disabled={saving} style={{ border: 0, padding: 0, minWidth: 0 }}><OperationFields initiallyCollapsed={false} onChange={setOperation} onSectionsChange={setSections} operation={operation} phase={phase} sections={sections} theme={theme} /></fieldset>
    </div>
  </>
  const actions = <div className={cx('data-source-modal-footer')}>
    <Button disabled={saving} onClick={cancel}>取消</Button>{phase === 'selection' ? <Button disabled={saving || directories.length === 0} onClick={handleNext} type="primary">下一步：填写配置</Button> : <Button disabled={saving || directories.length === 0} loading={saving} onClick={() => void handleSave()} type="primary">保存</Button>}
  </div>
  const modalTitle = <div className={cx('data-source-operation-modal-title')}><span className={cx('data-source-operation-modal-icon')}><ApiOutlined /><small>API</small></span><span className={cx('data-source-operation-modal-title-copy')}><strong>{editing ? '编辑 API' : '新建 API'}</strong><span>配置接口的请求信息、参数及请求/响应体</span></span></div>
  if (embedded) return <div className="source-operation-editor">{form}{actions}</div>
  return (
    <Modal
      bodyStyle={{ maxHeight: 'calc(100vh - 170px)', overflowY: 'auto', padding: '20px 22px 22px' }}
      centered className={cx('data-source-editor-modal')} destroyOnClose
      footer={actions}
      keyboard={!saving} maskClosable={!saving} onCancel={cancel} title={modalTitle} visible={open} width={1040}
      wrapClassName={cx('data-source-editor-modal-wrap', `theme-${theme}`)}
    >
      {form}
    </Modal>
  )
}
