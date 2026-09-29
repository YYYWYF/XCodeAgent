import { Alert, Button, Input, Space, Tag } from 'antd'
import { DownOutlined, UpOutlined } from '@ant-design/icons'
import { useContext, useEffect, useMemo, useState, type ReactElement } from 'react'
import type { WorkflowApiDesignDraft, WorkflowApiField } from '../../../../typings'
import { apiDesignFieldKey, validateApiDesignDraft } from '../WorkflowRunCard/apiDesignSerialization'
import ResponseFieldMapping, { ResponseFieldMappingHeader } from './ResponseFieldMapping'
import { RuleEditingContext } from './RuleEditor'
import { staticFields, type StaticData } from './staticData'
import './StaticMapping.less'
import StaticPreviewParameters from './StaticPreviewParameters'
import { staticPreviewInputs } from './staticPreviewInputs'

type Props = { draft: WorkflowApiDesignDraft; readOnly: boolean; disabled: boolean; onChange: (draft: WorkflowApiDesignDraft) => void }

/** 编辑静态 JSON；未应用修改阻止外层保存，避免确认旧数据。 */
export function StaticDataEditor({ draft, readOnly, disabled, onChange }: Props): ReactElement {
  const saved = draft.staticData === undefined ? '' : JSON.stringify(draft.staticData, null, 2)
  const [text, setText] = useState(saved)
  const [error, setError] = useState('')
  const track = useContext(RuleEditingContext)
  const dirty = !readOnly && text !== saved
  useEffect(() => { setText(saved); setError('') }, [saved])
  useEffect(() => { if (!dirty) return; track(1); return () => track(-1) }, [dirty, track])
  /** 显式校验后替换内容，已有映射保留并交由来源失效校验提示。 */
  const apply = (): void => {
    try {
      const data = JSON.parse(text) as StaticData
      staticFields(data)
      onChange({ ...draft, staticData: data })
      setText(JSON.stringify(data, null, 2)); setError('')
    } catch (reason) { setError(reason instanceof SyntaxError ? 'JSON 格式有误，请检查引号、逗号和括号。' : reason instanceof Error ? reason.message : '静态数据无效。') }
  }
  return <div className="static-data-editor">
    <div className="static-data-toolbar"><Tag>JSON</Tag><span>{Array.isArray(draft.staticData) ? `对象列表 · ${draft.staticData.length} 条` : draft.staticData ? '单个对象' : '尚未配置'}</span></div>
    {readOnly ? <pre>{saved}</pre> : <Input.TextArea aria-label="静态 JSON 数据" spellCheck={false} disabled={disabled} value={text} autoSize={{ minRows: 7, maxRows: 20 }} placeholder={'{\n  "id": 1,\n  "name": "电子产品"\n}'} onChange={(event) => { setText(event.target.value); setError('') }} />}
    <div className="static-data-toolbar static-data-footer"><span>支持对象或同结构对象列表；随接口配置保存，无需连接外部数据源。</span>{!readOnly && <Space>{dirty && <Button disabled={disabled} onClick={() => { setText(saved); setError('') }}>放弃修改</Button>}<Button className="static-data-apply" disabled={disabled || !text.trim()} onClick={apply}>校验并应用数据</Button></Space>}</div>
    {dirty && <p className="static-data-hint">数据尚未应用，请先校验并应用，再暂存或确认映射。</p>}
    {error && <Alert type="error" showIcon message={error} />}
  </div>
}

