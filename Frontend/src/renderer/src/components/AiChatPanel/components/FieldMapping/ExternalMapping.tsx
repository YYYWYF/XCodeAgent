import MappingDescription from './MappingDescription'
import { DeleteOutlined, DownOutlined, UndoOutlined, UpOutlined } from '@ant-design/icons'
import { Alert, Button, Dropdown, Typography } from 'antd'
import { useEffect, useState, type ReactElement } from 'react'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import type { WorkflowApiDesignDraft, WorkflowApiField, WorkflowApiSourceField, WorkflowApiValueRight } from '../../../../typings'
import type { ApiDesignExternalOperationMetadata } from '../../../../service/dataSources'
import { apiDesignFieldKey } from '../WorkflowRunCard/apiDesignSerialization'
import { mappingCandidates } from './model'
import { externalTargetEnabled } from './externalRequestTargets'
import ValueRuleEditor from './ValueRuleEditor'
import ResponseFieldMapping, { ResponseFieldMappingHeader } from './ResponseFieldMapping'
import './ExternalMapping.less'

type Props = {
  fields: WorkflowApiField[]
  metadata: ApiDesignExternalOperationMetadata
  selection: BindingSelection
  draft: WorkflowApiDesignDraft
  editable: boolean
  readOnly: boolean
  busy: boolean
  errors: Record<string, string>
  onChange: (draft: WorkflowApiDesignDraft) => void
}
type ExternalField = NonNullable<ApiDesignExternalOperationMetadata['fields']>[number]
const REQUEST_SECTIONS = ['path', 'query', 'header', 'request_body']
const SECTION_LABELS: Record<string, string> = { path: 'Path', query: 'Query', header: 'Header', request_body: 'Body' }

/** 以区段和路径标识当前 Operation 的请求目标。 */
function fieldKey(field: { section: string; path: string }): string { return `${field.section}:${field.path}` }