/** 独立展示数据内容，折叠时保留编辑器及未应用的输入。 */
export function StaticDataCard({ errors, ...props }: Props & { errors: Record<string, string> }): ReactElement {
  const [collapsed, setCollapsed] = useState(!props.readOnly)
  useEffect(() => { setCollapsed(!props.readOnly) }, [props.readOnly])
  useEffect(() => { if (!props.readOnly && errors.__staticData) setCollapsed(false) }, [errors, props.readOnly])
  return <section className={`binding-section static-data-section${collapsed ? ' is-collapsed' : ''}`}>
    <div className="database-section-heading"><div className="database-section-title is-card"><h4>数据内容</h4></div>
      <Button className="database-section-toggle" type="text" aria-label={`${collapsed ? '展开' : '收起'}数据内容`} aria-expanded={!collapsed}
        icon={collapsed ? <DownOutlined /> : <UpOutlined />} onClick={() => setCollapsed((value) => !value)} /></div>
    <div className="database-section-content" hidden={collapsed}>
      <StaticDataEditor {...props} />
      {errors.__staticData && <Alert type="error" showIcon message={errors.__staticData} />}
    </div>
  </section>
}

/** 静态来源只提供返回映射和确定性预览，不展示操作、查询或映射说明。 */
export default function StaticMapping({ draft, fields, readOnly, disabled, previewDisabled, errors, onChange }: Props & { fields: WorkflowApiField[]; previewDisabled: boolean; errors: Record<string, string> }): ReactElement {
  const [collapsed, setCollapsed] = useState(!readOnly)
  const [previewError, setPreviewError] = useState('')
  const [parameterOpen, setParameterOpen] = useState<WorkflowApiDesignDraft>()
  const [previewValidation, setPreviewValidation] = useState<{ draft: WorkflowApiDesignDraft; errors: Record<string, string> }>()
  const fieldErrors = { ...(previewValidation?.draft === draft ? previewValidation.errors : {}), ...errors }
  useEffect(() => { setCollapsed(!readOnly) }, [readOnly])
  // 返回字段校验失败时展开；静态数据错误由数据内容卡片展示。
  useEffect(() => {
    if (!readOnly && fields.some((field) => field.side === 'response' && errors[apiDesignFieldKey(field)])) setCollapsed(false)
  }, [errors, readOnly, fields])
  useEffect(() => { setPreviewError(''); setParameterOpen(undefined) }, [draft])
  const sources = useMemo(() => { try { return staticFields(draft.staticData) } catch { return [] } }, [draft.staticData])
  /** 预览不执行自然语言；不完整或依赖运行时上下文的映射明确报出原因。 */
  const showPreview = (): void => {
    try {
      const validationErrors = validateApiDesignDraft(draft)
      setPreviewValidation({ draft, errors: validationErrors })
      setPreviewError('')
      if (Object.keys(validationErrors).length) {
        // 字段错误交给行内提示；只有 JSON 本身错误在预览入口说明。
        if (validationErrors.__staticData) setPreviewError(validationErrors.__staticData)
        return
      }
      staticPreviewInputs(draft)
      setParameterOpen(draft)
    } catch (reason) { setPreviewError(reason instanceof Error ? reason.message : '无法预览。') }
  }
  return <section className={`binding-section static-mapping${collapsed ? ' is-collapsed' : ''}`}>
    <div className="database-section-heading"><div className="database-section-title is-card"><h4>返回字段</h4></div><Button className="database-section-toggle" type="text" aria-label={`${collapsed ? '展开' : '收起'}返回字段`} aria-expanded={!collapsed} icon={collapsed ? <DownOutlined /> : <UpOutlined />} onClick={() => setCollapsed(!collapsed)} /></div>
    {!collapsed && <div className="database-section-content">
      <div className={`database-return-table${readOnly ? ' is-readonly' : ''}`}><ResponseFieldMappingHeader readOnly={readOnly} />{fields.filter((field) => field.side === 'response').map((field) => <ResponseFieldMapping key={apiDesignFieldKey(field)} draft={draft} field={field} fields={fields} sources={sources} readOnly={readOnly} disabled={disabled} error={fieldErrors[apiDesignFieldKey(field)]} onChange={onChange} />)}</div>
      <div className="static-preview-actions"><Button disabled={previewDisabled || disabled && !readOnly} onClick={showPreview}>预览返回结果</Button></div>
      {previewError && <Alert type="info" showIcon message={previewError} />}
    </div>}
    {parameterOpen === draft && <StaticPreviewParameters draft={draft} onCancel={() => setParameterOpen(undefined)} />}
  </section>
}