/** 按外部请求目标配置取值，同一应用入参可被任意目标复用。 */
export default function ExternalMapping({ fields, metadata, selection, draft, editable, readOnly, busy, errors, onChange }: Props): ReactElement {
  const [collapsed, setCollapsed] = useState({ request: !readOnly, response: !readOnly })
  // 编辑态收起、详情态展开，仅在模式变化时恢复默认状态。
  useEffect(() => { setCollapsed({ request: !readOnly, response: !readOnly }) }, [readOnly])
  const targets = (metadata.fields || []).filter((field) => field.section !== 'response_body')
  const bindings = draft.externalApiBindings || []
  const responseFields = fields.filter((field) => field.side === 'response')
  // 保存校验失败时只展开包含错误的映射卡片。
  useEffect(() => {
    if (readOnly || !Object.keys(errors).length) return
    setCollapsed((current) => ({
      request: Object.keys(errors).some((key) => key === '__externalApiBindings' || key.startsWith('__externalApiBinding:')) ? false : current.request,
      response: fields.some((field) => field.side === 'response' && errors[apiDesignFieldKey(field)]) ? false : current.response
    }))
  }, [errors, readOnly, fields])
  const disabled = !editable || busy
  const removedTargets = REQUEST_SECTIONS.flatMap((section) => targets.filter((field) => field.section === section && !externalTargetEnabled(field, selection, bindings)))
  const visibleTargets = REQUEST_SECTIONS.flatMap((section) => targets.filter((field) => field.section === section && !removedTargets.includes(field)))
  /** 验证绑定属于当前选定接口，失效字段保留可清理入口。 */
  const matches = (field: Extract<WorkflowApiSourceField, { sourceType: 'external_api' }>): boolean =>
    selection.sourceType === 'external_api' && field.sourceId === selection.sourceId && field.directoryId === selection.directoryId && field.operationId === selection.operationId
  const stale = bindings.filter((item) => !matches(item.externalField) || !targets.some((field) => fieldKey(field) === fieldKey(item.externalField)))
  /** 更新单个外部目标，唯一性按目标身份保证而不占用输入参数。 */
  const update = (target: ExternalField, right?: WorkflowApiValueRight): void => {
    if (selection.sourceType !== 'external_api') return
    onChange({ ...draft, externalApiBindings: [...bindings.filter((item) => !matches(item.externalField) || fieldKey(item.externalField) !== fieldKey(target)),
      { externalField: { ...selection, section: target.section as WorkflowApiField['location'], path: target.path, type: target.type, description: target.description }, ...(right ? { right } : {}) }] })
  }
  /** 移除可选目标绑定，列表和恢复入口均从草稿派生，切换接口后仍保持一致。 */
  const remove = (target: ExternalField): void => {
    if (disabled || readOnly || target.required) return
    onChange({ ...draft, externalApiBindings: bindings.filter((item) => !matches(item.externalField) || fieldKey(item.externalField) !== fieldKey(target)) })
  }
  /** 恢复已移除参数的配置行，取值需重新填写，不猜测已删除的绑定。 */
  const restore = (key: string): void => {
    const target = removedTargets.find((target) => fieldKey(target) === key)
    if (disabled || readOnly || !target) return
    update(target)
    setCollapsed((current) => ({ ...current, request: false }))
  }
  /** 统一映射卡片的标题与右侧折叠操作。 */
  const heading = (section: 'request' | 'response', title: string): ReactElement => <div className="database-section-heading">
    <div className="database-section-title is-card"><h4>{title}</h4>{section === 'request' && !readOnly ? <span className="external-request-count">{visibleTargets.length} 个参数</span> : null}</div>
    <div className="external-heading-actions">
    {section === 'request' && !readOnly && removedTargets.length > 0 ? <Dropdown overlayClassName="external-restore-menu" placement="bottomRight" trigger={['click']} disabled={disabled} menu={{
      items: REQUEST_SECTIONS.filter((group) => removedTargets.some((target) => target.section === group)).map((group) => ({
        type: 'group' as const, key: group, label: SECTION_LABELS[group],
        children: removedTargets.filter((target) => target.section === group).map((target) => ({
          key: fieldKey(target), label: <span className="external-restore-option"><code title={target.path}>{target.path}</code><span>{target.type}</span></span>
        }))
      })),
      onClick: ({ key }) => restore(key)
    }}><Button className="external-restore-trigger" type="link" disabled={disabled} icon={<UndoOutlined />}>恢复参数 <span className="external-restore-count">{removedTargets.length}</span><DownOutlined /></Button></Dropdown> : null}
    <Button className="database-section-toggle" type="text" aria-expanded={!collapsed[section]} aria-label={`${collapsed[section] ? '展开' : '收起'}${title}`}
      icon={collapsed[section] ? <DownOutlined /> : <UpOutlined />} onClick={() => setCollapsed((current) => ({ ...current, [section]: !current[section] }))} />
    </div>
  </div>
  return <div className={`external-mapping${readOnly ? ' is-readonly' : ''}`}>
    <MappingDescription draft={draft} disabled={disabled} readOnly={readOnly} onChange={onChange} />
    <section className={`binding-section external-mapping-section${collapsed.request ? ' is-collapsed' : ''}`}>
      {heading('request', '入参映射')}
      {!collapsed.request ? <>
      {stale.length ? <Alert type="warning" showIcon message="部分外部入参已失效，请清除后重新配置。" description={stale.map((item) => <div key={JSON.stringify(item.externalField)}>{item.externalField.path}<Button type="link" disabled={disabled} onClick={() => onChange({ ...draft, externalApiBindings: bindings.filter((candidate) => candidate !== item) })}>清除</Button></div>)} /> : null}
      {visibleTargets.length ? <div className={`external-request-editor${readOnly ? ' is-readonly' : ''}`}>
          <div className="external-request-grid external-request-head"><span>参数归属</span><span>外部 API 入参</span>{readOnly ? <><span>值来源</span><span>取值内容</span></> : <><div className="field-value-control"><div className="field-value-inputs"><span>值来源</span><span>取值内容</span></div></div><span /></>}</div>
          {visibleTargets.map((target) => {
            const binding = bindings.find((item) => matches(item.externalField) && fieldKey(item.externalField) === fieldKey(target))
            const error = errors[`__externalApiBinding:${fieldKey(target)}`]
            return <div className="external-request-grid external-request-row" key={fieldKey(target)}>
              <span className="external-request-location">{SECTION_LABELS[target.section]}</span>
              <div className="external-request-field"><code title={target.path}>{target.path}</code><span className="field-response-type">{target.type}</span>{target.required ? <span className="external-request-required" aria-label="必填">*</span> : null}</div>
              <ValueRuleEditor title={target.path} right={binding?.right} fields={fields} type={target.type} readOnly={readOnly} disabled={disabled} onChange={(right) => update(target, right)} />
              {!readOnly ? <Button className="external-request-remove" type="text" icon={<DeleteOutlined />} disabled={disabled || target.required} aria-label={target.required ? `${target.path} 是必填参数，不可移除` : `删除可选参数 ${target.path}`} onClick={() => remove(target)} /> : null}
              {error ? <small className="field-rule-error">{error}</small> : null}
            </div>
          })}
        </div> : null}
      {!targets.length ? <Typography.Text type="secondary">当前外部 API 没有请求参数</Typography.Text> : null}
      {errors.__externalApiBindings ? <small className="field-rule-error">{errors.__externalApiBindings}</small> : null}
      </> : null}
    </section>
    <section className={`binding-section external-mapping-section${collapsed.response ? ' is-collapsed' : ''}`}>
      {heading('response', '返回字段')}
      {!collapsed.response ? <>
      <div className={`database-return-table${readOnly ? ' is-readonly' : ''}`}>{responseFields.length ? <ResponseFieldMappingHeader readOnly={readOnly} /> : null}{responseFields.map((field) => <ResponseFieldMapping key={apiDesignFieldKey(field)} field={field} fields={fields} sources={mappingCandidates(field, selection, metadata)} draft={draft} readOnly={readOnly} disabled={disabled} error={errors[apiDesignFieldKey(field)]} onChange={onChange} />)}</div>
      {!responseFields.length ? <Typography.Text type="secondary">当前应用 API 没有返回字段</Typography.Text> : null}
      </> : null}
    </section>
  </div>
}
